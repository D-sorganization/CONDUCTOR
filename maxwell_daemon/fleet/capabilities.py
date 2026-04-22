"""Fleet capability registry and Tailscale-aware node selection.

This module is intentionally self-contained: it models node capabilities,
resource snapshots, policy checks, and the in-memory registry used to pick a
dispatch target. No I/O is performed here. Callers hand in snapshots and get a
structured selection result with explicit rejection reasons.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from maxwell_daemon.contracts import require

__all__ = [
    "FleetAssignment",
    "FleetNode",
    "InMemoryFleetCapabilityRegistry",
    "NodeCapability",
    "NodeDecision",
    "NodePolicy",
    "NodeResourceSnapshot",
    "TailscalePeerStatus",
    "parse_tailscale_status_json",
]


@dataclass(frozen=True, slots=True)
class NodeCapability:
    """One capability observation on a node."""

    name: str
    observed_at: datetime
    value: str | int | float | bool | None = None

    def __post_init__(self) -> None:
        require(bool(self.name.strip()), "capability name must not be empty")
        _require_aware_datetime(self.observed_at, "capability timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class NodeResourceSnapshot:
    """Operational snapshot for a node at a point in time."""

    captured_at: datetime
    heartbeat_at: datetime | None
    active_sessions: int

    def __post_init__(self) -> None:
        _require_aware_datetime(self.captured_at, "captured_at must be timezone-aware")
        if self.heartbeat_at is not None:
            _require_aware_datetime(
                self.heartbeat_at,
                "heartbeat_at must be timezone-aware when present",
            )
        require(self.active_sessions >= 0, "active_sessions must be non-negative")


@dataclass(frozen=True, slots=True)
class NodePolicy:
    """Allow-list policy and concurrency limits for a node."""

    allowed_repos: frozenset[str] | None = None
    allowed_tools: frozenset[str] | None = None
    max_concurrent_sessions: int = 1
    heartbeat_stale_after_seconds: int = 300

    def __post_init__(self) -> None:
        require(self.max_concurrent_sessions > 0, "max_concurrent_sessions must be positive")
        require(
            self.heartbeat_stale_after_seconds > 0,
            "heartbeat_stale_after_seconds must be positive",
        )
        if self.allowed_repos is not None:
            object.__setattr__(self, "allowed_repos", frozenset(self.allowed_repos))
        if self.allowed_tools is not None:
            object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))


@dataclass(frozen=True, slots=True)
class TailscalePeerStatus:
    """Parsed Tailscale peer status for a node."""

    peer_id: str
    hostname: str
    online: bool
    tailnet_ip: str | None = None
    current_address: str | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FleetNode:
    """A dispatchable fleet node."""

    node_id: str
    hostname: str
    capabilities: tuple[NodeCapability, ...]
    resource_snapshot: NodeResourceSnapshot
    policy: NodePolicy
    tailscale_status: TailscalePeerStatus | None = None

    def __post_init__(self) -> None:
        require(bool(self.node_id.strip()), "node_id must not be empty")
        require(bool(self.hostname.strip()), "hostname must not be empty")
        names = [cap.name for cap in self.capabilities]
        require(len(names) == len(set(names)), "capability names must be unique per node")

    @property
    def capability_names(self) -> frozenset[str]:
        return frozenset(cap.name for cap in self.capabilities)


@dataclass(frozen=True, slots=True)
class NodeDecision:
    """Selection outcome for a single node."""

    node_id: str
    node_name: str
    score: int | None
    reasons: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.score is not None and not self.reasons


@dataclass(frozen=True, slots=True)
class FleetAssignment:
    """Routing result for a single repo/tool request."""

    repo: str
    tool: str
    required_capabilities: tuple[str, ...]
    selected_node: FleetNode | None
    rejected_nodes: tuple[NodeDecision, ...]
    explanation: str


class InMemoryFleetCapabilityRegistry:
    """Pure in-memory registry used by the first capability-routing slice."""

    def __init__(self, nodes: tuple[FleetNode, ...] = ()) -> None:
        self._nodes: dict[str, FleetNode] = {node.node_id: node for node in nodes}

    def upsert(self, node: FleetNode) -> None:
        self._nodes[node.node_id] = node

    def remove(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)

    def list_nodes(self) -> tuple[FleetNode, ...]:
        return tuple(self._nodes[node_id] for node_id in sorted(self._nodes))

    def eligible_nodes(
        self,
        *,
        repo: str,
        tool: str,
        required_capabilities: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> tuple[NodeDecision, ...]:
        decisions, _ = self._evaluate_nodes(
            repo=repo,
            tool=tool,
            required_capabilities=required_capabilities,
            now=now,
        )
        return decisions

    def select(
        self,
        *,
        repo: str,
        tool: str,
        required_capabilities: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> FleetAssignment:
        decisions, scored_nodes = self._evaluate_nodes(
            repo=repo,
            tool=tool,
            required_capabilities=required_capabilities,
            now=now,
        )
        if not scored_nodes:
            return FleetAssignment(
                repo=repo,
                tool=tool,
                required_capabilities=required_capabilities,
                selected_node=None,
                rejected_nodes=decisions,
                explanation=_summarize_selection(repo, tool, None, decisions),
            )
        score, node = scored_nodes[0]
        for candidate_score, candidate_node in scored_nodes[1:]:
            if candidate_score > score:
                score = candidate_score
                node = candidate_node
                continue
            if candidate_score == score and (
                candidate_node.hostname,
                candidate_node.node_id,
            ) < (
                node.hostname,
                node.node_id,
            ):
                node = candidate_node
        return FleetAssignment(
            repo=repo,
            tool=tool,
            required_capabilities=required_capabilities,
            selected_node=node,
            rejected_nodes=tuple(
                decision for decision in decisions if decision.node_id != node.node_id
            ),
            explanation=_summarize_selection(repo, tool, node, decisions),
        )

    def _evaluate_nodes(
        self,
        *,
        repo: str,
        tool: str,
        required_capabilities: tuple[str, ...],
        now: datetime | None,
    ) -> tuple[tuple[NodeDecision, ...], tuple[tuple[int, FleetNode], ...]]:
        current_time = now or datetime.now(timezone.utc)
        decisions: list[NodeDecision] = []
        scored_nodes: list[tuple[int, FleetNode]] = []
        for node in self.list_nodes():
            score, reasons = _evaluate_node(
                node,
                repo=repo,
                tool=tool,
                required_capabilities=required_capabilities,
                now=current_time,
            )
            decisions.append(
                NodeDecision(
                    node_id=node.node_id,
                    node_name=node.hostname,
                    score=score,
                    reasons=reasons,
                )
            )
            if score is not None and not reasons:
                scored_nodes.append((score, node))
        return tuple(decisions), tuple(scored_nodes)


def parse_tailscale_status_json(raw: str | Mapping[str, Any]) -> tuple[TailscalePeerStatus, ...]:
    """Parse a Tailscale status JSON payload into a stable tuple of peers."""

    payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    peers = payload.get("Peer") or payload.get("Peers") or payload.get("peers") or {}
    peer_items: list[tuple[str, Mapping[str, Any]]]
    if isinstance(peers, Mapping):
        peer_items = [(str(peer_id), peer_data) for peer_id, peer_data in peers.items()]
    elif isinstance(peers, list):
        peer_items = []
        for index, peer_data in enumerate(peers):
            if not isinstance(peer_data, Mapping):
                continue
            peer_id = str(peer_data.get("ID") or peer_data.get("id") or index)
            peer_items.append((peer_id, peer_data))
    else:
        raise ValueError("tailscale status payload must contain Peer, Peers, or peers data")

    statuses = [
        TailscalePeerStatus(
            peer_id=peer_id,
            hostname=str(peer_data.get("HostName") or peer_data.get("hostname") or peer_id),
            online=bool(peer_data.get("Online", peer_data.get("online", False))),
            tailnet_ip=str(peer_data.get("TailscaleIPs", [None])[0])
            if peer_data.get("TailscaleIPs")
            else peer_data.get("tailnet_ip"),
            current_address=_extract_current_address(peer_data),
            last_seen_at=_parse_timestamp(peer_data.get("LastSeen") or peer_data.get("last_seen")),
        )
        for peer_id, peer_data in sorted(peer_items, key=lambda item: item[0])
    ]
    return tuple(statuses)


def _evaluate_node(
    node: FleetNode,
    *,
    repo: str,
    tool: str,
    required_capabilities: tuple[str, ...],
    now: datetime,
) -> tuple[int | None, tuple[str, ...]]:
    reasons: list[str] = []
    policy = node.policy

    if policy.allowed_repos is not None and repo not in policy.allowed_repos:
        reasons.append(f"repo {repo!r} not allowed")
    if policy.allowed_tools is not None and tool not in policy.allowed_tools:
        reasons.append(f"tool {tool!r} not allowed")

    missing_capabilities = tuple(
        capability
        for capability in required_capabilities
        if capability not in node.capability_names
    )
    if missing_capabilities:
        reasons.append(
            "missing capabilities: "
            + ", ".join(repr(capability) for capability in missing_capabilities)
        )

    snapshot = node.resource_snapshot
    if snapshot.active_sessions >= policy.max_concurrent_sessions:
        reasons.append(
            "max concurrent sessions reached "
            f"({snapshot.active_sessions}/{policy.max_concurrent_sessions})"
        )

    heartbeat_at = snapshot.heartbeat_at
    if heartbeat_at is None:
        reasons.append("heartbeat missing")
    else:
        age_seconds = max(0, int((now - heartbeat_at).total_seconds()))
        if age_seconds > policy.heartbeat_stale_after_seconds:
            reasons.append(
                f"heartbeat stale ({age_seconds}s > {policy.heartbeat_stale_after_seconds}s)"
            )

    if node.tailscale_status is not None and not node.tailscale_status.online:
        reasons.append("tailscale peer offline")

    if reasons:
        return None, tuple(reasons)

    freshness_seconds = 0
    if heartbeat_at is not None:
        freshness_seconds = max(0, policy.heartbeat_stale_after_seconds - age_seconds)
    remaining_sessions = policy.max_concurrent_sessions - snapshot.active_sessions
    score = (remaining_sessions * 1_000_000) + freshness_seconds
    return score, ()


def _summarize_selection(
    repo: str,
    tool: str,
    selected_node: FleetNode | None,
    decisions: tuple[NodeDecision, ...],
) -> str:
    rejected = [decision for decision in decisions if decision.reasons]
    if selected_node is None:
        return (
            f"no eligible node for repo {repo!r} and tool {tool!r}; "
            f"rejections={_format_rejections(rejected)}"
        )
    return (
        f"selected {selected_node.hostname!r} for repo {repo!r} and tool {tool!r}; "
        f"rejections={_format_rejections(rejected)}"
    )


def _format_rejections(rejections: list[NodeDecision]) -> str:
    if not rejections:
        return "[]"
    parts = [
        f"{decision.node_name}:{'|'.join(decision.reasons)}"
        for decision in sorted(
            rejections, key=lambda decision: (decision.node_name, decision.node_id)
        )
    ]
    return "[" + "; ".join(parts) + "]"


def _require_aware_datetime(value: datetime, message: str) -> None:
    require(value.tzinfo is not None and value.utcoffset() is not None, message)


def _extract_current_address(peer_data: Mapping[str, Any]) -> str | None:
    current_addr = peer_data.get("CurAddr") or peer_data.get("current_address")
    return str(current_addr) if current_addr is not None else None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        _require_aware_datetime(value, "parsed timestamp must be timezone-aware")
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        _require_aware_datetime(parsed, "parsed timestamp must be timezone-aware")
        return parsed
    raise ValueError(f"unsupported timestamp value: {value!r}")

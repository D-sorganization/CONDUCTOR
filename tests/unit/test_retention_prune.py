"""Retention / TTL pruning for tasks, cost ledger, and audit log (#148)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maxwell_daemon.audit import AuditLogger, verify_chain
from maxwell_daemon.backends import TokenUsage
from maxwell_daemon.config.models import RetentionConfig
from maxwell_daemon.core import CostLedger, CostRecord
from maxwell_daemon.core.pruner import prune_once
from maxwell_daemon.core.task_store import TaskStore
from maxwell_daemon.daemon.runner import Task, TaskKind, TaskStatus

# ── helpers ──────────────────────────────────────────────────────────────────


def _fresh_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": uuid.uuid4().hex[:12],
        "prompt": "hello",
        "kind": TaskKind.PROMPT,
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _set_task_timestamps(store: TaskStore, task_id: str, *, when: datetime) -> None:
    """Back-date a task's created_at/updated_at/finished_at to *when*.

    Tests can't use the public API to write timestamps in the past, so we poke
    directly at the SQLite file the store opens. This mirrors the store's own
    connection pattern.
    """
    iso = when.isoformat()
    conn = sqlite3.connect(store._path)
    try:
        conn.execute(
            "UPDATE tasks SET created_at = ?, updated_at = ?, finished_at = ? WHERE id = ?",
            (iso, iso, iso, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def _cost_record(ts: datetime, cost: float = 0.1) -> CostRecord:
    return CostRecord(
        ts=ts,
        backend="claude",
        model="claude-sonnet-4-6",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        cost_usd=cost,
        repo="test/repo",
        agent_id="agent-x",
    )


# ── TaskStore.prune ──────────────────────────────────────────────────────────


class TestTaskStorePrune:
    @pytest.fixture
    def store(self, tmp_path: Path) -> TaskStore:
        return TaskStore(tmp_path / "tasks.db")

    def test_drops_old_terminal_tasks(self, store: TaskStore) -> None:
        old = _fresh_task()
        store.save(old)
        store.update_status(old.id, TaskStatus.COMPLETED, finished_at=datetime.now(timezone.utc))
        _set_task_timestamps(store, old.id, when=datetime.now(timezone.utc) - timedelta(days=45))

        removed = store.prune(retention_days=30, max_tasks=10_000)
        assert removed == 1
        assert store.get(old.id) is None

    def test_below_cap_and_within_ttl_keeps_everything(self, store: TaskStore) -> None:
        for _ in range(5):
            t = _fresh_task()
            store.save(t)
            store.update_status(t.id, TaskStatus.COMPLETED)
        # All tasks just written — well inside the 30-day TTL.
        removed = store.prune(retention_days=30, max_tasks=10_000)
        assert removed == 0
        assert len(store.list_tasks(limit=100)) == 5

    def test_row_cap_drops_oldest_terminal_first(self, store: TaskStore) -> None:
        # Create 5 old, completed tasks (eligible), 1 fresh queued one (not eligible).
        # min_age_seconds=0 lets the cap delete recent terminal tasks too.
        old_ids: list[str] = []
        base = datetime.now(timezone.utc) - timedelta(days=10)
        for i in range(5):
            t = _fresh_task()
            store.save(t)
            store.update_status(t.id, TaskStatus.COMPLETED)
            # Stagger timestamps so we know which is oldest.
            _set_task_timestamps(store, t.id, when=base + timedelta(hours=i))
            old_ids.append(t.id)

        queued = _fresh_task()
        store.save(queued)

        # Cap to 3 rows → must drop 3 oldest terminal tasks. Queued is never eligible.
        removed = store.prune(retention_days=365, max_tasks=3, min_age_seconds=0)
        assert removed == 3
        survivors = {t.id for t in store.list_tasks(limit=100)}
        assert queued.id in survivors  # queued always preserved
        # The two most-recent terminal tasks survive.
        assert old_ids[3] in survivors
        assert old_ids[4] in survivors
        # The three oldest are gone.
        for oid in old_ids[:3]:
            assert oid not in survivors

    def test_unknown_status_is_never_pruned(self, store: TaskStore) -> None:
        """QUEUED / RUNNING / DISPATCHED tasks must survive regardless of age."""
        for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.DISPATCHED):
            t = _fresh_task()
            store.save(t)
            store.update_status(t.id, status)
            _set_task_timestamps(store, t.id, when=datetime.now(timezone.utc) - timedelta(days=365))

        removed = store.prune(retention_days=1, max_tasks=1, min_age_seconds=0)
        assert removed == 0
        assert len(store.list_tasks(limit=100)) == 3

    def test_safety_floor_protects_recent_rows(self, store: TaskStore) -> None:
        """Even with an absurdly low cap, rows younger than min_age_seconds survive."""
        for _ in range(5):
            t = _fresh_task()
            store.save(t)
            store.update_status(t.id, TaskStatus.COMPLETED)
        # Default min_age_seconds=3600 blocks deletion of anything recent.
        removed = store.prune(retention_days=30, max_tasks=1)
        assert removed == 0


# ── CostLedger.prune ─────────────────────────────────────────────────────────


class TestCostLedgerPrune:
    @pytest.fixture
    def ledger(self, tmp_path: Path) -> CostLedger:
        return CostLedger(tmp_path / "ledger.db")

    def test_drops_old_rows(self, ledger: CostLedger) -> None:
        old_ts = datetime.now(timezone.utc) - timedelta(days=90)
        ledger.record(_cost_record(old_ts, cost=1.0))
        ledger.record(_cost_record(datetime.now(timezone.utc), cost=2.0))

        removed = ledger.prune(retention_days=30, max_rows=10_000)
        assert removed == 1
        total = ledger.total_since(datetime.now(timezone.utc) - timedelta(days=365))
        assert total == pytest.approx(2.0)

    def test_below_cap_within_ttl_keeps_everything(self, ledger: CostLedger) -> None:
        for _ in range(5):
            ledger.record(_cost_record(datetime.now(timezone.utc), cost=0.1))
        removed = ledger.prune(retention_days=30, max_rows=10_000)
        assert removed == 0

    def test_row_cap_drops_oldest(self, ledger: CostLedger) -> None:
        base = datetime.now(timezone.utc) - timedelta(days=10)
        for i in range(5):
            ledger.record(_cost_record(base + timedelta(hours=i), cost=float(i)))
        # Cap at 2 rows; safety floor disabled.
        removed = ledger.prune(retention_days=365, max_rows=2, min_age_seconds=0)
        assert removed == 3
        # Surviving rows are the two newest — costs 3.0 and 4.0 → sum 7.0.
        total = ledger.total_since(datetime.now(timezone.utc) - timedelta(days=365))
        assert total == pytest.approx(7.0)

    def test_safety_floor_blocks_cap_deletion_of_recent(self, ledger: CostLedger) -> None:
        for _ in range(5):
            ledger.record(_cost_record(datetime.now(timezone.utc), cost=0.1))
        removed = ledger.prune(retention_days=30, max_rows=1)  # default min_age=3600
        assert removed == 0


# ── AuditLogger.prune ────────────────────────────────────────────────────────


class TestAuditLoggerPrune:
    def test_rotate_removes_old_entries(self, tmp_path: Path) -> None:
        logger = AuditLogger(tmp_path / "audit.jsonl", retention_days=30)
        logger.log_api_call(method="GET", path="/x", status=200)
        # Back-date first entry to 60 days ago.
        raw = (tmp_path / "audit.jsonl").read_text().splitlines()
        assert len(raw) == 1
        import json

        obj = json.loads(raw[0])
        obj["timestamp"] = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        (tmp_path / "audit.jsonl").write_text(json.dumps(obj) + "\n")
        # Add a fresh entry.
        logger2 = AuditLogger(tmp_path / "audit.jsonl", retention_days=30)
        logger2.log_api_call(method="GET", path="/y", status=200)

        removed = logger2.prune()
        assert removed >= 1
        # The chain must remain valid after pruning.
        assert verify_chain(tmp_path / "audit.jsonl") == []

    def test_below_cap_keeps_everything(self, tmp_path: Path) -> None:
        logger = AuditLogger(tmp_path / "audit.jsonl", retention_days=30, max_entries=1000)
        for i in range(5):
            logger.log_api_call(method="GET", path=f"/p{i}", status=200)
        removed = logger.prune()
        assert removed == 0

    def test_row_cap_drops_oldest_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        logger = AuditLogger(
            path,
            retention_days=365,  # disable TTL trimming for this test
            max_entries=3,
            min_age_seconds=0,  # disable safety floor so cap can act
        )
        import json

        # Write 5 old entries manually (back-dated beyond the safety floor).
        lines: list[str] = []
        prev = "0" * 64
        import hashlib

        for i in range(5):
            obj: dict[str, object] = {
                "timestamp": (
                    datetime.now(timezone.utc) - timedelta(days=5) + timedelta(minutes=i)
                ).isoformat(),
                "event_type": "api_call",
                "method": "GET",
                "path": f"/old{i}",
                "status": 200,
                "user": None,
                "request_id": None,
                "details": {},
                "prev_hash": prev,
            }
            payload = json.dumps(obj, sort_keys=True)
            obj["entry_hash"] = hashlib.sha256(payload.encode()).hexdigest()
            prev = obj["entry_hash"]  # type: ignore[assignment]
            lines.append(json.dumps(obj, separators=(",", ":")))
        path.write_text("\n".join(lines) + "\n")

        removed = logger.prune()
        # 5 entries capped to 3 → should drop 2; plus the rotation event adds 1
        # back, so total entries after prune == 3 (cap) + 1 (rotation) = 4.
        assert removed >= 2
        # Chain must verify cleanly after a cap prune.
        assert verify_chain(path) == []

    def test_safety_floor_blocks_recent_cap_prune(self, tmp_path: Path) -> None:
        logger = AuditLogger(
            tmp_path / "audit.jsonl",
            retention_days=30,
            max_entries=1,
            min_age_seconds=3600,  # 1 hour
        )
        for i in range(5):
            logger.log_api_call(method="GET", path=f"/p{i}", status=200)
        removed = logger.prune()
        assert removed == 0  # all entries younger than 1 hour


# ── prune_once integration ───────────────────────────────────────────────────


class TestPruneOnce:
    def test_touches_all_three_stores(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path / "tasks.db")
        ledger = CostLedger(tmp_path / "ledger.db")
        audit = AuditLogger(tmp_path / "audit.jsonl", retention_days=30, max_entries=100)

        # Old task (completed, 60 days ago)
        t = _fresh_task()
        store.save(t)
        store.update_status(t.id, TaskStatus.COMPLETED)
        _set_task_timestamps(store, t.id, when=datetime.now(timezone.utc) - timedelta(days=60))
        # Old ledger row
        ledger.record(_cost_record(datetime.now(timezone.utc) - timedelta(days=60)))
        # One fresh audit entry (won't be pruned, but function must not error).
        audit.log_api_call(method="GET", path="/health", status=200)

        cfg = RetentionConfig(retention_days=30)
        n_tasks, n_ledger, n_audit = prune_once(cfg, task_store=store, ledger=ledger, audit=audit)
        assert n_tasks == 1
        assert n_ledger == 1
        # Audit has only fresh entries → nothing to remove.
        assert n_audit == 0

    def test_none_args_are_safe(self) -> None:
        cfg = RetentionConfig()
        n_tasks, n_ledger, n_audit = prune_once(cfg)
        assert (n_tasks, n_ledger, n_audit) == (0, 0, 0)

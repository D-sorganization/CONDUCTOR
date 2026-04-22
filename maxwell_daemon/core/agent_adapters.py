"""Gate-aware external agent adapter contract.

Adapters expose coding tools and model-backed delegates through a small,
policy-friendly contract. The scheduler and gate runtime can reason over this
metadata without knowing provider-specific command lines or SDK details.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maxwell_daemon.backends import BackendResponse, ILLMBackend, Message, MessageRole
from maxwell_daemon.core.artifacts import Artifact

__all__ = [
    "AdapterCapability",
    "AdapterOperation",
    "AdapterRegistry",
    "AdapterRegistryError",
    "AdapterRunRequest",
    "AdapterRunResult",
    "AdapterRunStatus",
    "BackendExternalAgentAdapter",
    "CodexCLIExternalAgentAdapter",
    "ExternalAgentAdapter",
]

_WRITE_OPERATIONS = frozenset({"implement"})
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|token)\s*=\s*([^\s]+)"
)


class AdapterOperation(str, Enum):
    PROBE = "probe"
    PLAN = "plan"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VALIDATE = "validate"
    CHECKPOINT = "checkpoint"
    CANCEL = "cancel"


class AdapterRunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    POLICY_VIOLATION = "policy_violation"


class AdapterCapability(BaseModel):
    """Static and probed capability metadata for one adapter."""

    model_config = ConfigDict(use_enum_values=False)

    adapter_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    version: str | None = None
    supported_roles: tuple[str, ...] = ()
    supported_operations: tuple[AdapterOperation, ...]
    capability_tags: tuple[str, ...] = ()
    context_limits: dict[str, int] = Field(default_factory=dict)
    cost_model: str | None = None
    required_credentials: tuple[str, ...] = ()
    required_binaries: tuple[str, ...] = ()
    workspace_requirements: tuple[str, ...] = ()
    can_edit_files: bool = False
    can_run_tests: bool = False
    supports_background: bool = False
    safety_notes: tuple[str, ...] = ()

    @field_validator(
        "supported_roles",
        "capability_tags",
        "required_credentials",
        "required_binaries",
        "workspace_requirements",
        "safety_notes",
    )
    @classmethod
    def _reject_blank_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("capability entries must be non-empty")
        return values

    @field_validator("supported_operations")
    @classmethod
    def _require_operations(
        cls, values: tuple[AdapterOperation, ...]
    ) -> tuple[AdapterOperation, ...]:
        if not values:
            raise ValueError("supported_operations must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError("supported_operations must be unique")
        return values

    @model_validator(mode="after")
    def _write_capability_matches_operations(self) -> AdapterCapability:
        if AdapterOperation.IMPLEMENT in self.supported_operations and not self.can_edit_files:
            raise ValueError("implement support requires can_edit_files=True")
        return self


class AdapterRunRequest(BaseModel):
    """One governed adapter invocation."""

    model_config = ConfigDict(use_enum_values=False)

    adapter_id: str = Field(..., min_length=1)
    operation: AdapterOperation
    task_id: str = Field(..., min_length=1)
    work_item_id: str | None = Field(default=None, min_length=1)
    prompt: str = ""
    workspace_root: Path | None = None
    allow_writes: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_execution_contract(self) -> AdapterRunRequest:
        if self.operation.value in _WRITE_OPERATIONS and self.workspace_root is None:
            raise ValueError("write operations require workspace_root")
        if self.operation is AdapterOperation.REVIEW and self.allow_writes:
            raise ValueError("review operations must be read-only")
        return self


class AdapterRunResult(BaseModel):
    """Structured evidence returned by an adapter run."""

    model_config = ConfigDict(use_enum_values=False, arbitrary_types_allowed=True)

    adapter_id: str = Field(..., min_length=1)
    operation: AdapterOperation
    task_id: str = Field(..., min_length=1)
    status: AdapterRunStatus
    summary: str = Field(..., min_length=1)
    changed_files: tuple[str, ...] = ()
    commands_run: tuple[str, ...] = ()
    tests_run: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    cost_estimate_usd: float | None = Field(default=None, ge=0)
    stdout_snippet: str | None = None
    stderr_snippet: str | None = None
    checkpoint: str | None = None
    policy_warnings: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "changed_files", "commands_run", "tests_run", "artifact_ids", "policy_warnings"
    )
    @classmethod
    def _reject_blank_result_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("result entries must be non-empty")
        return values

    @model_validator(mode="after")
    def _redact_sensitive_result_data(self) -> AdapterRunResult:
        self.stdout_snippet = _redact_text(self.stdout_snippet)
        self.stderr_snippet = _redact_text(self.stderr_snippet)
        self.raw = _redact_mapping(self.raw)
        return self

    @classmethod
    def unavailable(
        cls,
        *,
        adapter_id: str,
        operation: AdapterOperation,
        task_id: str,
        summary: str,
    ) -> AdapterRunResult:
        return cls(
            adapter_id=adapter_id,
            operation=operation,
            task_id=task_id,
            status=AdapterRunStatus.UNAVAILABLE,
            summary=summary,
        )


class ExternalAgentAdapter(Protocol):
    """Protocol implemented by external coding-tool adapters."""

    def capability(self) -> AdapterCapability:
        """Return static capability metadata and last known probe information."""

    async def run(self, request: AdapterRunRequest) -> AdapterRunResult:
        """Run one governed operation and return structured evidence."""

    async def cancel(self, task_id: str) -> AdapterRunResult:
        """Best-effort cancellation hook."""


class AdapterRegistryError(ValueError):
    """Raised when adapter registration violates the registry contract."""


class AdapterRegistry:
    """Small in-memory registry for external agent adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, ExternalAgentAdapter] = {}

    def register(self, adapter: ExternalAgentAdapter) -> None:
        capability = adapter.capability()
        if capability.adapter_id in self._adapters:
            raise AdapterRegistryError(f"adapter {capability.adapter_id!r} already registered")
        self._adapters[capability.adapter_id] = adapter

    def get(self, adapter_id: str) -> ExternalAgentAdapter | None:
        return self._adapters.get(adapter_id)

    def capabilities(self) -> list[AdapterCapability]:
        return [adapter.capability() for adapter in self._adapters.values()]

    async def run(self, request: AdapterRunRequest) -> AdapterRunResult:
        adapter = self._adapters.get(request.adapter_id)
        if adapter is None:
            return AdapterRunResult.unavailable(
                adapter_id=request.adapter_id,
                operation=request.operation,
                task_id=request.task_id,
                summary=f"adapter {request.adapter_id!r} is not registered",
            )
        capability = adapter.capability()
        if request.operation not in capability.supported_operations:
            return AdapterRunResult.unavailable(
                adapter_id=request.adapter_id,
                operation=request.operation,
                task_id=request.task_id,
                summary=(
                    f"adapter {request.adapter_id!r} does not support {request.operation.value!r}"
                ),
            )
        return await adapter.run(request)

    async def cancel(self, adapter_id: str, *, task_id: str) -> AdapterRunResult:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            return AdapterRunResult.unavailable(
                adapter_id=adapter_id,
                operation=AdapterOperation.CANCEL,
                task_id=task_id,
                summary=f"adapter {adapter_id!r} is not registered",
            )
        return await adapter.cancel(task_id)


class BackendExternalAgentAdapter:
    """Expose an existing LLM backend through the external-agent contract."""

    def __init__(
        self,
        *,
        adapter_id: str,
        display_name: str,
        backend: ILLMBackend,
        model: str,
        version: str | None = None,
        supported_operations: Sequence[AdapterOperation] = (
            AdapterOperation.PROBE,
            AdapterOperation.PLAN,
            AdapterOperation.REVIEW,
            AdapterOperation.CHECKPOINT,
            AdapterOperation.CANCEL,
        ),
    ) -> None:
        self._backend = backend
        self._model = model
        self._capability = AdapterCapability(
            adapter_id=adapter_id,
            display_name=display_name,
            version=version,
            supported_roles=("planner", "reviewer"),
            supported_operations=tuple(supported_operations),
            capability_tags=("llm-backend", backend.name),
            context_limits={"max_context_tokens": backend.capabilities(model).max_context_tokens},
            cost_model="backend-token-estimate",
            required_credentials=(),
            required_binaries=(),
            workspace_requirements=(),
            can_edit_files=False,
            can_run_tests=False,
            supports_background=False,
            safety_notes=("Read-only wrapper around an existing LLM backend.",),
        )

    def capability(self) -> AdapterCapability:
        return self._capability

    async def run(self, request: AdapterRunRequest) -> AdapterRunResult:
        if request.operation is AdapterOperation.PROBE:
            healthy = await self._backend.health_check()
            status = AdapterRunStatus.SUCCESS if healthy else AdapterRunStatus.UNAVAILABLE
            return AdapterRunResult(
                adapter_id=self._capability.adapter_id,
                operation=request.operation,
                task_id=request.task_id,
                status=status,
                summary="backend probe succeeded" if healthy else "backend probe failed",
                raw={"healthy": healthy, "backend": self._backend.name},
            )

        response = await self._backend.complete(
            [Message(role=MessageRole.USER, content=_operation_prompt(request))],
            model=self._model,
        )
        return _response_to_result(
            adapter_id=self._capability.adapter_id,
            request=request,
            response=response,
        )

    async def cancel(self, task_id: str) -> AdapterRunResult:
        return AdapterRunResult(
            adapter_id=self._capability.adapter_id,
            operation=AdapterOperation.CANCEL,
            task_id=task_id,
            status=AdapterRunStatus.CANCELLED,
            summary="best-effort cancellation recorded; backend call may already be complete",
            policy_warnings=("backend wrapper has no provider-level cancellation handle",),
        )


class CodexCLIExternalAgentAdapter(BackendExternalAgentAdapter):
    """Read-only external-agent wrapper for the existing Codex CLI backend."""

    def __init__(
        self,
        *,
        model: str,
        backend: ILLMBackend | None = None,
        adapter_id: str = "codex-cli-readonly",
        binary: str = "codex",
        profile: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        if backend is None:
            from maxwell_daemon.backends.codex_cli import CodexCLIBackend

            backend = CodexCLIBackend(
                binary=binary,
                approval="suggest",
                profile=profile,
                timeout=timeout,
            )
        super().__init__(
            adapter_id=adapter_id,
            display_name="Codex CLI Read-Only",
            backend=backend,
            model=model,
            version=None,
            supported_operations=(
                AdapterOperation.PROBE,
                AdapterOperation.PLAN,
                AdapterOperation.REVIEW,
                AdapterOperation.CHECKPOINT,
                AdapterOperation.CANCEL,
            ),
        )
        self._capability = self._capability.model_copy(
            update={
                "required_binaries": (binary,),
                "workspace_requirements": ("assigned worktree recommended for context",),
                "safety_notes": (
                    "Uses Codex CLI in suggest approval mode through the existing backend.",
                    "Does not expose write-capable implement operation in this first contract slice.",
                ),
            }
        )


def _operation_prompt(request: AdapterRunRequest) -> str:
    if request.operation is AdapterOperation.PLAN:
        return f"Create a concise implementation plan.\n\n{request.prompt}".strip()
    if request.operation is AdapterOperation.REVIEW:
        return f"Review the work and return structured findings only.\n\n{request.prompt}".strip()
    if request.operation is AdapterOperation.VALIDATE:
        return f"Propose validation commands and expected evidence.\n\n{request.prompt}".strip()
    if request.operation is AdapterOperation.CHECKPOINT:
        return f"Summarize recoverable state for handoff.\n\n{request.prompt}".strip()
    return request.prompt


def _response_to_result(
    *,
    adapter_id: str,
    request: AdapterRunRequest,
    response: BackendResponse,
) -> AdapterRunResult:
    cost_estimate = None
    if response.usage.total_tokens:
        cost_estimate = None
    return AdapterRunResult(
        adapter_id=adapter_id,
        operation=request.operation,
        task_id=request.task_id,
        status=AdapterRunStatus.SUCCESS,
        summary=response.content or "backend returned an empty response",
        checkpoint=response.content if request.operation is AdapterOperation.CHECKPOINT else None,
        cost_estimate_usd=cost_estimate,
        raw={"finish_reason": response.finish_reason, "backend": response.backend},
    )


def _looks_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=***REDACTED***", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        redacted[key] = "***REDACTED***" if _looks_secret_key(key) else _redact_value(item)
    return redacted

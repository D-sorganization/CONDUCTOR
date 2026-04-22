from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from maxwell_daemon.backends import (
    BackendCapabilities,
    BackendResponse,
    ILLMBackend,
    Message,
    TokenUsage,
)
from maxwell_daemon.core.agent_adapters import (
    AdapterCapability,
    AdapterOperation,
    AdapterRegistry,
    AdapterRegistryError,
    AdapterRunRequest,
    AdapterRunResult,
    AdapterRunStatus,
    BackendExternalAgentAdapter,
    CodexCLIExternalAgentAdapter,
)
from maxwell_daemon.core.artifacts import Artifact, ArtifactKind


class FakeBackend(ILLMBackend):
    name = "fake-backend"

    def __init__(self, *, response_text: str = "ok", healthy: bool = True) -> None:
        self.response_text = response_text
        self.healthy = healthy
        self.messages: list[Message] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> BackendResponse:
        self.messages = messages
        return BackendResponse(
            content=self.response_text,
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model=model,
            backend=self.name,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        yield self.response_text

    async def health_check(self) -> bool:
        return self.healthy

    def capabilities(self, model: str) -> BackendCapabilities:
        return BackendCapabilities(max_context_tokens=4096)


class FakeAdapter:
    def __init__(self, capability: AdapterCapability) -> None:
        self._capability = capability

    def capability(self) -> AdapterCapability:
        return self._capability

    async def run(self, request: AdapterRunRequest) -> AdapterRunResult:
        return AdapterRunResult(
            adapter_id=request.adapter_id,
            operation=request.operation,
            task_id=request.task_id,
            status=AdapterRunStatus.SUCCESS,
            summary="done",
            changed_files=("src/example.py",),
        )

    async def cancel(self, task_id: str) -> AdapterRunResult:
        return AdapterRunResult(
            adapter_id=self._capability.adapter_id,
            operation=AdapterOperation.CANCEL,
            task_id=task_id,
            status=AdapterRunStatus.CANCELLED,
            summary="best-effort cancellation recorded",
        )


def _capability(
    adapter_id: str,
    operations: tuple[AdapterOperation, ...] = (AdapterOperation.PLAN,),
    *,
    can_edit_files: bool = False,
) -> AdapterCapability:
    return AdapterCapability(
        adapter_id=adapter_id,
        display_name=adapter_id.title(),
        supported_operations=operations,
        can_edit_files=can_edit_files,
    )


def test_registry_rejects_duplicate_adapter_ids() -> None:
    registry = AdapterRegistry()
    adapter = FakeAdapter(_capability("codex-cli"))

    registry.register(adapter)

    with pytest.raises(AdapterRegistryError, match="already registered"):
        registry.register(adapter)


def test_missing_required_operation_returns_unavailable_result(tmp_path: Path) -> None:
    registry = AdapterRegistry()
    registry.register(FakeAdapter(_capability("planner", (AdapterOperation.PLAN,))))
    request = AdapterRunRequest(
        adapter_id="planner",
        operation=AdapterOperation.IMPLEMENT,
        task_id="task-1",
        workspace_root=tmp_path,
        allow_writes=True,
    )

    result = asyncio.run(registry.run(request))

    assert result.status is AdapterRunStatus.UNAVAILABLE
    assert "does not support" in result.summary


def test_write_operation_requires_workspace_assignment() -> None:
    with pytest.raises(ValueError, match="workspace_root"):
        AdapterRunRequest(
            adapter_id="writer",
            operation=AdapterOperation.IMPLEMENT,
            task_id="task-1",
            allow_writes=True,
        )


def test_review_operation_cannot_request_write_permission(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="read-only"):
        AdapterRunRequest(
            adapter_id="reviewer",
            operation=AdapterOperation.REVIEW,
            task_id="task-1",
            workspace_root=tmp_path,
            allow_writes=True,
        )


def test_adapter_result_preserves_changed_files_and_artifacts() -> None:
    artifact = Artifact(
        id="artifact-1",
        task_id="task-1",
        kind=ArtifactKind.PLAN,
        name="plan",
        media_type="text/plain",
        path=Path("task-1/plan.txt"),
        sha256="0" * 64,
        size_bytes=4,
        created_at=datetime.now(timezone.utc),
    )

    result = AdapterRunResult(
        adapter_id="writer",
        operation=AdapterOperation.IMPLEMENT,
        task_id="task-1",
        status=AdapterRunStatus.SUCCESS,
        summary="implemented",
        changed_files=("src/app.py",),
        artifacts=(artifact,),
        artifact_ids=("artifact-1",),
    )

    assert result.changed_files == ("src/app.py",)
    assert result.artifacts == (artifact,)
    assert result.artifact_ids == ("artifact-1",)


def test_probe_result_redacts_secret_values() -> None:
    result = AdapterRunResult(
        adapter_id="probe",
        operation=AdapterOperation.PROBE,
        task_id="task-1",
        status=AdapterRunStatus.SUCCESS,
        summary="probe complete",
        stdout_snippet="token=abc123 ok=true",
        raw={"api_key": "sk-secret", "nested": {"token": "abc123", "safe": "visible"}},
    )

    assert result.stdout_snippet == "token=***REDACTED*** ok=true"
    assert result.raw["api_key"] == "***REDACTED***"
    assert result.raw["nested"]["token"] == "***REDACTED***"
    assert result.raw["nested"]["safe"] == "visible"


def test_cancellation_is_best_effort_and_recorded() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAdapter(_capability("runner", (AdapterOperation.CANCEL,))))

    result = asyncio.run(registry.cancel("runner", task_id="task-1"))

    assert result.operation is AdapterOperation.CANCEL
    assert result.status is AdapterRunStatus.CANCELLED
    assert "cancellation" in result.summary


def test_backend_adapter_wraps_existing_backend_for_plan() -> None:
    backend = FakeBackend(response_text="plan output")
    adapter = BackendExternalAgentAdapter(
        adapter_id="codex-cli-readonly",
        display_name="Codex CLI Read-Only",
        backend=backend,
        model="gpt-test",
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    request = AdapterRunRequest(
        adapter_id="codex-cli-readonly",
        operation=AdapterOperation.PLAN,
        task_id="task-1",
        prompt="ship the feature",
    )

    result = asyncio.run(registry.run(request))

    assert result.status is AdapterRunStatus.SUCCESS
    assert result.summary == "plan output"
    assert adapter.capability().capability_tags == ("llm-backend", "fake-backend")
    assert "Create a concise implementation plan" in backend.messages[0].content


def test_codex_cli_adapter_exposes_readonly_existing_backend_contract() -> None:
    backend = FakeBackend(response_text="review output")
    adapter = CodexCLIExternalAgentAdapter(model="gpt-test", backend=backend)
    capability = adapter.capability()

    assert capability.adapter_id == "codex-cli-readonly"
    assert capability.required_binaries == ("codex",)
    assert capability.can_edit_files is False
    assert AdapterOperation.IMPLEMENT not in capability.supported_operations
    assert AdapterOperation.REVIEW in capability.supported_operations

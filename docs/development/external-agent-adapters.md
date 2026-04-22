# External Agent Adapter Template

Use this checklist when adding an Aider, Continue, Codex CLI, Cline, OpenHands,
or local-runner adapter.

## Capability Metadata

Every adapter must provide an `AdapterCapability` with:

- stable `adapter_id`
- display name and optional version
- supported roles and operations
- capability tags
- context limits
- cost or quota model
- required credentials and binaries
- workspace requirements
- write/test/background support flags
- safety notes and terms boundaries

Do not declare `implement` unless the adapter can safely edit inside an assigned
workspace. Review adapters must remain read-only.

## Run Contract

Every run accepts an `AdapterRunRequest` and returns an `AdapterRunResult`.
Results should include:

- status and summary
- changed files
- commands and tests run
- artifact ids or artifact metadata
- cost/quota estimate when known
- stdout/stderr snippets
- checkpoint text for recovery
- policy warnings or violations

Probe and run metadata must be redacted before it reaches UI or persisted
evidence.

## Minimal Adapter Skeleton

```python
from maxwell_daemon.core.agent_adapters import (
    AdapterCapability,
    AdapterOperation,
    AdapterRunRequest,
    AdapterRunResult,
    AdapterRunStatus,
)


class MyToolAdapter:
    def capability(self) -> AdapterCapability:
        return AdapterCapability(
            adapter_id="my-tool",
            display_name="My Tool",
            supported_operations=(AdapterOperation.PROBE, AdapterOperation.REVIEW),
            required_binaries=("my-tool",),
            can_edit_files=False,
        )

    async def run(self, request: AdapterRunRequest) -> AdapterRunResult:
        return AdapterRunResult(
            adapter_id="my-tool",
            operation=request.operation,
            task_id=request.task_id,
            status=AdapterRunStatus.SUCCESS,
            summary="completed",
        )

    async def cancel(self, task_id: str) -> AdapterRunResult:
        return AdapterRunResult(
            adapter_id="my-tool",
            operation=AdapterOperation.CANCEL,
            task_id=task_id,
            status=AdapterRunStatus.CANCELLED,
            summary="best-effort cancellation recorded",
        )
```

## Tests

Add focused tests for:

- duplicate adapter id rejection
- unsupported operations returning `unavailable`
- write operations requiring `workspace_root`
- review operations staying read-only
- evidence preserving changed files and artifacts
- probe/run data redaction
- best-effort cancellation recording

import asyncio
from typing import Any

import pytest

from maxwell_daemon.core.execution_sandbox import ExecutionSandbox


@pytest.mark.asyncio
async def test_execution_sandbox_cleanup_flags() -> None:
    # We test the interface and guarantee that the --rm flag is present
    sandbox = ExecutionSandbox()

    # We won't actually run docker in unit tests if it's not installed,
    # but we can mock the subprocess call to verify the contract.
    class MockProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"hello from sandbox", b""

    # Intercept create_subprocess_exec
    original_exec = asyncio.create_subprocess_exec
    cmd_run: list[Any] = []

    async def mock_exec(*args: Any, **kwargs: Any) -> MockProcess:
        cmd_run.extend(args)
        return MockProcess()

    asyncio.create_subprocess_exec = mock_exec  # type: ignore[assignment]
    try:
        result = await sandbox.run_command("echo hello")

        assert result.exit_code == 0
        assert result.stdout == "hello from sandbox"
        assert "docker" in cmd_run
        assert "--rm" in cmd_run  # Strict verification of disk space cleanup
        assert "--network" in cmd_run
        assert "none" in cmd_run  # Strict verification of isolation
    finally:
        asyncio.create_subprocess_exec = original_exec

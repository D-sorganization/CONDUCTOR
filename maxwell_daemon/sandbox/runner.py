"""Execution wrapper for sandbox policy decisions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from maxwell_daemon.contracts import ensure
from maxwell_daemon.sandbox.policy import DecisionStatus, GateDecision, GateEvidence, SandboxPolicy


@dataclass(slots=True, frozen=True)
class SandboxRunResult:
    """Raw execution result from an injected command executor."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str = ""


class CommandExecutor(Protocol):
    async def execute(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> SandboxRunResult: ...


class SubprocessCommandExecutor:
    """Small subprocess adapter kept separate from sandbox policy evaluation."""

    async def execute(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> SandboxRunResult:
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=dict(env),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            return SandboxRunResult(
                returncode=proc.returncode,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                duration_seconds=time.monotonic() - start,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxRunResult(
                returncode=None,
                duration_seconds=time.monotonic() - start,
                timed_out=True,
                error=f"timeout after {timeout_seconds:g}s",
            )
        except OSError as exc:
            return SandboxRunResult(
                returncode=None,
                duration_seconds=time.monotonic() - start,
                error=str(exc),
            )


class SandboxCommandRunner:
    """Validate policy, filter env, execute, and return a gate-like decision."""

    def __init__(self, *, executor: CommandExecutor | None = None) -> None:
        self._executor = executor or SubprocessCommandExecutor()

    async def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        policy: SandboxPolicy,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> GateDecision:
        validation = policy.validate_command(argv, cwd=cwd)
        if not validation.passed:
            return validation

        filtered_env = policy.env.filter(env)
        result = await self._executor.execute(
            validation.command,
            cwd=Path(validation.cwd),
            env=filtered_env,
            timeout_seconds=policy.timeout_seconds,
        )
        summary = policy.summarize_output(result.stdout, result.stderr or result.error, env=env)
        if result.timed_out:
            status: DecisionStatus = "timeout"
            passed = False
        elif result.error:
            status = "error"
            passed = False
        else:
            status = "passed" if result.returncode == 0 else "failed"
            passed = result.returncode == 0

        evidence = (
            GateEvidence("returncode", "" if result.returncode is None else str(result.returncode)),
            GateEvidence("duration_seconds", f"{result.duration_seconds:.3f}"),
            GateEvidence("summary", summary),
            GateEvidence("env_keys", ",".join(sorted(filtered_env))),
            GateEvidence("timed_out", str(result.timed_out).lower()),
        )
        decision = GateDecision(
            name="sandbox-command",
            passed=passed,
            status=status,
            command=validation.command,
            workspace_root=validation.workspace_root,
            cwd=validation.cwd,
            evidence=evidence,
        )
        ensure(bool(decision.evidence), "Sandbox command decisions must include evidence")
        return decision

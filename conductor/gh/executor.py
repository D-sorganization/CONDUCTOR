"""IssueExecutor — converts a GitHub issue into a draft PR via an LLM.

Orchestration only. All external side-effects — HTTP to GitHub, subprocess git,
LLM requests — go through injected collaborators so the executor is pure
control flow that's easy to test.

Modes:
  * ``plan``      — fetch issue, ask LLM for a plan, open an empty draft PR
                    seeded with that plan. Safe — no code ever written.
  * ``implement`` — ask the LLM for a unified diff, apply it to a fresh branch,
                    commit, push, open a draft PR. Still human-reviewed before
                    merge because the PR is opened as a draft.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from conductor.backends.base import ILLMBackend, Message, MessageRole
from conductor.gh.context import ContextBuilder
from conductor.gh.test_runner import TestResult, TestRunner
from conductor.gh.workspace import WorkspaceError

__all__ = ["IssueExecutionError", "IssueExecutor", "IssueResult"]

Mode = Literal["plan", "implement"]

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


class IssueExecutionError(RuntimeError):
    """Raised when issue → PR execution can't complete."""


@dataclass(slots=True, frozen=True)
class IssueResult:
    issue_number: int
    pr_url: str
    pr_number: int
    plan: str
    applied_diff: bool


class _GitHubProto(Protocol):
    async def get_issue(self, repo: str, number: int) -> Any: ...
    async def create_pull_request(
        self,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> Any: ...


class _WorkspaceProto(Protocol):
    async def ensure_clone(self, repo: str) -> Any: ...
    async def create_branch(self, repo: str, branch: str, *, base: str = "main") -> None: ...
    async def apply_diff(self, repo: str, diff: str) -> None: ...
    async def commit_and_push(self, repo: str, *, branch: str, message: str) -> None: ...


_SYSTEM_PROMPT = """You are a senior engineer drafting a pull request for a GitHub issue.

Respond with a single JSON object on its own:

{
  "plan": "A concise Markdown description of what the change does and why (shown in the PR body)",
  "diff": "A unified diff suitable for `git apply --index`. Empty string if no code change is appropriate yet."
}

Rules:
- The diff must use proper unified-diff format with `diff --git`, `---`, `+++`, and `@@` hunk headers.
- Never include files you haven't seen. Prefer small, surgical changes over sweeping rewrites.
- If you're unsure, return an empty diff and explain what's missing in the plan.
"""


class IssueExecutor:
    def __init__(
        self,
        *,
        github: _GitHubProto,
        workspace: _WorkspaceProto,
        backend: ILLMBackend,
        max_diff_retries: int = 2,
        context_builder: ContextBuilder | None = None,
        context_max_chars: int = 16_000,
        test_runner: TestRunner | Any | None = None,
        max_test_retries: int = 1,
        test_timeout_seconds: float = 300.0,
    ) -> None:
        self._gh = github
        self._ws = workspace
        self._backend = backend
        self._max_diff_retries = max_diff_retries
        self._context_builder = context_builder
        self._context_max_chars = context_max_chars
        self._test_runner = test_runner
        self._max_test_retries = max_test_retries
        self._test_timeout = test_timeout_seconds

    async def execute_issue(
        self,
        *,
        repo: str,
        issue_number: int,
        model: str,
        mode: Mode = "plan",
        base_branch: str = "main",
    ) -> IssueResult:
        issue = await self._gh.get_issue(repo, issue_number)
        branch = f"conductor/issue-{issue_number}"

        # Build context if we have a builder AND we're in implement mode (plan
        # mode doesn't need a clone; enable via a follow-up if it helps).
        context_prompt = ""
        if mode == "implement" and self._context_builder is not None:
            repo_path = await self._ws.ensure_clone(repo)
            ctx = await self._context_builder.build(repo_path, issue.body)
            context_prompt = ctx.to_prompt(max_chars=self._context_max_chars)

        plan, diff = await self._draft_change(
            issue_title=issue.title,
            issue_body=issue.body,
            model=model,
            context=context_prompt,
        )

        applied = False
        test_result: TestResult | None = None
        if mode == "implement":
            if not diff.strip():
                raise IssueExecutionError(
                    "LLM returned no diff but mode=implement — rerun in plan mode "
                    "or refine the issue."
                )
            # If we didn't already clone for context, clone now.
            if not context_prompt:
                await self._ws.ensure_clone(repo)
            await self._ws.create_branch(repo, branch, base=base_branch)
            plan, diff = await self._apply_with_retry(
                repo=repo,
                issue_title=issue.title,
                issue_body=issue.body,
                model=model,
                plan=plan,
                diff=diff,
            )
            if self._test_runner is not None:
                plan, diff, test_result = await self._validate_with_tests(
                    repo=repo,
                    branch=branch,
                    issue_title=issue.title,
                    issue_body=issue.body,
                    model=model,
                    plan=plan,
                    diff=diff,
                    base_branch=base_branch,
                )
            await self._ws.commit_and_push(
                repo,
                branch=branch,
                message=f"Fix #{issue_number}: {issue.title}",
            )
            applied = True

        pr_body = self._format_pr_body(
            issue_number=issue_number,
            plan=plan,
            applied=applied,
            test_result=test_result,
        )
        pr = await self._gh.create_pull_request(
            repo,
            head=branch,
            base=base_branch,
            title=f"Fix #{issue_number}: {issue.title}",
            body=pr_body,
            draft=True,
        )
        return IssueResult(
            issue_number=issue_number,
            pr_url=pr.url,
            pr_number=pr.number,
            plan=plan,
            applied_diff=applied,
        )

    async def _validate_with_tests(
        self,
        *,
        repo: str,
        branch: str,
        issue_title: str,
        issue_body: str,
        model: str,
        plan: str,
        diff: str,
        base_branch: str,
    ) -> tuple[str, str, TestResult]:
        """Run repo tests; if they fail, ask the LLM to refine the diff and retry.

        Returns the final (plan, diff, test_result). Raises if tests still fail
        after ``max_test_retries`` refinements.
        """
        assert self._test_runner is not None
        attempt = 0
        current_plan, current_diff = plan, diff
        repo_path = self._workspace_path(repo)

        while True:
            result = await self._test_runner.detect_and_run(repo_path, timeout=self._test_timeout)
            if result.passed:
                return current_plan, current_diff, result
            attempt += 1
            if attempt > self._max_test_retries:
                raise IssueExecutionError(
                    f"tests still failing after {attempt} attempt(s); "
                    f"last output: {result.output_tail[-500:]}"
                )
            # Reset the workspace to base and regenerate a corrected diff.
            current_plan, current_diff = await self._refine_from_tests(
                issue_title=issue_title,
                issue_body=issue_body,
                model=model,
                previous_plan=current_plan,
                previous_diff=current_diff,
                test_output=result.output_tail,
            )
            await self._ws.create_branch(repo, branch, base=base_branch)
            await self._apply_with_retry(
                repo=repo,
                issue_title=issue_title,
                issue_body=issue_body,
                model=model,
                plan=current_plan,
                diff=current_diff,
            )

    def _workspace_path(self, repo: str) -> Any:
        """Return the local checkout directory for ``repo``.

        Uses the workspace's ``path_for`` method when available (real
        Workspace) or falls back to a Path derived from the stub test double.
        """
        if hasattr(self._ws, "path_for"):
            return self._ws.path_for(repo)
        from pathlib import Path

        return Path("/tmp/conductor-workspace") / repo.split("/", 1)[1]

    async def _refine_from_tests(
        self,
        *,
        issue_title: str,
        issue_body: str,
        model: str,
        previous_plan: str,
        previous_diff: str,
        test_output: str,
    ) -> tuple[str, str]:
        prompt = (
            f"Your previous diff applied cleanly but the repo's own tests now fail.\n\n"
            f"Issue title: {issue_title}\n"
            f"Issue body:\n{issue_body or '(empty)'}\n\n"
            f"Your previous plan:\n{previous_plan}\n\n"
            f"Your previous diff:\n{previous_diff}\n\n"
            f"Test output (tail):\n{test_output}\n\n"
            "Return a corrected JSON object (same schema). Fix the failing tests."
        )
        response = await self._backend.complete(
            [
                Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                Message(role=MessageRole.USER, content=prompt),
            ],
            model=model,
            temperature=0.2,
        )
        return self._parse_response(response.content)

    async def _apply_with_retry(
        self,
        *,
        repo: str,
        issue_title: str,
        issue_body: str,
        model: str,
        plan: str,
        diff: str,
    ) -> tuple[str, str]:
        """Try to apply the diff; on failure, ask the LLM for a corrected diff."""
        attempts = 0
        last_error: str = ""
        current_plan, current_diff = plan, diff
        while True:
            try:
                await self._ws.apply_diff(repo, current_diff)
                return current_plan, current_diff
            except WorkspaceError as e:
                last_error = str(e)
                attempts += 1
                if attempts > self._max_diff_retries:
                    raise IssueExecutionError(
                        f"diff did not apply after {attempts} attempt(s); last error: {last_error}"
                    ) from e
                current_plan, current_diff = await self._refine_diff(
                    issue_title=issue_title,
                    issue_body=issue_body,
                    model=model,
                    previous_plan=current_plan,
                    previous_diff=current_diff,
                    error=last_error,
                )

    async def _refine_diff(
        self,
        *,
        issue_title: str,
        issue_body: str,
        model: str,
        previous_plan: str,
        previous_diff: str,
        error: str,
    ) -> tuple[str, str]:
        prompt = (
            f"Your previous diff did not apply cleanly.\n\n"
            f"Issue title: {issue_title}\n"
            f"Issue body:\n{issue_body or '(empty)'}\n\n"
            f"Your previous plan:\n{previous_plan}\n\n"
            f"Your previous diff:\n{previous_diff}\n\n"
            f"git apply failed with: {error}\n\n"
            "Return a corrected JSON object (same schema) with a diff that applies."
        )
        response = await self._backend.complete(
            [
                Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                Message(role=MessageRole.USER, content=prompt),
            ],
            model=model,
            temperature=0.2,
        )
        return self._parse_response(response.content)

    async def _draft_change(
        self, *, issue_title: str, issue_body: str, model: str, context: str = ""
    ) -> tuple[str, str]:
        prompt_parts = [f"Issue title: {issue_title}\n"]
        prompt_parts.append(f"Issue body:\n{issue_body or '(empty)'}\n")
        if context:
            prompt_parts.append(f"\n## Repository context\n\n{context}\n")
        prompt_parts.append("\nProduce the JSON plan now.")
        prompt = "\n".join(prompt_parts)
        response = await self._backend.complete(
            [
                Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                Message(role=MessageRole.USER, content=prompt),
            ],
            model=model,
            temperature=0.2,
        )
        plan, diff = self._parse_response(response.content)
        return plan, diff

    @staticmethod
    def _parse_response(raw: str) -> tuple[str, str]:
        content = raw.strip()
        fence_match = _FENCE_RE.match(content)
        if fence_match:
            content = fence_match.group(1).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise IssueExecutionError(f"Could not parse LLM response as JSON: {e}") from e

        plan = str(parsed.get("plan", "")).strip()
        diff = str(parsed.get("diff", "")).strip()
        if not plan:
            raise IssueExecutionError("LLM response missing non-empty 'plan' field")
        return plan, diff

    @staticmethod
    def _format_pr_body(
        *,
        issue_number: int,
        plan: str,
        applied: bool,
        test_result: TestResult | None = None,
    ) -> str:
        header_lines = [
            f"Closes #{issue_number}",
            "",
            f"> 🤖 Drafted by CONDUCTOR — {'code applied' if applied else 'plan only'}.",
        ]
        if test_result is not None:
            mark = "✅" if test_result.passed else "⚠️"
            verb = "passed" if test_result.passed else "failed"
            header_lines.append(
                f"> {mark} Tests {verb}: `{test_result.command}` "
                f"(rc={test_result.returncode}, {test_result.duration_seconds:.1f}s)"
            )
        header_lines.extend(["", ""])
        return "\n".join(header_lines) + plan

"""Cognitive Phases (formerly Phase-Gate Workflows).

This module defines the state machine pipeline for a task. A task progresses through
distinct cognitive phases: Strategize -> Implement -> Validate (The Maxwell Crucible).
If validation fails, the pipeline enforces revision, adhering to TDD and DbC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from maxwell_daemon.core.roles import Job, RolePlayer


class PhaseDecision(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    NEEDS_REVISION = "needs_revision"


@dataclass(slots=True)
class PipelineResult:
    status: PhaseDecision
    final_artifact: str
    history: list[str] = field(default_factory=list)


class CognitivePipeline:
    """Orchestrates the lifecycle of a task through orthogonal roles."""

    def __init__(
        self, strategist: RolePlayer, implementer: RolePlayer, validator: RolePlayer
    ) -> None:
        self.strategist = strategist
        self.implementer = implementer
        self.validator = validator

    async def run(self, job: Job, max_retries: int = 2) -> PipelineResult:
        """Runs the task through the Maxwell Cognitive Pipeline with Phase-Gates."""
        history: list[str] = []

        # Phase 1: Strategize (Architect)
        plan_response = await self.strategist.execute(job)
        plan = plan_response.content
        history.append(f"Strategist Plan:\n{plan}")

        # Phase 2 & 3: Implementation & Validation Loop (Hallucination Prevention)
        feedback = ""
        code = ""
        for attempt in range(max_retries + 1):
            if attempt == 0:
                impl_instructions = f"Implement the following plan. Adhere to DbC, DRY, and LOD.\n\nPlan:\n{plan}"
            else:
                impl_instructions = (
                    f"Revise your implementation to address this validation feedback:\n\n"
                    f"{feedback}\n\nOriginal Plan:\n{plan}\n\n"
                    f"Previous Code:\n{code}"
                )

            impl_job = Job(
                instructions=impl_instructions,
                context_data=job.context_data,
            )
            impl_response = await self.implementer.execute(impl_job)
            code = impl_response.content
            history.append(f"Implementation (Attempt {attempt + 1}):\n{code}")

            # Phase 3: The Maxwell Crucible (Validator / Trident QA)
            val_job = Job(
                instructions=(
                    f"Verify this implementation against the plan and architectural standards (DbC, DRY, Hallucination Prevention).\n"
                    f"Plan:\n{plan}\n\nCode:\n{code}\n\n"
                    f"Reply with exactly PASS if the code perfectly implements the plan, or FAIL with reasons."
                ),
                context_data=job.context_data,
            )
            val_response = await self.validator.execute(val_job)
            validation = val_response.content.strip()
            history.append(f"Crucible Validation (Attempt {attempt + 1}):\n{validation}")

            # Phase Gate Decision
            if validation.upper().startswith("PASS"):
                return PipelineResult(
                    status=PhaseDecision.SUCCESS, final_artifact=code, history=history
                )

            feedback = validation

        # If it reaches here, we exhausted all retries
        return PipelineResult(status=PhaseDecision.FAILURE, final_artifact=code, history=history)

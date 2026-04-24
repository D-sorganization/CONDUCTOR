"""Benchmark runner: runs standard prompts against multiple backends and records metrics."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STANDARD_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "hello",
        "prompt": "Say hello in one sentence.",
        "expected_keywords": ["hello"],
        "category": "basic",
    },
    {
        "id": "reverse_string",
        "prompt": "Reverse the string 'benchmark'. Reply with only the reversed string.",
        "expected_keywords": ["kramhcneb"],
        "category": "basic",
    },
    {
        "id": "count_words",
        "prompt": "How many words are in the phrase 'the quick brown fox'? Reply with just the number.",
        "expected_keywords": ["4"],
        "category": "basic",
    },
    {
        "id": "json_output",
        "prompt": 'Return a JSON object with keys "status" set to "ok" and "value" set to 42. Reply with only the JSON.',
        "expected_keywords": ["ok", "42"],
        "category": "structured",
    },
    {
        "id": "python_sum",
        "prompt": "Write a one-line Python function that sums a list of numbers.",
        "expected_keywords": ["sum", "def"],
        "category": "code",
    },
]


@dataclass
class PromptResult:
    """Result of running a single prompt against a backend."""

    prompt_id: str
    backend: str
    latency_ms: float
    token_count: int
    success: bool
    error: str | None = None
    output: str = ""


@dataclass
class BenchmarkResult:
    """Aggregated result for one backend across all prompts."""

    run_id: str
    backend: str
    started_at: datetime
    completed_at: datetime
    prompt_results: list[PromptResult] = field(default_factory=list)

    @property
    def latency_p50_ms(self) -> float:
        latencies = sorted(r.latency_ms for r in self.prompt_results)
        if not latencies:
            return 0.0
        mid = len(latencies) // 2
        return latencies[mid]

    @property
    def latency_p95_ms(self) -> float:
        latencies = sorted(r.latency_ms for r in self.prompt_results)
        if not latencies:
            return 0.0
        idx = max(0, int(len(latencies) * 0.95) - 1)
        return latencies[idx]

    @property
    def mean_latency_ms(self) -> float:
        if not self.prompt_results:
            return 0.0
        return sum(r.latency_ms for r in self.prompt_results) / len(self.prompt_results)

    @property
    def total_tokens(self) -> int:
        return sum(r.token_count for r in self.prompt_results)

    @property
    def success_rate(self) -> float:
        if not self.prompt_results:
            return 0.0
        passed = sum(1 for r in self.prompt_results if r.success)
        return passed / len(self.prompt_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "backend": self.backend,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "total_tokens": self.total_tokens,
            "success_rate": round(self.success_rate, 4),
            "prompt_count": len(self.prompt_results),
            "prompt_results": [
                {
                    "prompt_id": r.prompt_id,
                    "latency_ms": round(r.latency_ms, 2),
                    "token_count": r.token_count,
                    "success": r.success,
                    "error": r.error,
                }
                for r in self.prompt_results
            ],
        }


class BenchmarkRunner:
    """Run standard prompts against multiple backends and collect latency/quality metrics.

    The runner is intentionally backend-agnostic: callers supply a ``call_backend``
    callable so the class can be tested without network access.  In production the
    daemon wires in the real backend registry.
    """

    def __init__(
        self,
        call_backend: Any | None = None,
        prompts: list[dict[str, Any]] | None = None,
    ) -> None:
        self._call_backend = call_backend
        self._prompts = prompts if prompts is not None else STANDARD_PROMPTS

    def run(self, backends: list[str]) -> list[BenchmarkResult]:
        """Run the benchmark suite against each listed backend.

        Returns one ``BenchmarkResult`` per backend.
        """
        results = []
        for backend in backends:
            result = self._run_backend(backend)
            results.append(result)
        return results

    def _run_backend(self, backend: str) -> BenchmarkResult:
        run_id = f"bench-{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc)
        prompt_results: list[PromptResult] = []

        for prompt_spec in self._prompts:
            pr = self._run_prompt(run_id, backend, prompt_spec)
            prompt_results.append(pr)

        completed_at = datetime.now(timezone.utc)
        return BenchmarkResult(
            run_id=run_id,
            backend=backend,
            started_at=started_at,
            completed_at=completed_at,
            prompt_results=prompt_results,
        )

    def _run_prompt(
        self,
        run_id: str,
        backend: str,
        prompt_spec: dict[str, Any],
    ) -> PromptResult:
        prompt_id = prompt_spec["id"]
        prompt_text = prompt_spec["prompt"]
        expected_keywords: list[str] = prompt_spec.get("expected_keywords", [])

        if self._call_backend is None:
            # Stub mode: simulate a response for testing without live backends.
            return self._stub_result(prompt_id, backend, expected_keywords)

        t0 = time.monotonic()
        try:
            output, token_count = self._call_backend(backend, prompt_text)
            latency_ms = (time.monotonic() - t0) * 1000.0
            success = self._check_keywords(output, expected_keywords)
            return PromptResult(
                prompt_id=prompt_id,
                backend=backend,
                latency_ms=latency_ms,
                token_count=token_count,
                success=success,
                output=output,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000.0
            return PromptResult(
                prompt_id=prompt_id,
                backend=backend,
                latency_ms=latency_ms,
                token_count=0,
                success=False,
                error=str(exc),
            )

    @staticmethod
    def _check_keywords(output: str, keywords: list[str]) -> bool:
        lower = output.lower()
        return all(kw.lower() in lower for kw in keywords)

    @staticmethod
    def _stub_result(prompt_id: str, backend: str, expected_keywords: list[str]) -> PromptResult:
        """Return a deterministic stub result for CI / offline testing."""
        return PromptResult(
            prompt_id=prompt_id,
            backend=backend,
            latency_ms=50.0,
            token_count=20,
            success=True,
            output=" ".join(expected_keywords),
        )

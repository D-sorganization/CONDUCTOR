"""Leaderboard store: persists benchmark results as JSON and provides ranking queries."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maxwell_daemon.evals.benchmark import BenchmarkResult


class LeaderboardStore:
    """Persist benchmark results under a directory and expose ranking queries.

    Each run is stored as ``<root>/<run_id>.json``.  The leaderboard is computed
    on-the-fly from the most-recent result per backend so rankings stay current
    without a separate aggregation step.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, result: BenchmarkResult) -> Path:
        """Persist a single benchmark result and return the file path."""
        path = self._root / f"{result.run_id}.json"
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return path

    def save_all(self, results: list[BenchmarkResult]) -> list[Path]:
        """Persist a list of benchmark results (one file per result)."""
        return [self.save(r) for r in results]

    def load(self, run_id: str) -> dict[str, Any]:
        """Load a single run by ID.  Raises ``FileNotFoundError`` if absent."""
        path = self._root / f"{run_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"benchmark run not found: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        """Return all stored runs sorted newest-first."""
        runs = []
        for p in sorted(
            self._root.glob("bench-*.json"), key=lambda f: f.stat().st_mtime, reverse=True
        ):
            try:
                runs.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return runs

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def leaderboard(
        self,
        sort_by: str = "success_rate",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return a ranked list of backends.

        Each entry contains aggregated metrics for the *most recent* run of that
        backend.  ``sort_by`` accepts ``success_rate``, ``latency_p50_ms``,
        ``mean_latency_ms``, or ``total_tokens``.

        For latency fields, lower is better (ascending sort).
        For ``success_rate`` and ``total_tokens``, higher is better (descending).
        """
        all_runs = self.list_runs()

        # Keep only the most-recent run per backend.
        latest: dict[str, dict[str, Any]] = {}
        for run in all_runs:
            backend = run.get("backend", "unknown")
            if backend not in latest:
                latest[backend] = run

        rows: list[dict[str, Any]] = []
        for rank_idx, (backend, run) in enumerate(latest.items(), start=1):
            rows.append(
                {
                    "rank": rank_idx,  # will be reassigned after sort
                    "backend": backend,
                    "run_id": run.get("run_id"),
                    "success_rate": run.get("success_rate", 0.0),
                    "latency_p50_ms": run.get("latency_p50_ms", 0.0),
                    "latency_p95_ms": run.get("latency_p95_ms", 0.0),
                    "mean_latency_ms": run.get("mean_latency_ms", 0.0),
                    "total_tokens": run.get("total_tokens", 0),
                    "prompt_count": run.get("prompt_count", 0),
                    "completed_at": run.get("completed_at"),
                }
            )

        descending_fields = {"success_rate", "total_tokens"}
        reverse = sort_by in descending_fields
        with contextlib.suppress(TypeError):
            rows.sort(key=lambda r: r.get(sort_by, 0), reverse=reverse)

        # Re-assign rank after sorting.
        for i, row in enumerate(rows, start=1):
            row["rank"] = i

        return rows[:limit]

    def summary(self) -> dict[str, Any]:
        """Return a high-level summary of stored benchmark data."""
        runs = self.list_runs()
        backends = {r.get("backend") for r in runs}
        latest_at = max(
            (r.get("completed_at") for r in runs if r.get("completed_at")),
            default=None,
        )
        return {
            "total_runs": len(runs),
            "backends_seen": sorted(b for b in backends if b),
            "latest_run_at": latest_at,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

"""Periodic retention enforcement for task store, cost ledger, and audit log.

The daemon runs autonomously for weeks at a time. Without pruning, the SQLite
stores grow unbounded and query performance collapses (see #148). This module
wires a simple asyncio background loop that calls each store's ``prune()``
method on a configurable interval.

Policy defaults are conservative: 30-day TTL, large row caps, daily sweep, and
a one-hour safety floor that vetoes cap-based deletion of recent data.

The pruner is deliberately small — no scheduler library, no cron. If the
daemon process dies, the next restart resumes pruning on its normal cadence.
Missing one daily sweep is not a correctness problem.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maxwell_daemon.audit import AuditLogger
    from maxwell_daemon.config.models import RetentionConfig
    from maxwell_daemon.core.ledger import CostLedger
    from maxwell_daemon.core.task_store import TaskStore

__all__ = ["RetentionPruner", "prune_once"]

log = logging.getLogger(__name__)


def prune_once(
    config: RetentionConfig,
    *,
    task_store: TaskStore | None = None,
    ledger: CostLedger | None = None,
    audit: AuditLogger | None = None,
) -> tuple[int, int, int]:
    """Run a single prune pass across whichever stores are provided.

    Returns ``(n_tasks, n_ledger, n_audit)`` — the number of rows removed
    from each store. Stores passed as ``None`` are skipped.
    """
    n_tasks = n_ledger = n_audit = 0
    min_age = config.min_age_seconds

    if task_store is not None:
        try:
            n_tasks = task_store.prune(
                retention_days=config.retention_days,
                max_tasks=config.max_tasks,
                min_age_seconds=min_age,
            )
        except Exception:
            log.warning("task store prune failed", exc_info=True)

    if ledger is not None:
        try:
            n_ledger = ledger.prune(
                retention_days=config.retention_days,
                max_rows=config.max_ledger_rows,
                min_age_seconds=min_age,
            )
        except Exception:
            log.warning("ledger prune failed", exc_info=True)

    if audit is not None:
        try:
            n_audit = audit.prune()
        except Exception:
            log.warning("audit prune failed", exc_info=True)

    log.info(
        "pruned %d tasks, %d ledger rows, %d audit rows",
        n_tasks,
        n_ledger,
        n_audit,
    )
    return n_tasks, n_ledger, n_audit


class RetentionPruner:
    """Asyncio background loop that runs :func:`prune_once` on an interval.

    Usage::

        pruner = RetentionPruner(
            config=config.retention,
            task_store=daemon._task_store,
            ledger=daemon._ledger,
            audit=audit_logger,
        )
        await pruner.start()
        # ... later ...
        await pruner.stop()

    Idempotent start/stop. Per-tick exceptions are logged and swallowed so a
    transient SQLite lock timeout doesn't kill the loop.
    """

    def __init__(
        self,
        *,
        config: RetentionConfig,
        task_store: TaskStore | None = None,
        ledger: CostLedger | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self._config = config
        self._task_store = task_store
        self._ledger = ledger
        self._audit = audit
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def run_once(self) -> tuple[int, int, int]:
        """Synchronous single-pass prune — exposed for tests and admin endpoints."""
        return prune_once(
            self._config,
            task_store=self._task_store,
            ledger=self._ledger,
            audit=self._audit,
        )

    async def start(self) -> None:
        """Begin periodic pruning in a background task. Idempotent."""
        if not self._config.enabled:
            log.info("retention pruner disabled by config")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="retention-pruner")

    async def stop(self) -> None:
        """Signal the loop and wait for it. Idempotent."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError):
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        if self._stop_event is None:
            raise RuntimeError("_loop() called before start(); _stop_event is None")
        interval = float(self._config.prune_interval_seconds)
        loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            try:
                # Run the prune in a thread so SQLite work doesn't block the loop.
                await loop.run_in_executor(None, self.run_once)
            except Exception:
                log.warning("retention prune tick raised; continuing", exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return  # stop signalled during the wait
            except (TimeoutError, asyncio.TimeoutError):
                continue  # interval elapsed; next tick

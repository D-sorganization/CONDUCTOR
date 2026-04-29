# ADR-004: Symphony SSH Appendix A — Remote Worker Safety

**Status:** Accepted  
**Date:** 2026-04-28  
**Deciders:** Maxwell-Daemon Core Team  

---

## Context

Issue #767 identified that the Maxwell-Daemon fleet dispatch system needed safety guarantees when dispatching tasks to remote SSH workers. The Symphony specification defines Appendix A with three requirements:

- **A.1** — Remote workspace resolution: tools must never silently resolve local paths on a remote host
- **A.2** — Once side effects start, transparent failover is forbidden (a half-written file or launched remote process cannot be safely retried on another host)
- **A.3** — Per-host concurrency caps to prevent resource exhaustion on SSH targets with limited workspace or memory

## Decision

Implement all three requirements as follows:

### A.1 — Local Path Resolution Audit

The existing `_resolve()` helper in `maxwell_daemon.tools.builtins` already validates that every path argument resolves inside the workspace root. The SSH session pool (`maxwell_daemon.ssh.session`) operates on an explicit remote host; no local path resolution occurs. The built-in tools (write_file, edit_file, run_bash) are scoped to a workspace `Path` object bound at registry construction time. On a remote worker, this `Path` is the remote filesystem path where the repository was cloned. No additional code changes were needed for A.1 because the existing architecture already satisfies it.

### A.2 — Side-Effect Gate and Failover Refusal

1. **Add `side_effects_started: bool` to the `Task` dataclass** (`daemon/runner.py`).
2. **Add `_SIDE_EFFECT_RISK_LEVELS`** (`mcp.py`) — a frozenset of risk levels that constitute side effects:
   - `local_write`
   - `command_execution`
   - `network_write`
   - `external_side_effect`
   - `destructive`
3. **Add `on_side_effect: Callable[[str], None]` callback to `ToolRegistry`** (`mcp.py`). The callback fires *before* the tool handler runs, because even a handler crash may leave partial side effects (e.g. a half-written file).
4. **Wire the callback through `build_default_registry`** (`tools/builtins.py`) and the agent loop (`backends/agent_loop.py`) to a closure that sets `task.side_effects_started = True` and persists to the task store.
5. **Refuse transparent failover in `_dispatch_to_fleet`** (`daemon/runner.py`). When a DISPATCHED task's worker goes offline:
   - If `side_effects_started is False`: requeue normally (existing behavior).
   - If `side_effects_started is True`: log an error, emit a `TASK_FAILED` event with `reason: "failover_refused"`, and leave the task in DISPATCHED status pending human intervention.

### A.3 — Per-Host Concurrency Caps

1. **Add `max_concurrent_agents_per_host: int | None`** to `MachineConfig` (`config/models.py`).
2. **Add the field to `MachineState`** (`fleet/dispatcher.py`).
3. **Override `available_slots`** to use the cap when set:
   ```python
   effective_capacity = max_concurrent_agents_per_host or capacity
   ```
4. **Wire the field** through both `MachineState` constructions in `_dispatch_to_fleet` (`daemon/runner.py`).

## Consequences

- **Positive:** Remote workers are protected from silent path escapes
- **Positive:** Partial side effects cannot be double-executed via failover
- **Positive:** Operators can limit SSH-target concurrency without reducing global worker count
- **Negative:** `side_effects_started` flag adds one boolean column to task persistence
- **Negative:** `max_concurrent_agents_per_host` adds one optional field to fleet config

## Rationale

The pre-execution notification for side effects is intentionally conservative: we mark the flag before the handler runs because a handler crash mid-way through (e.g., after opening a file handle but before fsync) may leave the system in an inconsistent state. Failover refusal is logged at ERROR level and emits a `TASK_FAILED` event so operators can set up alerts. The per-host cap takes precedence over generic capacity so operators can set it independently per machine based on remote resource constraints.

---

## References

- Issue #767
- `SPEC.md` — Symphony SSH Appendix A
- `maxwell_daemon/daemon/runner.py` — Task model, _dispatch_to_fleet
- `maxwell_daemon/fleet/dispatcher.py` — MachineState.available_slots
- `maxwell_daemon/tools/mcp.py` — ToolRegistry, _SIDE_EFFECT_RISK_LEVELS
- `maxwell_daemon/config/models.py` — MachineConfig
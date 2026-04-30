# Troubleshooting Runbook

This guide contains standard operating procedures for resolving common
issues with the Maxwell Daemon in production environments.

For deployment-shaped guidance (env vars, capacity, docker-compose) see
[`production-deployment.md`](production-deployment.md). For
metrics-driven alerts that often fire alongside these symptoms, see
[`monitoring.md`](monitoring.md).

## Quick triage

When a production incident comes in, check these three things first:

1. `curl -fsS http://127.0.0.1:8080/api/health` — is the API up?
2. `curl -fsS http://127.0.0.1:8080/api/status` — pipeline state and
   active task.
3. `curl -fsS http://127.0.0.1:8080/metrics | grep -E 'maxwell_daemon_(active_tasks|queue_depth|requests_total)'`
   — current load.

The next three sections cover the highest-frequency production
symptoms; everything below them is the historical runbook.

## Symptom: Stuck task (running but no progress)

A task is reported as `RUNNING` in `/api/status` but emits no events for
several minutes and `maxwell_daemon_active_tasks` stays pinned at >= 1.

* **Check**:
  ```bash
  curl -s http://127.0.0.1:8080/api/status | jq '.active_task'
  curl -s http://127.0.0.1:8080/metrics | grep maxwell_daemon_active_tasks
  tail -n 200 ~/.local/share/maxwell-daemon/logs/daemon.log | jq 'select(.task_id == "<id>")'
  ```
* **Common causes**: provider-side stall (LLM call hanging), critic
  process wedged, sandbox subprocess never exited, stall-detector
  threshold not configured.
* **Fix**:
  1. Set `agent.stall_timeout_seconds` in config so the daemon
     auto-cancels and retries silent runs (logs as
     `event=stall_detected`).
  2. If the task is genuinely stuck, abort and requeue via
     `POST /api/control/abort` followed by a fresh dispatch.
  3. Restart the daemon as a last resort — the state machine is
     idempotent and resumes from the persisted `TaskStore`.

## Symptom: `database is locked` / SQLite OperationalError

Cost-ledger writes raise `sqlite3.OperationalError: database is locked`,
or ledger writes time out.

* **Check**:
  ```bash
  curl -s http://127.0.0.1:8080/metrics | grep maxwell_ledger_connections_in_use
  ls -la ~/.local/share/maxwell-daemon/ledger.db*
  lsof ~/.local/share/maxwell-daemon/ledger.db 2>/dev/null
  ```
* **Common causes**: another process opened the same ledger file (e.g.,
  a stale daemon, a manual `sqlite3` shell holding a write lock), the
  data directory lives on a network filesystem that doesn't honor
  fcntl locks, or WAL mode was turned off.
* **Fix**:
  1. Ensure only one daemon writes the ledger at a time — running two
     daemons against the same `ledger.db` is unsupported.
  2. Close any external `sqlite3` shell sessions on the ledger.
  3. Confirm the ledger lives on a local filesystem (ext4, xfs, apfs).
     Avoid NFS, SMB, and most overlay/network volumes.
  4. Verify WAL mode: `sqlite3 ledger.db 'pragma journal_mode;'` should
     return `wal`. If not, back the file up and let the daemon
     recreate it.

## Symptom: High memory / OOM kills

The daemon's RSS grows unbounded or the kernel OOM-kills it.

* **Check**:
  ```bash
  ps -o rss,vsz,cmd -C python | grep maxwell
  du -sh ~/.local/share/maxwell-daemon/
  curl -s http://127.0.0.1:8080/metrics | grep -E 'maxwell_daemon_(active_tasks|live_tasks_dict_size)'
  ```
* **Common causes**: memory annealer disabled
  (`memory_dream_interval_seconds` is `0` or unset), too many
  concurrent tasks for the host, leaked subprocess output captured in
  memory, runaway prompt context.
* **Fix**:
  1. Enable the memory annealer in config:
     `memory_dream_interval_seconds = 900` (or any positive value) so
     verbose logs are compacted into `architectural_state.md`.
  2. Lower `agent.max_concurrent_tasks` until RSS stabilizes.
  3. Turn on aggressive compression for very long contexts:
     `MAXWELL_AGGRESSIVE_COMPRESSION=on`.
  4. Prune old artifacts under
     `~/.local/share/maxwell-daemon/artifacts/`.
  5. If RSS keeps climbing under steady load, capture a heap snapshot
     and file an issue with the trace.

---

## Symptom: Tasks queued but not running

* **Check**: Verify worker count, queue depth, and check recent errors in logs.
* **Commands**:
  ```bash
  curl -s http://127.0.0.1:8000/api/v1/tasks | jq '.[] | select(.status == "QUEUED")'
  curl -s http://127.0.0.1:8000/metrics
  tail -f ~/.local/share/maxwell-daemon/logs/daemon.log | jq .
  ```
* **Common causes**: Budget exhausted, worker crash, gate misconfiguration.
* **Fix**: Restart daemon, check `config.toml` budgets, or adjust gate thresholds.

## Symptom: Cost forecast exceeds budget

* **Check**: Inspect `cost_ledger.db` via scripts.
* **Commands**:
  ```bash
  sqlite3 ~/.local/share/maxwell-daemon/ledger.db "SELECT * FROM ledger ORDER BY timestamp DESC LIMIT 10;"
  # Or use a script to breakdown by backend/model/repo
  ```
* **Common causes**: Inefficient prompts, cache misses, wrong model selected.
* **Fix**: Compress context (`MAXWELL_AGGRESSIVE_COMPRESSION=on`), clear cache, switch to a cheaper model for the task.

## Symptom: High latency on task execution

* **Check**: Gate execution times, critic run times, queue depth.
* **Commands**: Benchmark task by task, inspect critic profiles.
* **Common causes**: Slow critic (e.g. large file to review), queue backlog.
* **Fix**: Increase worker count, optimize gate policy, increase timeout.

## Symptom: Out of memory

* **Check**: Memory annealer status, raw log accumulation.
* **Commands**:
  ```bash
  du -sh ~/.local/share/maxwell-daemon/
  ```
* **Common causes**: Memory annealer disabled, artifact bloat.
* **Fix**: Run memory anneal cycle (`memory_dream_interval_seconds > 0`), prune old artifacts.

## Symptom: Fleet worker goes silent

* **Check**: Worker heartbeat (`last_seen` timestamp in coordinator).
* **Commands**: Check coordinator logs and task reassignment logs.
* **Common causes**: Network partition, OOM on worker, process crash.
* **Fix**: Manual reassignment if needed, restart worker, check resources on worker node.

## Symptom: Service exits with status=226/NAMESPACE

* **Check**: systemd sandboxing paths are correct.
* **Commands**:
  ```bash
  cat /etc/systemd/system/maxwell-daemon.service | grep -E 'WorkingDirectory|ReadWritePaths|ExecStart'
  ls -la <paths from above>
  ```
* **Common causes**: `WorkingDirectory` or `ExecStart` binary path doesn't exist (e.g., repo was moved). `ProtectHome=read-only` is blocking access to a directory not listed in `ReadWritePaths`.
* **Fix**: Verify all paths in the unit file exist. If the repo was migrated to a new location, update the unit file with `sudo sed -i 's|old/path|new/path|g' /etc/systemd/system/maxwell-daemon.service && sudo systemctl daemon-reload`.

## Symptom: Service exits with status=203/EXEC

* **Check**: The `ExecStart` binary exists and has a valid shebang.
* **Commands**:
  ```bash
  head -1 /path/to/.venv/bin/maxwell-daemon
  # If the shebang points to a nonexistent path, the venv is stale
  ```
* **Common causes**: Virtual environment was moved from a different directory. The shebang in `.venv/bin/maxwell-daemon` points to the old path.
* **Fix**: Delete and recreate the venv: `rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -e . && deactivate`

## Symptom: Config validation error on startup (status=1/FAILURE)

* **Check**: The journal will show the exact Pydantic validation error.
* **Commands**:
  ```bash
  sudo journalctl -u maxwell-daemon --since "5 min ago" --no-pager | grep -A 5 ValidationError
  ```
* **Common causes**: Missing required fields (`version`, `type`, `model`, `agent.default_backend`), unknown top-level keys (e.g., `logging:`), `host: 0.0.0.0` without `jwt_secret`, backend key name not matching `agent.default_backend`.
* **Fix**: Compare your config against the minimal example in `docs/getting-started/configuration.md`. See `docs/operations/wsl2-node-deployment.md` for a full table of gotchas.

## Symptom: OperationalError — unable to open database file

* **Check**: The data directory exists and the systemd unit allows writes to it.
* **Commands**:
  ```bash
  ls -la ~/.local/share/maxwell-daemon/
  grep ReadWritePaths /etc/systemd/system/maxwell-daemon.service
  ```
* **Common causes**: `~/.local/share/maxwell-daemon/` doesn't exist, or it's not in the unit's `ReadWritePaths` while `ProtectHome=read-only` is set.
* **Fix**: `mkdir -p ~/.local/share/maxwell-daemon` then add the path to `ReadWritePaths` in the unit file and `sudo systemctl daemon-reload && sudo systemctl restart maxwell-daemon`.

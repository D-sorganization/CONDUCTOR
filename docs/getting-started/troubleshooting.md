# Troubleshooting

Start with the smallest failing command and work outward: config load, backend
health, API health, then task execution.

## Fast Triage Order

Use the same shell for each command so environment variables and config
selection stay consistent:

```bash
maxwell-daemon status
maxwell-daemon health
maxwell-daemon tasks list
maxwell-daemon checks run
```

If the failure only appears through the API, compare the CLI result with the
HTTP endpoint before changing code or config:

```bash
curl -s localhost:8080/health | jq
curl -s localhost:8080/api/v1/tasks | jq
```

## Config Does Not Load

Check which config file is being used:

```bash
echo "$MAXWELL_CONFIG"
maxwell-daemon status
```

Common causes:

- Environment variables referenced as `${VAR}` are unset.
- YAML indentation changed the shape of `backends`, `agent`, or `repos`.
- A repo override names a backend that is not registered.
- A relative repo path is valid on your laptop but not on the runner host.

## Backend Health Fails

Run:

```bash
maxwell-daemon health
```

For remote APIs, confirm the key is present in the same shell that runs the
daemon. For Ollama, confirm the server is up:

```bash
curl http://localhost:11434/api/tags
```

If only one backend is unhealthy, route a smoke prompt to a known-good backend
before debugging queue behavior:

```bash
maxwell-daemon ask "Say ready" --backend local
```

## Tasks Stay Queued

Check that the runner is active:

```bash
maxwell-daemon tasks list
maxwell-daemon-runner
```

If a runner exits immediately, inspect the logs for config errors or missing
repository paths. Repository paths are resolved on the worker machine, not on
the coordinator that created the task.

Confirm the task kind matches the worker you started:

```bash
maxwell-daemon tasks list --kind prompt
maxwell-daemon tasks list --kind issue
```

Issue tasks also require both `issue_repo` and `issue_number`. If the API
returns `422`, check that both fields are present in the JSON payload.

## Issue Dispatch Fails

Start with a read-only lookup of the target issue:

```bash
gh issue view 42 -R example/my-service
```

Then dispatch through the CLI so Maxwell applies the same validation used by
the API:

```bash
maxwell-daemon issue dispatch example/my-service 42 --mode implement
```

Common causes:

- The GitHub token can read the repository but cannot push branches.
- The issue already has an active PR, so the queue should skip it.
- The configured workspace path does not exist on the runner host.
- The issue task payload omitted `issue_repo` or `issue_number`.

## Checks Are Skipped

`maxwell-daemon checks run` only runs checks whose trigger matches the selected
event. If a check is reported as skipped, rerun with the event you are testing:

```bash
maxwell-daemon checks run --event pull_request
maxwell-daemon checks run --event push
```

Skipped results include the requested event and the configured trigger events.
Use that metadata to decide whether the check configuration or the command is
wrong.

## API Returns 401

If `api.auth_token` is set, every `/api/v1/*` request must include:

```http
Authorization: Bearer <token>
```

The `/health` and `/metrics` endpoints are intentionally unauthenticated for
infrastructure probes.

## API Returns 422

Validation errors mean the request reached Maxwell but failed its contract.
Keep the response body; it should name the missing or invalid field.

Common examples:

- `kind` is set to `issue` without `issue_repo` and `issue_number`.
- `repo_path` points outside an allowed workspace.
- A numeric field, such as a timeout or limit, is negative.
- A requested backend, model, or route is not configured.

Fix the payload first, then retry the exact same request so you know whether
the failure moved.

## WebSocket Events Disconnect

Slow subscribers are dropped so they do not block the daemon. Consume events
promptly and reconnect from the last task state your client has persisted.

Browser WebSocket APIs cannot set authorization headers, so pass the API token
as a query parameter:

```text
wss://host/api/v1/events?token=<token>
```

## Fleet Nodes Disappear

Use the coordinator to check the last heartbeat time, then verify the worker
directly:

```bash
curl -s localhost:8080/api/v1/fleet | jq
curl -s http://worker-host:8080/health | jq
```

Common causes:

- The worker API is bound to `127.0.0.1` instead of a reachable interface.
- Firewall rules allow SSH but not the Maxwell API port.
- The node clock is far enough out of sync that heartbeats look stale.
- The worker is alive but has no capacity for the requested tags.

## Artifacts Or Logs Are Missing

Task results can succeed even when an adapter cannot attach optional artifacts.
Check the task detail first:

```bash
maxwell-daemon tasks show <task-id>
```

If stdout, stderr, diffs, or session metadata are missing, confirm the adapter
supports those capabilities and that the artifact directory is writable by the
runner process.

## Documentation Build Fails

The documentation site is strict about broken links and missing nav entries.
Run the same build locally before opening a docs PR:

```bash
python -m mkdocs build --strict
```

If a page renders locally but fails in CI, check for case-sensitive path
differences. Windows and macOS often accept links that Linux rejects.

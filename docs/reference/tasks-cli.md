# Tasks CLI

Manage the daemon's task queue from the command line. All commands talk to the running daemon via its REST API.

## `conductor tasks list`

```
conductor tasks list [--status STATUS] [--kind KIND] [--repo REPO] [--limit N]
                     [--daemon-url URL] [--auth-token TOKEN]
```

Prints newest-first. Filters stack:

```bash
# Every failed implement-mode task against the upstream repo:
conductor tasks list --status failed --kind issue --repo D-sorg/upstream
```

## `conductor tasks show ID`

```
conductor tasks show TASK_ID [--daemon-url URL] [--auth-token TOKEN]
```

Prints every non-null field of a single task, including PR URL, test command, cost, timestamps.

## `conductor tasks cancel ID`

```
conductor tasks cancel TASK_ID [--daemon-url URL] [--auth-token TOKEN]
```

Sets status to `cancelled` for queued tasks. Returns non-zero exit code for running/completed/failed tasks — they can't be rolled back, only prevented from starting.

## Authentication

Set `CONDUCTOR_API_TOKEN` in the environment or pass `--auth-token`. The token is the same one configured under `api.auth_token` in `conductor.yaml`.

## Remote daemons

Point `--daemon-url` or `CONDUCTOR_DAEMON_URL` at any reachable daemon:

```bash
conductor tasks list --daemon-url https://conductor.internal:8080
```

SSH tunnel into a remote host to stay off the public internet:

```bash
ssh -L 8080:localhost:8080 fleet-host &
conductor tasks list
```

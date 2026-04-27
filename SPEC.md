# Maxwell-Daemon Specification

**Version:** 0.1.0  
**Contract Version:** 2025-04-01  
**Last Updated:** 2026-04-27

---

## 1. Overview

Maxwell-Daemon is an autonomous AI control plane. It exposes an HTTP API for task dispatch, pipeline control, fleet management, and agent orchestration. The browser-served `/ui/` control plane is the canonical operator UI.

---

## 2. HTTP API Surface

All endpoints are versioned under `/api/v1/` unless noted.

### 2.1 System Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Liveness probe |
| GET | `/readyz` | None | Readiness probe |
| GET | `/healthz` | None | Health check |
| GET | `/api/version` | None | Daemon semver + contract version |
| GET | `/api/health` | None | Gate state, current focus |
| GET | `/api/status` | None | Pipeline state, active task, gates, sandbox |

### 2.2 Tasks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/tasks` | Viewer+ | Submit a task |
| GET | `/api/v1/tasks` | Viewer+ | List tasks |
| GET | `/api/v1/tasks/{task_id}` | Viewer+ | Fetch a task |
| POST | `/api/v1/tasks/{task_id}/cancel` | Operator+ | Cancel a task |

### 2.3 Dispatch & Control

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/dispatch` | Token | Submit a signed task envelope |
| POST | `/api/control/{action}` | Operator+ | Pipeline control (pause, resume, abort) |

### 2.4 Backends

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/backends` | Viewer+ | List configured backends |
| GET | `/api/v1/backends/available` | Viewer+ | List available backend types |
| POST | `/api/v1/backends` | Admin | Configure a backend |
| POST | `/api/v1/backends/{name}/test` | Admin | Test a backend |

### 2.5 Actions & Approvals

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/actions` | Viewer+ | List actions |
| GET | `/api/v1/actions/{action_id}` | Viewer+ | Fetch an action |
| POST | `/api/v1/actions/{action_id}/approve` | Operator+ | Approve an action |
| POST | `/api/v1/actions/{action_id}/reject` | Operator+ | Reject an action |

### 2.6 Work Items

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/work-items` | Operator+ | Create a work item |
| GET | `/api/v1/work-items` | Viewer+ | List work items |
| GET | `/api/v1/work-items/{item_id}` | Viewer+ | Fetch a work item |
| PATCH | `/api/v1/work-items/{item_id}` | Operator+ | Update a work item |
| POST | `/api/v1/work-items/{item_id}/transition` | Operator+ | Transition work item state |

### 2.7 Task Graphs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/task-graphs` | Operator+ | Create a task graph |
| GET | `/api/v1/task-graphs` | Viewer+ | List task graphs |
| GET | `/api/v1/task-graphs/{graph_id}` | Viewer+ | Fetch a task graph |
| POST | `/api/v1/task-graphs/{graph_id}/start` | Operator+ | Start a task graph |

### 2.8 Memory

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/memory/assemble` | Operator+ | Assemble memory context |
| POST | `/api/v1/memory/record` | Operator+ | Record memory |

### 2.9 Issues

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/issues` | Operator+ | Create an issue record |
| POST | `/api/v1/issues/dispatch` | Operator+ | Dispatch from issue |
| POST | `/api/v1/issues/ab-dispatch` | Operator+ | A/B dispatch |
| POST | `/api/v1/issues/batch-dispatch` | Operator+ | Batch dispatch |
| GET | `/api/v1/issues/{owner}/{name}` | Viewer+ | List repo issues |

### 2.10 Artifacts

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/artifacts/{artifact_id}` | Viewer+ | Fetch artifact metadata |
| GET | `/api/v1/artifacts/{artifact_id}/content` | Viewer+ | Fetch artifact content |

### 2.11 Templates

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/templates` | Viewer+ | List templates |
| GET | `/api/v1/templates/{template_id}` | Viewer+ | Fetch a template |

### 2.12 Fleet

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/fleet` | Viewer+ | Fleet overview |
| GET | `/api/v1/fleet/capabilities` | Viewer+ | Fleet capabilities |
| GET | `/api/v1/fleet/nodes` | Viewer+ | Fleet nodes |

### 2.13 Audit

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/audit` | Viewer+ | Audit log |
| GET | `/api/v1/audit/verify` | Viewer+ | Verify audit integrity |

### 2.14 Workers

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/workers` | Viewer+ | Worker status |
| PUT | `/api/v1/workers` | Operator+ | Set worker count |

### 2.15 Delegate Sessions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/delegate-sessions` | Viewer+ | List delegate sessions |
| GET | `/api/v1/delegate-sessions/{session_id}` | Viewer+ | Fetch session |

### 2.16 Cost

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/cost` | Viewer+ | Cost summary (month-to-date + by backend) |

### 2.17 Evals

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/evals/run` | Operator+ | Run eval suite |
| GET | `/api/v1/evals/leaderboard` | Viewer+ | Eval leaderboard |

### 2.18 Webhooks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/webhooks/github` | None | GitHub webhook |
| POST | `/api/webhooks/trigger` | Operator+ | Generic webhook trigger |

### 2.19 SSH

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/ssh/sessions` | Admin | List SSH sessions |
| GET | `/api/v1/ssh/keys` | Admin | List SSH keys |
| GET | `/api/v1/ssh/keys/{machine}` | Admin | Get SSH key |
| DELETE | `/api/v1/ssh/keys/{machine}` | Admin | Delete SSH key |
| POST | `/api/v1/ssh/connect` | Admin | Connect SSH |
| POST | `/api/v1/ssh/run` | Admin | Run SSH command |
| GET | `/api/v1/ssh/files` | Admin | List remote files |

### 2.20 Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/auth/token` | Admin | Issue JWT token |
| POST | `/api/v1/auth/refresh` | Token | Refresh access token |
| POST | `/api/v1/auth/revoke` | Operator+ | Revoke token |
| GET | `/api/v1/auth/me` | Token | Current user info |

### 2.21 Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/admin/prune` | Admin | Prune history |
| POST | `/api/reload` | Admin | Reload configuration |

---

## 3. WebSocket Events

| Path | Description |
|------|-------------|
| `WS /api/v1/events` | Agent progress streaming |
| `WS /api/v1/ssh/shell` | Interactive SSH shell |

---

## 4. Fleet Manifest Schema

The fleet manifest (`fleet.yaml`) defines repos and agent slots. Validate changes with `maxwell_daemon/fleet/config.py`.

```yaml
# Example fleet.yaml structure
repositories:
  - name: example-repo
    url: https://github.com/org/example
    agent_slots: 2
    enabled: true
```

---

## 5. Event System

- Agent progress streamed via `GET /api/v1/events` (SSE) and `WS /api/v1/ws`
- Always test event propagation when modifying the daemon loop

---

## 6. Authentication & Authorization

- **Simple Bearer:** `api.auth_token` in config for basic endpoints
- **JWT/RBAC:** Role enum (`Admin`, `Operator`, `Viewer`), JWTConfig, `require_role()`
- TLS termination at reverse proxy (nginx, caddy) or enable TLS in uvicorn

---

## 7. Cost Ledger

Costs tracked in WAL-mode SQLite file. Never replace with ORM-based abstraction without a migration.

---

## 8. Append-Only Contract

This API contract is **append-only**: add endpoints freely; never break existing ones without a major-version bump advertised at `GET /api/version`.

---

## 9. UI

The UI (`maxwell_daemon/api/ui/`) is a plain JS SPA — no build step required. The browser-served `/ui/` is the canonical operator UI.

---

## 10. Cross-Repo Rules

- Maxwell-Daemon **never calls back** into `runner-dashboard` or `Repository_Management`
- Cross-repo traffic is always *into* the daemon — never out
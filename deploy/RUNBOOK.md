# Maxwell-Daemon Deployment Runbook

**Version:** 0.1.0  
**Last Updated:** 2026-04-27

---

## 1. Overview

This runbook covers deployment, health checks, troubleshooting, and rollback procedures for Maxwell-Daemon.

---

## 2. Prerequisites

- Python 3.10+
- `uv` or `pip` for dependency management
- systemd (Linux) or equivalent service manager
- Reverse proxy (nginx/caddy) for TLS termination

---

## 3. Deployment Procedure

### 3.1 Standard Deploy

```bash
cd /opt/maxwell-daemon
git pull origin main
uv sync --all-extras
sudo systemctl restart maxwell-daemon
```

### 3.2 Record Deployment

```bash
echo "$(git rev-parse HEAD) $(date -Iseconds)" >> .deployment_history
```

---

## 4. Health Checks

### 4.1 HTTP Health Endpoints

| Endpoint | Purpose | Expected |
|----------|---------|----------|
| `GET /health` | Liveness | `200 OK` |
| `GET /readyz` | Readiness | `200 OK` |
| `GET /api/version` | Version | JSON with semver |

### 4.2 Manual Health Check

```bash
curl -sf http://localhost:8080/health && echo "OK" || echo "FAIL"
```

### 4.3 systemd Status

```bash
sudo systemctl status maxwell-daemon
sudo journalctl -u maxwell-daemon -f
```

---

## 5. Troubleshooting

### 5.1 Service Won't Start

1. Check logs: `journalctl -u maxwell-daemon -n 100`
2. Verify config: `python -m maxwell_daemon config validate`
3. Check port binding: `ss -tlnp | grep 8080`

### 5.2 High Memory Usage

1. Check worker count: `GET /api/v1/workers`
2. Reduce workers: `PUT /api/v1/workers?count=N`
3. Review active tasks: `GET /api/v1/tasks`

### 5.3 API Unresponsive

1. Check `/health` and `/readyz`
2. Verify reverse proxy config
3. Check disk space for SQLite cost ledger

---

## 6. Rollback Procedure

### 6.1 Automatic Rollback (Previous Version)

```bash
cd /opt/maxwell-daemon/deploy
./rollback.sh
```

### 6.2 Rollback to Specific Version

```bash
cd /opt/maxwell-daemon/deploy
./rollback.sh v0.1.0
```

### 6.3 Manual Rollback

1. Stop service: `sudo systemctl stop maxwell-daemon`
2. Checkout previous version: `git checkout <VERSION>`
3. Reinstall deps: `uv sync --all-extras`
4. Start service: `sudo systemctl start maxwell-daemon`
5. Verify health: `curl -sf http://localhost:8080/health`

---

## 7. Monitoring

- Prometheus metrics exposed on `/metrics`
- Grafana dashboard: `deploy/grafana/maxwell-daemon-dashboard.json`
- Audit log: `GET /api/v1/audit`

---

## 8. Emergency Contacts

- Fleet orchestrator issues → `Repository_Management`
- Dashboard UI issues → `runner-dashboard`
- Daemon issues → This repo (Maxwell-Daemon)
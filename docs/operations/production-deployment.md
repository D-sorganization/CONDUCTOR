# Production Deployment Guide

This guide covers running a single-instance Maxwell-Daemon in production
(VM, bare-metal, or a single container host). It is intentionally
opinionated and focuses on the smallest deployment that is still
operable.

For broader topology options (Ansible fleets, Tailscale, Terraform), see
[`deployment.md`](deployment.md). For high-availability and horizontal
scaling, see the follow-up issues tracked from #801 — those are out of
scope for phase 1.

---

## Prerequisites

Hardware (single-instance):

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 2 vCPU | 4 vCPU |
| RAM | 2 GiB | 4 GiB |
| Disk | 10 GiB SSD | 25 GiB SSD |
| Network | Outbound HTTPS to LLM providers | Same, plus inbound 8080 from your VPC |

Software:

- Docker 24+ **or** Python 3.12 with `uv`
- Persistent volume mounted at the daemon's data directory
  (`~/.local/share/maxwell-daemon` on Linux, or `/var/lib/maxwell-daemon`
  for system installs). The SQLite cost ledger and task store live here.
- A reverse proxy (nginx, Caddy, or a cloud LB) terminating TLS in
  front of the daemon. Maxwell-Daemon does not terminate TLS itself.
- A Prometheus scraper (optional but strongly recommended). See
  [`monitoring.md`](monitoring.md).

---

## Environment Variables

The daemon reads its YAML config from `--config` and overlays the
following environment variables. The most common are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAXWELL_CONFIG` | `~/.config/maxwell-daemon/maxwell-daemon.yaml` | Path to config file. |
| `MAXWELL_PORT` | `8080` | API listen port. |
| `MAXWELL_HOST` | `127.0.0.1` | Bind address. Set to `0.0.0.0` only behind a reverse proxy. |
| `MAXWELL_LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `MAXWELL_LOG_FORMAT` | `json` (non-TTY) | `console` for human-readable, `json` for log shippers. |
| `MAXWELL_LOG_FILE` | _(unset)_ | Optional rotating-file destination. |
| `MAXWELL_REDACT_LOGS` | `1` | Set to `0` only for debugging — leaks secrets. |
| `MAXWELL_API_TOKEN` | _(unset)_ | Required when binding to a non-loopback host. |
| `MAXWELL_AGGRESSIVE_COMPRESSION` | `off` | Trade quality for token savings on long contexts. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. | _(unset)_ | Provider credentials. Pass through your secret store, never bake into images. |

Any environment variable beginning with `MAXWELL_` overrides the matching
key in `maxwell-daemon.yaml`. For the full list, see
[`getting-started/configuration.md`](../getting-started/configuration.md).

---

## Single-Instance docker-compose Example

The repo ships a working `docker-compose.yml` at the root. A minimal
production override looks like this:

```yaml
# docker-compose.production.yml
services:
  maxwell-daemon:
    image: ghcr.io/d-sorganization/maxwell-daemon:latest
    container_name: maxwell-daemon
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"   # bind to loopback; reverse proxy terminates TLS
    environment:
      MAXWELL_HOST: "0.0.0.0"
      MAXWELL_PORT: "8080"
      MAXWELL_LOG_LEVEL: "INFO"
      MAXWELL_LOG_FORMAT: "json"
      MAXWELL_API_TOKEN: "${MAXWELL_API_TOKEN}"
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
    volumes:
      - maxwell-data:/home/maxwell/.local/share/maxwell-daemon
      - ./maxwell-daemon.yaml:/app/config/maxwell-daemon.yaml:ro
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

volumes:
  maxwell-data:
```

Bring it up with:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

Smoke-test:

```bash
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/api/version
```

---

## Capacity Planning

These numbers are starting points. Always validate against your own
workload before sizing prod capacity.

| Tier | Tasks/hour (sustained) | Concurrent tasks | RAM | Disk growth |
| --- | --- | --- | --- | --- |
| Solo developer | up to ~30 | 1-2 | 2 GiB | < 100 MiB / week |
| Small team | up to ~150 | 2-4 | 4 GiB | ~ 500 MiB / week |
| Heavy use | up to ~500 | 4-8 | 8 GiB | ~ 2 GiB / week |

Notes:

- **Disk** is dominated by the cost ledger (`ledger.db`), task store,
  and raw agent logs in the data directory. Enable the memory annealer
  (`memory_dream_interval_seconds > 0` in config) to compact old logs.
- **RAM** scales with concurrent tasks more than with throughput.
  Each in-flight task holds its prompt context in memory.
- **CPU** is rarely the bottleneck — outbound LLM latency dominates.
- **Outbound bandwidth** is roughly proportional to total tokens. Budget
  ~1 KiB per 250 tokens of completion as a rough rule of thumb.

For multi-instance scaling and load-balancing the API across several
daemons, see the follow-up issue scaling.md placeholder linked from #801.

---

## Operational Checklist

Before treating a deployment as production-ready:

- [ ] `/api/health` reachable from the load balancer.
- [ ] `/metrics` scraped by Prometheus.
- [ ] Alerts wired up per [`monitoring.md`](monitoring.md).
- [ ] `MAXWELL_API_TOKEN` set whenever the daemon listens on a
      non-loopback interface.
- [ ] Backups configured for the data volume (`ledger.db` is the
      audit-of-record; protect it accordingly).
- [ ] Log shipping configured (`MAXWELL_LOG_FORMAT=json` to stdout,
      collected by your platform's log driver).
- [ ] Runbooks linked from [`troubleshooting.md`](troubleshooting.md).

---

## Related Docs

- [`monitoring.md`](monitoring.md) — Prometheus metrics, alert rules, dashboards.
- [`troubleshooting.md`](troubleshooting.md) — runbook for common production issues.
- [`deployment.md`](deployment.md) — alternative deployment topologies.
- [`security.md`](security.md) — sandbox guarantees, secret handling, network exposure.
- [`production-readiness.md`](production-readiness.md) — readiness gates and review checklist.

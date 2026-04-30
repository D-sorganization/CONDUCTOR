# Monitoring

Maxwell-Daemon exposes Prometheus metrics at `GET /metrics` (no auth, on
the same port as the API). The complete metric surface is defined in
[`maxwell_daemon/metrics.py`](../../maxwell_daemon/metrics.py); this page
documents the operationally important subset and shows how to alert on
it.

For log-format and event-stream details, see
[`observability.md`](observability.md). For deployment topology see
[`production-deployment.md`](production-deployment.md).

---

## Metric Reference

All metric names are emitted exactly as listed below.

### Request and cost counters

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `maxwell_daemon_requests_total` | counter | `backend`, `model`, `status` | Total agent requests. `status` is `success`, `error`, or `budget_exceeded`. |
| `maxwell_daemon_tokens_total` | counter | `backend`, `model` | Prompt + completion tokens for successful requests. |
| `maxwell_daemon_request_cost_usd_total` | counter | `backend`, `model` | Cumulative billed cost in USD. |
| `maxwell_daemon_cache_hit_tokens_total` | counter | `backend`, `model` | Tokens served from prompt cache. |
| `maxwell_daemon_free_requests_total` | counter | `backend`, `model` | Successful requests with verified zero billed cost (local Ollama, cached hits). |

### Latency histograms

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `maxwell_daemon_request_duration_seconds` | histogram | `backend`, `model` | Per-request wall-clock duration. |
| `maxwell_daemon_queue_latency_ms` | histogram | _(none)_ | Latency to dequeue a task from the priority queue. |
| `maxwell_daemon_http_request_duration_seconds` | histogram | `endpoint` | HTTP request latency by endpoint. |

### Gauges

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `maxwell_daemon_active_tasks` | gauge | _(none)_ | Tasks currently in a non-terminal state. |
| `maxwell_daemon_live_tasks_dict_size` | gauge | _(none)_ | Tasks held in the hot in-memory dict. |
| `maxwell_daemon_queue_depth` | gauge | _(none)_ | Current task queue depth. |
| `maxwell_daemon_token_budget_allocation` | gauge | `task_id`, `budget_remaining`, `model_chosen` | Per-task safe budget allocation in USD. |
| `maxwell_daemon_cost_forecast_usd` | gauge | _(none)_ | Linear month-end spend forecast from the cost ledger. |
| `maxwell_daemon_cache_hit_rate` | gauge | _(none)_ | Prompt cache hit rate (0.0 - 1.0). |
| `maxwell_ledger_connections_in_use` | gauge | _(none)_ | Active SQLite connections in the ledger pool. |

### Verdicts and HTTP

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `maxwell_daemon_gate_verdicts_total` | counter | `verdict`, `severity` | Gate verdicts by outcome and severity. |
| `maxwell_daemon_http_requests_total` | counter | `method`, `endpoint`, `status` | HTTP requests by method, endpoint, and HTTP status code. |

---

## Prometheus Scrape Config

```yaml
# prometheus.yml
scrape_configs:
  - job_name: maxwell-daemon
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets:
          - "maxwell-daemon.internal:8080"
        labels:
          service: maxwell-daemon
          env: production
```

If you secure `/metrics` behind your API token, add:

```yaml
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/maxwell-token
```

---

## Sample Alert Rules

Save as `alert-rules.yaml` and load via `rule_files:` in `prometheus.yml`.

```yaml
groups:
  - name: maxwell-daemon
    interval: 30s
    rules:
      - alert: MaxwellDaemonDown
        expr: up{job="maxwell-daemon"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Maxwell-Daemon is unreachable"
          description: "Prometheus has been unable to scrape /metrics for 2m."

      - alert: MaxwellHighErrorRate
        expr: |
          sum(rate(maxwell_daemon_requests_total{status="error"}[5m]))
            /
          sum(rate(maxwell_daemon_requests_total[5m])) > 0.10
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Agent request error rate above 10%"
          description: "{{ $value | humanizePercentage }} of agent requests are failing over the last 5m."

      - alert: MaxwellBudgetExceededSpiking
        expr: rate(maxwell_daemon_requests_total{status="budget_exceeded"}[10m]) > 0.1
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Budget-exceeded rejections are climbing"
          description: "Tasks are being rejected for budget. Consider raising budgets or compressing context."

      - alert: MaxwellQueueBacklog
        expr: maxwell_daemon_queue_depth > 50
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Task queue backlog above 50"
          description: "Queue depth has stayed above 50 for 10m. Workers may be stalled."

      - alert: MaxwellRequestLatencyP95High
        expr: |
          histogram_quantile(0.95,
            sum by (le) (rate(maxwell_daemon_request_duration_seconds_bucket[10m]))
          ) > 60
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "p95 request duration above 60s"
          description: "Upstream model latency or local stalls are degrading the pipeline."

      - alert: MaxwellCostForecastHigh
        expr: maxwell_daemon_cost_forecast_usd > 500
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Month-end cost forecast above $500"
          description: "Linear forecast from the ledger exceeds $500 — review usage."

      - alert: MaxwellLedgerConnectionsExhausted
        expr: maxwell_ledger_connections_in_use >= 8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Ledger connection pool saturated"
          description: "All SQLite connections are checked out. Expect ledger writes to block."

      - alert: MaxwellGateRejectionsSpiking
        expr: |
          sum(rate(maxwell_daemon_gate_verdicts_total{verdict="reject"}[10m]))
            > 1
        for: 15m
        labels:
          severity: info
        annotations:
          summary: "Gate is rejecting more than 1 verdict/sec"
          description: "Implementer or critic output may be regressing."
```

Tune thresholds against a steady-state baseline before paging on them.

---

## Grafana Dashboard

A starter dashboard JSON is planned and will live at
`grafana/maxwell-daemon-dashboard.json`. Until that lands, use these
panels as a starting point against any blank Grafana board:

| Row | Panel | Query |
| --- | --- | --- |
| Throughput | Requests/sec by status | `sum by (status) (rate(maxwell_daemon_requests_total[5m]))` |
| Throughput | Tokens/sec by backend | `sum by (backend) (rate(maxwell_daemon_tokens_total[5m]))` |
| Cost | Spend / hour | `sum(rate(maxwell_daemon_request_cost_usd_total[1h])) * 3600` |
| Cost | Month-end forecast | `maxwell_daemon_cost_forecast_usd` |
| Latency | p50 / p95 / p99 request duration | `histogram_quantile(0.95, sum by (le) (rate(maxwell_daemon_request_duration_seconds_bucket[10m])))` |
| Pipeline | Active tasks | `maxwell_daemon_active_tasks` |
| Pipeline | Queue depth | `maxwell_daemon_queue_depth` |
| Pipeline | Cache hit rate | `maxwell_daemon_cache_hit_rate` |
| Gate | Verdicts/sec by outcome | `sum by (verdict) (rate(maxwell_daemon_gate_verdicts_total[5m]))` |

Track progress on the official dashboard JSON in the
[Grafana dashboard issue](https://github.com/D-sorganization/Maxwell-Daemon/issues/15).

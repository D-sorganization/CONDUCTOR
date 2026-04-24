# Performance Tuning Guide

This guide covers optimizing the Maxwell Daemon for token efficiency, throughput, cost management, and reliability.

## Token Efficiency

* **Enable Memory Annealer**: Ensure `memory_dream_interval_seconds` is set to a value > 0 in your config to continuously prune and summarize episodic memories.
* **Enable Context Compression**: Run the daemon with `MAXWELL_AGGRESSIVE_COMPRESSION=on` to reduce the prompt size.
* **Monitor Cache Hits**: Watch the `cache_hit_rate` metric to see how well your repetitive tasks are utilizing LLM context caching.
* **Dashboard Trends**: Monitor the `cost_forecast_usd` trend in the dashboard to identify anomalies in token usage.

## Throughput Optimization

* **Worker Count**: Set worker count to roughly 1 per available CPU core. Start with 2 and measure before scaling up.
* **Queue Depth**: Set the queue depth to 2-3x the worker count to avoid memory bloat from unexecuted tasks holding onto large contexts.
* **Gate Timeout**: If your gate critics legitimately need more time to review large codebases, increase the gate timeout.
* **Backend Pooling**: Reuse connections (via `httpx` pool or `asyncio` connection cache) to reduce TLS handshake overhead to backends.

## Cost Management

* **Budget Thresholds**: Set alerting thresholds at 80% and 95% of your `max_daily_usd` or `max_monthly_usd` budgets.
* **Model Selection**: Favor cheaper, faster models (e.g. Haiku) for routine tasks like formatting or simple reviews. Use more expensive models (e.g. Opus) strictly for complex planning or architectural changes.
* **Batching**: Group similar tasks together to maximize context cache reuse.

## Reliability

* **Backup `cost_ledger.db`**: Backup this file daily. It is an immutable, append-only log and typically very small (<10MB).
* **Backup Stores**: `task_store.db` and `work_item_store.db` are optional but good to backup if you rely on historical task states.
* **Retention Policy**: Set `task_retention_days = 90` to automatically prune old records and prevent SQLite unbounded growth.
* **Monitor Heartbeats**: Monitor the `worker_last_seen` heartbeat for fleet workers; expect < 30s drift in healthy fleets.

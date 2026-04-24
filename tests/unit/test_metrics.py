"""Prometheus metrics — counters, histograms, and /metrics exposure."""

from __future__ import annotations

from unittest.mock import Mock

from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from maxwell_daemon import metrics as metrics_mod
from maxwell_daemon.metrics import (
    MAXWELL_FREE_REQUESTS_TOTAL,
    MAXWELL_REQUEST_COST,
    MAXWELL_REQUESTS_TOTAL,
    MAXWELL_TOKENS_TOTAL,
    build_registry,
    mount_metrics_endpoint,
    record_request,
)


class TestRecordRequest:
    def test_counts_successful_request(self) -> None:
        before = MAXWELL_REQUESTS_TOTAL.labels(
            backend="claude", model="m", status="success"
        )._value.get()
        record_request(
            backend="claude",
            model="m",
            status="success",
            tokens=100,
            cost_usd=0.05,
            duration_seconds=1.5,
        )
        after = MAXWELL_REQUESTS_TOTAL.labels(
            backend="claude", model="m", status="success"
        )._value.get()
        assert after == before + 1

    def test_records_token_total(self) -> None:
        before = MAXWELL_TOKENS_TOTAL.labels(backend="claude", model="m")._value.get()
        record_request(
            backend="claude",
            model="m",
            status="success",
            tokens=500,
            cost_usd=0.01,
            duration_seconds=0.5,
        )
        after = MAXWELL_TOKENS_TOTAL.labels(backend="claude", model="m")._value.get()
        assert after == before + 500

    def test_free_request_increments_free_counter_not_cost(self) -> None:
        # A zero-cost success (local Ollama, cached hit) should bump the
        # free-requests counter while leaving the USD cost counter flat.
        free_before = MAXWELL_FREE_REQUESTS_TOTAL.labels(
            backend="ollama", model="llama3"
        )._value.get()
        cost_before = MAXWELL_REQUEST_COST.labels(backend="ollama", model="llama3")._value.get()
        record_request(
            backend="ollama",
            model="llama3",
            status="success",
            tokens=10,
            cost_usd=0.0,
            duration_seconds=0.1,
        )
        free_after = MAXWELL_FREE_REQUESTS_TOTAL.labels(
            backend="ollama", model="llama3"
        )._value.get()
        cost_after = MAXWELL_REQUEST_COST.labels(backend="ollama", model="llama3")._value.get()
        assert free_after == free_before + 1
        assert cost_after == cost_before

    def test_priced_request_does_not_increment_free_counter(self) -> None:
        free_before = MAXWELL_FREE_REQUESTS_TOTAL.labels(backend="claude", model="m")._value.get()
        record_request(
            backend="claude",
            model="m",
            status="success",
            tokens=10,
            cost_usd=0.02,
            duration_seconds=0.1,
        )
        free_after = MAXWELL_FREE_REQUESTS_TOTAL.labels(backend="claude", model="m")._value.get()
        assert free_after == free_before

    def test_unknown_cost_does_not_increment_free_counter(self) -> None:
        free_before = MAXWELL_FREE_REQUESTS_TOTAL.labels(
            backend="claude", model="unknown-cost"
        )._value.get()
        record_request(
            backend="claude",
            model="unknown-cost",
            status="success",
            tokens=10,
            duration_seconds=0.1,
        )
        free_after = MAXWELL_FREE_REQUESTS_TOTAL.labels(
            backend="claude", model="unknown-cost"
        )._value.get()
        assert free_after == free_before

    def test_error_status_skips_token_and_cost(self) -> None:
        # Error path still bumps the request counter but not tokens/cost.
        tokens_before = MAXWELL_TOKENS_TOTAL.labels(backend="claude", model="err")._value.get()
        record_request(backend="claude", model="err", status="error")
        tokens_after = MAXWELL_TOKENS_TOTAL.labels(backend="claude", model="err")._value.get()
        assert tokens_after == tokens_before


class TestMetricsEndpoint:
    def test_endpoint_returns_200(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        mount_metrics_endpoint(app)
        client = TestClient(app)
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "maxwell_daemon_" in r.text

    def test_content_type_is_prometheus_text(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        mount_metrics_endpoint(app)
        client = TestClient(app)
        r = client.get("/metrics")
        assert r.headers["content-type"].startswith("text/plain")


class TestBuildRegistry:
    def test_returns_collector_registry(self) -> None:
        reg = build_registry()
        assert isinstance(reg, CollectorRegistry)


class TestGateAndQueueMetrics:
    def test_record_cache_hit_sets_gauge(self, monkeypatch) -> None:
        gauge = Mock()
        monkeypatch.setattr(metrics_mod, "MAXWELL_CACHE_HIT_RATE", gauge)

        metrics_mod.record_cache_hit(0.42)

        gauge.set.assert_called_once_with(0.42)

    def test_record_gate_verdict_increments_labeled_counter(self, monkeypatch) -> None:
        counter = Mock()
        labels = Mock()
        counter.labels.return_value = labels
        monkeypatch.setattr(metrics_mod, "MAXWELL_GATE_VERDICTS_TOTAL", counter)

        metrics_mod.record_gate_verdict("blocked", "p1")

        counter.labels.assert_called_once_with(verdict="blocked", severity="p1")
        labels.inc.assert_called_once_with()

    def test_record_queue_depth_sets_gauge(self, monkeypatch) -> None:
        gauge = Mock()
        monkeypatch.setattr(metrics_mod, "MAXWELL_QUEUE_DEPTH", gauge)

        metrics_mod.record_queue_depth(7)

        gauge.set.assert_called_once_with(7)

    def test_record_queue_latency_observes_histogram(self, monkeypatch) -> None:
        histogram = Mock()
        monkeypatch.setattr(metrics_mod, "MAXWELL_QUEUE_LATENCY_MS", histogram)

        metrics_mod.record_queue_latency(12.5)

        histogram.observe.assert_called_once_with(12.5)

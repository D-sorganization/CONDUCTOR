"""Validate the shipped Grafana dashboard against the metrics we actually export."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "grafana" / "conductor-dashboard.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


class TestDashboardShape:
    def test_has_title_and_uid(self, dashboard: dict) -> None:
        assert dashboard["title"] == "CONDUCTOR — Agent Fleet"
        assert dashboard["uid"]
        assert dashboard["schemaVersion"] >= 36

    def test_has_panels(self, dashboard: dict) -> None:
        assert len(dashboard["panels"]) >= 5

    def test_templating_has_datasource(self, dashboard: dict) -> None:
        assert any(v.get("name") == "DS_PROMETHEUS" for v in dashboard["templating"]["list"])


class TestMetricsAreReal:
    """Every metric referenced in PromQL must actually be emitted by the code."""

    def _referenced_metrics(self, dashboard: dict) -> set[str]:
        # Match conductor_* metric names, stripping trailing suffixes added by
        # prometheus_client (_total for counters; _bucket/_sum/_count for histograms).
        name_re = re.compile(r"\bconductor_[a-z_]+\b")
        found: set[str] = set()
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for m in name_re.findall(expr):
                    base = re.sub(r"_(total|bucket|sum|count)$", "", m)
                    found.add(base)
        return found

    def _exported_metrics(self) -> set[str]:
        from conductor.metrics import (
            CONDUCTOR_COST_FORECAST_USD,
            CONDUCTOR_REQUEST_COST,
            CONDUCTOR_REQUEST_DURATION,
            CONDUCTOR_REQUESTS_TOTAL,
            CONDUCTOR_TOKENS_TOTAL,
        )

        # `._name` is the internal base-name attribute on prometheus_client
        # collectors — stable enough for our own metrics.
        return {
            CONDUCTOR_REQUESTS_TOTAL._name,
            CONDUCTOR_TOKENS_TOTAL._name,
            CONDUCTOR_REQUEST_COST._name,
            CONDUCTOR_REQUEST_DURATION._name,
            CONDUCTOR_COST_FORECAST_USD._name,
        }

    def test_every_referenced_metric_exists(self, dashboard: dict) -> None:
        referenced = self._referenced_metrics(dashboard)
        exported = self._exported_metrics()
        missing = referenced - exported
        assert not missing, (
            f"Dashboard references metrics not exported by conductor.metrics: {missing}"
        )

"""Tests for ``SecurityHeadersMiddleware`` and the CORS scaffolding.

Phase-1 of issue #797: validate that the new security headers ship on every
response, that HSTS is gated on the HTTPS scheme, that CSP stays off-by-default,
and that ``api.cors.enabled`` actually wires up FastAPI's CORS middleware with
the configured allow-list.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maxwell_daemon.api import create_app
from maxwell_daemon.api.security_headers import (
    DEFAULT_CSP,
    SecurityHeadersMiddleware,
)
from maxwell_daemon.config import MaxwellDaemonConfig
from maxwell_daemon.daemon import Daemon


@pytest.fixture
def daemon(
    minimal_config: MaxwellDaemonConfig,
    isolated_ledger_path: Path,
    tmp_path: Path,
) -> Iterator[Daemon]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    d = Daemon(
        minimal_config,
        ledger_path=isolated_ledger_path,
        task_store_path=tmp_path / "tasks.db",
        work_item_store_path=tmp_path / "work_items.db",
        task_graph_store_path=tmp_path / "task_graphs.db",
        artifact_store_path=tmp_path / "artifacts.db",
        artifact_blob_root=tmp_path / "artifacts",
        action_store_path=tmp_path / "actions.db",
        delegate_lifecycle_store_path=tmp_path / "delegate_sessions.db",
    )
    loop.run_until_complete(d.start(worker_count=1))
    try:
        yield d
    finally:
        loop.run_until_complete(d.stop())
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)


# ---------------------------------------------------------------------------
# Direct middleware tests — exercise the class without spinning up the daemon.
# ---------------------------------------------------------------------------


def _bare_app(*, enable_csp: bool = False, csp_policy: str | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_csp=enable_csp,
        csp_policy=csp_policy,
    )

    @app.get("/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    return app


class TestSecurityHeadersMiddleware:
    def test_default_headers_present(self) -> None:
        client = TestClient(_bare_app())
        r = client.get("/probe")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert r.headers["X-XSS-Protection"] == "0"

    def test_xss_protection_is_disabled_not_enabled(self) -> None:
        # Modern browsers' legacy auditor is exploitable; we MUST disable it.
        client = TestClient(_bare_app())
        r = client.get("/probe")
        assert r.headers["X-XSS-Protection"] == "0"
        assert "mode=block" not in r.headers["X-XSS-Protection"]

    def test_hsts_absent_on_http(self) -> None:
        client = TestClient(_bare_app())
        r = client.get("/probe")  # default base URL is http://testserver
        assert "Strict-Transport-Security" not in r.headers

    def test_hsts_present_on_https(self) -> None:
        # TestClient honours the scheme of base_url; using https:// causes
        # request.url.scheme to be "https" inside the middleware.
        client = TestClient(_bare_app(), base_url="https://testserver")
        r = client.get("/probe")
        assert r.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"

    def test_csp_absent_when_disabled(self) -> None:
        client = TestClient(_bare_app(enable_csp=False))
        r = client.get("/probe")
        assert "Content-Security-Policy" not in r.headers

    def test_csp_present_when_enabled(self) -> None:
        client = TestClient(_bare_app(enable_csp=True))
        r = client.get("/probe")
        assert r.headers["Content-Security-Policy"] == DEFAULT_CSP

    def test_csp_custom_policy(self) -> None:
        custom = "default-src 'none'"
        client = TestClient(_bare_app(enable_csp=True, csp_policy=custom))
        r = client.get("/probe")
        assert r.headers["Content-Security-Policy"] == custom

    def test_does_not_clobber_upstream_header(self) -> None:
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/iframe-friendly")
        def iframe_friendly() -> dict[str, str]:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"ok": "yes"},
                headers={"X-Frame-Options": "DENY"},
            )

        client = TestClient(app)
        r = client.get("/iframe-friendly")
        # Endpoint-set value must win over the middleware default.
        assert r.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# Integration with create_app — the middleware is actually registered.
# ---------------------------------------------------------------------------


class TestSecurityHeadersWiredIntoApp:
    def test_headers_on_health_endpoint(self, daemon: Daemon) -> None:
        with TestClient(create_app(daemon)) as client:
            r = client.get("/health")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert r.headers["X-XSS-Protection"] == "0"

    def test_csp_off_by_default_in_create_app(self, daemon: Daemon) -> None:
        with TestClient(create_app(daemon)) as client:
            r = client.get("/health")
        assert "Content-Security-Policy" not in r.headers


# ---------------------------------------------------------------------------
# CORS configuration tests.
# ---------------------------------------------------------------------------


class TestCORSConfig:
    def test_cors_disabled_by_default(self, daemon: Daemon) -> None:
        with TestClient(create_app(daemon)) as client:
            r = client.get(
                "/health",
                headers={"Origin": "https://evil.example.com"},
            )
        # When the middleware is not registered, FastAPI does not emit any
        # Access-Control-Allow-Origin header on the response.
        assert "access-control-allow-origin" not in {k.lower() for k in r.headers}

    def test_cors_enabled_allows_whitelisted_origin(
        self,
        register_recording_backend: None,
        isolated_ledger_path: Path,
        tmp_path: Path,
    ) -> None:
        cfg = MaxwellDaemonConfig.model_validate(
            {
                "backends": {"primary": {"type": "recording", "model": "test-model"}},
                "agent": {"default_backend": "primary"},
                "api": {
                    "cors": {
                        "enabled": True,
                        "allowed_origins": ["https://dashboard.example.com"],
                    }
                },
            }
        )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        d = Daemon(
            cfg,
            ledger_path=isolated_ledger_path,
            task_store_path=tmp_path / "tasks.db",
            work_item_store_path=tmp_path / "work_items.db",
            task_graph_store_path=tmp_path / "task_graphs.db",
            artifact_store_path=tmp_path / "artifacts.db",
            artifact_blob_root=tmp_path / "artifacts",
            action_store_path=tmp_path / "actions.db",
            delegate_lifecycle_store_path=tmp_path / "delegate_sessions.db",
        )
        loop.run_until_complete(d.start(worker_count=1))
        try:
            with TestClient(create_app(d)) as client:
                r_allowed = client.get(
                    "/health",
                    headers={"Origin": "https://dashboard.example.com"},
                )
                r_blocked = client.get(
                    "/health",
                    headers={"Origin": "https://evil.example.com"},
                )
        finally:
            loop.run_until_complete(d.stop())
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            asyncio.set_event_loop(None)

        # Whitelisted origin is echoed back.
        assert (
            r_allowed.headers.get("access-control-allow-origin") == "https://dashboard.example.com"
        )
        # Non-whitelisted origin gets no CORS header (browser will block it).
        assert "access-control-allow-origin" not in {k.lower() for k in r_blocked.headers}

"""Security-headers middleware for the FastAPI app.

Adds a minimal, conservative set of HTTP response headers that mitigate the
most common browser-side attacks (MIME sniffing, click-jacking, mixed-content
downgrades, referrer leakage) without altering the wire contract consumed by
``runner-dashboard``.

The middleware is a Starlette :class:`BaseHTTPMiddleware` so it composes with
the existing correlation, rate-limit, and request-id middlewares already
registered in :func:`maxwell_daemon.api.server.create_app`.

Phase-1 scope (issue #797): headers only.  Cookie hardening, WebSocket auth,
and CSP enforcement are deferred follow-ups and are intentionally **not**
wired up here.

Usage — register once in ``create_app``::

    from maxwell_daemon.api.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)

CSP is opt-in (default ``False``) so the dashboard's vanilla-JS UI cannot
accidentally regress when this middleware ships.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from maxwell_daemon.logging import get_logger

__all__ = [
    "DEFAULT_CSP",
    "SecurityHeadersMiddleware",
]

log = get_logger(__name__)

# A conservative default Content-Security-Policy used **only** when callers
# explicitly opt in via ``enable_csp=True``.  The dashboard UI inlines styles
# and scripts in some places so a stricter policy would break it; tightening
# the policy is tracked as a follow-up to issue #797.
DEFAULT_CSP: str = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defensive HTTP response headers to every response.

    Headers applied unconditionally:

    * ``X-Content-Type-Options: nosniff`` — disable MIME sniffing.
    * ``X-Frame-Options: SAMEORIGIN`` — block click-jacking via foreign frames.
    * ``Referrer-Policy: strict-origin-when-cross-origin`` — limit referrer
      leakage on cross-origin navigations.
    * ``X-XSS-Protection: 0`` — explicitly disable the legacy reflected-XSS
      auditor (modern advice; the auditor itself is exploitable).

    Conditional headers:

    * ``Strict-Transport-Security`` is only emitted when the inbound request
      was served over HTTPS — i.e. ``request.url.scheme == "https"``.  Sending
      HSTS on a plain-HTTP response is a no-op for browsers, but emitting it
      only on the secure scheme keeps local-loopback dev flows uncluttered.
    * ``Content-Security-Policy`` is opt-in via ``enable_csp=True``.  When
      enabled the policy in :data:`DEFAULT_CSP` (or a caller-supplied
      override) is set.  Off by default to avoid breaking the dashboard UI.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enable_csp: bool = False,
        csp_policy: str | None = None,
    ) -> None:
        super().__init__(app)
        self._enable_csp = enable_csp
        self._csp_policy = csp_policy if csp_policy is not None else DEFAULT_CSP

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response: Response = await call_next(request)

        # ``setdefault`` semantics: do not clobber a header an upstream
        # endpoint or middleware has deliberately set.  Frameworks and
        # specific routes occasionally need a different X-Frame-Options
        # (e.g. embedding a widget) and we should defer to them.
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-XSS-Protection", "0")

        if request.url.scheme == "https":
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        if self._enable_csp:
            headers.setdefault("Content-Security-Policy", self._csp_policy)

        return response

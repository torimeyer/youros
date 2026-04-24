"""Security response headers middleware.

Adds a baseline set of security headers to every response coming out of
the FastAPI app. Scoped to be safe for a localhost-first dev setup:
* HSTS is only emitted over real HTTPS requests.
* CSP relaxes in dev (Vite HMR) when `MYOS_ENV` != `production` so the
  browser can load the dev server's eval-based hot reload bundle.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Frontend CSP used when we are serving the built bundle. The `connect-src`
# entry allows wss: and https: so chat WebSockets and any LLM provider
# proxy calls work. If we ever stop proxying third-party APIs through the
# backend we can tighten this.
_PROD_CSP = (
    "default-src 'self'; "
    "connect-src 'self' ws: wss: https:; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'"
)

# Dev CSP: Vite's HMR runtime needs `unsafe-eval` for fast refresh and
# loads the dev server on a separate origin. We also allow inline styles
# since Vite injects them for HMR.
_DEV_CSP = (
    "default-src 'self'; "
    "connect-src 'self' ws: wss: http: https:; "
    "img-src 'self' data: blob: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
)


def _csp_for_env() -> str:
    if os.environ.get("MYOS_ENV", "").lower() == "production":
        return _PROD_CSP
    return _DEV_CSP


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a conservative set of security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        headers = response.headers

        # Clickjacking and MIME sniffing
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        headers.setdefault("Content-Security-Policy", _csp_for_env())

        # Only emit HSTS on real HTTPS requests so the browser never
        # locks onto the http:// dev URL for a year.
        scheme = request.url.scheme
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        is_https = scheme == "https" or forwarded_proto == "https"
        if is_https:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response

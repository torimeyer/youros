"""Loopback-bind guard middleware.

Defense in depth for gap G5 of `.ostk/plans/security-privacy-review.md`.

Today the backend binds to `127.0.0.1` only, so every unauthenticated
`/api/*` route is safe-by-posture. The moment someone runs uvicorn with
`HOST=0.0.0.0` (cloud, container, tunneled dev env) every endpoint is
open to the internet with no auth. This middleware rejects any request
whose client is not on a loopback address, unless the operator has
explicitly set ``ALLOW_NON_LOOPBACK=1`` to opt in.

Exceptions:
* ``/api/health`` is always allowed so load balancers / probes work.
* ``ALLOW_NON_LOOPBACK`` truthy value (``1``, ``true``, ``yes``) opts
  out of the check entirely.

# TODO: when auth is added, let valid auth bypass this check.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_TRUTHY = {"1", "true", "yes", "on"}
_HEALTH_PATHS = {"/api/health"}


def _is_truthy(val: str | None) -> bool:
    return bool(val) and val.strip().lower() in _TRUTHY


def _is_loopback_client(request: Request) -> bool:
    client = request.client
    if client is None:
        # No client info (in-process ASGI test transport, lifespan).
        # Treat as loopback: ASGITransport does not set a remote peer.
        return True
    host = (client.host or "").strip()
    if not host:
        return True
    if host in _LOOPBACK_HOSTS:
        return True
    # IPv4-mapped loopback (e.g. ::ffff:127.0.0.1).
    if host.startswith("::ffff:127."):
        return True
    return False


class LoopbackGuardMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback requests unless explicitly allowed."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Health endpoint is always reachable for probes.
        if request.url.path in _HEALTH_PATHS:
            return await call_next(request)

        # Explicit opt-out for intentional non-loopback exposure.
        if _is_truthy(os.environ.get("ALLOW_NON_LOOPBACK")):
            return await call_next(request)

        # TODO: when auth is added, let valid auth bypass this check.

        if _is_loopback_client(request):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "error": (
                    "non-loopback requests require ALLOW_NON_LOOPBACK=1 "
                    "or an auth token"
                )
            },
        )

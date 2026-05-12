"""SlowCallMiddleware: log requests that exceed the latency threshold.

Keeps a fixed-size in-memory ring of the most recent slow calls and
exposes them via get_slow_calls() so the /api/internal/slow-calls
router can return them.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_THRESHOLD_MS: float = 500.0
_RING_SIZE: int = 64

_ring: deque[dict[str, Any]] = deque(maxlen=_RING_SIZE)


def get_slow_calls() -> list[dict[str, Any]]:
    return list(_ring)


def clear_slow_calls() -> None:
    """Reset the ring; intended for tests."""
    _ring.clear()


class SlowCallMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that records requests slower than _THRESHOLD_MS."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        started_at = time.time()
        response = await call_next(request)
        latency_ms = (time.time() - started_at) * 1000.0

        if latency_ms >= _THRESHOLD_MS:
            method = request.method
            path = request.url.path
            logger.warning(
                "slow request %s %s %.0fms", method, path, latency_ms
            )
            _ring.append(
                {
                    "method": method,
                    "path": path,
                    "latency_ms": round(latency_ms, 1),
                    "started_at": started_at,
                }
            )

        return response

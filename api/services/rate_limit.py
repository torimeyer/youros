"""Lightweight in-memory per-IP rate limiting.

We run locally so a full backend like redis/memcached is overkill. This
module keeps a ring buffer of recent request timestamps per (ip, route
bucket) key and rejects the N+1th request in a sliding one-minute window.

Scope:
* Spawn-heavy routes ("agents.spawn") are limited to 20/min.
* Chat send paths ("chat.send") are limited to 60/min.
* Catch-all GET polling ("api.get") is limited to 300/min.

Call ``rate_limit_check(request, bucket)`` from a router. Tests can reset
state via ``_reset_all()``.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request

# bucket name -> (max requests, window seconds)
LIMITS: Dict[str, Tuple[int, float]] = {
    "agents.spawn": (20, 60.0),
    "chat.send": (60, 60.0),
    "api.get": (300, 60.0),
}

_buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _reset_all() -> None:
    """Test helper. Wipes all in-memory counters."""
    _buckets.clear()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def rate_limit_check(request: Request, bucket: str) -> None:
    """Raise HTTP 429 if the caller exceeded ``bucket``'s limit.

    Bypass when MYOS_DISABLE_RATE_LIMIT=1 (used by tests that do not
    exercise the limiter specifically).
    """
    if os.environ.get("MYOS_DISABLE_RATE_LIMIT") == "1":
        return

    limit = LIMITS.get(bucket)
    if limit is None:
        return
    max_n, window = limit

    ip = _client_ip(request)
    key = (ip, bucket)
    now = time.monotonic()
    cutoff = now - window

    dq = _buckets[key]
    while dq and dq[0] < cutoff:
        dq.popleft()

    if len(dq) >= max_n:
        retry = int(window - (now - dq[0])) + 1
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "bucket": bucket,
                "limit": max_n,
                "window_seconds": int(window),
                "retry_after_seconds": max(retry, 1),
                "message": (
                    f"Too many requests on {bucket}. "
                    f"Limit is {max_n} per {int(window)} seconds."
                ),
            },
            headers={"Retry-After": str(max(retry, 1))},
        )

    dq.append(now)

"""Tiny in-memory TTL cache for connection-status endpoints.

Each of the four connection-status endpoints (Gmail, Calendar, Drive, Slack)
does cheap work: a file existence check plus a JSON parse of the token file.
Cheap is not free. On a cold tab switch it still costs a disk read plus JSON
decode per endpoint, which adds up when a page header wants four dots to
appear at once.

This module caches the resolved status dict in process memory for a short
TTL. The TTL is short on purpose. Connection state rarely changes, but when
it does (connect, disconnect, reauth) we want the UI to reflect that right
away. The OAuth callback and disconnect handlers call `invalidate` to drop
the stale entry.

Thread-safety: the cache is a plain dict guarded by a Lock. FastAPI runs
request handlers in a thread pool when they are sync and in a shared event
loop when they are async, so we protect the dict with a lock to be safe.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

# Default TTL in seconds. A fresh browser tab rarely needs a status value
# newer than 60 seconds because connection state is durable, and any
# connect/disconnect flow invalidates the cache explicitly.
DEFAULT_TTL_SECONDS = 60.0

_lock = threading.Lock()
_store: dict[str, tuple[float, dict[str, Any]]] = {}


def get(key: str, ttl: float = DEFAULT_TTL_SECONDS) -> dict[str, Any] | None:
    """Return the cached value for ``key`` if still fresh, else None."""
    with _lock:
        entry = _store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            _store.pop(key, None)
            return None
        return value


def set(key: str, value: dict[str, Any], ttl: float = DEFAULT_TTL_SECONDS) -> None:
    """Store ``value`` under ``key`` for up to ``ttl`` seconds."""
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def invalidate(key: str) -> None:
    """Drop the cached value for ``key``. No-op if not present."""
    with _lock:
        _store.pop(key, None)


def invalidate_all() -> None:
    """Drop every cached value. Used by tests and by broad state changes."""
    with _lock:
        _store.clear()


def get_or_compute(
    key: str,
    compute: Callable[[], dict[str, Any]],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Return cached value or run ``compute`` and cache the result.

    Kept as a helper so endpoint handlers stay one-liner readable.
    """
    cached = get(key, ttl=ttl)
    if cached is not None:
        return cached
    fresh = compute()
    set(key, fresh, ttl=ttl)
    return fresh

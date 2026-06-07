"""Spawn-burst token bucket for /api/agents/spawn.

Allows MYOS_SPAWN_BURST_LIMIT (default 3) spawns in any 30-second sliding
window. Excess spawns sleep until a slot opens. After MYOS_SPAWN_MAX_WAIT_S
(default 90s), the waiter gets HTTP 429 with Retry-After.

Logging: throttle events are appended to ~/.youros/logs/spawn_throttle.log.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_BURST_LIMIT = int(os.environ.get("MYOS_SPAWN_BURST_LIMIT", "3"))
_WINDOW_S = 30.0
_MAX_WAIT_S = float(os.environ.get("MYOS_SPAWN_MAX_WAIT_S", "90"))


def _append_throttle_log(msg: str) -> None:
    try:
        log_dir = Path.home() / ".youros" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(log_dir / "spawn_throttle.log", "a") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


class SpawnBurstThrottle:
    """Async-safe sliding-window token bucket.

    Allows burst_limit spawns in any window_s second window. Excess callers
    sleep until a slot opens. After max_wait_s total wait, raises HTTP 429.
    """

    def __init__(
        self,
        burst_limit: int = _BURST_LIMIT,
        window_s: float = _WINDOW_S,
        max_wait_s: float = _MAX_WAIT_S,
    ) -> None:
        self._burst_limit = burst_limit
        self._window_s = window_s
        self._max_wait_s = max_wait_s
        self._lock = asyncio.Lock()
        self._timestamps: Deque[float] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    async def acquire(self, agent_name: str = "") -> float:
        """Wait for a spawn slot. Returns seconds waited (0.0 = immediate).

        Raises HTTPException(429) when the wait budget is exhausted.
        """
        wait_start = time.monotonic()

        while True:
            async with self._lock:
                now = time.monotonic()
                self._prune(now)
                if len(self._timestamps) < self._burst_limit:
                    self._timestamps.append(now)
                    return time.monotonic() - wait_start

                sleep_for = self._timestamps[0] + self._window_s - now

            elapsed = time.monotonic() - wait_start
            if elapsed + max(sleep_for, 0.0) > self._max_wait_s:
                retry_after = int(self._window_s) + 1
                _append_throttle_log(
                    f"THROTTLE_REJECT name={agent_name} "
                    f"waited={elapsed:.1f}s limit={self._burst_limit}/{self._window_s:.0f}s"
                )
                logger.warning(
                    "spawn.throttle.reject name=%s waited=%.1fs", agent_name, elapsed
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "spawn_burst_throttled",
                        "message": (
                            f"Too many spawns in a {self._window_s:.0f}s burst. "
                            f"Limit is {self._burst_limit} per {self._window_s:.0f}s window. "
                            f"Retry in {retry_after}s."
                        ),
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            sleep_for = max(sleep_for, 0.05)
            _append_throttle_log(
                f"THROTTLE_WAIT name={agent_name} sleep={sleep_for:.1f}s "
                f"elapsed={elapsed:.1f}s limit={self._burst_limit}/{self._window_s:.0f}s"
            )
            logger.info(
                "spawn.throttle.wait name=%s sleep=%.1fs elapsed=%.1fs",
                agent_name,
                sleep_for,
                elapsed,
            )
            await asyncio.sleep(sleep_for)


_throttle = SpawnBurstThrottle()


async def acquire_spawn_slot(agent_name: str = "") -> float:
    """Acquire a spawn slot from the global throttle. Returns seconds waited."""
    return await _throttle.acquire(agent_name)


def reset_for_testing() -> None:
    """Reset global throttle state. For tests only."""
    global _throttle
    _throttle = SpawnBurstThrottle()

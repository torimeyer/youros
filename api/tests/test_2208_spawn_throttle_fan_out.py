"""→2208/→2209 — Spawn burst throttle: measured fan-out behavior, leave-as-is.

DECISION (2026-06-14): LEAVE AS-IS at default limit=3 per 30s window.

Measured fan-out (SpawnBurstThrottle with a 0.25s mini-window; behavior
scales identically to the real 30s window — only the unit changes):

  N= 1  immediate=1  throttled=0  wait_max≈0.000s
  N= 3  immediate=3  throttled=0  wait_max≈0.000s
  N= 4  immediate=3  throttled=1  wait_max≈0.250s  (1 window)
  N= 6  immediate=3  throttled=3  wait_max≈0.250s  (1 window)
  N= 8  immediate=3  throttled=5  wait_max≈0.500s  (2 windows)

Real-scale projection (limit=3, window=30s):
  Agents 1-3:   0s wait (immediate)
  Agents 4-6:  ~30s wait (one window)
  Agents 7-9:  ~60s wait (two windows)
  Agents 10+:  ~90s → HTTP 429 (max_wait exhausted)

Why leave the limit at 3:
  1. Production throttle log NEVER created — zero THROTTLE_WAIT or
     THROTTLE_REJECT events in the entire history of the system.
  2. This throttle IS needle →1544 (shipped 2026-05-20, commit 1dc57124).
     It was added after a 6-agent simultaneous burst overloaded the event
     loop and all six agents abandoned with <600 bytes. limit=3 forces a
     large fan-out to spread as 3-per-30s — that spreading is the protection.
  3. Raising to 5 allows 5 simultaneous spawns — one short of the 6-agent
     burst that caused the original meltdown — for zero measured benefit.
  4. The manual 2-3 burst rule (feedback_smaller_spawn_waves.md) is now
     redundant with the throttle and can be relaxed; the throttle stays at 3.

These tests lock the chosen behavior so the decision is not re-litigated.
"""
from __future__ import annotations

import asyncio

import pytest

from services.spawn_throttle import SpawnBurstThrottle


# ── helpers ────────────────────────────────────────────────────────────────


async def _burst(n: int, limit: int, window_s: float, max_wait_s: float = 5.0):
    """Simulate N concurrent spawns; return sorted (index, wait_s) pairs."""
    throttle = SpawnBurstThrottle(
        burst_limit=limit, window_s=window_s, max_wait_s=max_wait_s
    )
    results: list[tuple[int, float]] = []

    async def _one(idx: int) -> None:
        waited = await throttle.acquire(f"test-agent-{idx}")
        results.append((idx, waited))

    await asyncio.gather(*[asyncio.create_task(_one(i)) for i in range(n)])
    return sorted(results)


# ── behavior tests (0.25s window — scales 1:120 to real 30s) ───────────────

_W = 0.25  # mini window — tests complete in <2s total
_L = 3     # mirrors _BURST_LIMIT default


@pytest.mark.asyncio
async def test_burst_within_limit_is_immediate():
    """First _BURST_LIMIT spawns never wait."""
    results = await _burst(n=_L, limit=_L, window_s=_W)
    for idx, waited in results:
        assert waited < 0.05, f"agent {idx} should be immediate, got {waited:.3f}s"


@pytest.mark.asyncio
async def test_4th_spawn_waits_one_window():
    """The (limit+1)th spawn waits approximately one full window."""
    results = await _burst(n=_L + 1, limit=_L, window_s=_W)
    waits = [w for _, w in results]
    n_immediate = sum(1 for w in waits if w < 0.05)
    n_throttled = sum(1 for w in waits if w >= 0.05)
    assert n_immediate == _L, f"expected {_L} immediate, got {n_immediate}"
    assert n_throttled == 1, f"expected 1 throttled, got {n_throttled}"
    max_wait = max(waits)
    assert _W * 0.8 <= max_wait <= _W * 3, (
        f"throttled agent should wait ~1 window ({_W}s), got {max_wait:.3f}s"
    )


@pytest.mark.asyncio
async def test_burst_of_6_waits_one_window_not_two():
    """Agents 4–6 all wait ~1 window, not serialized one-by-one."""
    results = await _burst(n=6, limit=_L, window_s=_W)
    waits = [w for _, w in results]
    n_immediate = sum(1 for w in waits if w < 0.05)
    n_throttled = sum(1 for w in waits if w >= 0.05)
    assert n_immediate == _L
    assert n_throttled == 3
    max_wait = max(waits)
    # All 3 excess agents clear in one window (they wake up together).
    # Allow up to 2× window for scheduler jitter on CI.
    assert max_wait <= _W * 2.5, (
        f"6-agent burst should clear in ≤1 window; max_wait={max_wait:.3f}s"
    )


@pytest.mark.asyncio
async def test_burst_of_8_waits_two_windows():
    """Agents 7–8 fall into window 2 (~2× window wait)."""
    results = await _burst(n=8, limit=_L, window_s=_W)
    waits = [w for _, w in results]
    max_wait = max(waits)
    # With limit=3 and 8 agents, windows 1+2 clear 6; agents 7-8 need window 3.
    assert max_wait >= _W * 1.5, (
        f"expected ≥2 windows for 8-agent burst, got max_wait={max_wait:.3f}s"
    )


@pytest.mark.asyncio
async def test_429_when_max_wait_exhausted():
    """A sustained stream beyond max_wait raises HTTP 429."""
    from fastapi import HTTPException

    # 10 concurrent spawns, tiny max_wait so the later ones hit the budget.
    throttle = SpawnBurstThrottle(burst_limit=_L, window_s=_W, max_wait_s=_W * 1.5)
    statuses: list[int] = []

    async def _one(idx: int) -> None:
        try:
            await throttle.acquire(f"test-{idx}")
            statuses.append(200)
        except HTTPException as exc:
            statuses.append(exc.status_code)

    await asyncio.gather(*[asyncio.create_task(_one(i)) for i in range(10)])
    assert 429 in statuses, "should see at least one 429 when max_wait exhausted"

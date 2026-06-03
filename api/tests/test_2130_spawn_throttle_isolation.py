"""→2130 regression: spawn_throttle must reset between tests.

Bug: ``services.spawn_throttle._throttle`` is a module-level singleton with
an ``asyncio.Lock`` bound to the first event loop that touches it, plus a
sliding-window deque of timestamps that accumulates across tests.

Without an autouse fixture calling ``reset_for_testing()``:
  - After 3 spawns within a 30s window (the burst limit), the 4th spawn
    blocks inside the throttle for up to MYOS_SPAWN_MAX_WAIT_S (default 90s).
  - With pytest-timeout's signal method ineffective for async hangs, the
    full backend suite wedges 10+ minutes and never completes.

These tests pin the contract: each test sees a fresh throttle, the
``time.monotonic()`` deque is empty, and acquiring a slot is immediate.
"""
from __future__ import annotations

import asyncio

import pytest

from services import spawn_throttle


@pytest.mark.asyncio
async def test_acquire_slot_is_immediate_with_empty_throttle():
    # Fixture (added in same change) must reset between tests.
    waited = await spawn_throttle.acquire_spawn_slot("regression-2130-a")
    assert waited < 0.5, f"first acquire must be immediate, waited={waited}s"


@pytest.mark.asyncio
async def test_throttle_state_does_not_leak_into_next_test():
    # Fill the bucket up to its limit so deque has _BURST_LIMIT entries.
    for i in range(spawn_throttle._BURST_LIMIT):
        waited = await spawn_throttle.acquire_spawn_slot(f"regression-2130-fill-{i}")
        assert waited < 0.5
    # Sanity: deque is full.
    assert len(spawn_throttle._throttle._timestamps) == spawn_throttle._BURST_LIMIT


@pytest.mark.asyncio
async def test_throttle_is_fresh_after_previous_test_filled_it():
    # If the autouse reset fixture is missing, the previous test left the
    # deque full and the lock bound to a dead event loop, so this acquire
    # would hang until pytest-timeout SIGALRMs the process.
    waited = await asyncio.wait_for(
        spawn_throttle.acquire_spawn_slot("regression-2130-fresh"),
        timeout=2.0,
    )
    assert waited < 0.5, f"new test must see a fresh throttle, waited={waited}s"
    # The deque starts empty for each test; the line above appended one entry.
    assert len(spawn_throttle._throttle._timestamps) == 1

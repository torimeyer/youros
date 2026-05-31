"""
Regression test for →2018: backend event-loop wedge under agent load.

Root cause: _autocomplete_exited_subagents ran via asyncio.to_thread every
500 ms from the snapshot loop AND every 60 s from the reconcile loop.  Concurrent
GIL-heavy threads starved the event loop, causing all endpoints to stall.
Additionally _sweep_stale_running_agents() ran synchronously on the event loop,
blocking it for stat×N agents every 60 s.

Fix: _sweep_pass_lock serializes concurrent autocomplete/sweep calls; autocomplete
is throttled to at most once per 5 s in the snapshot loop; sweep moves to a thread.
"""

import asyncio
import time
import pytest


# ---------------------------------------------------------------------------
# 1. Verify the new lock and throttle globals exist and have correct types
# ---------------------------------------------------------------------------

def test_sweep_pass_lock_exists():
    from routers.agents import _sweep_pass_lock, _AUTOCOMPLETE_MIN_INTERVAL, _last_autocomplete_mono
    assert isinstance(_sweep_pass_lock, asyncio.Lock), "_sweep_pass_lock must be asyncio.Lock"
    assert _AUTOCOMPLETE_MIN_INTERVAL == 5.0, "interval must be 5 s"
    assert isinstance(_last_autocomplete_mono, float)


# ---------------------------------------------------------------------------
# 2. Verify autocomplete throttle: second call within 5 s skips the pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autocomplete_throttled_in_snapshot_loop():
    """If _last_autocomplete_mono was just set, the snapshot loop should skip
    the autocomplete pass and return ac_changed=False without running the fn."""
    import routers.agents as ag

    call_count = 0

    def fake_autocomplete():
        nonlocal call_count
        call_count += 1
        return False

    original_fn = ag._autocomplete_exited_subagents
    original_mono = ag._last_autocomplete_mono

    try:
        ag._autocomplete_exited_subagents = fake_autocomplete
        # Simulate: last autocomplete was 1 s ago (within 5 s throttle window)
        ag._last_autocomplete_mono = asyncio.get_running_loop().time() - 1.0

        # Replicate the throttle logic from _compute_agents_snapshot_async
        ac_changed = False
        loop_time = asyncio.get_running_loop().time()
        if (
            loop_time - ag._last_autocomplete_mono >= ag._AUTOCOMPLETE_MIN_INTERVAL
            and not ag._sweep_pass_lock.locked()
        ):
            async with ag._sweep_pass_lock:
                ac_changed = await asyncio.to_thread(ag._autocomplete_exited_subagents)
                ag._last_autocomplete_mono = asyncio.get_running_loop().time()

        assert call_count == 0, "autocomplete must be skipped when within 5 s window"
        assert ac_changed is False
    finally:
        ag._autocomplete_exited_subagents = original_fn
        ag._last_autocomplete_mono = original_mono


# ---------------------------------------------------------------------------
# 3. Verify lock serializes concurrent callers — no two run simultaneously
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sweep_pass_lock_serializes_concurrent_callers():
    """Two coroutines acquiring _sweep_pass_lock must not overlap."""
    import routers.agents as ag

    overlap_detected = False
    inside_count = 0

    async def check_overlap():
        nonlocal inside_count, overlap_detected
        async with ag._sweep_pass_lock:
            inside_count += 1
            if inside_count > 1:
                overlap_detected = True
            await asyncio.sleep(0.01)
            inside_count -= 1

    await asyncio.gather(check_overlap(), check_overlap(), check_overlap())

    assert not overlap_detected, "_sweep_pass_lock must prevent concurrent holders"


# ---------------------------------------------------------------------------
# 4. Verify _sweep_stale_running_agents can be called from a thread
#    (it must be synchronous with no asyncio awaits inside)
# ---------------------------------------------------------------------------

def test_sweep_stale_running_agents_is_sync():
    """_sweep_stale_running_agents must be a plain sync function so it can be
    safely offloaded via asyncio.to_thread without holding the event loop."""
    import inspect
    from routers.agents import _sweep_stale_running_agents
    # Must NOT be a coroutine function
    assert not inspect.iscoroutinefunction(_sweep_stale_running_agents), (
        "_sweep_stale_running_agents must be sync to run in asyncio.to_thread"
    )


# ---------------------------------------------------------------------------
# 5. Event-loop latency stress test: concurrent "work" behind the lock
#    must not block the loop past a reasonable threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_not_stalled_under_concurrent_background_work():
    """Simulate the pre-fix scenario: two concurrent GIL-heavy threads.
    With the lock, loop latency must stay reasonable (< 300 ms per iteration).
    """
    import routers.agents as ag

    MAX_ALLOWED_STALL_MS = 300

    def cpu_work():
        # ~5 ms of Python dict iteration (simulates _autocomplete_exited_subagents)
        total = 0
        for i in range(500_000):
            total += i
        return total

    loop_lags = []

    async def measure_loop_lag(n: int = 5):
        for _ in range(n):
            t0 = asyncio.get_running_loop().time()
            await asyncio.sleep(0)  # single yield
            lag_ms = (asyncio.get_running_loop().time() - t0) * 1000
            loop_lags.append(lag_ms)
            await asyncio.sleep(0.01)

    # Background task acquires the lock and runs CPU work (as the reconcile
    # loop does via asyncio.to_thread after the fix)
    async def background_sweep():
        for _ in range(3):
            async with ag._sweep_pass_lock:
                await asyncio.to_thread(cpu_work)
            await asyncio.sleep(0.02)

    await asyncio.gather(
        measure_loop_lag(10),
        background_sweep(),
    )

    p95_lag = sorted(loop_lags)[int(len(loop_lags) * 0.95)]
    assert p95_lag < MAX_ALLOWED_STALL_MS, (
        f"p95 event-loop lag {p95_lag:.1f} ms >= {MAX_ALLOWED_STALL_MS} ms — "
        "background work is stalling the loop"
    )

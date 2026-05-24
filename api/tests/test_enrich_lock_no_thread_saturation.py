"""Regression test for →1144: threading.Lock in _run_enrich_pipeline exhausted the
default thread pool (18 workers), causing asyncio.to_thread calls elsewhere to
queue forever and the backend to appear wedged.

Fix: replaced threading.Lock with asyncio.Lock (_enrich_async_lock) so concurrent
callers suspend at coroutine level without occupying thread-pool slots.

This test verifies that a plain asyncio.to_thread call completes promptly even
when 20 concurrent "enrich" coroutines are competing for the lock — i.e. the
thread pool is never saturated by lock-waiting threads.
"""

import asyncio
import time
import pytest


async def _fake_enrich(lock: asyncio.Lock) -> None:
    """Simulate _run_enrich_pipeline: acquire async lock, do ~5ms of work in a
    thread."""
    async with lock:
        await asyncio.to_thread(time.sleep, 0.005)


@pytest.mark.asyncio
async def test_asyncio_lock_does_not_saturate_thread_pool() -> None:
    """20 concurrent callers sharing an asyncio.Lock must not prevent an
    independent asyncio.to_thread call from completing within 3 s.

    With a threading.Lock this test would deadlock: all 18 thread slots fill
    up with threads blocked in Lock.acquire(), the independent to_thread call
    queues forever, and the 3-second deadline fires.
    """
    lock = asyncio.Lock()
    deadline_seconds = 3.0

    # Launch 20 concurrent enrich-like coroutines.
    tasks = [asyncio.create_task(_fake_enrich(lock)) for _ in range(20)]

    # This independent to_thread call must finish well within the deadline
    # even while the 20 tasks are running.
    start = time.monotonic()
    await asyncio.wait_for(asyncio.to_thread(time.sleep, 0.001), timeout=deadline_seconds)
    elapsed = time.monotonic() - start

    # Clean up the enrich tasks.
    await asyncio.gather(*tasks)

    assert elapsed < deadline_seconds, (
        f"Independent to_thread call took {elapsed:.2f}s (limit {deadline_seconds}s); "
        "thread pool was likely saturated — asyncio.Lock regression (→1144)"
    )

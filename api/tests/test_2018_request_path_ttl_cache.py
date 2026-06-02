"""→2018: request-path starvation regression test.

Background
----------
Hot read-only dashboard endpoints (/api/tasks/counts, /api/specs/counts,
/api/agents, ...) all funnel into a small set of ostk service primitives.
The worst offender is ``OstkService.list_tasks``: it spawns an ostk call
and parses the result, and several endpoints invoke it back-to-back (not
at the exact same instant) every few seconds as the dashboard polls. With
only in-flight coalescing, each serial poller re-ran that heavy work, and
when many landed together the GIL-heavy parse starved the event loop until
requests timed out (HTTP 000). Restarting uvicorn only cleared it
temporarily.

Fix under test
--------------
``ostk._ttl_cached_call`` adds a short-TTL (default 2s) result cache on top
of the existing coalescing, so concurrent AND closely-serial pollers share
ONE computed result. Writes (``ostk work add/close/...``) invalidate it so
counts stay correct.

These tests assert the heavy producer runs at most once per window under a
concurrent burst, and that a write busts the cache. They exercise the real
asyncio machinery (no live server needed), so they stay fast and
deterministic.
"""

import asyncio
import time

import pytest

import services.ostk as ostk_mod
from services.ostk import (
    _ttl_cached_call,
    invalidate_ttl_cache,
    _maybe_invalidate_after_write,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    # Each test starts from a clean cache + no in-flight futures.
    invalidate_ttl_cache()
    ostk_mod._inflight_calls.clear()
    yield
    invalidate_ttl_cache()
    ostk_mod._inflight_calls.clear()


@pytest.mark.asyncio
async def test_concurrent_burst_collapses_to_one_producer():
    """20 concurrent callers within the TTL window must trigger exactly
    ONE underlying (heavy) computation, not 20. This is the property that
    stops the dashboard's concurrent pollers from each spawning GIL-heavy
    work and wedging the loop."""
    calls = {"n": 0}

    async def heavy_producer():
        calls["n"] += 1
        # Simulate the cost of the real ostk subprocess + JSON parse so a
        # burst genuinely overlaps in time.
        await asyncio.sleep(0.05)
        return ["needle-a", "needle-b", "needle-c"]

    key = ("list_tasks", "/repo", ("work", "list", "--json", "--status", "open"))

    results = await asyncio.gather(
        *[_ttl_cached_call(key, heavy_producer, ttl=2.0) for _ in range(20)]
    )

    assert calls["n"] == 1, f"expected 1 heavy computation, got {calls['n']}"
    # Every caller got the same correct result.
    for r in results:
        assert r == ["needle-a", "needle-b", "needle-c"]


@pytest.mark.asyncio
async def test_serial_pollers_within_window_share_cache():
    """Serial callers a few ms apart (the real dashboard pattern) hit the
    cache and do NOT re-run the producer until the TTL expires."""
    calls = {"n": 0}

    async def producer():
        calls["n"] += 1
        return calls["n"]

    key = ("list_tasks", "/repo", ("work", "list", "--json"))

    first = await _ttl_cached_call(key, producer, ttl=0.3)
    # Several serial reads inside the window.
    for _ in range(5):
        await asyncio.sleep(0.01)
        again = await _ttl_cached_call(key, producer, ttl=0.3)
        assert again == first
    assert calls["n"] == 1, "cache should serve serial reads without recompute"

    # After the window expires, the next read recomputes once.
    await asyncio.sleep(0.35)
    await _ttl_cached_call(key, producer, ttl=0.3)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_write_invalidates_cache():
    """A task write must bust the cached list so the next read recomputes
    (so the sidebar count is correct right after add/close)."""
    calls = {"n": 0}

    async def producer():
        calls["n"] += 1
        return calls["n"]

    key = ("list_tasks", "/repo", ("work", "list", "--json"))

    await _ttl_cached_call(key, producer, ttl=5.0)
    await _ttl_cached_call(key, producer, ttl=5.0)
    assert calls["n"] == 1  # cached

    # Simulate a successful `ostk work add ...` running through _run.
    _maybe_invalidate_after_write(("work", "add", "New task"))

    await _ttl_cached_call(key, producer, ttl=5.0)
    assert calls["n"] == 2, "write should have invalidated the cache"


@pytest.mark.asyncio
async def test_read_verbs_do_not_invalidate():
    """A read-only verb (work list) must NOT invalidate the cache."""
    calls = {"n": 0}

    async def producer():
        calls["n"] += 1
        return calls["n"]

    key = ("list_tasks", "/repo", ("work", "list", "--json"))
    await _ttl_cached_call(key, producer, ttl=5.0)
    _maybe_invalidate_after_write(("work", "list", "--json"))
    await _ttl_cached_call(key, producer, ttl=5.0)
    assert calls["n"] == 1, "read verb must not bust the cache"


@pytest.mark.asyncio
async def test_concurrent_burst_does_not_serialize_latency():
    """The whole point: a burst of N concurrent pollers must finish in
    roughly ONE producer-time, not N x producer-time. If the cache/coalesce
    regressed into per-caller work, total wall time would blow up and the
    loop would starve. We assert a generous bound that still catches the
    O(N) regression."""
    producer_time = 0.05
    n = 30

    async def heavy_producer():
        await asyncio.sleep(producer_time)
        return ["x"]

    key = ("list_tasks", "/repo", ("work", "list", "--json"))

    start = time.monotonic()
    await asyncio.gather(
        *[_ttl_cached_call(key, heavy_producer, ttl=2.0) for _ in range(n)]
    )
    elapsed = time.monotonic() - start

    # One producer run is ~0.05s. Serializing 30 would be ~1.5s. Anything
    # under ~5x a single run proves the work collapsed.
    assert elapsed < producer_time * 5, (
        f"burst took {elapsed:.3f}s; expected ~{producer_time:.3f}s "
        f"(work did not collapse — starvation regression)"
    )

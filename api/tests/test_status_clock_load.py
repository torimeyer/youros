"""Load test: /api/status/clock p99 latency under concurrent calls.

Guards against regression of the kernel-name cache (→1233).
If this test fails, the cache layer is not returning synchronously
and the health probe is back to shelling out per request.
"""

import asyncio
import time

import pytest
import services.ostk as ostk_mod


@pytest.mark.asyncio
async def test_status_clock_p99_under_50_concurrent_calls(client):
    seeded = {
        "kernel": "v-test",
        "session": "0s",
        "wall": "",
        "audit": "0 events",
        "swap": "unknown",
        "focus": "",
    }
    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.update(seeded)

    try:
        async def one_call() -> float:
            t0 = time.perf_counter()
            r = await client.get("/api/status/clock")
            assert r.status_code == 200
            assert r.json()["kernel"] == "v-test"
            return time.perf_counter() - t0

        durations = await asyncio.gather(*[one_call() for _ in range(50)])
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    durations = sorted(durations)
    p99 = durations[-1]  # slowest of 50 ≈ p99
    assert p99 < 0.2, f"p99 {p99 * 1000:.1f}ms exceeded 200ms — cache regression?"

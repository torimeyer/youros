"""
->2018 LIVE-FREEZE reproduction (attempt #5).

Why this test exists even though api/tests/test_2018_event_loop_wedge.py
already passes: that test only exercises the sweep-lock mechanics in
isolation. It never drives the real ASGI app under the load the live
frontend actually applies, so it cannot catch the production freeze.

The live freeze: the dashboard keeps several WebSockets open and polls
GET /api/specs + GET /api/agents repeatedly. GET /api/specs runs heavy
*synchronous* file I/O directly on the event-loop thread:
ostk.list_docs() globs docs/draft, docs/spec, ~/.myos/specs and read_text()s
every file; then per surviving doc list_specs calls compute_shipped()
(read_text + exists + glob), _read_umbrella_fields() (read_text), and
compute_spec_readiness() (read_text). None of it is offloaded to a thread
and none of it is cached. So while one /api/specs request is parsing
files, the single uvicorn event loop cannot make progress on ANYTHING
else -- every other request and every WebSocket publish tick stalls
behind it. Under sustained concurrent polling the loop never catches up
and the server appears wedged (HTTP 000 / refused) until restart.

WHAT WE MEASURE -- event-loop responsiveness, the ground-truth freeze
signal. A background "heartbeat" coroutine asks the loop to wake it every
10 ms. The *excess* delay beyond 10 ms is exactly how long the loop was
blocked by synchronous work on the loop thread. If a handler hogs the
loop for N ms, the heartbeat is N ms late. That is the freeze, measured
directly -- independent of any client-side buffering that can mask it in
a latency probe.

The load shape mirrors the live frontend:
  * several concurrent GET /api/specs pollers (the heavy endpoint), and
  * the dashboard WebSocket publish loops, emulated by repeatedly calling
    the real _build_dashboard_data() the way each open /ws/dashboard/data
    connection does every few seconds.

Pre-fix: list_specs blocks the loop, heartbeat excess-delay p95 >> the
bound (~200 ms observed even with only ~13 docs; the live box has more).
Post-fix (offload the sync scan to a worker thread): the loop keeps
ticking and excess-delay stays small.
"""

import asyncio
import os
import time

import pytest

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_REAPER_ENABLED", "0")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from main import app  # noqa: E402
from routers import dashboard  # noqa: E402


def _p95(samples):
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


# Max time (seconds) the event loop is allowed to be blocked at p95 while
# /api/specs is polled. 150 ms is already generous -- a healthy async
# handler never blocks the loop for more than a few ms. The live freeze
# blows past multiple seconds; pre-fix in-process we see ~200 ms windows
# even with a tiny corpus.
LOOP_BLOCK_P95_BUDGET = 0.15


@pytest.mark.asyncio
async def test_specs_polling_does_not_starve_event_loop():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Warm caches so we measure steady state, not cold start.
        await client.get("/api/specs")
        await client.get("/api/agents")

        stop = asyncio.Event()
        excess = []  # heartbeat excess-delay samples (seconds beyond 10ms)

        async def heartbeat():
            # Ask the loop to wake us every 10 ms. However late it is = how
            # long the loop was blocked by synchronous on-loop work.
            while not stop.is_set():
                t0 = time.perf_counter()
                await asyncio.sleep(0.01)
                excess.append((time.perf_counter() - t0) - 0.01)

        async def specs_poll():
            while not stop.is_set():
                try:
                    await client.get("/api/specs")
                except Exception:
                    pass

        async def dashboard_publish_loop():
            # Mirror an open /ws/dashboard/data connection's periodic
            # publish (every 5 s live; tightened here to emulate several
            # connections collectively keeping the builder hot).
            while not stop.is_set():
                try:
                    await dashboard._build_dashboard_data()
                except Exception:
                    pass
                await asyncio.sleep(0.05)

        hb = asyncio.create_task(heartbeat())
        tasks = [asyncio.create_task(specs_poll()) for _ in range(6)]
        tasks += [asyncio.create_task(dashboard_publish_loop()) for _ in range(4)]

        # Run the load for a few seconds of steady state.
        await asyncio.sleep(4.0)

        stop.set()
        for t in tasks:
            t.cancel()
        hb.cancel()
        await asyncio.gather(*tasks, hb, return_exceptions=True)

        p95 = _p95(excess)
        worst = max(excess) if excess else 0.0
        print(
            "\n[->2018 repro] heartbeats=%d loop-block p95=%.0fms worst=%.0fms"
            % (len(excess), p95 * 1000, worst * 1000)
        )

        assert p95 < LOOP_BLOCK_P95_BUDGET, (
            "Event loop blocked for p95=%.0fms (worst=%.0fms) under concurrent "
            "/api/specs polling -- list_specs is running synchronous file I/O "
            "on the loop thread and starving every other task. This is the "
            "->2018 live freeze." % (p95 * 1000, worst * 1000)
        )

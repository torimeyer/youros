"""
->2018 FULL-FRONTEND-LOAD reproduction (attempt #6).

The prior fix (be3496a8) offloaded the synchronous /api/specs scan, and
tests/test_2018_live_freeze_repro.py proves /api/specs no longer starves
the loop. But the LIVE backend still wedges (HTTP 000) under the real
frontend's *aggregate* load, because /api/specs was not the only handler
doing synchronous file I/O on the event-loop thread.

The live frontend opens ~7 WebSockets (dashboard, notifications x3,
calendar x2) and polls a handful of endpoints continuously. Measured live
on 2026-05-31 with the merged fix in place: fresh GET /api/specs and
GET /api/agents time out (HTTP 000) and the backend log shows
``slow request GET /api/sessions/active 937ms``.

GET /api/sessions/active is the remaining loop-hog. It is declared
``async def`` but does ALL of its work inline on the loop thread:
  * _get_sessions(): iterdir() over .ostk/sessions, then for every
    session dir open()+seek()+read() the tail of events.jsonl and
    json.loads each of the last 20 lines.
  * _claude_code_transcript_sessions(): glob("*.jsonl") over
    ~/.claude/projects/<repo> and stat() every transcript.
None of it is offloaded. Each call is cheap with a handful of sessions
but the live box has dozens of session dirs and transcripts, so each
poll blocks the loop for ~1 s. With the dashboard WS publish loops and
the other pollers all queued behind it, the loop never catches up and
the server appears dead until restart.

WHAT WE MEASURE -- the same event-loop heartbeat used by the specs repro:
a coroutine asks the loop to wake it every 10 ms; the excess delay beyond
10 ms is exactly how long the loop was blocked by synchronous on-loop
work. We drive the real ASGI app with the full frontend load shape:
  * 7 concurrent WS-equivalent dashboard publish loops, and
  * concurrent pollers for /api/specs, /api/agents, /api/sessions/active,
    /api/calendar/events.

To make the on-disk cost deterministic (independent of how many sessions
happen to exist on the CI box), we point routers.sessions.SESSIONS_DIR at
a temp dir seeded with many recent session dirs, each with a realistic
events.jsonl. This mirrors a busy live box.

Pre-fix: get_active_sessions blocks the loop for ~hundreds of ms per
poll; heartbeat excess-delay p95 blows past the budget.
Post-fix (offload the sync scan to a worker thread): the loop keeps
ticking and excess-delay stays small.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pytest

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_REAPER_ENABLED", "0")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from main import app  # noqa: E402
from routers import dashboard  # noqa: E402
from routers import sessions as sessions_router  # noqa: E402


def _p95(samples):
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


# Same budget as the specs repro: a healthy async handler never blocks the
# loop for more than a few ms. 150 ms is generous.
LOOP_BLOCK_P95_BUDGET = 0.15

# Number of seeded session dirs. A busy live box easily has this many open
# Claude Code tabs / torichat turns / spawned fleet members writing events.
# (The live box that wedged had ~96 stale procs plus dozens of transcripts.)
NUM_SEEDED_SESSIONS = 300
# Lines per events.jsonl tail (each gets json.loads'd by _parse_event).
EVENTS_PER_SESSION = 120


def _seed_sessions(root):
    """Create NUM_SEEDED_SESSIONS recent session dirs with realistic
    events.jsonl files so _get_sessions does real on-loop file I/O."""
    now_iso = datetime.now(timezone.utc).isoformat()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(NUM_SEEDED_SESSIONS):
        sdir = root / f"session-{i:04d}"
        sdir.mkdir(exist_ok=True)
        lines = []
        for j in range(EVENTS_PER_SESSION):
            lines.append(json.dumps({
                "ts": now_iso,
                "kind": "tool_call",
                "data": {"tool": "bash", "args": "x" * 40, "seq": j},
            }))
        (sdir / "events.jsonl").write_text("\n".join(lines) + "\n")


@pytest.mark.asyncio
async def test_full_frontend_load_does_not_starve_event_loop(tmp_path, monkeypatch):
    # Point the sessions scan at a seeded busy directory so the cost is
    # deterministic and meaningfully large, like the live box.
    seeded = tmp_path / "sessions"
    _seed_sessions(seeded)
    monkeypatch.setattr(sessions_router, "SESSIONS_DIR", seeded)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Warm caches so we measure steady state, not cold start.
        await client.get("/api/specs")
        await client.get("/api/agents")
        await client.get("/api/sessions/active")
        await client.get("/api/calendar/events")

        stop = asyncio.Event()
        excess = []  # heartbeat excess-delay samples (seconds beyond 10ms)

        async def heartbeat():
            while not stop.is_set():
                t0 = time.perf_counter()
                await asyncio.sleep(0.01)
                excess.append((time.perf_counter() - t0) - 0.01)

        def _poller(path):
            async def _poll():
                while not stop.is_set():
                    try:
                        await client.get(path)
                    except Exception:
                        pass
            return _poll

        async def dashboard_publish_loop():
            # Each open /ws/dashboard/data connection publishes periodically.
            while not stop.is_set():
                try:
                    await dashboard._build_dashboard_data()
                except Exception:
                    pass
                await asyncio.sleep(0.05)

        hb = asyncio.create_task(heartbeat())
        tasks = []
        # Pollers mirroring the live frontend's HTTP traffic.
        tasks += [asyncio.create_task(_poller("/api/sessions/active")()) for _ in range(5)]
        tasks += [asyncio.create_task(_poller("/api/specs")()) for _ in range(2)]
        tasks += [asyncio.create_task(_poller("/api/agents")()) for _ in range(2)]
        tasks += [asyncio.create_task(_poller("/api/calendar/events")()) for _ in range(2)]
        # 7 dashboard WS publish loops (the live frontend holds ~7 sockets).
        tasks += [asyncio.create_task(dashboard_publish_loop()) for _ in range(7)]

        await asyncio.sleep(4.0)

        stop.set()
        for t in tasks:
            t.cancel()
        hb.cancel()
        await asyncio.gather(*tasks, hb, return_exceptions=True)

        p95 = _p95(excess)
        worst = max(excess) if excess else 0.0
        print(
            "\n[->2018 full-load] heartbeats=%d loop-block p95=%.0fms worst=%.0fms"
            % (len(excess), p95 * 1000, worst * 1000)
        )

        assert p95 < LOOP_BLOCK_P95_BUDGET, (
            "Event loop blocked for p95=%.0fms (worst=%.0fms) under the full "
            "frontend load (7 dashboard publish loops + sessions/specs/agents/"
            "calendar pollers). A handler is running synchronous file I/O on the "
            "loop thread (prime suspect: GET /api/sessions/active -> _get_sessions "
            "/ _claude_code_transcript_sessions). This is the ->2018 live freeze."
            % (p95 * 1000, worst * 1000)
        )

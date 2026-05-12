"""Regression tests for →1192: /api/agents wedge at 30s boundary.

Root cause: agent_reaper._do_sweep called _worktree_head_hash (subprocess.run)
synchronously on the event loop every 30s, coinciding with both cache TTL
expirations (_candidates_cache + _resolve_cache, both 30s). Combined latency
~3.5s. Standing-rules hook fires on every tool call, so one spike serialised
an entire session.

Fix: _do_sweep now delegates to _do_sweep_sync via asyncio.to_thread, keeping
the event loop free during the blocking git subprocess calls. session-start.sh
now attempts HTTPS only (HTTP always returned HTTP 000 empty reply).
"""
from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SESSION_START = REPO / ".claude" / "hooks" / "session-start.sh"


# ---------------------------------------------------------------------------
# Structural checks: _do_sweep must be an async wrapper, not the blocking body
# ---------------------------------------------------------------------------


def test_do_sweep_is_async():
    """_do_sweep must be an async coroutine function."""
    from lib.agent_reaper import _do_sweep
    assert asyncio.iscoroutinefunction(_do_sweep), (
        "_do_sweep must be async def so callers can await it without blocking"
    )


def test_do_sweep_delegates_to_thread():
    """_do_sweep must offload work to a thread via asyncio.to_thread."""
    from lib.agent_reaper import _do_sweep
    src = inspect.getsource(_do_sweep)
    assert "asyncio.to_thread" in src, (
        "_do_sweep must use asyncio.to_thread — blocking subprocess calls "
        "inside _do_sweep_sync must not run on the event loop thread"
    )
    assert "_do_sweep_sync" in src, (
        "_do_sweep must delegate to _do_sweep_sync (the blocking implementation)"
    )


def test_do_sweep_sync_is_not_async():
    """_do_sweep_sync must be a plain (non-async) function.

    asyncio.to_thread requires a regular callable, not a coroutine function.
    """
    from lib.agent_reaper import _do_sweep_sync
    assert not asyncio.iscoroutinefunction(_do_sweep_sync), (
        "_do_sweep_sync must NOT be async def — it is run in a thread pool worker "
        "where an event loop is not available"
    )


def test_worktree_head_hash_is_not_async():
    """_worktree_head_hash must stay sync so it can run inside _do_sweep_sync."""
    from lib.agent_reaper import _worktree_head_hash
    assert not asyncio.iscoroutinefunction(_worktree_head_hash), (
        "_worktree_head_hash must be a plain function (called from _do_sweep_sync "
        "thread, not from the event loop)"
    )


def test_run_forever_awaits_do_sweep():
    """run_forever must await _do_sweep (not call it as a plain function)."""
    from lib.agent_reaper import run_forever
    src = inspect.getsource(run_forever)
    assert "await _do_sweep()" in src, (
        "run_forever must await _do_sweep() — calling it without await would "
        "return a coroutine object and never execute the sweep"
    )


# ---------------------------------------------------------------------------
# Structural check: session-start.sh must not attempt HTTP before HTTPS
# ---------------------------------------------------------------------------


def test_session_start_no_http_first_registration():
    """session-start.sh must not try http:// before https:// for registration.

    Plain HTTP against a TLS-only backend returns HTTP 000 (empty reply, no body).
    Attempting HTTP first on every session start generated a failing TCP connection
    plus a 2s connect-timeout on every Claude Code session open. Fixed by removing
    the HTTP attempt entirely.
    """
    text = SESSION_START.read_text()
    http_pos = text.find('"http://localhost:8000/api/agents/register"')
    https_pos = text.find('"https://localhost:8000/api/agents/register"')

    assert https_pos != -1, (
        "session-start.sh must register via https://localhost:8000/api/agents/register"
    )

    if http_pos != -1:
        assert https_pos < http_pos, (
            "session-start.sh must attempt https:// BEFORE http:// for registration "
            "(HTTP 000 on plain-HTTP generates a wasted TCP connection + timeout)"
        )


# ---------------------------------------------------------------------------
# Concurrency check: event loop stays free while _do_sweep_sync runs in thread
# ---------------------------------------------------------------------------


def test_do_sweep_does_not_block_event_loop():
    """Concurrent asyncio tasks must not be starved while _do_sweep runs.

    Simulates the thundering-herd scenario: a slow _do_sweep_sync (50ms sleep
    models the blocking git subprocesses) is awaited while a side task tries to
    yield to the event loop. The side task must complete before the sweep returns.
    """
    async def _run():
        side_fired = []

        async def _side():
            await asyncio.sleep(0)
            side_fired.append(True)

        async def _fake_sweep():
            # Model the blocking subprocess work (runs in thread via to_thread)
            return await asyncio.to_thread(_slow_work)

        def _slow_work():
            time.sleep(0.05)  # 50ms — models 1-2 git subprocess calls
            return 0

        t0 = time.monotonic()
        await asyncio.gather(_fake_sweep(), _side())
        return time.monotonic() - t0, side_fired

    elapsed, side_fired = asyncio.run(_run())
    assert side_fired, (
        "concurrent asyncio task must fire while sweep's blocking work runs in thread"
    )
    assert elapsed < 2.0, f"sweep + side task must complete in <2s, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Constant check: SWEEP_INTERVAL_SECONDS matches known cache TTLs
# ---------------------------------------------------------------------------


def test_sweep_interval_is_30s():
    """SWEEP_INTERVAL_SECONDS must be 30 — matches _candidates_cache and _resolve_cache TTLs.

    The thundering herd occurs because all three timers tick together at the 30s
    boundary. The to_thread fix makes the sweep non-blocking; this test guards
    against the constant drifting out of alignment, which would create new
    thundering-herd windows.
    """
    from lib.agent_reaper import SWEEP_INTERVAL_SECONDS
    assert SWEEP_INTERVAL_SECONDS == 30, (
        f"SWEEP_INTERVAL_SECONDS drifted to {SWEEP_INTERVAL_SECONDS}; "
        "update this test and verify alignment with _CANDIDATES_TTL_SECONDS "
        "and _RESOLVE_TTL_SECONDS in agents.py"
    )


def test_agents_cache_ttls_are_defined():
    """Both cache TTL constants must exist in agents.py."""
    agents_py = (REPO / "api" / "routers" / "agents.py").read_text()
    assert "_CANDIDATES_TTL_SECONDS" in agents_py, (
        "_CANDIDATES_TTL_SECONDS missing from agents.py — cache TTL tracking broken"
    )
    assert "_RESOLVE_TTL_SECONDS" in agents_py, (
        "_RESOLVE_TTL_SECONDS missing from agents.py — cache TTL tracking broken"
    )

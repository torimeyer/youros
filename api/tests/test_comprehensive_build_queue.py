"""Tests for the comprehensive build concurrency queue.

Covers:
  1. 5 concurrent start attempts → 3 running, 2 queued
  2. Completing 1 running build promotes the first queued entry
  3. Cancelling a queued build removes it cleanly
  4. Backend restart mid-queue: queued entries survive (persist round-trip)
  5. Integration with spawn endpoint: comprehensive spawn past the limit
     returns build_state="queued" with HTTP 200
  6. Same-task lock still prevents duplicate builds for the same task
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset():
    """Clear all in-process build queue state between tests."""
    from services.build_queue import _reset_for_tests
    _reset_for_tests()


def _make_kwargs(task_id: str, spawn_id: str) -> Dict[str, Any]:
    return {
        "name": spawn_id,
        "prompt": f"implement task {task_id}",
        "model": "sonnet",
        "budget": 2.0,
        "task_id": task_id,
        "template": "comprehensive",
        "locks": [f"tasks/{task_id}"],
    }


# ---------------------------------------------------------------------------
# 1. 5 starts → 3 running 2 queued
# ---------------------------------------------------------------------------


def test_five_starts_three_running_two_queued():
    from services.build_queue import (
        BUILD_CONCURRENCY,
        queue_depth,
        running_count,
        try_start_build,
    )

    _reset()
    assert BUILD_CONCURRENCY == 3, "test assumes default of 3"

    results = []
    for i in range(5):
        state = try_start_build(
            spawn_id=f"implement-task{i}",
            task_id=f"task{i}",
            spawn_kwargs=_make_kwargs(f"task{i}", f"implement-task{i}"),
        )
        results.append(state)

    assert results[:3] == ["running", "running", "running"], results
    assert results[3:] == ["queued", "queued"], results
    assert running_count() == 3
    assert queue_depth() == 2

    _reset()


# ---------------------------------------------------------------------------
# 2. Completing 1 promotes a queued entry
# ---------------------------------------------------------------------------


def test_finish_build_promotes_queued():
    from services.build_queue import (
        finish_build,
        get_build_state,
        queue_depth,
        running_count,
        try_start_build,
    )

    _reset()

    for i in range(5):
        try_start_build(
            spawn_id=f"agent-{i}",
            task_id=f"t{i}",
            spawn_kwargs=_make_kwargs(f"t{i}", f"agent-{i}"),
        )

    assert running_count() == 3
    assert queue_depth() == 2

    # Complete agent-0: should promote agent-3 (first in queue).
    next_entry = finish_build("agent-0")
    assert next_entry is not None, "expected a queued entry to be promoted"
    assert next_entry.spawn_id == "agent-3"
    assert next_entry.state == "running"

    # Running set: agent-1, agent-2, agent-3.
    assert running_count() == 3
    assert queue_depth() == 1
    assert get_build_state("agent-3") == "running"
    assert get_build_state("agent-4") == "queued"

    _reset()


# ---------------------------------------------------------------------------
# 3. Cancelling a queued build removes it cleanly
# ---------------------------------------------------------------------------


def test_cancel_queued_build():
    from services.build_queue import (
        cancel_queued_build,
        finish_build,
        get_build_state,
        queue_depth,
        running_count,
        try_start_build,
    )

    _reset()

    for i in range(5):
        try_start_build(
            spawn_id=f"bld-{i}",
            task_id=f"t{i}",
            spawn_kwargs=_make_kwargs(f"t{i}", f"bld-{i}"),
        )

    assert queue_depth() == 2

    # Cancel bld-4 (second in queue).
    removed = cancel_queued_build("bld-4")
    assert removed is True
    assert queue_depth() == 1
    assert get_build_state("bld-4") is None

    # Cancelling a running or unknown entry does nothing harmful.
    assert cancel_queued_build("bld-0") is False   # running, not queued
    assert cancel_queued_build("nonexistent") is False

    # Complete bld-0: promotes bld-3 (only remaining queued).
    next_entry = finish_build("bld-0")
    assert next_entry is not None
    assert next_entry.spawn_id == "bld-3"
    assert queue_depth() == 0

    _reset()


# ---------------------------------------------------------------------------
# 4. Restart of backend mid-queue does not lose pending
# ---------------------------------------------------------------------------


def test_restart_preserves_queued_entries():
    """Queue entries written to disk survive a simulated module reload."""
    import sys
    import tempfile
    from unittest.mock import patch as _patch

    _reset()

    entries = [
        {
            "spawn_id": "implement-restarted-a",
            "task_id": "ta",
            "spawn_kwargs": _make_kwargs("ta", "implement-restarted-a"),
            "state": "queued",
            "enqueued_at": 1000.0,
        },
        {
            "spawn_id": "implement-restarted-b",
            "task_id": "tb",
            "spawn_kwargs": _make_kwargs("tb", "implement-restarted-b"),
            "state": "queued",
            "enqueued_at": 1001.0,
        },
    ]

    # Use a temp directory to avoid racing with the live server's build_queue.json.
    with tempfile.TemporaryDirectory() as _tmpdir:
        _tmp_path = Path(_tmpdir)
        _q_path = _tmp_path / "build_queue.json"
        _q_path.write_text(json.dumps({"queue": entries}))

        # Patch youros_home BEFORE the import so _QUEUE_PATH resolves to tmpdir.
        import services.youros_paths as _yp_mod
        _orig_youros_home = _yp_mod.youros_home

        def _patched_youros_home():
            return _tmp_path

        _yp_mod.youros_home = _patched_youros_home

        if "services.build_queue" in sys.modules:
            del sys.modules["services.build_queue"]
        import services.build_queue as bq_fresh

        try:
            assert bq_fresh.queue_depth() == 2, (
                f"Expected 2 queued entries after restart, got {bq_fresh.queue_depth()}"
            )
            assert bq_fresh.get_build_state("implement-restarted-a") == "queued"
            assert bq_fresh.get_build_state("implement-restarted-b") == "queued"
        finally:
            bq_fresh._reset_for_tests()
            _yp_mod.youros_home = _orig_youros_home
            # Remove so subsequent imports reload cleanly with real youros_home.
            if "services.build_queue" in sys.modules:
                del sys.modules["services.build_queue"]


# ---------------------------------------------------------------------------
# 5. Integration: spawn endpoint returns build_state="queued" at limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_returns_queued_at_limit(monkeypatch):
    """Comprehensive spawn at concurrency limit returns 200 with build_state=queued."""
    from main import app
    from routers.agents import active_agents, agent_metadata
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests

    # Reset both queues.
    _reset()
    _reset_spawn_lock_registry_for_tests()

    # Stub out subprocess exec so no real claude process starts.
    from routers import agents as agents_mod
    import services.spawn_throttle as _spawn_throttle_mod

    # Bypass the burst throttle: with burst_limit=3 and 5 concurrent spawns,
    # spawns 4-5 would sleep ~30 s waiting for a slot.
    async def _noop_throttle(agent_name=""):
        return 0.0

    monkeypatch.setattr(_spawn_throttle_mod, "acquire_spawn_slot", _noop_throttle)

    # Stub _run_json so any list_tasks/list_docs path that reaches it returns
    # an empty list instead of running a real subprocess or raising OstkError.
    async def _noop_run_json(*args, **kwargs):
        return []

    monkeypatch.setattr(agents_mod.ostk, "_run_json", _noop_run_json)

    async def _noop_exec(*args, **kwargs):
        class _FakeStdin:
            async def drain(self):
                pass
            def write(self, d):
                pass
            def close(self):
                pass
            def is_closing(self):
                return False
            def can_write_eof(self):
                return True
            def write_eof(self):
                pass

        class _FakeStdout:
            async def read(self, n):
                return b""

        class _FakeStderr:
            async def read(self, n):
                return b""

        class _FakeProc:
            pid = 9999
            returncode = 0
            stdin = _FakeStdin()
            stdout = _FakeStdout()
            stderr = _FakeStderr()
            async def wait(self):
                return 0
            async def communicate(self):
                return b"", b""

        return _FakeProc()

    async def _noop_run(*args, **kwargs):
        return ""

    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _noop_exec)
    monkeypatch.setattr(agents_mod.ostk, "_run", _noop_run)

    from httpx import ASGITransport, AsyncClient

    agent_names = [f"implement-qtest{i}" for i in range(5)]
    for n in agent_names:
        agent_metadata.pop(n, None)
        active_agents.pop(n, None)

    responses = []
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Spawn all 5 concurrently so slots fill up before any drain task
            # has a chance to run and release a slot between sequential awaits.
            responses = list(await asyncio.gather(*[
                client.post(
                    "/api/agents/spawn",
                    json={
                        "name": f"implement-qtest{i}",
                        "prompt": f"Implement this task: 'task {i}'. Follow the comprehensive build pattern.",
                        "model": "sonnet",
                        "budget": 2.0,
                        "task_id": f"qtask{i}",
                        "template": "comprehensive",
                        "locks": [f"tasks/qtask{i}"],
                    },
                )
                for i in range(5)
            ]))

        statuses = [r.status_code for r in responses]
        assert all(s == 200 for s in statuses), f"Expected all 200, got {statuses}"

        bodies = [r.json() for r in responses]
        build_states = [b.get("build_state") for b in bodies]
        running_states = [s for s in build_states if s == "running"]
        queued_states = [s for s in build_states if s == "queued"]

        assert len(queued_states) == 2, (
            f"Expected 2 queued, got build_states={build_states}"
        )
        assert len(running_states) == 3, (
            f"Expected 3 running, got build_states={build_states}"
        )
    finally:
        for n in agent_names:
            agent_metadata.pop(n, None)
            active_agents.pop(n, None)
        _reset()
        _reset_spawn_lock_registry_for_tests()


# ---------------------------------------------------------------------------
# 6. Same-task lock prevents duplicate builds for the same task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_task_still_returns_409(monkeypatch):
    """Two comprehensive builds for the same task: second gets 409 (lock conflict)."""
    from main import app
    from routers.agents import active_agents, agent_metadata
    from services.spawn_isolation import (
        _reset_spawn_lock_registry_for_tests,
        acquire_spawn_locks,
    )

    _reset()
    _reset_spawn_lock_registry_for_tests()

    # Pre-acquire the lock as if the first build is already running.
    holder = "implement-dup-first"
    ok, _, _ = acquire_spawn_locks(spawn_id=holder, locks=["tasks/dup-task"])
    assert ok

    second = "implement-dup-second"
    agent_metadata.pop(second, None)
    active_agents.pop(second, None)

    try:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": second,
                    "prompt": "Implement this task: 'dup'. Follow the comprehensive build pattern.",
                    "model": "sonnet",
                    "budget": 2.0,
                    "task_id": "dup-task",
                    "template": "comprehensive",
                    "locks": ["tasks/dup-task"],
                },
            )
        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail") or {}
        assert holder in str(detail), f"409 body must name the holder: {detail}"
    finally:
        agent_metadata.pop(second, None)
        active_agents.pop(second, None)
        _reset()
        _reset_spawn_lock_registry_for_tests()


# ---------------------------------------------------------------------------
# 7. Startup drain: queued entries auto-start when _running is empty (→2497)
# ---------------------------------------------------------------------------


def test_startup_drain_promotes_queued_when_slots_available():
    """After a restart _running=0; drain_startup_queue() should fill available slots."""
    from services.build_queue import (
        BUILD_CONCURRENCY,
        drain_startup_queue,
        get_build_state,
        queue_depth,
        running_count,
    )

    _reset()

    # Manually seed the queue (simulates entries restored from disk on restart).
    from services import build_queue as bq_mod
    from services.build_queue import BuildEntry
    import time as _t

    entries = [
        BuildEntry(
            spawn_id=f"implement-restart-{i}",
            task_id=f"t{i}",
            spawn_kwargs=_make_kwargs(f"t{i}", f"implement-restart-{i}"),
            state="queued",
            enqueued_at=_t.time() - (10 - i),
        )
        for i in range(4)
    ]
    with bq_mod._lock:
        bq_mod._queue.extend(entries)

    # _running is already empty after _reset(); simulates a fresh backend start.
    assert running_count() == 0
    assert queue_depth() == 4

    to_start = drain_startup_queue()

    # Should promote up to BUILD_CONCURRENCY (3) entries.
    assert len(to_start) == min(BUILD_CONCURRENCY, 4), (
        f"Expected {min(BUILD_CONCURRENCY, 4)} entries to promote, got {len(to_start)}"
    )
    assert running_count() == len(to_start)
    assert queue_depth() == 4 - len(to_start)

    for entry in to_start:
        assert get_build_state(entry.spawn_id) == "running"

    # The leftover entry must still be queued.
    assert get_build_state("implement-restart-3") == "queued"

    _reset()


# ---------------------------------------------------------------------------
# 8. Promotion failure: return_to_queue frees the slot and re-queues (→2497)
# ---------------------------------------------------------------------------


def test_promotion_failure_returns_entry_to_queue():
    """If spawn fails during promotion, return_to_queue() frees the slot and re-queues."""
    from services.build_queue import (
        finish_build,
        get_build_state,
        queue_depth,
        return_to_queue,
        running_count,
        try_start_build,
    )

    _reset()

    # Fill concurrency limit + add one queued.
    for i in range(4):
        try_start_build(
            spawn_id=f"bq-pr-{i}",
            task_id=f"t{i}",
            spawn_kwargs=_make_kwargs(f"t{i}", f"bq-pr-{i}"),
        )

    assert running_count() == 3
    assert queue_depth() == 1
    assert get_build_state("bq-pr-3") == "queued"

    # Complete bq-pr-0: promotes bq-pr-3 (moves it to _running).
    promoted = finish_build("bq-pr-0")
    assert promoted is not None
    assert promoted.spawn_id == "bq-pr-3"
    assert get_build_state("bq-pr-3") == "running"
    assert queue_depth() == 0

    # Simulate a spawn failure during promotion: call return_to_queue.
    return_to_queue(promoted)

    # bq-pr-3 should be back in queue (front), slot freed.
    assert get_build_state("bq-pr-3") == "queued"
    assert queue_depth() == 1
    # Running should be down to 2 (bq-pr-1 and bq-pr-2).
    assert running_count() == 2

    _reset()

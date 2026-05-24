"""Tests for api/main.py FastAPI bootstrap.

Covers two stability invariants for the live demo:
  1. The Claude CLI prewarm hook must not block the startup event. If it
     does, the HTTP server is unavailable for 30+ seconds after launch and
     the demo browser sees connection refused.
  2. The /api/health endpoint returns 200 without touching any downstream.
     scripts/health.sh and scripts/backend_watchdog.sh poll this endpoint
     and any latency or error from a downstream import would cascade into
     a watchdog restart.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_returns_200_without_downstream(client):
    """/api/health must be a tiny in-memory return, no I/O.

    The watchdog probes this every 30 seconds. If the handler ever calls
    out to the database, ostk, or another service, a transient downstream
    hiccup would mark the backend as dead and trigger a restart loop.
    """
    start = time.monotonic()
    resp = await client.get("/api/health")
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "myos-api"
    # Generous bound: under load this still finishes in < 0.1s. We assert
    # 1s so the test is not flaky on a busy CI box but still catches a
    # regression that adds a real downstream call.
    assert elapsed < 1.0, f"/api/health took {elapsed:.2f}s, must be in-memory only"


@pytest.mark.asyncio
async def test_startup_does_not_block_on_claude_prewarm(monkeypatch):
    """The startup event must return promptly even if prewarm_cli hangs.

    Regression guard: an earlier shape of api/main.py awaited prewarm_cli
    directly inside the on_event("startup") handler. When the local claude
    binary was slow or missing, startup blocked for 30 seconds and the
    HTTP server never bound to port 8000. We assert here that the prewarm
    hook spawns its work as a background task and returns quickly.
    """
    # Patch prewarm_cli with a coroutine that sleeps "forever". If the
    # startup handler awaits it directly, our assertion below will fail
    # because the handler will not return within the bound.
    blocked_event = asyncio.Event()

    async def _hang() -> None:
        # Mark that prewarm was at least invoked, then hang.
        blocked_event.set()
        await asyncio.sleep(60)

    # Import lazily so the patch lands before the handler runs.
    from services import claude_code_provider as ccp

    with patch.object(ccp, "prewarm_cli", side_effect=_hang):
        # Re-import main to access the startup hook function directly.
        # We call the registered on_event handler in isolation rather than
        # spinning up the whole ASGI lifespan, which is faster and lets us
        # bound wall-clock precisely.
        import main

        # With lifespan pattern, startup functions are plain async functions
        # called directly from the lifespan context manager rather than
        # registered in app.router.on_startup.
        hook = main.prewarm_claude_cli
        assert hook is not None, "prewarm_claude_cli must exist in main"

        start = time.monotonic()
        await asyncio.wait_for(hook(), timeout=2.0)
        elapsed = time.monotonic() - start

    # The hook itself must return in well under a second. The 2s
    # asyncio.wait_for above is a hard ceiling; this is the soft guard.
    assert elapsed < 1.0, (
        f"prewarm_claude_cli startup hook took {elapsed:.2f}s, "
        "must dispatch prewarm to a background task and return immediately"
    )


@pytest.mark.asyncio
async def test_prewarm_uses_create_task_not_await():
    """Verify the source of prewarm_claude_cli wraps work in create_task.

    A direct `await claude_code_provider.prewarm_cli()` inside the startup
    handler would block the event loop. We assert the source spawns a
    background task instead. This is a static check so it catches the
    regression even on machines where the claude binary is not installed.
    """
    import inspect

    import main

    src = inspect.getsource(main.prewarm_claude_cli)
    assert "create_task" in src, (
        "prewarm_claude_cli must dispatch work via asyncio.create_task "
        "so the startup event returns immediately"
    )
    # The handler itself must NOT directly await prewarm_cli at top level.
    # We allow `await claude_code_provider.prewarm_cli()` inside the inner
    # _warm coroutine, but the outer handler body must not.
    outer_lines = []
    in_inner = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("async def _warm"):
            in_inner = True
            continue
        if in_inner and stripped.startswith("asyncio.create_task"):
            in_inner = False
            continue
        if not in_inner:
            outer_lines.append(line)
    outer_src = "\n".join(outer_lines)
    assert "await claude_code_provider.prewarm_cli" not in outer_src, (
        "prewarm_claude_cli outer body must not directly await prewarm_cli"
    )


@pytest.mark.asyncio
async def test_test_artifact_sweep_runs_periodically(monkeypatch):
    """The sweep hook must spawn a background task that calls cleanup.

    Verifies that schedule_test_artifact_sweep dispatches its work via
    asyncio.create_task (so startup never blocks) and that the loop
    actually invokes cleanup_test_artifacts at least once. We patch
    asyncio.sleep to skip the 30 second delay and the 300 second
    interval, then cancel the loop after the first call.
    """
    import inspect

    import main

    # Static check: outer body uses create_task and wraps the sleep
    # cycle in an inner coroutine so the startup event returns
    # immediately. Same shape as prewarm_claude_cli.
    src = inspect.getsource(main.schedule_test_artifact_sweep)
    assert "create_task" in src, (
        "schedule_test_artifact_sweep must dispatch work via "
        "asyncio.create_task so startup returns immediately"
    )
    assert "300" in src, "sweep cadence must be every 300 seconds"

    call_count = {"n": 0}
    done = asyncio.Event()

    async def _fake_cleanup():
        call_count["n"] += 1
        done.set()
        return {"deleted": 0, "deleted_ids": [], "deleted_labels": 0, "deleted_label_ids": []}

    # Patch the cleanup endpoint that the loop imports lazily.
    from routers import tasks as tasks_module
    monkeypatch.setattr(tasks_module, "cleanup_test_artifacts", _fake_cleanup)

    # Skip every asyncio.sleep so the loop runs immediately.
    real_sleep = asyncio.sleep

    async def _instant_sleep(_secs):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    # Disable env guard so the hook actually runs.
    monkeypatch.delenv("MYOS_NO_SWEEP", raising=False)

    start = time.monotonic()
    await asyncio.wait_for(main.schedule_test_artifact_sweep(), timeout=2.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"schedule_test_artifact_sweep took {elapsed:.2f}s, must dispatch "
        "to a background task and return immediately"
    )

    # Wait for the inner loop to fire the first cleanup call.
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_test_artifact_sweep_logs_warning_when_it_finds_leaks(
    monkeypatch, caplog
):
    """When cleanup deletes anything, the loop must log a WARNING with titles.

    The warning must include the leaked titles so a developer can
    grep audit/logs to find which code path created them. It must
    also include the follow-up line nudging investigation of
    _reject_bad_title.
    """
    import logging

    import main

    leaked = ["e2e-smoke-1", "e2e-smoke-2", "e2e-smoke-3"]
    fired = asyncio.Event()

    async def _fake_cleanup():
        return {
            "deleted": 3,
            "deleted_ids": ["t1", "t2", "t3"],
            "deleted_labels": 0,
            "deleted_label_ids": [],
        }

    async def _fake_list_tasks(status="open"):
        return [
            {"id": "t1", "title": leaked[0]},
            {"id": "t2", "title": leaked[1]},
            {"id": "t3", "title": leaked[2]},
            {"id": "ok", "title": "Real user task"},
        ]

    from routers import tasks as tasks_module
    from services import ostk as ostk_module

    monkeypatch.setattr(tasks_module, "cleanup_test_artifacts", _fake_cleanup)
    monkeypatch.setattr(ostk_module.ostk, "list_tasks", _fake_list_tasks)

    real_sleep = asyncio.sleep
    sleeps = {"n": 0}

    async def _instant_sleep(_secs):
        sleeps["n"] += 1
        # After the first cleanup logs, halt the loop by raising.
        if fired.is_set():
            raise asyncio.CancelledError()
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    monkeypatch.delenv("MYOS_NO_SWEEP", raising=False)

    caplog.set_level(logging.WARNING, logger="main")

    await main.schedule_test_artifact_sweep()
    # Give the inner loop a few ticks to run the first iteration.
    for _ in range(20):
        await real_sleep(0)
        warning_messages = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        if any("test_artifact_sweep cleaned" in m for m in warning_messages):
            fired.set()
            break

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    ]
    cleaned_logs = [
        m for m in warning_messages if "test_artifact_sweep cleaned" in m
    ]
    assert cleaned_logs, f"expected cleaned warning, got {warning_messages}"
    # Title list must appear in the warning.
    assert "e2e-smoke-1" in cleaned_logs[0]
    investigate_logs = [
        m for m in warning_messages if "investigate" in m
    ]
    assert investigate_logs, "expected follow-up investigate warning"

    # Drain any remaining background tasks so pytest does not see
    # warnings about unfinished tasks.
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await real_sleep(0)


@pytest.mark.asyncio
async def test_test_artifact_sweep_can_be_disabled_with_env(monkeypatch):
    """Setting MYOS_NO_SWEEP=1 must skip the sweep entirely.

    Tests need a way to opt out of the periodic loop so the test
    suite never races a real sweep against its own fixtures. When
    the env var is set, the hook must return without scheduling
    any background task and without invoking cleanup.
    """
    import main

    called = {"n": 0}

    async def _fake_cleanup():
        called["n"] += 1
        return {"deleted": 0}

    from routers import tasks as tasks_module
    monkeypatch.setattr(tasks_module, "cleanup_test_artifacts", _fake_cleanup)
    monkeypatch.setenv("MYOS_NO_SWEEP", "1")

    # Track whether create_task was invoked at all by patching it.
    spawned = {"n": 0}
    real_create_task = asyncio.create_task

    def _tracking_create_task(coro, *args, **kwargs):
        spawned["n"] += 1
        return real_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _tracking_create_task)

    await main.schedule_test_artifact_sweep()
    # Flush a few ticks so any rogue inner coroutine has a chance to run.
    for _ in range(5):
        await asyncio.sleep(0)

    assert spawned["n"] == 0, (
        "MYOS_NO_SWEEP=1 must prevent any background task from being scheduled"
    )
    assert called["n"] == 0, (
        "MYOS_NO_SWEEP=1 must prevent cleanup_test_artifacts from running"
    )

"""Tests: worktree_reaper.run_forever is wired into api/main.py startup (R5 follow-up).

Verifies:
- schedule_worktree_reaper() calls asyncio.create_task with run_forever by default
- schedule_worktree_reaper() is a no-op when MYOS_REAPER_ENABLED=0
- Both schedule_spawn_lock_sweep and schedule_worktree_reaper are awaited
  from the same FastAPI lifespan startup path
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# schedule_worktree_reaper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_task_when_enabled_by_default(monkeypatch):
    """Default env: asyncio.create_task is called once with a run_forever coro."""
    monkeypatch.delenv("MYOS_REAPER_ENABLED", raising=False)

    created = []

    async def fake_run_forever():
        pass

    with patch("services.worktree_reaper.run_forever", new=fake_run_forever), \
         patch("asyncio.create_task", side_effect=lambda c: created.append(c)):
        import main
        await main.schedule_worktree_reaper()

    assert len(created) == 1, "expected exactly one create_task call"
    for c in created:
        if asyncio.iscoroutine(c):
            c.close()


@pytest.mark.asyncio
async def test_skips_task_when_disabled_via_env(monkeypatch):
    """MYOS_REAPER_ENABLED=0 must prevent create_task from being called."""
    monkeypatch.setenv("MYOS_REAPER_ENABLED", "0")

    created = []

    with patch("asyncio.create_task", side_effect=lambda c: created.append(c)):
        import main
        await main.schedule_worktree_reaper()

    assert created == [], "create_task must not be called when MYOS_REAPER_ENABLED=0"


# ---------------------------------------------------------------------------
# Lifespan wiring: both schedule_spawn_lock_sweep and schedule_worktree_reaper
# must be awaited in the same startup path
# ---------------------------------------------------------------------------

_LIFESPAN_STARTUP_NOOPS = [
    "main.fix_audit_watermark",
    "main.schedule_upgrade_check",
    "main.schedule_label_backfill",
    "main.schedule_settings_sync_pull",
    "main.schedule_gmail_unread_notification",
    "main.schedule_overdue_task_check",
    "main.prewarm_claude_cli",
    "main.pregenerate_briefing",
    "main.prewarm_savings",
    "main.schedule_session_task_reaper",
    "main.schedule_ghost_spawn_reaper",
    "main.backfill_chat_ack_bots",
    "main.sweep_stale_backend_sessions",
    "main.schedule_agent_reconciliation",
    "main.schedule_recurring_task_spawner",
    "main.schedule_test_artifact_sweep",
    "main.schedule_test_artifact_spec_sweep",
    "main.install_signal_shutdown_hook",
    "main.notify_chat_clients_on_shutdown",
]


@pytest.mark.asyncio
async def test_lifespan_calls_both_spawn_lock_sweep_and_worktree_reaper():
    """schedule_spawn_lock_sweep and schedule_worktree_reaper are both awaited in lifespan."""
    import main
    from fastapi import FastAPI

    spawn_lock_called = []
    reaper_called = []

    with ExitStack() as stack:
        for target in _LIFESPAN_STARTUP_NOOPS:
            stack.enter_context(patch(target, new=AsyncMock()))

        stack.enter_context(
            patch(
                "routers.agents.schedule_spawn_lock_sweep",
                new=AsyncMock(side_effect=lambda: spawn_lock_called.append(True)),
            )
        )
        stack.enter_context(
            patch(
                "main.schedule_worktree_reaper",
                new=AsyncMock(side_effect=lambda: reaper_called.append(True)),
            )
        )

        app = FastAPI()
        async with main.lifespan(app):
            pass

    assert spawn_lock_called, "schedule_spawn_lock_sweep was not called from lifespan"
    assert reaper_called, "schedule_worktree_reaper was not called from lifespan"

"""Task 2964: spawn-path cleanup.

Three scope items:

1. YOUROS_SPAWN_FORCE_CUSTOM escape hatch: evaluated against its documented
   AC3 exit criteria and KEPT. Criteria (i) (fallback rate at or near zero
   across a full release cycle) and (v) (supervised zero-fallback
   verification of three agent types) are not met: the backend log records
   OSTK_RUN_FALLBACK warnings (reason=no_agentfile) through the current
   release cycle, and no supervised verification is recorded in the spec.
   test_escape_hatch_and_exit_criteria_still_present pins the keep decision.

2. The last hard-wired ".claude/worktrees" spawn call site
   (_provision_worktree_isolation) resolves through
   services.spawn_isolation.worktree_path_for(), so the task-2963
   YOUROS_WORKTREES_DIR override applies to real agent spawns.

3. Dead texting-notify plumbing (task 2967 removed services/text_bridge.py)
   is gone: no text_bridge import in routers/agents.py, no AgentSpawn.notify
   field, no notify forwarding in tool_executor. A stray "notify" key in a
   spawn payload is ignored cleanly instead of reaching code whose import
   would crash.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Item 2: worktree provisioning honors the configurable workspace root
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provision_worktree_isolation_honors_configured_root(monkeypatch, tmp_path):
    """The spawn path must build the worktree path through worktree_path_for()
    so YOUROS_WORKTREES_DIR moves real agent workspaces."""
    from routers import agents as agents_router
    from services import spawn_isolation
    from services.spawn_isolation import short_worktree_id

    custom_root = tmp_path / "custom-workspaces"
    monkeypatch.setenv("YOUROS_WORKTREES_DIR", str(custom_root))
    captured = {}

    async def _fake_create_worktree(*, project_root, agent_name, branch, wt_path):
        captured["wt_path"] = Path(wt_path)
        captured["branch"] = branch
        return False, "stop-before-any-fs-side-effects"

    monkeypatch.setattr(spawn_isolation, "create_worktree", _fake_create_worktree)

    with pytest.raises(HTTPException):
        await agents_router._provision_worktree_isolation("wtloc-2964-agent")

    wt_id = short_worktree_id("wtloc-2964-agent")
    assert captured["wt_path"] == custom_root / f"agent-{wt_id}"
    assert captured["branch"] == f"worktree-agent-{wt_id}"


@pytest.mark.asyncio
async def test_provision_worktree_isolation_default_root_unchanged(monkeypatch):
    """With the override unset, the path stays byte-for-byte the historical
    <project_root>/.claude/worktrees/agent-<short id>."""
    from config import PROJECT_ROOT
    from routers import agents as agents_router
    from services import spawn_isolation
    from services.spawn_isolation import short_worktree_id

    monkeypatch.delenv("YOUROS_WORKTREES_DIR", raising=False)
    captured = {}

    async def _fake_create_worktree(*, project_root, agent_name, branch, wt_path):
        captured["wt_path"] = Path(wt_path)
        return False, "stop"

    monkeypatch.setattr(spawn_isolation, "create_worktree", _fake_create_worktree)

    with pytest.raises(HTTPException):
        await agents_router._provision_worktree_isolation("wtloc-2964-default")

    assert captured["wt_path"] == (
        Path(PROJECT_ROOT) / ".claude" / "worktrees"
        / f"agent-{short_worktree_id('wtloc-2964-default')}"
    )


# ---------------------------------------------------------------------------
# Item 3: dead texting-notify plumbing is gone
# ---------------------------------------------------------------------------

def test_agent_spawn_schema_has_no_notify_field():
    from models.schemas import AgentSpawn
    assert "notify" not in AgentSpawn.model_fields


def test_agent_spawn_ignores_stray_notify_key():
    """Payloads that still carry the removed key must not crash or keep it."""
    from models.schemas import AgentSpawn
    body = AgentSpawn(
        name="stray-notify-2964",
        prompt="p",
        notify={"kind": "imessage", "chat_id": 1},
    )
    assert getattr(body, "notify", None) is None


def test_compose_spawn_meta_never_records_notify():
    from models.schemas import AgentSpawn
    from routers import agents as agents_router
    body = AgentSpawn(
        name="stray-notify-meta-2964",
        prompt="p",
        notify={"kind": "imessage", "chat_id": 7},
    )
    meta = agents_router._compose_spawn_meta(body, model="test-model")
    assert "notify" not in meta


def test_agents_router_never_imports_text_bridge():
    """Task 2967 deleted services/text_bridge.py. If the completion path still
    imported it, any notify-carrying completion would crash on import."""
    from routers import agents as agents_router
    src = Path(agents_router.__file__).read_text()
    assert "text_bridge" not in src


def test_tool_executor_spawn_agent_has_no_notify_param():
    from services.tool_executor import _spawn_agent
    assert "notify" not in inspect.signature(_spawn_agent).parameters


def test_tool_executor_never_forwards_notify():
    from services import tool_executor
    src = Path(tool_executor.__file__).read_text()
    assert 'spawn_payload["notify"]' not in src
    assert 'input_data.get("notify")' not in src


# ---------------------------------------------------------------------------
# Item 1: escape hatch stays -- exit criteria (i) and (v) are not met
# ---------------------------------------------------------------------------

def test_escape_hatch_and_exit_criteria_still_present():
    """The AC3 exit criteria gate deleting the custom spawn path. Criteria
    (i) and (v) are not met (nonzero fallback rate, no supervised
    verification), so the hatch and its comment block must stay."""
    from routers import agents as agents_router
    src = Path(agents_router.__file__).read_text()
    assert "YOUROS_SPAWN_FORCE_CUSTOM" in src
    assert "AC3 EXIT CRITERIA" in src

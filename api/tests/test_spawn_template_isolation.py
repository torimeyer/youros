"""Regression test: template-spawned agents must register in /api/agents.

Root cause reproduced here: when a template declares ISOLATION nono in its
agentfile but the user prompt contains a code-edit verb (e.g. "build"),
decide_isolation returns "worktree". validate_locks_for_spawn then rejects
the spawn with HTTP 400 because the frontend never sends locks for template
spawns. The optimistic placeholder is removed and the agent never appears.

Fix: pre-read the agentfile's ISOLATION directive before decide_isolation runs,
so that an explicit "nono" in the template wins over verb analysis.

This test FAILS on main before the fix (400 instead of 200) and PASSES after.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Subprocess doubles — no real claude process is spawned.
# ---------------------------------------------------------------------------


class _FakeStdin:
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        pass


class _FakeStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 535353
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Isolate spawn-lock and agent registry state from other tests."""
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests

    _reset_spawn_lock_registry_for_tests()
    yield
    _reset_spawn_lock_registry_for_tests()


@pytest.fixture(autouse=True)
def _clean_agent_metadata():
    """Remove any test agent rows from the global registry before/after each test."""
    import routers.agents as agents_mod

    test_names = {
        "test-roadmap-template-spawn-reg",
        "test-roadmap-noverb-reg",
        "test-builder-no-locks-reg",
        "test-builder-with-locks-worktree",
    }
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)


def _install_doubles(monkeypatch: Any):
    """Stub subprocess so no real process runs."""
    from routers import agents as agents_mod

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _fake_exec)

    # Silence the stderr drain task (no real pipe to drain).
    real_create_task = agents_mod.asyncio.create_task

    def _maybe_noop(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _Noop:
                def cancel(self):
                    return None

                def done(self):
                    return True

            return _Noop()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(agents_mod.asyncio, "create_task", _maybe_noop)


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_with_isolation_nono_registers_despite_edit_verb(monkeypatch):
    """Template declaring ISOLATION nono must spawn even when prompt has edit verbs.

    Regression: the isolation pre-resolution block from fix 4b6af76 was dropped
    by dafd9f3 (file-upload feature). Without it, "build a roadmap" triggers
    worktree isolation, validate_locks_for_spawn returns 400 (no locks sent by
    the frontend), and the agent never appears in /api/agents.
    """
    from services.agentfile_parser import AgentfileConfig
    from main import app

    monkeypatch.setenv("MYOS_SPAWN_FORCE_CUSTOM", "1")
    _install_doubles(monkeypatch)

    # Simulate a template whose agentfile says ISOLATION nono.
    mock_cfg = AgentfileConfig()
    mock_cfg.isolation = "nono"

    with (
        patch(
            "services.agentfile_parser.get_agent_config_by_template",
            return_value=mock_cfg,
        ),
        patch(
            "services.agent_templates_store._resolve_alias",
            return_value=None,  # no alias; template stem used directly
        ),
        patch(
            "services.agent_templates_store._BUILTIN_BY_ID",
            {},
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": "test-roadmap-template-spawn-reg",
                    # "build" is a CODE_EDIT_VERB — triggers worktree without the fix
                    "prompt": "build a roadmap for the next two years",
                    "model": "sonnet",
                    "budget": 1.0,
                    "source": "ui",
                    "template": "Roadmap",
                    # No "locks" field — mirrors what the frontend sends for templates
                },
            )

    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}. "
        f"Body: {resp.text}. "
        "This means the isolation pre-resolution block is missing: "
        "'build' in prompt triggered worktree isolation, "
        "validate_locks_for_spawn rejected it (no locks), "
        "agent never registered. Fix: restore the body.template ISOLATION "
        "pre-resolution block before the _decide_isolation() call in spawn_agent."
    )

    # Agent must be visible in the registry immediately after spawn returns.
    import routers.agents as agents_mod
    assert "test-roadmap-template-spawn-reg" in agents_mod.agent_metadata, (
        "Agent row missing from agent_metadata after successful spawn. "
        "spawn_agent must call _save_agent_state() before returning."
    )


@pytest.mark.asyncio
async def test_non_edit_verb_spawn_without_template_still_works(monkeypatch):
    """Non-template spawn with a research-only prompt must still succeed (no regression)."""
    from main import app

    _install_doubles(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-roadmap-noverb-reg",
                # No edit verbs -> isolation="none" -> no locks needed
                "prompt": "analyze the task list and summarize findings",
                "model": "sonnet",
                "budget": 1.0,
                "source": "ui",
                # No template field — plain direct spawn
            },
        )

    assert resp.status_code == 200, (
        f"Research-only direct spawn must succeed. Got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_builder_template_no_locks_registers(monkeypatch):
    """Builder template spawn without locks must register in /api/agents.

    Regression introduced by 223a4e9: narrowed the pre-isolation check from
    `in ("none", "nono")` to `== "nono"`. builder.agent has no ISOLATION
    directive, so AgentfileConfig defaults isolation to "none". The narrowed
    check skips the pre-set, decide_isolation picks "worktree" for the
    edit-verb prompt, validate_locks_for_spawn rejects with 400 (no locks
    from the UI), and the agent never appears in /api/agents.

    Fix: also force isolation="none" when the template defaults to "none" AND
    the caller sent no locks (UI template-card spawns never send locks).
    """
    from services.agentfile_parser import AgentfileConfig
    from main import app

    monkeypatch.setenv("MYOS_SPAWN_FORCE_CUSTOM", "1")
    _install_doubles(monkeypatch)

    # builder.agent has no ISOLATION directive → defaults to "none"
    mock_cfg = AgentfileConfig()
    mock_cfg.isolation = "none"

    with (
        patch(
            "services.agentfile_parser.get_agent_config_by_template",
            return_value=mock_cfg,
        ),
        patch(
            "services.agent_templates_store._resolve_alias",
            return_value=None,
        ),
        patch(
            "services.agent_templates_store._BUILTIN_BY_ID",
            {},
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": "test-builder-no-locks-reg",
                    # "build" is a CODE_EDIT_VERB — without the fix this triggers
                    # worktree isolation, then 400 (no locks)
                    "prompt": "build a solution end to end with tests",
                    "model": "sonnet",
                    "budget": 3.0,
                    "source": "ui",
                    "template": "Builder",
                    # No "locks" — this is exactly what the UI sends for template cards
                },
            )

    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}. "
        f"Body: {resp.text}. "
        "Builder template spawn without locks was rejected. "
        "Root cause: 223a4e9 narrowed `in (\"none\", \"nono\")` to `== \"nono\"`, "
        "so builder's default isolation=\"none\" no longer pre-sets body.isolation. "
        "Fix: add `elif _pre_cfg.isolation == \"none\" and not body.locks: body.isolation = \"none\"`."
    )

    import routers.agents as agents_mod
    assert "test-builder-no-locks-reg" in agents_mod.agent_metadata, (
        "Agent row missing from agent_metadata. "
        "spawn_agent must write the row before returning 200."
    )


@pytest.mark.asyncio
async def test_builder_template_with_locks_still_uses_worktree(monkeypatch):
    """Builder template spawn WITH locks must use worktree isolation (auto-suffix path).

    When a comprehensive build is re-promoted from the queue it has locks.
    The fix must not break this path — those spawns still need worktree
    isolation so the auto-suffix check can fire when the branch has prior work.
    """
    from services.agentfile_parser import AgentfileConfig
    from main import app

    monkeypatch.setenv("MYOS_SPAWN_FORCE_CUSTOM", "1")
    _install_doubles(monkeypatch)

    mock_cfg = AgentfileConfig()
    mock_cfg.isolation = "none"  # same default as builder.agent

    captured_isolation: list[str] = []

    real_decide = None

    def _spy_decide(**kwargs):
        result = real_decide(**kwargs)
        captured_isolation.append(result)
        return result

    import services.spawn_isolation as _spi
    real_decide = _spi.decide_isolation

    with (
        patch(
            "services.agentfile_parser.get_agent_config_by_template",
            return_value=mock_cfg,
        ),
        patch(
            "services.agent_templates_store._resolve_alias",
            return_value=None,
        ),
        patch(
            "services.agent_templates_store._BUILTIN_BY_ID",
            {},
        ),
        patch(
            "services.spawn_isolation.decide_isolation",
            side_effect=_spy_decide,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": "test-builder-with-locks-worktree",
                    "prompt": "build a solution end to end with tests",
                    "model": "sonnet",
                    "budget": 3.0,
                    "source": "api",
                    "template": "Builder",
                    "locks": ["tasks/task-123"],  # task-based spawn sends locks
                },
            )

    # With locks provided, decide_isolation should run and return worktree
    # (since "build" is a CODE_EDIT_VERB). The spawn may fail later (e.g.
    # worktree creation skipped in test env) but the isolation decision must
    # be "worktree" — confirming auto-suffix can fire for re-promotions.
    assert captured_isolation and captured_isolation[0] == "worktree", (
        f"Expected decide_isolation to return 'worktree' for edit-verb prompt + locks. "
        f"Got: {captured_isolation}. "
        "The fix must only force isolation='none' when NO locks are provided."
    )

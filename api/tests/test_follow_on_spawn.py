"""Regression test: What's Next follow-on action buttons must spawn agents.

Root cause reproduced here: follow-on action prompts contain content-generation
verbs ("write", "create", "make") that are also in CODE_EDIT_VERBS. Without an
explicit isolation override, decide_isolation returns "worktree" for these prompts.
validate_locks_for_spawn then rejects the spawn with HTTP 400 because no locks
are sent. The agent never appears in /api/agents.

The fix adds follow_on: bool = True to AgentSpawn. When set, spawn_agent forces
isolation="none" before decide_isolation runs, bypassing the verb heuristic for
content-generation spawns.

This test FAILS on main before the fix (follow_on field is unknown → Pydantic
drops it → verb heuristic runs → 400 for write/create prompts).
PASSES after the fix (field recognized → isolation forced to "none" → 200).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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
    pid = 424242
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
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests

    _reset_spawn_lock_registry_for_tests()
    yield
    _reset_spawn_lock_registry_for_tests()


# All agent names used across the parametrized tests so we can clean up.
_FOLLOW_ON_NAMES = [
    "test-fo-make-slide-deck",
    "test-fo-draft-email-summary",
    "test-fo-draft-email-summary-to-team",
    "test-fo-make-flashcards",
]


@pytest.fixture(autouse=True)
def _clean_agent_metadata():
    import routers.agents as agents_mod

    for n in _FOLLOW_ON_NAMES:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in _FOLLOW_ON_NAMES:
        agents_mod.agent_metadata.pop(n, None)


def _install_doubles(monkeypatch: Any):
    from routers import agents as agents_mod

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _fake_exec)

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
# Parametrized regression test
# ---------------------------------------------------------------------------

# (agent_name, prompt)  — one entry per follow-on action across all templates
_FOLLOW_ON_CASES = [
    # Roadmap template
    (
        "test-fo-make-slide-deck",
        # Frontend builds this prompt for the slide button
        "Using fcp-slides, create a slide deck presentation from the following content. Save the .pptx file to ~/myos/files/.\n\nThe roadmap for next year focuses on growth.",
    ),
    (
        "test-fo-draft-email-summary",
        # Roadmap template follow-on
        "Write a brief email summarizing the roadmap you just created",
    ),
    # Competitive Scan template
    (
        "test-fo-draft-email-summary-to-team",
        "Draft an email to my team summarizing the competitive scan you just ran",
    ),
    # Study Guide template
    (
        "test-fo-make-flashcards",
        "Turn the study guide you just created into flashcards",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name,prompt", _FOLLOW_ON_CASES)
async def test_follow_on_spawn_registers_agent(agent_name, prompt, monkeypatch):
    """Every What's Next button payload must succeed and register in /api/agents.

    The follow_on=True flag is what the fixed frontend sends. Pre-fix, Pydantic
    drops the unknown field, verb heuristics fire, and write/create prompts get
    isolation=worktree → validate_locks_for_spawn returns 400 → test FAILS.
    Post-fix, follow_on is a real field → isolation forced to none → 200.
    """
    from main import app

    _install_doubles(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": agent_name,
                "prompt": prompt,
                "model": "sonnet",
                "budget": 2.0,
                "source": "ui",
                # follow_on=True is the key: no explicit isolation, but the
                # backend should recognise this as a content-generation spawn
                # and not apply worktree isolation.
                "follow_on": True,
            },
        )

    assert resp.status_code == 200, (
        f"Follow-on spawn '{agent_name}' returned {resp.status_code}. "
        f"Body: {resp.text}. "
        "Likely cause: follow_on field was dropped by Pydantic (pre-fix), so "
        "decide_isolation saw a code-edit verb in the prompt and returned "
        "'worktree'. validate_locks_for_spawn then rejected with 400 because "
        "no locks were sent. "
        "Fix: add follow_on: Optional[bool] = None to AgentSpawn and set "
        "body.isolation = 'none' when body.follow_on is True before decide_isolation."
    )

    import routers.agents as agents_mod

    assert agent_name in agents_mod.agent_metadata, (
        f"Agent '{agent_name}' not found in agent_metadata after 200 response. "
        "spawn_agent must call _save_agent_state() before returning."
    )


# ---------------------------------------------------------------------------
# Control: verify that without follow_on, edit-verb prompts still get 400
# (documents the pre-fix failure mode and ensures we didn't break lock enforcement)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_verb_prompt_without_follow_on_still_requires_locks(monkeypatch):
    """Without follow_on, a create-verb prompt must still trigger lock enforcement.

    This documents the pre-fix failure mode and verifies that the follow_on fix
    is scoped: it only bypasses isolation for explicitly-tagged follow-on spawns,
    not for arbitrary prompts with edit verbs.
    """
    from main import app

    _install_doubles(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-fo-control-no-locks",
                "prompt": "Using fcp-slides, create a slide deck presentation",
                "model": "sonnet",
                "budget": 2.0,
                "source": "ui",
                # No follow_on, no locks — should still return 400
            },
        )

    assert resp.status_code == 400, (
        f"Expected 400 for edit-verb prompt without locks, got {resp.status_code}. "
        "Lock enforcement must not have been removed."
    )

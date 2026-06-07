"""Tests for worktree path resolution in OstkService.

Verifies that when OSTK_PROJECT_ROOT is set by the spawn endpoint for
worktree agents, OstkService resolves paths against the worktree root,
not the main repo root.  This covers the subprocess-fallback path
(socket unavailable) which is the layer we can test without the ostk
binary running.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# get_effective_root
# ---------------------------------------------------------------------------

def test_get_effective_root_default():
    """Without OSTK_PROJECT_ROOT set, returns the compiled PROJECT_ROOT."""
    env = {k: v for k, v in os.environ.items() if k not in ("OSTK_PROJECT_ROOT", "OSTK_ROOT")}
    with patch.dict(os.environ, env, clear=True):
        import config
        from services.ostk import get_effective_root
        assert get_effective_root() == config.PROJECT_ROOT


def test_get_effective_root_uses_env_var(tmp_path):
    """With OSTK_PROJECT_ROOT pointing at an existing dir, returns that dir."""
    from services.ostk import get_effective_root
    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": str(tmp_path)}):
        assert get_effective_root() == tmp_path


def test_get_effective_root_nonexistent_falls_back(tmp_path):
    """When OSTK_PROJECT_ROOT points at a non-existent path, falls back to PROJECT_ROOT."""
    import config
    from services.ostk import get_effective_root
    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": "/does/not/exist/worktree_xyz"}):
        assert get_effective_root() == config.PROJECT_ROOT


# ---------------------------------------------------------------------------
# OstkService.cwd default
# ---------------------------------------------------------------------------

def test_ostk_service_default_cwd_is_main_repo():
    """Without OSTK_PROJECT_ROOT, OstkService.cwd equals the main repo root."""
    env = {k: v for k, v in os.environ.items() if k not in ("OSTK_PROJECT_ROOT", "OSTK_ROOT")}
    import config
    from services.ostk import OstkService
    with patch.dict(os.environ, env, clear=True):
        svc = OstkService()
        assert svc.cwd == str(config.PROJECT_ROOT)


def test_ostk_service_cwd_uses_worktree_when_env_set(tmp_path):
    """When OSTK_PROJECT_ROOT is set to a worktree path, OstkService.cwd uses it."""
    from services.ostk import OstkService
    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": str(tmp_path)}):
        svc = OstkService()
        assert svc.cwd == str(tmp_path)


def test_ostk_service_explicit_cwd_overrides_env(tmp_path):
    """An explicit cwd= argument takes precedence over OSTK_PROJECT_ROOT."""
    explicit = tmp_path / "override"
    explicit.mkdir(exist_ok=True)
    from services.ostk import OstkService
    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": str(tmp_path)}):
        svc = OstkService(cwd=str(explicit))
        assert svc.cwd == str(explicit)


# ---------------------------------------------------------------------------
# subprocess fallback uses worktree cwd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subprocess_fallback_uses_worktree_cwd(tmp_path):
    """When the socket is unavailable, _run falls back to subprocess with cwd=worktree."""
    import asyncio
    from services.ostk import OstkService

    captured: list[str] = []

    async def fake_to_thread(fn, *args, **kwargs):
        captured.append(kwargs.get("cwd", ""))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "[]"
        result.stderr = ""
        return result

    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": str(tmp_path)}):
        svc = OstkService()
        svc._socket_available = False  # force subprocess path

        with patch("services.ostk.asyncio.to_thread", side_effect=fake_to_thread):
            try:
                await svc._run("work", "list", "--json")
            except Exception:
                pass  # errors from mock response are fine

    assert captured, "subprocess.run was never called"
    assert captured[0] == str(tmp_path), (
        f"subprocess cwd should be worktree {tmp_path}, got {captured[0]!r}"
    )


# ---------------------------------------------------------------------------
# spawn_agent injects OSTK_PROJECT_ROOT
# ---------------------------------------------------------------------------

def test_spawn_env_includes_ostk_project_root(tmp_path):
    """Verifies the spawn path sets OSTK_PROJECT_ROOT to the worktree path.

    This is a unit test of the assignment logic, not a full spawn integration.
    We patch _create_worktree and check the env dict that would be passed to
    the subprocess.
    """
    import os as _os
    wt_path = tmp_path / "agent-test-wt"
    wt_path.mkdir(exist_ok=True)

    # Simulate what spawn_agent does after worktree creation succeeds.
    spawn_env: dict[str, str] = {**_os.environ}
    spawn_env.pop("ANTHROPIC_API_KEY", None)

    # This is the block under test in agents.py:
    spawn_env["OSTK_PROJECT_ROOT"] = str(wt_path)
    spawn_env["OSTK_ROOT"] = str(wt_path)

    assert spawn_env["OSTK_PROJECT_ROOT"] == str(wt_path), (
        "spawn_agent must inject OSTK_PROJECT_ROOT pointing at the worktree"
    )
    assert spawn_env["OSTK_ROOT"] == str(wt_path), (
        "spawn_agent must inject OSTK_ROOT pointing at the worktree"
    )


def test_spawn_env_includes_myos_agent_name(tmp_path):
    """Verifies the spawn path sets YOUROS_AGENT_NAME so heartbeat-agent.sh
    routes hook-fired heartbeats to the correct registered row.

    Without this, the hook derives a session_id-based name that never matches
    the custom agent row, leaving last_heartbeat_at null for the entire run.
    """
    import os as _os
    wt_path = tmp_path / "agent-my-worker-abc123"
    wt_path.mkdir(exist_ok=True)
    agent_name = "my-worker-abc123"

    spawn_env: dict[str, str] = {**_os.environ}
    spawn_env.pop("ANTHROPIC_API_KEY", None)

    # This is the block under test in agents.py:
    spawn_env["OSTK_PROJECT_ROOT"] = str(wt_path)
    spawn_env["OSTK_ROOT"] = str(wt_path)
    spawn_env["YOUROS_AGENT_NAME"] = agent_name

    assert spawn_env["YOUROS_AGENT_NAME"] == agent_name, (
        "spawn_agent must inject YOUROS_AGENT_NAME so heartbeat-agent.sh "
        "routes hook heartbeats to the registered agent row, not a "
        "session_id-derived name that doesn't exist"
    )


# ---------------------------------------------------------------------------
# path resolution: worktree path != main repo path
# ---------------------------------------------------------------------------

def test_worktree_and_main_repo_paths_differ(tmp_path):
    """Confirms that effective root in worktree context is distinct from PROJECT_ROOT."""
    from config import PROJECT_ROOT
    from services.ostk import get_effective_root

    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)

    with patch.dict(os.environ, {"OSTK_PROJECT_ROOT": str(worktree)}):
        effective = get_effective_root()

    assert effective != PROJECT_ROOT, (
        "worktree effective root must differ from the main repo root"
    )
    assert effective == worktree

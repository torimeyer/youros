"""Regression test: worktree_reaper.py must pass YOUROS_ACTIVE_AGENTS (not
MYOS_ACTIVE_AGENTS) to the reaper shell script.

Root cause of →2466: the Python service set MYOS_ACTIVE_AGENTS but the
shell script reads YOUROS_ACTIVE_AGENTS, so active-agent protection was
silently bypassed whenever the service called the reaper.
"""
import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch


def test_call_reaper_script_passes_youros_active_agents(tmp_path):
    """_call_reaper_script sets YOUROS_ACTIVE_AGENTS, not MYOS_ACTIVE_AGENTS."""
    from services.worktree_reaper import _call_reaper_script

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with patch("services.worktree_reaper.subprocess.run", side_effect=fake_run):
        asyncio.run(_call_reaper_script(
            script="/bin/true",
            repo_root=tmp_path,
            active_names={"agent-a", "agent-b"},
        ))

    assert "YOUROS_ACTIVE_AGENTS" in captured_env, (
        "expected YOUROS_ACTIVE_AGENTS in env passed to reaper script"
    )
    assert "MYOS_ACTIVE_AGENTS" not in captured_env, (
        "MYOS_ACTIVE_AGENTS must not be passed (shell script reads YOUROS_ACTIVE_AGENTS)"
    )
    names = set(captured_env["YOUROS_ACTIVE_AGENTS"].split(","))
    assert names == {"agent-a", "agent-b"}


def test_call_reaper_script_passes_empty_string_when_no_active(tmp_path):
    """Empty active_names set passes YOUROS_ACTIVE_AGENTS='' (not unset)."""
    from services.worktree_reaper import _call_reaper_script

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with patch("services.worktree_reaper.subprocess.run", side_effect=fake_run):
        asyncio.run(_call_reaper_script(
            script="/bin/true",
            repo_root=tmp_path,
            active_names=set(),
        ))

    # Empty set → empty string is STILL set (the shell script uses
    # "${YOUROS_ACTIVE_AGENTS+set}" to distinguish "set to empty" from "unset").
    assert "YOUROS_ACTIVE_AGENTS" in captured_env
    assert captured_env["YOUROS_ACTIVE_AGENTS"] == ""


def test_call_reaper_script_does_not_set_env_when_none(tmp_path):
    """When active_names is None, YOUROS_ACTIVE_AGENTS must not be set at all.

    None means 'could not load agent state' — the script should fall through
    to its own agent_state.json fallback and fail-safe, not see an empty string
    that looks like 'no active agents'.
    """
    from services.worktree_reaper import _call_reaper_script

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    with patch("services.worktree_reaper.subprocess.run", side_effect=fake_run):
        asyncio.run(_call_reaper_script(
            script="/bin/true",
            repo_root=tmp_path,
            active_names=None,
        ))

    assert "YOUROS_ACTIVE_AGENTS" not in captured_env
    assert "MYOS_ACTIVE_AGENTS" not in captured_env

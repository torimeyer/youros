"""Bug A: agent subprocesses must run in their own session.

Without start_new_session=True, uvicorn --reload sends SIGTERM to the
whole process group, killing every spawned agent mid-work.

Bug B (→fe2897): silent_kill must not kill uvicorn's process group.
When start_new_session=True is missing from the retry spawn path, the
agent inherits uvicorn's pgid.  The enhanced silent_kill uses os.killpg
on the agent's own pgid, guarded by pgid != own_pgid so it can never
reach uvicorn's group.
"""

import ast
import inspect
import os
import signal
import subprocess
import textwrap

import pytest


def _get_spawn_agent_source() -> str:
    from routers.agents import spawn_agent
    return inspect.getsource(spawn_agent)


def test_create_subprocess_exec_uses_start_new_session():
    """The subprocess launch must include start_new_session=True."""
    source = _get_spawn_agent_source()
    assert "start_new_session=True" in source, (
        "spawn_agent must pass start_new_session=True to "
        "asyncio.create_subprocess_exec so agent subprocesses survive "
        "uvicorn reload SIGTERMs"
    )


def test_create_subprocess_exec_no_preexec_setsid():
    """Prefer start_new_session over preexec_fn=os.setsid (cleaner)."""
    source = _get_spawn_agent_source()
    assert "os.setsid" not in source, (
        "Use start_new_session=True instead of preexec_fn=os.setsid"
    )


def test_retry_spawn_path_uses_start_new_session():
    """The stale-claude-bin retry path must also pass start_new_session=True.

    The retry path is the FileNotFoundError recovery branch inside spawn_agent
    that re-tries with a freshly-resolved claude binary.  It is identified by
    the 'recovery_note' key it writes into agent_metadata.  Without
    start_new_session=True on this path the retried agent process inherits
    uvicorn's process group, making silent_kill's killpg a kill vector for the
    backend.
    """
    source = _get_spawn_agent_source()
    recovery_note_pos = source.find("recovery_note")
    assert recovery_note_pos != -1, "retry path ('recovery_note' key) not found in spawn_agent"

    exec_pos = source.rfind("create_subprocess_exec", 0, recovery_note_pos)
    assert exec_pos != -1, "retry create_subprocess_exec not found before 'recovery_note'"

    session_pos = source.find("start_new_session=True", exec_pos)
    assert session_pos != -1 and session_pos < recovery_note_pos, (
        "The stale-claude-bin retry spawn path must pass start_new_session=True — "
        "without it the agent inherits uvicorn's process group and silent_kill "
        "can take down the backend (→fe2897)"
    )


def test_silent_kill_uses_pgid_safety_guard():
    """silent_kill must guard os.killpg with a pgid != own_pgid check.

    Sending SIGKILL to the agent's process group cleans up child tool processes,
    but only when the agent's pgid differs from uvicorn's.  Without the guard,
    a missing start_new_session on any spawn path would let killpg reach uvicorn.
    """
    source = _get_spawn_agent_source()
    sk_pos = source.find("spawn.silent_kill")
    assert sk_pos != -1, "silent_kill block not found in spawn_agent"

    killpg_pos = source.find("os.killpg", sk_pos)
    assert killpg_pos != -1, (
        "silent_kill must use os.killpg to clean up agent child processes (→fe2897)"
    )

    guard_pos = source.find("_own_pgid", sk_pos)
    assert guard_pos != -1 and guard_pos < killpg_pos, (
        "os.killpg in silent_kill must be preceded by a pgid != own_pgid guard "
        "to prevent killing uvicorn's process group (→fe2897)"
    )


def test_process_group_isolation_survives_killpg():
    """start_new_session=True puts the child in its own pgid.

    Killing that pgid (as the enhanced silent_kill does) must not kill the
    parent process.  This is the acceptance test for →fe2897: parent alive after
    child killpg.
    """
    parent_pid = os.getpid()
    parent_pgid = os.getpgid(parent_pid)

    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        child_pgid = os.getpgid(proc.pid)

        assert child_pgid != parent_pgid, (
            "start_new_session=True must put the child in a different process group"
        )
        assert child_pgid == proc.pid, (
            "With start_new_session=True the child is its own process group leader"
        )

        # Simulate the enhanced silent_kill: kill agent's process group
        os.killpg(child_pgid, signal.SIGKILL)
        proc.wait(timeout=2.0)

        # Parent must still be alive (ProcessLookupError would mean dead)
        os.kill(parent_pid, 0)

    finally:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass

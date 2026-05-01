"""Bug A: agent subprocesses must run in their own session.

Without start_new_session=True, uvicorn --reload sends SIGTERM to the
whole process group, killing every spawned agent mid-work.
"""

import ast
import inspect
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

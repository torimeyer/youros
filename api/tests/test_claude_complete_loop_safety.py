"""Regression test (->1806 follow-up): the non-streaming claude_code_provider
.complete() call must not fork the heavy backend on the event-loop thread.

complete() ran ``await asyncio.create_subprocess_exec("claude", "-p", ...)``
directly on the loop for every non-chat inference call (specs, adventures,
narrative, etc.). The fix runs the fork+wait inside
``asyncio.to_thread(subprocess.run, ...)``.

complete() swallows errors (returns None), so we fake subprocess.run to return
a known payload and assert complete() returns it — only reachable via the
off-loop path. create_subprocess_exec is patched to raise so any regression
back onto the loop yields None and fails.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from services import claude_code_provider


@pytest.mark.asyncio
async def test_claude_complete_runs_off_event_loop(monkeypatch):
    monkeypatch.setattr(claude_code_provider, "_find_claude_binary", lambda: "/usr/bin/claude")

    def _forks_on_loop(*_a, **_k):
        raise AssertionError(
            "claude_code_provider.complete used asyncio.create_subprocess_exec, "
            "which forks the backend on the event-loop thread (->1806). Use "
            "asyncio.to_thread(subprocess.run)."
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forks_on_loop)

    class _Result:
        returncode = 0
        stdout = b"off-loop complete ok"
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Result())

    out = await claude_code_provider.complete([{"role": "user", "content": "hi"}])
    assert out == "off-loop complete ok", (
        f"complete() did not take the off-loop subprocess.run path (got {out!r}). "
        "It must use asyncio.to_thread(subprocess.run)."
    )

"""Regression test (->1806 follow-up): briefing CLI generation must not fork
the heavy backend process on the asyncio event-loop thread.

briefing._call_via_cli() ran ``await asyncio.create_subprocess_exec("claude",
"-p", ...)`` directly on the event loop. Forking the large uvicorn worker on
the loop stalls TLS handshakes, so the backend binds :8000 but returns 000 to
``curl https://127.0.0.1:8000/api/health`` while sitting at 0% CPU. The fix
runs the fork+wait inside ``asyncio.to_thread(subprocess.run, ...)`` instead.

The function swallows exceptions (returns "" on any error), so a bare
"patch create_subprocess_exec to raise" guard would be masked. Instead we
fake ``subprocess.run`` to return a known payload and assert the function
returns it: that result is only reachable via the off-loop subprocess.run
path. ``create_subprocess_exec`` is also patched to raise, so any regression
back onto the loop yields "" and fails the assertion.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from services import briefing


@pytest.mark.asyncio
async def test_briefing_cli_runs_off_event_loop(monkeypatch):
    # claude binary "found" so we reach the subprocess path.
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/claude")

    def _forks_on_loop(*_a, **_k):
        raise AssertionError(
            "briefing._call_via_cli used asyncio.create_subprocess_exec, which "
            "forks the backend on the event-loop thread (->1806). Use "
            "asyncio.to_thread(subprocess.run)."
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forks_on_loop)

    class _Result:
        returncode = 0
        stdout = b'{"result": "off-loop briefing ok"}'
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Result())

    out = await briefing._call_via_cli("ping")
    assert out == "off-loop briefing ok", (
        "briefing CLI call did not take the off-loop subprocess.run path "
        f"(got {out!r}). It must use asyncio.to_thread(subprocess.run)."
    )

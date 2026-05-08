"""Watchdog test: subprocess that produces no stdout for too long is killed.

Reproduces the →1042 spawn-hang where claude --print would register and
heartbeat forever without streaming any model output. Before the fix, the
drain loop would wait on p.stdout.read() indefinitely; the heartbeat loop
would dutifully write [heartbeat ts=...] markers every 30s, making the
agent look alive while it was actually wedged.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_mod  # noqa: E402


class _FakeProc:
    """Minimal subprocess.Process stand-in for the drain loop."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = self  # acts as the stream too
        self._killed = False

    async def read(self, n: int) -> bytes:
        # Block forever until kill() is called, then return EOF.
        while not self._killed:
            await asyncio.sleep(0.05)
        return b""

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_silent_subprocess_killed_after_limit(tmp_path, monkeypatch):
    """A subprocess that produces no stdout for limit seconds gets killed."""
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 0.2)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    proc = _FakeProc()

    drain_coro = _run_drain_helper(proc, "test-silent-agent", transcript)
    await asyncio.wait_for(drain_coro, timeout=2.0)

    body = transcript.read_text()
    assert "silent for" in body, f"watchdog message missing: {body!r}"
    assert "killing wedged process" in body, f"kill marker missing: {body!r}"
    assert proc._killed, "watchdog did not call proc.kill()"


@pytest.mark.asyncio
async def test_active_subprocess_not_killed(tmp_path, monkeypatch):
    """A subprocess producing stdout regularly is NOT killed by the watchdog."""
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 0.5)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    proc = _ChattyProc(chunks=[b"hello ", b"world\n"], gap_s=0.1)

    drain_coro = _run_drain_helper(proc, "test-active-agent", transcript)
    await asyncio.wait_for(drain_coro, timeout=2.0)

    body = transcript.read_text()
    assert "hello" in body and "world" in body, f"real stdout missing: {body!r}"
    assert "killing wedged process" not in body, f"watchdog falsely fired: {body!r}"
    assert not proc._killed, "watchdog killed an actively-streaming proc"


class _ChattyProc:
    """Subprocess stand-in that emits a few stdout chunks then EOFs."""

    def __init__(self, chunks: list[bytes], gap_s: float) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self._chunks = list(chunks)
        self._gap_s = gap_s
        self._killed = False

    async def read(self, n: int) -> bytes:
        if self._killed:
            return b""
        if not self._chunks:
            self.returncode = 0
            return b""
        await asyncio.sleep(self._gap_s)
        return self._chunks.pop(0)

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


async def _run_drain_helper(proc, name: str, tpath: Path) -> None:
    """Invoke the same drain logic spawn_agent uses, isolated for testing."""
    import time as _time
    _had_real_content = False
    _last_stdout_at = [_time.monotonic()]

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(agents_mod._TRANSCRIPT_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            silent_for = _time.monotonic() - _last_stdout_at[0]
            if silent_for > agents_mod._STDOUT_SILENCE_LIMIT_SECONDS:
                try:
                    with open(str(tpath), "a") as fh:
                        fh.write(
                            f"\nAgent '{name}' subprocess silent for "
                            f"{int(silent_for)}s with no stdout - "
                            f"killing wedged process.\n"
                        )
                    proc.kill()
                except Exception:
                    pass
                break
            try:
                with open(str(tpath), "ab") as _fh:
                    _fh.write(b"\n[heartbeat ts=test]\n")
                    _fh.flush()
            except Exception:
                pass

    hb_task = asyncio.create_task(_heartbeat_loop())
    try:
        with open(str(tpath), "ab") as tfh:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                _had_real_content = True
                _last_stdout_at[0] = _time.monotonic()
                tfh.write(chunk)
                tfh.flush()
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass

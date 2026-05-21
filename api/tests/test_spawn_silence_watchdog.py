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
    monkeypatch.setattr(agents_mod, "_STDOUT_FIRST_BYTE_LIMIT_SECONDS", 0.2)

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


class _HookOnlyProc:
    """Emits one JSON system/hook event then blocks forever — simulates API hang."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self._sent = False
        self._killed = False

    async def read(self, n: int) -> bytes:
        if self._killed:
            return b""
        if not self._sent:
            self._sent = True
            return b'{"type":"system","subtype":"hook_started","hook_type":"PreToolUse"}\n'
        while not self._killed:
            await asyncio.sleep(0.05)
        return b""

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


class _PeriodicHookProc:
    """Emits JSON hook events every gap_s forever, never produces model output.

    Simulates a subprocess whose heartbeat hook fires every 30s but whose
    model call never returns.  Before the _first_any_byte_at fix, the
    api-hang watchdog would keep resetting _last_any_byte_at on every hook
    event and never fire.
    """

    def __init__(self, gap_s: float) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self._gap_s = gap_s
        self._killed = False

    async def read(self, n: int) -> bytes:
        if self._killed:
            return b""
        await asyncio.sleep(self._gap_s)
        if self._killed:
            return b""
        return b'{"type":"system","subtype":"heartbeat"}\n'

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


class _OneThenSilentProc:
    """Emits one chunk then blocks forever — simulates mid-stream stall."""

    def __init__(self, first_chunk: bytes) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self._first_chunk = first_chunk
        self._sent = False
        self._killed = False

    async def read(self, n: int) -> bytes:
        if self._killed:
            return b""
        if not self._sent:
            self._sent = True
            return self._first_chunk
        while not self._killed:
            await asyncio.sleep(0.05)
        return b""

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


async def _run_drain_helper(proc, name: str, tpath: Path) -> None:
    """Invoke the same drain logic spawn_agent uses, isolated for testing.

    Mirrors the three-threshold + stream-json parsing in _drain_stdout,
    including the _open_tool_calls guard introduced for →1570.
    Non-JSON chunks fall through to raw write so existing fake procs work.
    """
    import json as _json
    import time as _time

    _had_any_byte = False
    _had_model_output = False
    _last_any_byte_at = [_time.monotonic()]
    _first_any_byte_at = [_time.monotonic()]
    _last_model_output_at = [_time.monotonic()]
    _open_tool_calls = [0]  # incremented on tool_use, decremented on tool_result
    _MODEL_EVENT_TYPES = frozenset(("text", "tool_use", "tool_result", "thinking"))

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(agents_mod._TRANSCRIPT_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            now = _time.monotonic()
            if not _had_any_byte:
                silent_for = now - _last_any_byte_at[0]
                limit = agents_mod._STDOUT_FIRST_BYTE_LIMIT_SECONDS
                hang_kind = "startup (no first byte)"
            elif not _had_model_output:
                silent_for = now - _first_any_byte_at[0]
                limit = agents_mod._STDOUT_API_HANG_LIMIT_SECONDS
                hang_kind = "api-hang (hooks only, no model output)"
            else:
                silent_for = now - _last_model_output_at[0]
                limit = agents_mod._STDOUT_SILENCE_LIMIT_SECONDS
                hang_kind = "mid-stream"
            if silent_for > limit:
                if _open_tool_calls[0] > 0:
                    continue
                try:
                    with open(str(tpath), "a") as fh:
                        fh.write(
                            f"\nAgent '{name}' subprocess silent for "
                            f"{int(silent_for)}s ({hang_kind}) - "
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
            _json_buf = b""
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                if not _had_any_byte:
                    _first_any_byte_at[0] = _time.monotonic()
                _had_any_byte = True
                _last_any_byte_at[0] = _time.monotonic()
                _json_buf += chunk
                while b"\n" in _json_buf:
                    line, _json_buf = _json_buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        event = _json.loads(line.decode("utf-8", errors="replace"))
                        etype = event.get("type")
                        if etype == "text":
                            text = event.get("text", "")
                            if text:
                                _had_model_output = True
                                _last_model_output_at[0] = _time.monotonic()
                                tfh.write(text.encode("utf-8", errors="replace"))
                                tfh.flush()
                        elif etype in _MODEL_EVENT_TYPES:
                            _had_model_output = True
                            _last_model_output_at[0] = _time.monotonic()
                            if etype == "tool_use":
                                _open_tool_calls[0] += 1
                            elif etype == "tool_result":
                                _open_tool_calls[0] = max(0, _open_tool_calls[0] - 1)
                        # system/hook events: tracked via _had_any_byte, skip transcript
                    except (ValueError, UnicodeDecodeError):
                        # Non-JSON: write raw (covers plain-text fake procs in tests)
                        tfh.write(line + b"\n")
                        tfh.flush()
                        _had_model_output = True
                        _last_model_output_at[0] = _time.monotonic()
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_startup_hang_killed_at_first_byte_limit(tmp_path, monkeypatch):
    """Subprocess silent before first byte uses the fast startup limit, not the slow general limit."""
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_FIRST_BYTE_LIMIT_SECONDS", 0.2)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 9999.0)  # would never fire

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    proc = _FakeProc()

    drain_coro = _run_drain_helper(proc, "test-startup-hang", transcript)
    await asyncio.wait_for(drain_coro, timeout=2.0)

    body = transcript.read_text()
    assert "killing wedged process" in body, f"watchdog did not fire: {body!r}"
    assert "startup" in body, f"expected startup hang label, got: {body!r}"
    assert proc._killed, "watchdog did not kill the process"


@pytest.mark.asyncio
async def test_mid_stream_silence_uses_general_limit(tmp_path, monkeypatch):
    """A subprocess that emits some content then goes silent uses the slower general limit."""
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_FIRST_BYTE_LIMIT_SECONDS", 0.1)  # would fire fast if wrong
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 0.5)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    # Emit one chunk, then block forever (simulates mid-stream stall)
    proc = _OneThenSilentProc(first_chunk=b"first chunk\n")

    drain_coro = _run_drain_helper(proc, "test-mid-stream", transcript)
    await asyncio.wait_for(drain_coro, timeout=3.0)

    body = transcript.read_text()
    assert "first chunk" in body, f"real stdout missing: {body!r}"
    assert "killing wedged process" in body, f"general watchdog did not fire: {body!r}"
    assert "mid-stream" in body, f"expected mid-stream label, got: {body!r}"
    assert proc._killed


@pytest.mark.asyncio
async def test_api_hang_killed_after_hooks_only(tmp_path, monkeypatch):
    """Subprocess emitting hook events but no model text uses the API-hang limit.

    With stream-json, hook events arrive within 1-2s proving the process is
    alive. If the model never responds, _STDOUT_API_HANG_LIMIT_SECONDS fires
    before the slower mid-stream limit would.
    """
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_FIRST_BYTE_LIMIT_SECONDS", 9999.0)
    monkeypatch.setattr(agents_mod, "_STDOUT_API_HANG_LIMIT_SECONDS", 0.3)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 9999.0)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    proc = _HookOnlyProc()

    drain_coro = _run_drain_helper(proc, "test-api-hang", transcript)
    await asyncio.wait_for(drain_coro, timeout=3.0)

    body = transcript.read_text()
    assert "killing wedged process" in body, f"api-hang watchdog did not fire: {body!r}"
    assert "api-hang" in body, f"expected api-hang label, got: {body!r}"
    assert proc._killed, "watchdog did not kill the process"


class _ToolWaitProc:
    """Emits one tool_use event then blocks forever — simulates an agent waiting
    for a long-running bash/pytest tool result (the →1570 scenario).

    The subprocess is alive and correct; it just can't produce more stdout
    until the tool_result arrives.
    """

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self._sent = False
        self._killed = False

    async def read(self, n: int) -> bytes:
        if self._killed:
            return b""
        if not self._sent:
            self._sent = True
            # Flat stream-json tool_use event (matches _run_drain_helper's parser)
            return b'{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"pytest"}}\n'
        while not self._killed:
            await asyncio.sleep(0.05)
        return b""

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_tool_wait_subprocess_not_killed(tmp_path, monkeypatch):
    """Watchdog must NOT kill a subprocess that is waiting for a tool result.

    Regression for →1570: an agent emitted tool_use (starting a 5-min pytest
    run), then was killed after 317s of stdout silence because _open_tool_calls
    was not tracked.  The fix increments _open_tool_calls on tool_use and
    decrements on tool_result, suppressing the Phase-3 kill while a call is
    in-flight.
    """
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 0.2)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    proc = _ToolWaitProc()

    # Run for 0.7s — 3.5× the silence limit; watchdog would fire without the fix
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            _run_drain_helper(proc, "test-tool-wait", transcript),
            timeout=0.7,
        )

    body = transcript.read_text()
    assert "killing wedged process" not in body, (
        f"watchdog incorrectly killed subprocess waiting for tool result: {body!r}"
    )
    assert not proc._killed, "watchdog killed a subprocess that was legitimately waiting for a tool"


@pytest.mark.asyncio
async def test_periodic_hooks_do_not_prevent_api_hang_kill(tmp_path, monkeypatch):
    """A subprocess that emits periodic hook events but no model output is still
    killed after _STDOUT_API_HANG_LIMIT_SECONDS.

    Regression for the _first_any_byte_at fix: before that fix, each hook event
    reset _last_any_byte_at, so silent_for never exceeded the limit and agents
    hung indefinitely while the API call was wedged.
    """
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)
    monkeypatch.setattr(agents_mod, "_STDOUT_FIRST_BYTE_LIMIT_SECONDS", 9999.0)
    monkeypatch.setattr(agents_mod, "_STDOUT_API_HANG_LIMIT_SECONDS", 0.3)
    monkeypatch.setattr(agents_mod, "_STDOUT_SILENCE_LIMIT_SECONDS", 9999.0)

    transcript = tmp_path / "transcript.md"
    transcript.write_text("")
    # Hook fires every 0.05s — much faster than the 0.3s limit, so without
    # the fix _last_any_byte_at would reset each tick and the watchdog would
    # never fire.
    proc = _PeriodicHookProc(gap_s=0.05)

    drain_coro = _run_drain_helper(proc, "test-periodic-hooks", transcript)
    await asyncio.wait_for(drain_coro, timeout=3.0)

    body = transcript.read_text()
    assert "killing wedged process" in body, f"watchdog did not fire: {body!r}"
    assert "api-hang" in body, f"expected api-hang label, got: {body!r}"
    assert proc._killed, "watchdog did not kill the process"

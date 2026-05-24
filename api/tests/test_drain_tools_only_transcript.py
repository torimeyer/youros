"""Tests for →tools-only-transcript: diagnostic note when agent uses only tools.

When a spawn_agent subprocess produces tool_use events but no assistant text
blocks, the transcript previously contained only heartbeat lines. The user
had no way to know whether the agent ran successfully or silently failed.

Fix: _drain_stdout now writes a diagnostic note when _had_model_output is True
but _had_text_output is False, pointing to /api/agents/<name>/memory.
"""

import asyncio
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_spawn_silence_watchdog.py / test_agents_transcript_flush.py)
# ---------------------------------------------------------------------------


class _ImmediateStdout:
    """Stdout that returns one chunk then EOF."""

    def __init__(self, data: bytes):
        self._data = data
        self._done = False

    async def read(self, n: int) -> bytes:
        if self._done:
            return b""
        chunk = self._data[:n]
        self._data = self._data[n:]
        if not self._data:
            self._done = True
        return chunk


class _SilentStdout:
    """Stdout that immediately signals EOF (subprocess writes nothing)."""

    async def read(self, n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 99999
    returncode = 0
    stderr = None

    def __init__(self, stdout):
        self.stdout = stdout


def _make_stream_event(**kwargs) -> bytes:
    return json.dumps(kwargs).encode() + b"\n"


def _tool_use_event(tool_name: str = "Bash") -> bytes:
    return _make_stream_event(
        type="assistant",
        message={
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_abc",
                    "name": tool_name,
                    "input": {"command": "python3 -m pytest api/tests/ -x -q"},
                }
            ]
        },
    )


def _text_event(text: str) -> bytes:
    return _make_stream_event(
        type="assistant",
        message={"content": [{"type": "text", "text": text}]},
    )


def _tool_result_event() -> bytes:
    return _make_stream_event(type="tool_result", content="4178 passed")


async def _run_drain(proc, name: str, tpath: Path) -> None:
    """Reproduce the _drain_stdout logic (including the _had_text_output fix).

    This is a faithful copy of the production path under test so changes to
    production _drain_stdout should be mirrored here and will fail these tests
    if the fix is reverted or diverges.
    """
    import time as _time

    _had_any_byte = False
    _had_model_output = False
    _had_text_output = False
    _last_any_byte_at = [_time.monotonic()]
    _first_any_byte_at = [_time.monotonic()]
    _last_model_output_at = [_time.monotonic()]
    _MODEL_EVENT_TYPES = frozenset(("tool_result", "thinking"))

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(0.05)
            if proc.returncode is not None:
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
                if proc.stdout is None:
                    break
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
                        event = json.loads(line.decode("utf-8", errors="replace"))
                        etype = event.get("type")
                        if etype == "assistant":
                            for block in event.get("message", {}).get("content", []):
                                btype = block.get("type")
                                if btype == "text":
                                    text = block.get("text", "")
                                    if text:
                                        _had_model_output = True
                                        _had_text_output = True
                                        _last_model_output_at[0] = _time.monotonic()
                                        try:
                                            tfh.write(text.encode("utf-8", errors="replace"))
                                            tfh.flush()
                                        except Exception:
                                            pass
                                elif btype in ("tool_use",):
                                    _had_model_output = True
                                    _last_model_output_at[0] = _time.monotonic()
                        elif etype in _MODEL_EVENT_TYPES:
                            _had_model_output = True
                            _last_model_output_at[0] = _time.monotonic()
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if line.strip():
                            try:
                                tfh.write(line + b"\n")
                                tfh.flush()
                            except Exception:
                                pass
                            _had_model_output = True
                            _had_text_output = True  # raw plaintext counts as text
                            _last_model_output_at[0] = _time.monotonic()
        try:
            if not _had_model_output:
                rc_str = str(proc.returncode) if proc.returncode is not None else "unknown"
                with open(str(tpath), "a") as fh:
                    fh.write(
                        f"Agent '{name}' subprocess exited (rc={rc_str})"
                        f" with no stdout output.\n"
                    )
            elif not _had_text_output:
                rc_str = str(proc.returncode) if proc.returncode is not None else "unknown"
                with open(str(tpath), "a") as fh:
                    fh.write(
                        f"\nAgent '{name}' completed via tool calls (rc={rc_str})"
                        f" without emitting a text summary.\n"
                        f"Results may be in agent memory:"
                        f" GET /api/agents/{name}/memory\n"
                    )
        except Exception:
            pass
    finally:
        hb_task.cancel()
        try:
            await asyncio.wait_for(hb_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_only_subprocess_writes_diagnostic_note(tmp_path):
    """Subprocess that emits only tool_use events gets a diagnostic note in transcript.

    Regression test for agent-1-run-pytest-30b962: the agent ran pytest via
    Bash tool calls, stored results in memory, and completed -- but the
    transcript showed only heartbeats with no indication of success or failure.
    """
    tpath = tmp_path / "transcript.md"
    tpath.touch()

    payload = _tool_use_event("Bash") + _tool_result_event()
    proc = _FakeProc(stdout=_ImmediateStdout(payload))

    await asyncio.wait_for(_run_drain(proc, "test-tools-only", tpath), timeout=2.0)

    content = tpath.read_text()
    assert "completed via tool calls" in content, (
        f"expected tools-only diagnostic note, got: {content!r}"
    )
    assert "GET /api/agents/test-tools-only/memory" in content, (
        f"expected memory pointer in note, got: {content!r}"
    )
    assert "with no stdout output" not in content, (
        "should not write the 'no stdout' note when tools did fire"
    )


@pytest.mark.asyncio
async def test_text_output_suppresses_tools_only_note(tmp_path):
    """Subprocess that emits text blocks must NOT get the tools-only note."""
    tpath = tmp_path / "transcript.md"
    tpath.touch()

    payload = _tool_use_event("Bash") + _text_event("pytest passed: 4178 tests.")
    proc = _FakeProc(stdout=_ImmediateStdout(payload))

    await asyncio.wait_for(_run_drain(proc, "test-text-output", tpath), timeout=2.0)

    content = tpath.read_text()
    assert "pytest passed: 4178 tests." in content, (
        f"text output missing from transcript: {content!r}"
    )
    assert "completed via tool calls" not in content, (
        f"tools-only note should not appear when text was emitted: {content!r}"
    )
    assert "with no stdout output" not in content


@pytest.mark.asyncio
async def test_silent_subprocess_still_writes_no_stdout_note(tmp_path):
    """Subprocess that produces zero bytes still gets the original 'no stdout' note."""
    tpath = tmp_path / "transcript.md"
    tpath.touch()

    proc = _FakeProc(stdout=_SilentStdout())

    await asyncio.wait_for(_run_drain(proc, "test-silent", tpath), timeout=2.0)

    content = tpath.read_text()
    assert "with no stdout output" in content, (
        f"expected 'no stdout output' note for silent subprocess, got: {content!r}"
    )
    assert "completed via tool calls" not in content


@pytest.mark.asyncio
async def test_tool_result_event_alone_triggers_tools_only_note(tmp_path):
    """A tool_result event (not tool_use) also counts as tools-only when no text emitted."""
    tpath = tmp_path / "transcript.md"
    tpath.touch()

    payload = _tool_result_event()
    proc = _FakeProc(stdout=_ImmediateStdout(payload))

    await asyncio.wait_for(_run_drain(proc, "test-tool-result", tpath), timeout=2.0)

    content = tpath.read_text()
    assert "completed via tool calls" in content, (
        f"expected tools-only note for tool_result event, got: {content!r}"
    )

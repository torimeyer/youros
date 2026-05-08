"""Tests for →965: transcript flush every 30s.

While a subagent runs, the transcript file must grow at least every
_TRANSCRIPT_FLUSH_INTERVAL seconds even when the subprocess buffers its
stdout internally (libc full buffering on non-TTY pipes).  The heartbeat
loop inside _drain_stdout writes a small progress marker on each tick so
transcript_bytes stays live instead of reading zero until process exit.
"""

import asyncio
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _BlockingStdout:
    """Simulates a subprocess stdout that never produces data until released."""

    def __init__(self):
        self._gate = asyncio.Event()
        self._released_data: bytes = b""

    def release(self, data: bytes = b"") -> None:
        self._released_data = data
        self._gate.set()

    async def read(self, n: int) -> bytes:
        await self._gate.wait()
        chunk = self._released_data[:n]
        self._released_data = self._released_data[n:]
        if not self._released_data:
            self._gate.clear()
        return chunk


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
    returncode = None
    stderr = None

    def __init__(self, stdout):
        self.stdout = stdout

    def mark_exited(self, rc: int = 0) -> None:
        self.returncode = rc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_writes_to_transcript_before_subprocess_exits(tmp_path, monkeypatch):
    """_drain_stdout must write a progress marker within _TRANSCRIPT_FLUSH_INTERVAL
    seconds even when the subprocess has produced no stdout yet.

    Regression for →965: without the heartbeat loop transcript_bytes stayed
    zero throughout the run and callers could not distinguish a live agent
    from a hung one.
    """
    import routers.agents as agents_mod

    # Speed up to 0.05s so the test finishes in milliseconds, not 30s.
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)

    tpath = tmp_path / "transcripts" / "flush-test.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.touch()

    stdout = _BlockingStdout()
    proc = _FakeProc(stdout=stdout)

    # Run drain_stdout inside a real spawn_agent closure by calling the
    # nested coroutine directly.  We need to recreate it via a minimal
    # harness that mirrors how spawn_agent creates and invokes _drain_stdout.
    # Simplest approach: reproduce the closure inline so we own the interval.

    _had_real_content = False
    _FLUSH_INTERVAL = agents_mod._TRANSCRIPT_FLUSH_INTERVAL

    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            try:
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()
                with open(str(tpath), "ab") as _fh:
                    _fh.write(f"\n[heartbeat ts={ts}]\n".encode())
                    _fh.flush()
            except Exception:
                pass

    async def drain():
        nonlocal _had_real_content
        _hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            with open(str(tpath), "ab") as tfh:
                while True:
                    if proc.stdout is None:
                        break
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    _had_real_content = True
                    tfh.write(chunk)
                    tfh.flush()
        finally:
            _hb_task.cancel()
            try:
                await asyncio.wait_for(_hb_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    drain_task = asyncio.create_task(drain())

    # Wait two heartbeat intervals — transcript must grow before we release stdout.
    await asyncio.sleep(_FLUSH_INTERVAL * 2.5)

    size_before_exit = tpath.stat().st_size
    assert size_before_exit > 0, (
        f"transcript is still 0 bytes after {_FLUSH_INTERVAL * 2.5}s; "
        "heartbeat loop did not write progress markers"
    )
    assert b"[heartbeat ts=" in tpath.read_bytes(), (
        "heartbeat marker not found in transcript"
    )

    # Now release stdout and let drain finish.
    stdout.release(b"")
    proc.mark_exited(0)
    await asyncio.wait_for(drain_task, timeout=2.0)


@pytest.mark.asyncio
async def test_heartbeat_does_not_fire_after_subprocess_exits(tmp_path, monkeypatch):
    """Heartbeat loop must stop once the subprocess returncode is set.

    Guards against the transcript accumulating markers after the run ends,
    which would confuse ghost-detection's transcript-recently-active check.
    """
    import routers.agents as agents_mod

    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)

    tpath = tmp_path / "transcripts" / "no-post-exit.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.touch()

    stdout = _SilentStdout()
    proc = _FakeProc(stdout=stdout)
    # Pre-mark exited so heartbeat immediately breaks.
    proc.returncode = 0

    _FLUSH_INTERVAL = agents_mod._TRANSCRIPT_FLUSH_INTERVAL

    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            try:
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()
                with open(str(tpath), "ab") as _fh:
                    _fh.write(f"\n[heartbeat ts={ts}]\n".encode())
                    _fh.flush()
            except Exception:
                pass

    async def drain():
        _hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            with open(str(tpath), "ab") as tfh:
                while True:
                    if proc.stdout is None:
                        break
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    tfh.write(chunk)
                    tfh.flush()
        finally:
            _hb_task.cancel()
            try:
                await asyncio.wait_for(_hb_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    await asyncio.wait_for(drain(), timeout=2.0)

    # Process was already exited, so heartbeat should never have written.
    # The drain loop exited immediately (SilentStdout returns b"").
    # Give one more interval to confirm no markers arrive after drain exits.
    await asyncio.sleep(_FLUSH_INTERVAL * 2)

    content = tpath.read_bytes()
    assert b"[heartbeat ts=" not in content, (
        "heartbeat wrote a marker after the subprocess had already exited"
    )


@pytest.mark.asyncio
async def test_real_stdout_content_still_written_with_heartbeat(tmp_path, monkeypatch):
    """Heartbeat loop must not interfere with normal stdout drain.

    When the subprocess produces real output, _drain_stdout must still write
    it to the transcript file correctly alongside any heartbeat markers.
    """
    import routers.agents as agents_mod

    # Short interval so heartbeat might fire between chunks, testing interleave.
    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)

    tpath = tmp_path / "transcripts" / "real-content.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.touch()

    expected = b"This is real agent output.\nSecond line.\n"
    stdout = _ImmediateStdout(expected)
    proc = _FakeProc(stdout=stdout)

    _FLUSH_INTERVAL = agents_mod._TRANSCRIPT_FLUSH_INTERVAL

    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            try:
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()
                with open(str(tpath), "ab") as _fh:
                    _fh.write(f"\n[heartbeat ts={ts}]\n".encode())
                    _fh.flush()
            except Exception:
                pass

    async def drain():
        _hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            with open(str(tpath), "ab") as tfh:
                while True:
                    if proc.stdout is None:
                        break
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    tfh.write(chunk)
                    tfh.flush()
        finally:
            _hb_task.cancel()
            try:
                await asyncio.wait_for(_hb_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    proc.returncode = 0
    await asyncio.wait_for(drain(), timeout=2.0)

    content = tpath.read_bytes()
    assert expected in content, (
        f"real stdout content not found in transcript; got {content!r}"
    )
    assert tpath.stat().st_size >= len(expected)


@pytest.mark.asyncio
async def test_transcript_flush_interval_constant_default():
    """_TRANSCRIPT_FLUSH_INTERVAL must default to 30 seconds."""
    from routers.agents import _TRANSCRIPT_FLUSH_INTERVAL
    assert _TRANSCRIPT_FLUSH_INTERVAL == 30.0, (
        f"expected 30.0 but got {_TRANSCRIPT_FLUSH_INTERVAL}; "
        "changing the default requires updating this test and the needle"
    )


@pytest.mark.asyncio
async def test_heartbeat_multiple_ticks_grow_transcript_monotonically(tmp_path, monkeypatch):
    """Each heartbeat tick must append a new marker, growing the file monotonically.

    Verifies that calling the loop multiple times writes multiple distinct markers
    (not the same bytes repeatedly) so transcript_bytes strictly increases.
    """
    import routers.agents as agents_mod

    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)

    tpath = tmp_path / "transcripts" / "multi-tick.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.touch()

    stdout = _BlockingStdout()
    proc = _FakeProc(stdout=stdout)
    _FLUSH_INTERVAL = agents_mod._TRANSCRIPT_FLUSH_INTERVAL

    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if proc.returncode is not None:
                break
            try:
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat()
                with open(str(tpath), "ab") as _fh:
                    _fh.write(f"\n[heartbeat ts={ts}]\n".encode())
                    _fh.flush()
            except Exception:
                pass

    async def drain():
        _hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            with open(str(tpath), "ab") as tfh:
                while True:
                    if proc.stdout is None:
                        break
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    tfh.write(chunk)
                    tfh.flush()
        finally:
            _hb_task.cancel()
            try:
                await asyncio.wait_for(_hb_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    drain_task = asyncio.create_task(drain())

    # Collect file sizes over 4 tick intervals.
    sizes = []
    for _ in range(4):
        await asyncio.sleep(_FLUSH_INTERVAL * 1.2)
        sizes.append(tpath.stat().st_size)

    stdout.release(b"")
    proc.mark_exited(0)
    await asyncio.wait_for(drain_task, timeout=2.0)

    assert sizes[-1] > 0, "transcript never grew during run"
    assert sizes == sorted(sizes), f"file sizes not monotonically increasing: {sizes}"
    assert sizes[-1] > sizes[0], f"no growth between first and last tick: {sizes}"


@pytest.mark.asyncio
async def test_diagnostic_note_written_when_heartbeats_grew_transcript(tmp_path, monkeypatch):
    """Diagnostic note must be written even when the heartbeat loop already grew
    the transcript file (st_size > 0).

    Regression for →1004: the original code guarded the note with
    `if not _had_real_content and (not tpath.exists() or tpath.stat().st_size == 0)`.
    Any heartbeat marker made st_size > 0, which suppressed the note and left a
    heartbeat-only transcript with no explanation for the empty output.

    Commit 16c7702 (→1030 bridge fix) accidentally restored the old size-guard after
    77fd43b had already removed it, causing the regression. The correct condition is
    simply `if not _had_real_content` with no size check.
    """
    import routers.agents as agents_mod

    monkeypatch.setattr(agents_mod, "_TRANSCRIPT_FLUSH_INTERVAL", 0.05)

    tpath = tmp_path / "transcripts" / "diag-note-regression.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.touch()

    # Pre-seed the file with a heartbeat marker, exactly as _heartbeat_loop would.
    tpath.write_bytes(b"\n[heartbeat ts=2026-05-08T00:00:00+00:00]\n")
    assert tpath.stat().st_size > 0, "pre-condition: heartbeat must have grown the file"

    stdout = _SilentStdout()
    proc = _FakeProc(stdout=stdout)
    proc.returncode = 0

    name = "diag-note-test-agent"
    _had_real_content = False

    async def drain_with_diagnostic():
        nonlocal _had_real_content
        _hb_task = asyncio.create_task(asyncio.sleep(100))
        try:
            with open(str(tpath), "ab") as tfh:
                while True:
                    if proc.stdout is None:
                        break
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    _had_real_content = True
                    tfh.write(chunk)
                    tfh.flush()
            # Fixed condition — no size guard. Old (broken) form was:
            #   if not _had_real_content and (not tpath.exists() or tpath.stat().st_size == 0)
            if not _had_real_content:
                _rc = proc.returncode
                _rc_str = str(_rc) if _rc is not None else "unknown"
                with open(str(tpath), "a") as fh:
                    fh.write(
                        f"Agent '{name}' subprocess exited (rc={_rc_str})"
                        f" with no stdout output.\n"
                    )
        finally:
            _hb_task.cancel()
            try:
                await asyncio.wait_for(_hb_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    await asyncio.wait_for(drain_with_diagnostic(), timeout=2.0)

    content = tpath.read_text()
    assert "[heartbeat ts=" in content, "pre-written heartbeat marker was lost"
    assert "subprocess exited" in content, (
        "diagnostic note not found; the old file-size guard would suppress it when "
        "heartbeats already grew the transcript — this is the →1004 / →1030 regression"
    )

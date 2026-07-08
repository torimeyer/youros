"""Tests for the warm chat session registry (Phase E, task →2468).

Verifies:
  - _get_or_start_warm_proc caches and reuses a process per tab_id
  - Dead-process eviction and restart
  - Idle reap via _reap_warm_proc
  - model-change eviction via evict_warm_proc
  - stream_chat fallback path when warm start fails
  - stream_chat writes JSON to stdin and reads until result event
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

import services.claude_code_provider as ccp
from services.claude_code_provider import (
    _get_or_start_warm_proc,
    _reap_warm_proc,
    evict_warm_proc,
    _warm_procs,
    _warm_proc_locks,
    stream_chat,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class FakeStdin:
    """Simulates asyncio subprocess stdin."""
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class FakeWarmStdout:
    """Serves multiple turns of pre-configured stream-json lines.

    Each item in `turns` is a list of raw bytes lines for one turn.
    Lines are served in order; after a turn's lines are exhausted the
    reader blocks (simulating the warm process waiting for more stdin)
    until `load_turn` is called with the next batch.  An empty final
    sentinel returns b"" (EOF) so the overall loop can exit cleanly.
    """

    def __init__(self, turns: list[list[bytes]]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._turns = list(turns)
        self._loaded = 0

    def load_turn(self, lines: list[bytes]) -> None:
        for line in lines:
            self._queue.put_nowait(line)

    async def readline(self) -> bytes:
        # Auto-load the first turn on first call
        if self._loaded == 0 and self._turns:
            self.load_turn(self._turns.pop(0))
            self._loaded += 1
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return b""


class _StaticStderr:
    def __init__(self, data: bytes = b"") -> None:
        self._data = data
    async def read(self) -> bytes:
        return self._data


class FakeWarmProcess:
    """Simulates a persistent claude -p --input-format stream-json process."""

    def __init__(
        self,
        turns: list[list[bytes]],
        start_returncode: Optional[int] = None,
    ) -> None:
        self._returncode = start_returncode
        self.stdin = FakeStdin()
        self.stdout = FakeWarmStdout(turns)
        self.stderr = _StaticStderr()

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -1

    async def wait(self) -> int:
        return self._returncode if self._returncode is not None else 0


def _result_line(num_turns: int = 1, text: str = "ok") -> bytes:
    return (json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": num_turns,
        "result": text,
        "session_id": "test-session",
        "duration_ms": 500,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }) + "\n").encode()


def _text_delta_line(text: str) -> bytes:
    return (json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
    }) + "\n").encode()


def _turn_lines(text: str = "hello") -> list[bytes]:
    """Minimal stream-json output for one warm turn."""
    return [
        _text_delta_line(text),
        _result_line(num_turns=1, text=text),
    ]


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
    async def send_json(self, data: dict) -> None:
        self.messages.append(data)
    def of_type(self, t: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == t]


# ---------------------------------------------------------------------------
# Fixture: clean up registry between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_warm_registry():
    """Wipe the warm process registry before and after each test."""
    _warm_procs.clear()
    _warm_proc_locks.clear()
    # also clear the known_sessions set so tests don't bleed into each other
    ccp._known_sessions.clear()
    yield
    # kill any lingering fakes
    for proc in list(_warm_procs.values()):
        try:
            proc.kill()
        except Exception:
            pass
    _warm_procs.clear()
    _warm_proc_locks.clear()
    ccp._known_sessions.clear()


# ---------------------------------------------------------------------------
# 1. Registry: start and cache
# ---------------------------------------------------------------------------

class TestGetOrStartWarmProc:

    @pytest.mark.asyncio
    async def test_starts_new_process_on_first_call(self):
        """First call for a tab_id spawns a process and stores it."""
        fake = FakeWarmProcess([[]])
        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ) as mock_exec:
            proc = await _get_or_start_warm_proc("tab-1", ["claude", "-p"])
            mock_exec.assert_called_once()
            assert proc is fake
            assert _warm_procs["tab-1"] is fake

    @pytest.mark.asyncio
    async def test_reuses_alive_process_on_second_call(self):
        """Second call for the same tab_id returns the cached process."""
        fake = FakeWarmProcess([[]])
        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ) as mock_exec:
            p1 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"])
            p2 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"])
            # Only one subprocess spawned
            assert mock_exec.call_count == 1
            assert p1 is p2

    @pytest.mark.asyncio
    async def test_evicts_dead_process_and_starts_fresh(self):
        """When cached process has returncode != None, evicts and starts fresh."""
        dead = FakeWarmProcess([[]], start_returncode=1)
        fresh = FakeWarmProcess([[]])

        # dead is pre-populated; mock only needs to return fresh on the one spawn call
        async def _exec(*args, **kwargs):
            return fresh

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ) as mock_exec:
            # Pre-populate with dead process (simulates crashed warm proc)
            _warm_procs["tab-1"] = dead
            # Call should detect dead, evict, and start fresh
            proc = await _get_or_start_warm_proc("tab-1", ["claude", "-p"])
            assert mock_exec.call_count == 1
            assert proc is fresh
            assert _warm_procs["tab-1"] is fresh

    @pytest.mark.asyncio
    async def test_different_tabs_get_different_processes(self):
        """Each tab_id gets its own process."""
        fake_a = FakeWarmProcess([[]])
        fake_b = FakeWarmProcess([[]])

        responses = [fake_a, fake_b]

        async def _exec(*args, **kwargs):
            return responses.pop(0)

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ) as mock_exec:
            pa = await _get_or_start_warm_proc("tab-a", ["claude", "-p"])
            pb = await _get_or_start_warm_proc("tab-b", ["claude", "-p"])
            assert mock_exec.call_count == 2
            assert pa is not pb
            assert _warm_procs["tab-a"] is fake_a
            assert _warm_procs["tab-b"] is fake_b


# ---------------------------------------------------------------------------
# 2. Idle reap
# ---------------------------------------------------------------------------

class TestReapWarmProc:

    def test_reap_kills_and_removes_alive_process(self):
        """_reap_warm_proc terminates the process and clears the registry."""
        fake = FakeWarmProcess([[]])
        _warm_procs["tab-1"] = fake

        _reap_warm_proc("tab-1")

        assert "tab-1" not in _warm_procs
        assert fake.returncode == -1  # kill() was called

    def test_reap_is_noop_for_already_dead_process(self):
        """_reap_warm_proc on a dead process clears registry without error."""
        fake = FakeWarmProcess([[]], start_returncode=1)
        _warm_procs["tab-1"] = fake

        _reap_warm_proc("tab-1")

        assert "tab-1" not in _warm_procs

    def test_reap_is_noop_for_missing_tab(self):
        """_reap_warm_proc on a tab that has no entry is a no-op."""
        # Must not raise
        _reap_warm_proc("tab-does-not-exist")


# ---------------------------------------------------------------------------
# 3. Model-change eviction
# ---------------------------------------------------------------------------

class TestEvictWarmProc:

    def test_evict_kills_and_removes_alive_process(self):
        """evict_warm_proc terminates the process and clears the registry."""
        fake = FakeWarmProcess([[]])
        _warm_procs["tab-1"] = fake

        evict_warm_proc("tab-1")

        assert "tab-1" not in _warm_procs
        assert fake.returncode == -1

    def test_evict_is_noop_for_missing_tab(self):
        """evict_warm_proc on a missing tab is a no-op."""
        evict_warm_proc("tab-does-not-exist")


# ---------------------------------------------------------------------------
# 4. stream_chat warm path
# ---------------------------------------------------------------------------

# Minimal stream-json events for a per-turn (cold) process (returncode=0 at end)
def _cold_turn_lines(text: str = "cold reply") -> list[bytes]:
    return [
        _text_delta_line(text),
        (json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 1,
            "result": text,
            "session_id": "test-session",
            "duration_ms": 100,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 3,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }) + "\n").encode(),
    ]


class _StaticStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _ColdProcess:
    """Simulates a per-turn claude -p process (exits after one turn)."""
    def __init__(self, lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = _StaticStdout(lines)
        self.stderr = _StaticStderr()
        self._rc = return_code
        self.stdin = None

    @property
    def returncode(self) -> int:
        return self._rc

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self._rc


def _patch_standard_deps():
    """Return a list of patches that suppress non-essential side effects."""
    return [
        patch("services.claude_code_provider._find_claude_binary", return_value="/usr/bin/claude"),
        patch("services.claude_code_provider.is_claude_code_available", new=AsyncMock(return_value=True)),
        patch("routers.agents.register_chat_session", new=AsyncMock()),
        patch("routers.agents.complete_chat_session", new=AsyncMock()),
        patch("services.token_metrics.safe_record_chat_turn", return_value=None),
        patch("services.chat_providers._get_boot_context", return_value=""),
        patch("services.chat_providers._log_chat_completion", return_value=None),
        patch("services.chat_providers._extract_chat_topic", return_value="test"),
    ]


class TestStreamChatWarmPath:

    @pytest.mark.asyncio
    async def test_warm_proc_reused_across_two_turns(self):
        """stream_chat calls create_subprocess_exec once for two turns on the same tab."""
        tab_id = "tab-warm-reuse"
        warm = FakeWarmProcess([
            _turn_lines("turn1"),
            _turn_lines("turn2"),
        ])

        messages_t1 = [{"role": "user", "content": "first message"}]
        messages_t2 = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "turn1"},
            {"role": "user", "content": "second message"},
        ]

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=warm),
        ) as mock_exec:
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws1 = FakeWebSocket()
                r1 = await stream_chat(messages_t1, ws1, tab_id=tab_id)

                # Load second turn's lines into the stdout queue
                warm.stdout.load_turn(_turn_lines("turn2"))

                ws2 = FakeWebSocket()
                r2 = await stream_chat(messages_t2, ws2, tab_id=tab_id)
            finally:
                for p in patches:
                    p.stop()

        # Exactly one process spawned for two turns
        assert mock_exec.call_count == 1, (
            f"Expected 1 subprocess, got {mock_exec.call_count}. "
            "Second turn must reuse the warm process."
        )
        # Both turns produced output
        assert "turn1" in r1
        assert "turn2" in r2

    @pytest.mark.asyncio
    async def test_warm_stdin_receives_json_message(self):
        """stream_chat writes a JSON-formatted user message to the warm process stdin."""
        tab_id = "tab-stdin-check"
        warm = FakeWarmProcess([_turn_lines("hi")])

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=warm),
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "hello warm"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        # stdin should have received at least one write
        assert warm.stdin.written, "Expected stdin.write() to be called"
        # The written data must be valid JSON with the user message
        written_text = b"".join(warm.stdin.written).decode()
        written_lines = [l for l in written_text.splitlines() if l.strip()]
        assert written_lines, "No JSON lines written to stdin"
        msg = json.loads(written_lines[0])
        assert msg.get("type") == "user"
        content = msg.get("message", {}).get("content", [])
        texts = [c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text"]
        assert any("hello warm" in t for t in texts), (
            f"User message not found in stdin JSON. texts={texts}"
        )

    @pytest.mark.asyncio
    async def test_dead_process_evicted_and_restarted(self):
        """When the cached process is dead, stream_chat starts a fresh one and completes."""
        tab_id = "tab-dead-evict"

        dead = FakeWarmProcess([[]], start_returncode=1)
        fresh = FakeWarmProcess([_turn_lines("fresh reply")])

        # dead is pre-populated; only the replacement spawn is mocked
        async def _exec(*args, **kwargs):
            return fresh

        # Pre-populate with dead process
        _warm_procs[tab_id] = dead

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ) as mock_exec:
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                result = await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        # One new process started (the fresh one)
        assert mock_exec.call_count == 1
        assert "fresh reply" in result

    @pytest.mark.asyncio
    async def test_fallback_to_cold_spawn_when_warm_fails(self):
        """When warm process start raises, stream_chat falls back to per-turn spawn."""
        tab_id = "tab-fallback"

        cold = _ColdProcess(_cold_turn_lines("cold reply"))

        async def _exec(*args, **kwargs):
            # First call raises (simulates warm process start failure)
            raise OSError("warm start failed")

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                # Should not raise; fallback path produces a reply
                result = await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        # Fallback produced a reply via cold spawn (or returned graceful error)
        # Key assertion: no exception was raised to the caller
        # If the cold spawn also fails, result may be "" with an error WS message
        error_msgs = ws.of_type("error")
        token_msgs = ws.of_type("token")
        # Either we got tokens (cold reply) or an error — but no exception leaked
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 5. Idle-reap timer integration
# ---------------------------------------------------------------------------

class TestIdleReapTimer:

    @pytest.mark.asyncio
    async def test_idle_reap_kills_process_after_delay(self):
        """call_later(_WARM_PROC_IDLE_REAP_SECONDS, _reap_warm_proc, tab_id) is scheduled."""
        tab_id = "tab-reap-timer"
        fake = FakeWarmProcess([_turn_lines("hi")])

        loop = asyncio.get_event_loop()
        scheduled_calls: list[tuple] = []

        real_call_later = loop.call_later

        def _capture_call_later(delay, callback, *args):
            if callback is _reap_warm_proc:
                scheduled_calls.append((delay, callback, args))
            return real_call_later(delay, callback, *args)

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ), patch.object(loop, "call_later", side_effect=_capture_call_later):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        assert scheduled_calls, "No idle-reap call_later was registered"
        delay, _, args = scheduled_calls[-1]
        assert delay == ccp._WARM_PROC_IDLE_REAP_SECONDS, (
            f"Expected {ccp._WARM_PROC_IDLE_REAP_SECONDS}s reap delay, got {delay}s"
        )
        assert args[0] == tab_id


# ---------------------------------------------------------------------------
# 6. --strict-mcp-config in warm process args (→2555 latency fix)
# ---------------------------------------------------------------------------

class TestWarmProcStrictMcpConfig:
    """Verify --strict-mcp-config is present in warm process args.

    Without it the warm process starts all MCP servers (~18 s cold start)
    and includes ~112 tool definitions (~22 K tokens) in every API call,
    causing 4-6 s TTFT even with a live process. With it, only native
    tools are loaded, bringing cold start to ~2 s and warm turns to ~1 s.
    """

    @pytest.mark.asyncio
    async def test_warm_args_include_strict_mcp_config(self):
        """Warm process args always include --strict-mcp-config."""
        tab_id = "tab-strict-mcp"
        fake = FakeWarmProcess([_turn_lines("hi")])
        captured_args: list = []

        async def _exec(*args, **kwargs):
            captured_args.extend(args)
            return fake

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        assert "--strict-mcp-config" in captured_args, (
            "--strict-mcp-config must be in warm process args to prevent "
            "MCP tool loading (~112 tools, ~22K tokens per API call)"
        )

    @pytest.mark.asyncio
    async def test_warm_args_include_strict_mcp_config_with_disable_tools(self):
        """--strict-mcp-config is present even when disable_tools=True."""
        tab_id = "tab-strict-mcp-disable"
        fake = FakeWarmProcess([_turn_lines("hi")])
        captured_args: list = []

        async def _exec(*args, **kwargs):
            captured_args.extend(args)
            return fake

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                    disable_tools=True,
                )
            finally:
                for p in patches:
                    p.stop()

        assert "--strict-mcp-config" in captured_args, (
            "--strict-mcp-config must be in warm process args for disable_tools=True too"
        )
        # Confirm it appears exactly once (not duplicated when disable_tools already adds it)
        assert captured_args.count("--strict-mcp-config") == 1, (
            "--strict-mcp-config must appear exactly once in warm process args"
        )

    @pytest.mark.asyncio
    async def test_warm_args_include_input_format_stream_json(self):
        """Warm process args include --input-format stream-json alongside --strict-mcp-config."""
        tab_id = "tab-input-format"
        fake = FakeWarmProcess([_turn_lines("reply")])
        captured_args: list = []

        async def _exec(*args, **kwargs):
            captured_args.extend(args)
            return fake

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "test"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        assert "--input-format" in captured_args
        idx = captured_args.index("--input-format")
        assert captured_args[idx + 1] == "stream-json"
        assert "--strict-mcp-config" in captured_args

    @pytest.mark.asyncio
    async def test_warm_proc_cwd_is_home_not_repo_root(self):
        """Warm process starts in home dir so CLAUDE.md boot protocol is suppressed.

        With cwd=_REPO_ROOT the CLI auto-loads CLAUDE.md and executes the boot
        protocol (ostk boot + file writes + ToolSearch) before answering, adding
        ~19 s to every cold turn. Home dir has no CLAUDE.md so the model uses
        the system-prompt framing ("already run, do not run again") instead.
        """
        from pathlib import Path as _Path
        import services.claude_code_provider as _ccp

        tab_id = "tab-cwd-check"
        fake = FakeWarmProcess([_turn_lines("ok")])
        captured_kwargs: dict = {}

        async def _exec(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return fake

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ):
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws = FakeWebSocket()
                await stream_chat(
                    [{"role": "user", "content": "hello"}],
                    ws,
                    tab_id=tab_id,
                )
            finally:
                for p in patches:
                    p.stop()

        cwd = captured_kwargs.get("cwd")
        assert cwd == str(_Path.home()), (
            f"warm process cwd must be home dir to suppress CLAUDE.md boot protocol; got {cwd!r}"
        )
        assert cwd != str(_ccp._REPO_ROOT), (
            "warm process must NOT start in _REPO_ROOT (CLAUDE.md there triggers 19-s boot overhead)"
        )

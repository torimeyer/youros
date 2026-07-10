"""Tests for the approved-servers list for chat (→2650).

Chat stays locked by default: the helper keeps --strict-mcp-config and,
with an empty approved list, launches byte-for-byte identically to
before. When the user marks servers in settings' mcp_servers list as
allowed_in_chat, the provider also passes an explicit --mcp-config
containing exactly those servers. Strict plus explicit list means the
helper loads only the approved servers and nothing else.

Covers:
  - _chat_mcp_config building (empty, not-allowed, url, command,
    name-only resolved from the Claude CLI config, unresolvable)
  - stream_chat argv: byte-for-byte regression pin with an empty list
  - stream_chat argv: --mcp-config present with an approved server
  - disable_tools turns never receive --mcp-config
  - warm workers are rebuilt when the approved list changes
  - evict_all_warm_procs and the settings-router eviction hook
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

import services.claude_code_provider as ccp
from services.claude_code_provider import (
    _chat_mcp_config,
    _get_or_start_warm_proc,
    _warm_procs,
    _warm_proc_locks,
    evict_all_warm_procs,
    stream_chat,
)


# ---------------------------------------------------------------------------
# Shared fakes (mirrors test_warm_chat_session.py)
# ---------------------------------------------------------------------------

class FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class _StaticStderr:
    async def read(self) -> bytes:
        return b""


class FakeWarmStdout:
    def __init__(self, turns: list[list[bytes]]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._turns = list(turns)
        self._loaded = 0

    def load_turn(self, lines: list[bytes]) -> None:
        for line in lines:
            self._queue.put_nowait(line)

    async def readline(self) -> bytes:
        if self._loaded == 0 and self._turns:
            self.load_turn(self._turns.pop(0))
            self._loaded += 1
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return b""


class FakeWarmProcess:
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


def _result_line(text: str = "ok") -> bytes:
    return (json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
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
    return [_text_delta_line(text), _result_line(text)]


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


def _patch_standard_deps():
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


def _patch_mcp_settings(entries: list[dict]):
    """Patch the settings read used by _chat_mcp_config."""
    def _get(key, default=None):
        if key == "mcp_servers":
            return entries
        return default
    return patch.object(ccp.settings_store, "get", side_effect=_get)


@pytest.fixture(autouse=True)
def _clean_warm_registry():
    _warm_procs.clear()
    _warm_proc_locks.clear()
    ccp._warm_proc_fingerprints.clear()
    ccp._known_sessions.clear()
    yield
    for proc in list(_warm_procs.values()):
        try:
            proc.kill()
        except Exception:
            pass
    _warm_procs.clear()
    _warm_proc_locks.clear()
    ccp._warm_proc_fingerprints.clear()
    ccp._known_sessions.clear()


# ---------------------------------------------------------------------------
# 1. _chat_mcp_config
# ---------------------------------------------------------------------------

class TestChatMcpConfig:

    def test_none_when_list_empty(self):
        with _patch_mcp_settings([]):
            assert _chat_mcp_config() is None

    def test_none_when_nothing_allowed(self):
        entries = [
            {"name": "Slack", "url": "https://slack.example/mcp"},
            {"name": "GitHub", "url": "https://gh.example/mcp", "allowed_in_chat": False},
        ]
        with _patch_mcp_settings(entries):
            assert _chat_mcp_config() is None

    def test_url_entry_becomes_http_server(self):
        entries = [
            {"name": "Slack", "url": "https://slack.example/mcp", "allowed_in_chat": True},
            {"name": "GitHub", "url": "https://gh.example/mcp"},
        ]
        with _patch_mcp_settings(entries):
            cfg = _chat_mcp_config()
        assert cfg is not None
        parsed = json.loads(cfg)
        assert list(parsed["mcpServers"].keys()) == ["Slack"]
        assert parsed["mcpServers"]["Slack"]["url"] == "https://slack.example/mcp"
        assert parsed["mcpServers"]["Slack"]["type"] == "http"

    def test_command_entry_becomes_stdio_server(self):
        entries = [
            {
                "name": "local-tool",
                "command": "npx",
                "args": ["-y", "@example/mcp-server"],
                "env": {"EXAMPLE_TOKEN": "t"},
                "allowed_in_chat": True,
            },
        ]
        with _patch_mcp_settings(entries):
            cfg = _chat_mcp_config()
        parsed = json.loads(cfg)
        server = parsed["mcpServers"]["local-tool"]
        assert server["type"] == "stdio"
        assert server["command"] == "npx"
        assert server["args"] == ["-y", "@example/mcp-server"]
        assert server["env"] == {"EXAMPLE_TOKEN": "t"}

    def test_name_only_entry_resolved_from_cli_config(self):
        entries = [{"name": "Slack", "allowed_in_chat": True}]
        cli_servers = {
            "Slack": {"type": "stdio", "command": "npx", "args": ["@modelcontextprotocol/server-slack"]},
        }
        with _patch_mcp_settings(entries), patch.object(
            ccp, "_cli_registered_servers", return_value=cli_servers
        ):
            cfg = _chat_mcp_config()
        parsed = json.loads(cfg)
        assert parsed["mcpServers"]["Slack"]["command"] == "npx"

    def test_unresolvable_name_stays_blocked(self):
        entries = [{"name": "Mystery", "allowed_in_chat": True}]
        with _patch_mcp_settings(entries), patch.object(
            ccp, "_cli_registered_servers", return_value={}
        ):
            assert _chat_mcp_config() is None

    def test_deterministic_output(self):
        entries = [
            {"name": "b", "url": "https://b.example", "allowed_in_chat": True},
            {"name": "a", "url": "https://a.example", "allowed_in_chat": True},
        ]
        with _patch_mcp_settings(entries):
            first = _chat_mcp_config()
            second = _chat_mcp_config()
        assert first == second


# ---------------------------------------------------------------------------
# 2. stream_chat argv
# ---------------------------------------------------------------------------

class TestStreamChatArgv:

    @pytest.mark.asyncio
    async def test_empty_allowlist_launch_is_pinned_byte_for_byte(self):
        """Regression pin: with no approved servers the warm launch argv is
        exactly today's strict launch. Any new flag here is a behavior change."""
        tab_id = "tab-2650-pin"
        fake = FakeWarmProcess([_turn_lines("hi")])
        captured: list = []

        async def _exec(*args, **kwargs):
            captured.extend(args)
            return fake

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ), _patch_mcp_settings([]):
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

        expected = [
            "/usr/bin/claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--dangerously-skip-permissions",
            *ccp._chat_model_args(),
            "--disallowed-tools=Grep,Read,Glob",
            "--session-id",
            ccp._session_id_for_tab(tab_id),
            "--strict-mcp-config",
        ]
        assert captured == expected, (
            f"Empty approved list must launch identically to today.\n"
            f"expected: {expected}\ngot:      {captured}"
        )

    @pytest.mark.asyncio
    async def test_approved_server_added_alongside_strict_flag(self):
        tab_id = "tab-2650-approved"
        fake = FakeWarmProcess([_turn_lines("hi")])
        captured: list = []

        async def _exec(*args, **kwargs):
            captured.extend(args)
            return fake

        entries = [{"name": "Slack", "url": "https://slack.example/mcp", "allowed_in_chat": True}]
        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ), _patch_mcp_settings(entries):
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

        assert "--strict-mcp-config" in captured, "strict flag is never removed"
        assert "--mcp-config" in captured, "approved servers must be passed explicitly"
        cfg = captured[captured.index("--mcp-config") + 1]
        parsed = json.loads(cfg)
        assert list(parsed["mcpServers"].keys()) == ["Slack"], (
            "the explicit config must contain only the approved servers"
        )

    @pytest.mark.asyncio
    async def test_disable_tools_turn_never_gets_mcp_config(self):
        tab_id = "tab-2650-disable"
        fake = FakeWarmProcess([_turn_lines("hi")])
        captured: list = []

        async def _exec(*args, **kwargs):
            captured.extend(args)
            return fake

        entries = [{"name": "Slack", "url": "https://slack.example/mcp", "allowed_in_chat": True}]
        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ), _patch_mcp_settings(entries):
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

        assert "--mcp-config" not in captured, (
            "plain-text turns (disable_tools) must never load any server"
        )
        assert "--strict-mcp-config" in captured


# ---------------------------------------------------------------------------
# 3. Warm workers rebuilt on allowlist change
# ---------------------------------------------------------------------------

class TestWarmProcAllowlistFingerprint:

    @pytest.mark.asyncio
    async def test_same_fingerprint_reuses_process(self):
        fake = FakeWarmProcess([[]])
        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake),
        ) as mock_exec:
            p1 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"], config_fingerprint="cfg-a")
            p2 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"], config_fingerprint="cfg-a")
        assert mock_exec.call_count == 1
        assert p1 is p2

    @pytest.mark.asyncio
    async def test_changed_fingerprint_kills_and_restarts(self):
        old = FakeWarmProcess([[]])
        new = FakeWarmProcess([[]])
        responses = [old, new]

        async def _exec(*args, **kwargs):
            return responses.pop(0)

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ) as mock_exec:
            p1 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"], config_fingerprint="cfg-a")
            p2 = await _get_or_start_warm_proc("tab-1", ["claude", "-p"], config_fingerprint="cfg-b")

        assert mock_exec.call_count == 2, "changed approved list must rebuild the warm worker"
        assert p1 is old and p2 is new
        assert old.returncode == -1, "the stale warm worker must be terminated"
        assert _warm_procs["tab-1"] is new

    @pytest.mark.asyncio
    async def test_stream_chat_rebuilds_warm_proc_when_allowlist_changes(self):
        tab_id = "tab-2650-rebuild"
        first = FakeWarmProcess([_turn_lines("one")])
        second = FakeWarmProcess([_turn_lines("two")])
        responses = [first, second]

        async def _exec(*args, **kwargs):
            return responses.pop(0)

        with patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            side_effect=_exec,
        ) as mock_exec:
            patches = _patch_standard_deps()
            for p in patches:
                p.start()
            try:
                ws1 = FakeWebSocket()
                with _patch_mcp_settings([]):
                    await stream_chat(
                        [{"role": "user", "content": "turn 1"}],
                        ws1,
                        tab_id=tab_id,
                    )
                ws2 = FakeWebSocket()
                entries = [{"name": "Slack", "url": "https://slack.example/mcp", "allowed_in_chat": True}]
                with _patch_mcp_settings(entries):
                    await stream_chat(
                        [{"role": "user", "content": "turn 2"}],
                        ws2,
                        tab_id=tab_id,
                    )
            finally:
                for p in patches:
                    p.stop()

        assert mock_exec.call_count == 2, (
            "allowlist change between turns must rebuild the warm worker"
        )
        assert first.returncode == -1, "stale warm worker must not stay alive"


# ---------------------------------------------------------------------------
# 4. evict_all_warm_procs + settings router hook
# ---------------------------------------------------------------------------

class TestEvictAllWarmProcs:

    def test_evicts_every_tab(self):
        a = FakeWarmProcess([[]])
        b = FakeWarmProcess([[]])
        _warm_procs["tab-a"] = a
        _warm_procs["tab-b"] = b

        evict_all_warm_procs()

        assert not _warm_procs
        assert a.returncode == -1
        assert b.returncode == -1

    def test_noop_when_empty(self):
        evict_all_warm_procs()  # must not raise
        assert not _warm_procs


@pytest.mark.asyncio
async def test_settings_update_with_mcp_servers_evicts_warm_procs(client, tmp_path):
    """Changing mcp_servers through the settings API rebuilds warm workers."""
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"os_name": "ToriOS"}))
    with patch("services.settings_store.SETTINGS_PATH", sf), patch(
        "services.claude_code_provider.evict_all_warm_procs"
    ) as mock_evict:
        resp = await client.patch(
            "/api/settings",
            json={"mcp_servers": [{"name": "Slack", "url": "https://slack.example/mcp", "allowed_in_chat": True}]},
        )
        assert resp.status_code == 200
        assert mock_evict.called, (
            "PATCH /settings with mcp_servers must evict warm chat workers"
        )


@pytest.mark.asyncio
async def test_settings_update_without_mcp_servers_keeps_warm_procs(client, tmp_path):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"os_name": "ToriOS"}))
    with patch("services.settings_store.SETTINGS_PATH", sf), patch(
        "services.claude_code_provider.evict_all_warm_procs"
    ) as mock_evict:
        resp = await client.patch("/api/settings", json={"dark_mode": True})
        assert resp.status_code == 200
        assert not mock_evict.called

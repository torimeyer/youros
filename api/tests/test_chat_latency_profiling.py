"""Tests for chat turn latency profiling (Phase 4B, task ->3093).

Covers:
  - TurnLatencySample dataclass + record_turn_latency bounded deque (maxlen=200)
  - Structured chat.turn_latency log line emission
  - get_latency_summary() per-provider and per-model p50/p95/mean calculation
  - End-to-end latency recording from gemini_cli_provider.stream_chat
  - End-to-end latency recording from claude_code_provider.stream_chat (cold + warm)
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

import services.claude_code_provider as ccp
import services.gemini_cli_provider as gcp
from services.chat_latency import (
    TurnLatencySample,
    clear_latency_samples,
    get_latency_summary,
    get_recent_latency_samples,
    record_turn_latency,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


class FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeStderr:
    async def read(self) -> bytes:
        return b""


class FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class FakeSubprocess:
    def __init__(self, lines: list[bytes], warm: bool = False) -> None:
        self.stdout = FakeStdout(lines)
        self.stderr = FakeStderr()
        self.stdin = FakeStdin() if warm else None
        self._rc = None if warm else 0

    @property
    def returncode(self):
        return self._rc

    def kill(self) -> None:
        self._rc = -9

    async def wait(self) -> int:
        return 0 if self._rc is None else self._rc


@pytest.fixture(autouse=True)
def _reset_latency_and_registries():
    clear_latency_samples()
    if hasattr(gcp, "_gemini_tab_sessions"):
        gcp._gemini_tab_sessions.clear()
    ccp._warm_procs.clear()
    ccp._warm_proc_locks.clear()
    ccp._known_sessions.clear()
    yield
    clear_latency_samples()
    for proc in list(ccp._warm_procs.values()):
        try:
            proc.kill()
        except Exception:
            pass
    ccp._warm_procs.clear()
    ccp._warm_proc_locks.clear()
    ccp._known_sessions.clear()


def test_record_turn_latency_bounds_at_200_and_logs_structured_line(caplog):
    with caplog.at_level(logging.INFO, logger="myos.chat.latency"):
        for i in range(250):
            record_turn_latency(
                TurnLatencySample(
                    provider="gemini_cli",
                    model="flash",
                    tab_id=f"tab-{i}",
                    warm_session=(i % 2 == 0),
                    spawn_ms=12.0 + i,
                    init_ms=45.0 + i,
                    first_token_ms=210.0 + i,
                    total_ms=500.0 + i,
                    timestamp="2026-11-13T20:00:00Z",
                )
            )

    samples = get_recent_latency_samples()
    assert len(samples) == 200
    assert samples[0].tab_id == "tab-50"
    assert samples[-1].tab_id == "tab-249"

    assert any("chat.turn_latency" in rec.message for rec in caplog.records)
    last_msg = [rec.message for rec in caplog.records if "chat.turn_latency" in rec.message][-1]
    assert "provider=gemini_cli" in last_msg
    assert "model=flash" in last_msg
    assert "total_ms=" in last_msg


def test_get_latency_summary_computes_per_provider_and_per_model_stats():
    for total, ft, spawn in [(100.0, 40.0, 5.0), (200.0, 80.0, 10.0), (300.0, 120.0, 15.0)]:
        record_turn_latency(
            TurnLatencySample(
                provider="claude_code",
                model="claude-sonnet-4-6",
                tab_id="t1",
                warm_session=True,
                spawn_ms=spawn,
                init_ms=None,
                first_token_ms=ft,
                total_ms=total,
                timestamp="2026-11-13T20:00:00Z",
            )
        )
    record_turn_latency(
        TurnLatencySample(
            provider="gemini_cli",
            model="gemini-2.5-flash",
            tab_id="t2",
            warm_session=False,
            spawn_ms=25.0,
            init_ms=90.0,
            first_token_ms=350.0,
            total_ms=600.0,
            timestamp="2026-11-13T20:00:01Z",
        )
    )

    summary = get_latency_summary()
    assert summary["sample_count"] == 4

    claude_stats = summary["by_provider"]["claude_code"]
    assert claude_stats["count"] == 3
    assert claude_stats["total_ms"]["p50"] == 200.0
    assert claude_stats["total_ms"]["mean"] == 200.0
    assert claude_stats["total_ms"]["p95"] >= 280.0
    assert claude_stats["first_token_ms"]["p50"] == 80.0
    assert claude_stats["init_ms"]["count"] == 0
    assert claude_stats["init_ms"]["p50"] is None

    gem_stats = summary["by_provider"]["gemini_cli"]
    assert gem_stats["count"] == 1
    assert gem_stats["init_ms"]["p50"] == 90.0
    assert gem_stats["total_ms"]["p50"] == 600.0

    assert "claude-sonnet-4-6" in summary["by_model"]
    assert "gemini-2.5-flash" in summary["by_model"]


@pytest.mark.asyncio
async def test_gemini_stream_chat_records_turn_latency_sample():
    lines = [
        (json.dumps({"type": "init", "session_id": "sid-lat-1", "model": "flash"}) + "\n").encode(),
        (json.dumps({"type": "message", "role": "assistant", "content": "fast answer", "delta": True}) + "\n").encode(),
        (json.dumps({"type": "result", "status": "success", "stats": {"input_tokens": 8, "output_tokens": 2}}) + "\n").encode(),
    ]
    proc = FakeSubprocess(lines, warm=False)
    ws = FakeWebSocket()

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        text = await gcp.stream_chat(
            [{"role": "user", "content": "ping"}],
            ws,
            tab_id="tab-lat-gem",
            model="flash",
        )

    assert text == "fast answer"
    samples = get_recent_latency_samples()
    assert len(samples) == 1
    s = samples[0]
    assert s.provider == "gemini_cli"
    assert s.model == "flash"
    assert s.tab_id == "tab-lat-gem"
    assert s.warm_session is False
    assert s.spawn_ms is not None and s.spawn_ms >= 0.0
    assert s.init_ms is not None and s.init_ms >= 0.0
    assert s.first_token_ms is not None and s.first_token_ms >= 0.0
    assert s.total_ms >= s.first_token_ms


@pytest.mark.asyncio
async def test_claude_stream_chat_records_cold_and_warm_turn_latency_samples():
    def _claude_turn_bytes(reply: str) -> list[bytes]:
        return [
            (json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}) + "\n").encode(),
            (json.dumps({
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": reply},
                },
            }) + "\n").encode(),
            (json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": reply,
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }) + "\n").encode(),
        ]

    warm_proc = FakeSubprocess(_claude_turn_bytes("turn-one"), warm=True)

    with (
        patch("services.claude_code_provider._find_claude_binary", return_value="/usr/bin/claude"),
        patch("services.claude_code_provider.asyncio.create_subprocess_exec", new=AsyncMock(return_value=warm_proc)),
        patch("routers.agents.register_chat_session", new=AsyncMock()),
        patch("routers.agents.complete_chat_session", new=AsyncMock()),
        patch("services.token_metrics.safe_record_chat_turn", return_value=None),
        patch("services.chat_providers._get_boot_context", return_value=""),
        patch("services.chat_providers._log_chat_completion", return_value=None),
        patch("services.chat_providers._extract_chat_topic", return_value="test"),
    ):
        ws1 = FakeWebSocket()
        await ccp.stream_chat([{"role": "user", "content": "one"}], ws1, tab_id="tab-lat-claude")

        warm_proc.stdout = FakeStdout(_claude_turn_bytes("turn-two"))
        ws2 = FakeWebSocket()
        await ccp.stream_chat(
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "turn-one"},
                {"role": "user", "content": "two"},
            ],
            ws2,
            tab_id="tab-lat-claude",
        )

    samples = get_recent_latency_samples()
    assert len(samples) == 2
    assert samples[0].provider == "claude_code"
    assert samples[0].warm_session is False
    assert samples[0].first_token_ms is not None

    assert samples[1].provider == "claude_code"
    assert samples[1].warm_session is True
    assert samples[1].first_token_ms is not None

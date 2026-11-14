"""Tests for needle ->3119: Claude 2.1.323 headless worker flags and subagent stream parsing.

Covers:
1. _build_claude_print_spawn_cmd:
   - Always passes --forward-subagent-text after --permission-mode.
   - Passes --max-budget-usd <amount> when budget > 0; omits when None or <= 0.
   - Passes --fallback-model sonnet when model is in the opus/fable primary tier;
     omits --fallback-model on sonnet/haiku so CLI never sees fallback == model.
2. End-to-end /api/agents/spawn and /api/agents/{name}/recover subprocess argv.
3. Nested subagent stream-json parsing (_extract_parent_tool_use_id,
   _parse_jsonl_transcript_entries, _format_jsonl_transcript,
   _extract_drain_stream_chunks, and GET /api/agents/{name}/transcript).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeStdout:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 613119
    returncode = 0

    def __init__(self) -> None:
        self.stdin = None
        self.stdout = _FakeStdout()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0


@pytest.fixture(autouse=True)
def _clean_registry():
    import routers.agents as agents_mod
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests

    _reset_spawn_lock_registry_for_tests()
    test_names = {
        "test-3119-opus-spawn",
        "test-3119-sonnet-spawn",
        "test-3119-haiku-nobudget",
        "test-3119-opus-recover",
        "test-3119-jsonl-subagent",
    }
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)
        agents_mod.active_agents.pop(n, None)
        agents_mod._resolve_cache.pop(n, None)
    yield
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)
        agents_mod.active_agents.pop(n, None)
        agents_mod._resolve_cache.pop(n, None)
    _reset_spawn_lock_registry_for_tests()


def _install_spawn_capture(monkeypatch: Any) -> list[list[str]]:
    import routers.agents as agents_mod

    captured_cmds: list[list[str]] = []

    async def _fake_exec(*args: Any, **_kwargs: Any) -> _FakeProc:
        captured_cmds.append([str(a) for a in args])
        return _FakeProc()

    async def _noop_run(*_args: Any, **_kwargs: Any) -> str:
        return ""

    monkeypatch.setenv("YOUROS_SPAWN_FORCE_CUSTOM", "1")
    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(agents_mod.ostk, "_run", _noop_run)

    real_create_task = agents_mod.asyncio.create_task

    def _quiet_background_tasks(coro: Any, *a: Any, **kw: Any) -> Any:
        cname = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if cname.startswith(("_drain_stderr", "_drain_stdout", "_startup_deadline_watchdog")):
            coro.close()

            class _DoneTask:
                def cancel(self) -> None:
                    return None

                def done(self) -> bool:
                    return True

                def add_done_callback(self, _cb: Any) -> None:
                    return None

            return _DoneTask()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(agents_mod.asyncio, "create_task", _quiet_background_tasks)
    return captured_cmds


# ---------------------------------------------------------------------------
# 1. Unit tests for _build_claude_print_spawn_cmd
# ---------------------------------------------------------------------------


def test_build_spawn_cmd_preserves_prefix_and_adds_forward_subagent_text(monkeypatch):
    import routers.agents as agents_mod
    from routers.agents import _build_claude_print_spawn_cmd

    monkeypatch.setattr(agents_mod, "CLAUDE_BIN", "/usr/local/bin/claude")
    cmd = _build_claude_print_spawn_cmd(
        model="claude-sonnet-5-5",
        perm_mode="acceptEdits",
        budget=2.0,
    )

    assert cmd[:9] == [
        "/usr/local/bin/claude",
        "--print",
        "--model",
        "claude-sonnet-5-5",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
    ]
    assert "--forward-subagent-text" in cmd
    assert cmd.index("--forward-subagent-text") > cmd.index("--permission-mode")


@pytest.mark.parametrize(
    "budget,expected_val",
    [
        (2.0, 2.0),
        (0.5, 0.5),
        (3.75, 3.75),
        (0.125, 0.125),
    ],
)
def test_build_spawn_cmd_includes_max_budget_usd_when_positive(budget, expected_val):
    from routers.agents import _build_claude_print_spawn_cmd

    cmd = _build_claude_print_spawn_cmd(
        model="claude-sonnet-5-5",
        perm_mode="default",
        budget=budget,
    )
    assert "--max-budget-usd" in cmd
    idx = cmd.index("--max-budget-usd")
    assert float(cmd[idx + 1]) == pytest.approx(expected_val)


@pytest.mark.parametrize("budget", [None, 0, 0.0, -1.0, -0.01])
def test_build_spawn_cmd_omits_max_budget_usd_when_none_or_nonpositive(budget):
    from routers.agents import _build_claude_print_spawn_cmd

    cmd = _build_claude_print_spawn_cmd(
        model="claude-sonnet-5-5",
        perm_mode="default",
        budget=budget,
    )
    assert "--max-budget-usd" not in cmd


@pytest.mark.parametrize(
    "heavy_model",
    [
        "opus",
        "claude-opus-5-5",
        "claude-opus-4-7",
        "fable",
        "claude-fable-1",
    ],
)
def test_build_spawn_cmd_adds_sonnet_fallback_for_opus_and_fable(heavy_model):
    from routers.agents import _build_claude_print_spawn_cmd

    cmd = _build_claude_print_spawn_cmd(
        model=heavy_model,
        perm_mode="bypassPermissions",
        budget=5.0,
    )
    assert "--fallback-model" in cmd
    idx = cmd.index("--fallback-model")
    assert cmd[idx + 1] == "sonnet"


@pytest.mark.parametrize(
    "non_heavy_model",
    [
        "sonnet",
        "claude-sonnet-5-5",
        "claude-sonnet-4-6",
        "haiku",
        "claude-haiku-5-5",
    ],
)
def test_build_spawn_cmd_omits_fallback_for_sonnet_and_haiku(non_heavy_model):
    from routers.agents import _build_claude_print_spawn_cmd

    cmd = _build_claude_print_spawn_cmd(
        model=non_heavy_model,
        perm_mode="bypassPermissions",
        budget=2.0,
    )
    assert "--fallback-model" not in cmd


# ---------------------------------------------------------------------------
# 2. End-to-end /api/agents/spawn and /api/agents/{name}/recover argv tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_endpoint_passes_budget_fallback_and_subagent_stream_for_opus(monkeypatch):
    from main import app

    captured = _install_spawn_capture(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-3119-opus-spawn",
                "prompt": "Inspect current configuration.",
                "model": "opus",
                "budget": 3.5,
                "isolation": "none",
            },
        )

    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" in argv
    assert "--max-budget-usd" in argv
    assert float(argv[argv.index("--max-budget-usd") + 1]) == pytest.approx(3.5)
    assert "--fallback-model" in argv
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"


@pytest.mark.asyncio
async def test_spawn_endpoint_sonnet_and_zero_budget_flags(monkeypatch):
    from main import app

    captured = _install_spawn_capture(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_sonnet = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-3119-sonnet-spawn",
                "prompt": "Summarize status.",
                "model": "sonnet",
                "budget": 2.0,
                "isolation": "none",
            },
        )
        resp_haiku = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-3119-haiku-nobudget",
                "prompt": "Quick ping.",
                "model": "haiku",
                "budget": 0.0,
                "isolation": "none",
            },
        )

    assert resp_sonnet.status_code == 200, resp_sonnet.text
    assert resp_haiku.status_code == 200, resp_haiku.text
    assert len(captured) == 2

    sonnet_argv = captured[0]
    assert "--forward-subagent-text" in sonnet_argv
    assert "--max-budget-usd" in sonnet_argv
    assert float(sonnet_argv[sonnet_argv.index("--max-budget-usd") + 1]) == pytest.approx(2.0)
    assert "--fallback-model" not in sonnet_argv

    haiku_argv = captured[1]
    assert "--forward-subagent-text" in haiku_argv
    assert "--max-budget-usd" not in haiku_argv
    assert "--fallback-model" not in haiku_argv


@pytest.mark.asyncio
async def test_recover_endpoint_preserves_worker_flags(monkeypatch):
    import routers.agents as agents_mod
    from main import app

    captured = _install_spawn_capture(monkeypatch)
    agents_mod.agent_metadata["test-3119-opus-recover"] = {
        "status": "failed",
        "model": "claude-opus-5-5",
        "budget": "4.25",
        "prompt": "Analyze service logs.",
        "recovery_count": 0,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/agents/test-3119-opus-recover/recover")

    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" in argv
    assert "--max-budget-usd" in argv
    assert float(argv[argv.index("--max-budget-usd") + 1]) == pytest.approx(4.25)
    assert "--fallback-model" in argv
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"


# ---------------------------------------------------------------------------
# 3. Backend nested subagent stream-json transcript parsing tests
# ---------------------------------------------------------------------------


def test_parse_jsonl_transcript_entries_preserves_parent_tool_use_id(tmp_path):
    from routers.agents import (
        _format_jsonl_transcript,
        _parse_jsonl_transcript_entries,
    )

    jsonl_file = tmp_path / "worker.jsonl"
    events = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Investigate auth latency"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Delegating DB check to subagent."},
                    {"type": "tool_use", "id": "toolu_sub_01", "name": "Agent", "input": {}},
                ],
            },
        },
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_sub_01",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Checking index cardinality first."},
                    {"type": "text", "text": "Found missing composite index on sessions."},
                    {"type": "tool_use", "id": "toolu_read_9", "name": "Read", "input": {}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "parent_tool_use_id": "toolu_sub_01",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read_9",
                        "content": "CREATE INDEX idx_sessions_user ON sessions(user_id);",
                    }
                ],
            },
        },
        {
            "type": "user",
            "isMeta": True,
            "parent_tool_use_id": "toolu_sub_01",
            "message": {"role": "user", "content": "<system-reminder>ignored</system-reminder>"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Root cause confirmed."}],
            },
        },
    ]
    jsonl_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    entries = _parse_jsonl_transcript_entries(jsonl_file)
    # Parent entries have is_subagent=False and parent_tool_use_id=None
    assert entries[0]["role"] == "User"
    assert entries[0]["is_subagent"] is False
    assert entries[0]["parent_tool_use_id"] is None

    assert entries[1]["role"] == "Assistant"
    assert entries[1]["is_subagent"] is False
    assert entries[1]["parent_tool_use_id"] is None

    # Subagent entries preserve parent_tool_use_id and is_subagent=True
    sub_entries = [e for e in entries if e["is_subagent"]]
    assert len(sub_entries) == 4
    assert all(e["parent_tool_use_id"] == "toolu_sub_01" for e in sub_entries)
    assert any("[thinking]" in e["body"] and "Checking index cardinality" in e["body"] for e in sub_entries)
    assert any("Found missing composite index" in e["body"] for e in sub_entries)
    assert any("[tool: Read]" in e["body"] for e in sub_entries)
    assert any(e["role"] == "Tool result" and "idx_sessions_user" in e["body"] for e in sub_entries)

    formatted = _format_jsonl_transcript(jsonl_file)
    assert "User: Investigate auth latency" in formatted
    assert "Assistant: Delegating DB check to subagent." in formatted
    assert "Assistant: [subagent: toolu_sub_01] [thinking] Checking index cardinality first." in formatted
    assert "Assistant: [subagent: toolu_sub_01] Found missing composite index on sessions." in formatted
    assert "Tool result: [subagent: toolu_sub_01] CREATE INDEX idx_sessions_user" in formatted
    assert "system-reminder" not in formatted
    assert "Assistant: Root cause confirmed." in formatted


@pytest.mark.asyncio
async def test_get_agent_transcript_endpoint_exposes_subagent_entries(tmp_path):
    import routers.agents as agents_mod
    from main import app

    jsonl_path = tmp_path / "subagent-run.jsonl"
    jsonl_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "parent_tool_use_id": "toolu_nested_77",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Subagent finished scan."}],
                },
            }
        )
        + "\n"
    )
    agents_mod.agent_metadata["test-3119-jsonl-subagent"] = {
        "spawned_at": "2026-11-14T01:00:00+00:00",
        "source": "claude-code",
        "transcript_path": str(jsonl_path),
        "transcript_path_source": "caller",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.PROJECT_ROOT", tmp_path):
            (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
            resp = await client.get("/api/agents/test-3119-jsonl-subagent/transcript")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "[subagent: toolu_nested_77] Subagent finished scan." in data["content"]
    assert len(data["entries"]) == 1
    assert data["entries"][0]["parent_tool_use_id"] == "toolu_nested_77"
    assert data["entries"][0]["is_subagent"] is True


def test_extract_drain_stream_chunks_annotates_forwarded_subagent_blocks():
    from routers.agents import _extract_drain_stream_chunks

    parent_evt = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Parent chunk."}]},
    }
    p_texts, p_had_model, p_tool_delta = _extract_drain_stream_chunks(parent_evt)
    assert p_texts == ["Parent chunk."]
    assert p_had_model is True
    assert p_tool_delta == 0

    sub_evt = {
        "type": "assistant",
        "parent_tool_use_id": "toolu_live_42",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "Inspecting router."},
                {"type": "text", "text": "Router verified."},
                {"type": "tool_use", "name": "Grep", "id": "t_grep"},
            ]
        },
    }
    s_texts, s_had_model, s_tool_delta = _extract_drain_stream_chunks(sub_evt)
    assert s_had_model is True
    assert s_tool_delta == 1
    assert any("[subagent: toolu_live_42] [thinking] Inspecting router." in t for t in s_texts)
    assert any("[subagent: toolu_live_42] Router verified." in t for t in s_texts)

    sub_user_result = {
        "type": "user",
        "parent_tool_use_id": "toolu_live_42",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t_grep", "content": "ok"}]
        },
    }
    u_texts, u_had_model, u_tool_delta = _extract_drain_stream_chunks(sub_user_result)
    assert u_texts == []
    assert u_had_model is True
    assert u_tool_delta == -1

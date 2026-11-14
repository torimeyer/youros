"""Tests for ground-truth external Claude and Gemini runtime session discovery (->3121)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import runtime_sessions


@pytest.fixture(autouse=True)
def _clear_runtime_cache():
    runtime_sessions.reset_runtime_sessions_cache()
    yield
    runtime_sessions.reset_runtime_sessions_cache()


def _fake_subprocess(stdout_bytes: bytes = b"[]", returncode: int = 0, hang_seconds: float = 0.0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)

    async def _communicate():
        if hang_seconds > 0:
            await asyncio.sleep(hang_seconds)
        return stdout_bytes, b""

    proc.communicate = _communicate
    return proc


@pytest.mark.asyncio
async def test_claude_discovery_verifies_pid_liveness_and_normalizes_shape():
    """Live PIDs and PermissionError PIDs are kept; ProcessLookupError ghosts are dropped."""
    sample = [
        {
            "pid": 14165,
            "cwd": "/Users/torimeyer/claude/torios",
            "kind": "interactive",
            "startedAt": 1794502911862,
            "sessionId": "6b60ca12-6edc-46a2-9529-1edf91b1a73b",
            "name": "torios-17",
            "status": "idle",
        },
        {
            "pid": 22010,
            "cwd": "/Users/torimeyer/work/core",
            "kind": "background",
            "startedAt": 1794502990000,
            "sessionId": "bg-sess-999",
            "name": "claude-bg-worker",
            "status": "active",
        },
        {
            "pid": 99999,
            "cwd": "/Users/torimeyer/claude/torios",
            "kind": "interactive",
            "startedAt": 1794502900000,
            "sessionId": "ghost-dead-session",
            "name": "ghost-1",
            "status": "active",
        },
    ]

    def _fake_kill(pid: int, sig: int):
        assert sig == 0
        if pid == 14165:
            return None
        if pid == 22010:
            raise PermissionError(1, "Operation not permitted")
        raise ProcessLookupError(3, "No such process")

    proc = _fake_subprocess(json.dumps(sample).encode("utf-8"))

    with (
        patch.object(runtime_sessions, "_find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec,
        patch("services.runtime_sessions.os.kill", side_effect=_fake_kill),
    ):
        results = await runtime_sessions.discover_claude_sessions()

    mock_exec.assert_awaited_once()
    call_args, call_kwargs = mock_exec.call_args
    assert list(call_args[:3]) == ["/usr/local/bin/claude", "agents", "--json"]
    assert call_kwargs.get("stdin") == asyncio.subprocess.DEVNULL

    ids = [r["id"] for r in results]
    assert "6b60ca12-6edc-46a2-9529-1edf91b1a73b" in ids
    assert "bg-sess-999" in ids
    assert "ghost-dead-session" not in ids

    interactive = next(r for r in results if r["id"] == "6b60ca12-6edc-46a2-9529-1edf91b1a73b")
    assert interactive["runtime"] == "claude"
    assert interactive["kind"] == "interactive"
    assert interactive["pid"] == 14165
    assert interactive["cwd"] == "/Users/torimeyer/claude/torios"
    assert interactive["status"] == "idle"
    assert interactive["external"] is True
    assert interactive["name"] == "torios-17"
    assert isinstance(interactive["last_event_at"], str)
    assert isinstance(interactive["age_seconds"], int)
    assert isinstance(interactive["activity"], str)
    assert interactive["recent_files"] == []

    bg = next(r for r in results if r["id"] == "bg-sess-999")
    assert bg["runtime"] == "claude"
    assert bg["kind"] == "background"
    assert bg["pid"] == 22010
    assert bg["status"] == "active"
    assert bg["external"] is True


@pytest.mark.asyncio
async def test_claude_discovery_timeout_kills_subprocess_and_returns_empty(monkeypatch):
    """A hung claude agents --json subprocess times out cleanly without raising."""
    monkeypatch.setattr(runtime_sessions, "CLAUDE_DISCOVERY_TIMEOUT_SECONDS", 0.05)
    proc = _fake_subprocess(hang_seconds=1.0)

    with (
        patch.object(runtime_sessions, "_find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        results = await runtime_sessions.discover_claude_sessions()

    assert results == []
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_claude_discovery_missing_binary_or_bad_json_returns_empty():
    """Missing claude binary or malformed CLI output degrades to an empty list."""
    with patch.object(runtime_sessions, "_find_claude_binary", return_value=None):
        assert await runtime_sessions.discover_claude_sessions() == []

    bad_proc = _fake_subprocess(b"not-valid-json", returncode=1)
    with (
        patch.object(runtime_sessions, "_find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock(return_value=bad_proc)),
    ):
        assert await runtime_sessions.discover_claude_sessions() == []


@pytest.mark.asyncio
async def test_gemini_local_jsonl_discovery_active_idle_stale_and_tail_cap(tmp_path):
    """Scans ~/.gemini/tmp/*/chats/session-*.jsonl locally with 4 KB tail cap and zero CLI calls."""
    now = datetime.now(timezone.utc)
    gemini_tmp = tmp_path / "gemini_tmp"

    # 1. Active session (<5 min) with .project_root and sessionId in JSONL
    active_proj = gemini_tmp / "torios-slug"
    (active_proj / "chats").mkdir(parents=True)
    (active_proj / ".project_root").write_text("/Users/torimeyer/claude/torios\n", encoding="utf-8")
    active_file = active_proj / "chats" / "session-2026-11-14T01-02-ad8a3b58.jsonl"
    active_lines = [
        json.dumps({"sessionId": "ad8a3b58-3a45-48e0-a0a5-4dba32d9b910", "kind": "main"}),
        json.dumps(
            {
                "type": "gemini",
                "thoughts": [{"subject": "Inspecting session router", "description": "Checking routes"}],
            }
        ),
    ]
    active_file.write_text("\n".join(active_lines) + "\n", encoding="utf-8")
    active_mtime = (now - timedelta(minutes=2)).timestamp()
    os.utime(active_file, (active_mtime, active_mtime))

    # 2. Idle session (15 min old) larger than 4 KB so header line is beyond the 4 KB tail
    idle_proj = gemini_tmp / "youros-core"
    (idle_proj / "chats").mkdir(parents=True)
    idle_file = idle_proj / "chats" / "session-2026-11-14T00-45-beef1234.jsonl"
    padding_line = json.dumps({"type": "user", "content": [{"text": "x" * 6000}]})
    tail_line = json.dumps({"$set": {"lastUpdated": "2026-11-14T00:50:00Z"}})
    idle_file.write_text(
        json.dumps({"sessionId": "header-hidden-beyond-4kb"}) + "\n" + padding_line + "\n" + tail_line + "\n",
        encoding="utf-8",
    )
    idle_mtime = (now - timedelta(minutes=15)).timestamp()
    os.utime(idle_file, (idle_mtime, idle_mtime))

    # 3. Stale session (45 min old) -> excluded
    stale_proj = gemini_tmp / "old-repo"
    (stale_proj / "chats").mkdir(parents=True)
    stale_file = stale_proj / "chats" / "session-2026-11-13T23-00-dead0000.jsonl"
    stale_file.write_text(json.dumps({"sessionId": "stale-gem-id"}) + "\n", encoding="utf-8")
    stale_mtime = (now - timedelta(minutes=45)).timestamp()
    os.utime(stale_file, (stale_mtime, stale_mtime))

    with (
        patch.object(runtime_sessions, "GEMINI_TMP_DIR", gemini_tmp),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock()) as mock_exec,
    ):
        results = await runtime_sessions.discover_gemini_sessions(now=now)

    # Zero subprocess calls for Gemini discovery (never invokes gemini --list-sessions)
    mock_exec.assert_not_called()

    assert len(results) == 2
    by_slug = {r["project_slug"]: r for r in results}
    assert "old-repo" not in by_slug

    active_row = by_slug["torios-slug"]
    assert active_row["id"] == "ad8a3b58-3a45-48e0-a0a5-4dba32d9b910"
    assert active_row["runtime"] == "gemini"
    assert active_row["kind"] == "interactive"
    assert active_row["status"] == "active"
    assert active_row["pid"] is None
    assert active_row["cwd"] == "/Users/torimeyer/claude/torios"
    assert active_row["external"] is True
    assert "Inspecting session router" in active_row["activity"]
    assert active_row["recent_files"] == []
    assert 100 <= active_row["age_seconds"] <= 140

    idle_row = by_slug["youros-core"]
    # Because file exceeded 4 KB, header line was skipped and filename stem supplied session_id
    assert idle_row["id"] == "session-2026-11-14T00-45-beef1234"
    assert idle_row["runtime"] == "gemini"
    assert idle_row["status"] == "idle"
    assert idle_row["cwd"] is None
    assert idle_row["external"] is True


@pytest.mark.asyncio
async def test_combined_discovery_uses_5s_monotonic_ttl_and_reset_helper(tmp_path):
    """Combined discovery caches for 5 seconds and clears on reset_runtime_sessions_cache()."""
    gemini_tmp = tmp_path / "empty_gemini"
    gemini_tmp.mkdir()
    sample = [
        {
            "pid": os.getpid(),
            "cwd": str(tmp_path),
            "kind": "interactive",
            "startedAt": 1794502911862,
            "sessionId": "cached-claude-1",
            "name": "cached-claude-1",
            "status": "active",
        }
    ]
    proc = _fake_subprocess(json.dumps(sample).encode("utf-8"))

    clock = [100.0]

    with (
        patch.object(runtime_sessions, "GEMINI_TMP_DIR", gemini_tmp),
        patch.object(runtime_sessions, "_find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec,
        patch("services.runtime_sessions.time.monotonic", side_effect=lambda: clock[0]),
    ):
        first = await runtime_sessions.discover_external_runtime_sessions()
        clock[0] = 103.0  # within 5s TTL
        second = await runtime_sessions.discover_external_runtime_sessions()
        assert len(first) == 1
        assert len(second) == 1
        assert mock_exec.await_count == 1

        # Advance past 5s TTL -> triggers fresh subprocess call
        clock[0] = 106.0
        await runtime_sessions.discover_external_runtime_sessions()
        assert mock_exec.await_count == 2

        # Explicit cache reset forces refresh even at same timestamp
        runtime_sessions.reset_runtime_sessions_cache()
        await runtime_sessions.discover_external_runtime_sessions()
        assert mock_exec.await_count == 3


@pytest.mark.asyncio
async def test_invalid_pids_and_backend_self_sessions_filtered(tmp_path):
    """Non-int/bool/negative PIDs and myos-api-* self-sessions are dropped cleanly."""
    now = datetime.now(timezone.utc)
    sample = [
        {"pid": True, "sessionId": "bool-pid", "status": "active"},
        {"pid": -5, "sessionId": "neg-pid", "status": "active"},
        {"pid": "14165", "sessionId": "str-pid", "status": "active"},
        {"pid": None, "sessionId": "none-pid", "status": "active"},
        {"pid": 44444, "sessionId": "myos-api-worker-1", "name": "myos-api-worker-1", "status": "active"},
        {"pid": 55555, "sessionId": "valid-live", "name": "valid-live", "status": "active"},
    ]
    proc = _fake_subprocess(json.dumps(sample).encode("utf-8"))
    killed_pids: list[int] = []

    def _fake_kill(pid: int, sig: int):
        killed_pids.append(pid)
        return None

    with (
        patch.object(runtime_sessions, "_find_claude_binary", return_value="/usr/local/bin/claude"),
        patch("services.runtime_sessions.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch("services.runtime_sessions.os.kill", side_effect=_fake_kill),
    ):
        claude_rows = await runtime_sessions.discover_claude_sessions(now=now)

    assert [r["id"] for r in claude_rows] == ["valid-live"]
    # os.kill was never invoked with bool/str/negative/None PIDs
    assert all(isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in killed_pids)


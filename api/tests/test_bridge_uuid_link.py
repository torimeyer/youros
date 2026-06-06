"""Tests for →1475: bridge links Claude Code session UUID at register time.

Verifies that:
1. POST /api/agents/register scans the agent's ~/.claude/projects/<encoded-cwd>/
   directory for a fresh .jsonl file and stores its path as transcript_path.
2. If no file found at register time, transcript_uuid_pending is set and retried
   on the next heartbeat.
3. POST /api/agents/{name}/heartbeat refreshes transcript_bytes from the linked
   file's actual size so the Agents page shows non-zero bytes.
4. transcript_path is visible in GET /api/agents rows for debugging.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_CHAT_NOTIFICATIONS", "1")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")


from routers.agents import _link_session_jsonl  # noqa: E402  (added by →1475)


# ---------------------------------------------------------------------------
# Unit tests for _link_session_jsonl
# ---------------------------------------------------------------------------


def test_link_finds_fresh_jsonl(tmp_path):
    """Finds a .jsonl file in the encoded-cwd project dir and sets transcript_path."""
    from datetime import datetime, timezone, timedelta

    # Register time = now
    now = datetime.now(timezone.utc)
    register_iso = now.isoformat()

    # Set up fake ~/.claude/projects/<encoded>/  directory
    fake_projects = tmp_path / ".claude" / "projects"
    fake_projects.mkdir(parents=True, exist_ok=True)

    cwd = "/fake/worktree/path"
    encoded = cwd.replace("/", "-").lstrip("-")
    project_dir = fake_projects / f"-{encoded}"
    project_dir.mkdir(exist_ok=True)

    # Write a fake session JSONL (size > 0) with mtime = now
    jsonl_file = project_dir / "aabbccdd-1111-2222-3333-444455556666.jsonl"
    jsonl_file.write_text(json.dumps({"type": "user", "message": "hello"}) + "\n")

    meta = {"worktree_path": cwd}

    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        result = _link_session_jsonl("test-agent", meta, register_iso)

    assert result is True
    assert meta.get("transcript_path") == str(jsonl_file)
    assert "transcript_uuid_pending" not in meta


def test_link_skips_old_jsonl(tmp_path):
    """Does not link a .jsonl file that predates the register time by >30s."""
    from datetime import datetime, timezone, timedelta
    import time

    now = datetime.now(timezone.utc)
    # Register happened AFTER the file was written (60s ago)
    register_iso = (now + timedelta(seconds=60)).isoformat()

    fake_projects = tmp_path / ".claude" / "projects"
    fake_projects.mkdir(parents=True, exist_ok=True)
    cwd = "/fake/worktree/old"
    encoded = cwd.replace("/", "-").lstrip("-")
    project_dir = fake_projects / f"-{encoded}"
    project_dir.mkdir(exist_ok=True)

    jsonl_file = project_dir / "old-session.jsonl"
    jsonl_file.write_text('{"type": "user"}\n')

    # Force mtime to be old (before the 30s grace window)
    old_mtime = (now - timedelta(seconds=5)).timestamp()
    os.utime(jsonl_file, (old_mtime, old_mtime))

    meta = {"worktree_path": cwd}

    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        result = _link_session_jsonl("test-agent", meta, register_iso)

    assert result is False
    assert "transcript_path" not in meta


def test_link_returns_false_when_no_project_dir(tmp_path):
    """Returns False gracefully when the project dir does not exist."""
    from datetime import datetime, timezone

    register_iso = datetime.now(timezone.utc).isoformat()
    fake_projects = tmp_path / ".claude" / "projects"
    fake_projects.mkdir(parents=True, exist_ok=True)

    meta = {"worktree_path": "/no/such/worktree"}

    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        result = _link_session_jsonl("test-agent", meta, register_iso)

    assert result is False


def test_link_skips_empty_jsonl(tmp_path):
    """Does not link a zero-byte .jsonl file."""
    from datetime import datetime, timezone

    register_iso = datetime.now(timezone.utc).isoformat()
    fake_projects = tmp_path / ".claude" / "projects"
    fake_projects.mkdir(parents=True, exist_ok=True)
    cwd = "/fake/empty"
    encoded = cwd.replace("/", "-").lstrip("-")
    project_dir = fake_projects / f"-{encoded}"
    project_dir.mkdir(exist_ok=True)

    empty = project_dir / "empty-session.jsonl"
    empty.write_text("")  # 0 bytes

    meta = {"worktree_path": cwd}

    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        result = _link_session_jsonl("test-agent", meta, register_iso)

    assert result is False


# ---------------------------------------------------------------------------
# Integration: register sets transcript_uuid_pending when no file available
# ---------------------------------------------------------------------------


def test_register_sets_uuid_pending_when_no_session_file(tmp_path):
    """When no .jsonl session file exists, register stores transcript_uuid_pending=True."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app, raise_server_exceptions=True)

    empty_projects = tmp_path / ".claude" / "projects"
    empty_projects.mkdir(parents=True, exist_ok=True)

    with patch("routers.agents._claude_code_projects_dir", return_value=empty_projects):
        with patch("routers.agents._autodiscover_recent_transcript_path", return_value=None):
            resp = client.post(
                "/api/agents/register",
                json={
                    "name": "test-uuid-link-agent",
                    "task": "UUID link test",
                    "source": "claude-code",
                },
            )
    assert resp.status_code == 200

    from routers.agents import agent_metadata
    meta = agent_metadata.get("test-uuid-link-agent") or {}
    assert meta.get("transcript_uuid_pending") is True


# ---------------------------------------------------------------------------
# Integration: heartbeat retries link and refreshes transcript_bytes
# ---------------------------------------------------------------------------


def test_heartbeat_links_uuid_and_refreshes_bytes(tmp_path):
    """After a failed-at-register link, heartbeat finds the file and updates
    transcript_bytes in the metadata row."""
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from main import app
    from routers.agents import agent_metadata

    client = TestClient(app, raise_server_exceptions=True)

    fake_projects = tmp_path / ".claude" / "projects"
    fake_projects.mkdir(parents=True, exist_ok=True)
    cwd = "/fake/worktree/hb-test"
    encoded = cwd.replace("/", "-").lstrip("-")
    project_dir = fake_projects / f"-{encoded}"
    project_dir.mkdir(exist_ok=True)

    # Register without a JSONL present → pending
    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        with patch("routers.agents._autodiscover_recent_transcript_path", return_value=None):
            resp = client.post(
                "/api/agents/register",
                json={
                    "name": "test-hb-link-agent",
                    "task": "Heartbeat link test",
                    "source": "claude-code",
                    "prompt": f"WORKTREE {cwd}",
                },
            )
    assert resp.status_code == 200

    # Set worktree_path so _link_session_jsonl uses the right dir
    meta = agent_metadata.get("test-hb-link-agent") or {}
    meta["worktree_path"] = cwd

    # Now write the session JSONL
    payload = json.dumps({"type": "user", "message": "starting work"}) + "\n" * 100
    jsonl_file = project_dir / "ccddee11-2222-3333-aaaa-bbbb12345678.jsonl"
    jsonl_file.write_text(payload)

    # Heartbeat should retry the link and refresh transcript_bytes
    with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects):
        resp = client.post(
            "/api/agents/test-hb-link-agent/heartbeat",
            json={"step": "working"},
        )
    assert resp.status_code == 200

    meta = agent_metadata.get("test-hb-link-agent") or {}
    assert meta.get("transcript_path") == str(jsonl_file), (
        f"transcript_path not set; meta keys: {list(meta.keys())}"
    )
    assert meta.get("transcript_bytes", 0) > 0, (
        f"transcript_bytes should be > 0 after link; got {meta.get('transcript_bytes')}"
    )
    assert "transcript_uuid_pending" not in meta

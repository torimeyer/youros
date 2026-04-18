"""Tests for api/services/heartbeat_idle.py — transcript-idle detection."""
import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import httpx
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.heartbeat_idle import (
    TRANSCRIPT_IDLE_SECONDS,
    decide_to_complete,
    find_transcript,
)


# ---------------------------------------------------------------------------
# Unit: decide_to_complete
# ---------------------------------------------------------------------------

def test_decide_false_when_no_transcript():
    assert decide_to_complete(None) is False


def test_decide_false_when_file_missing(tmp_path):
    missing = tmp_path / "ghost.jsonl"
    assert decide_to_complete(missing) is False


def test_decide_false_when_mtime_recent(tmp_path):
    f = tmp_path / "active.jsonl"
    f.write_text('{"type":"assistant"}\n')
    now = time.time()
    # mtime = now - 60 (within 120s threshold)
    assert decide_to_complete(f, threshold_seconds=120, _now=now + 60) is False


def test_decide_true_when_mtime_stale(tmp_path):
    f = tmp_path / "idle.jsonl"
    f.write_text('{"type":"assistant"}\n')
    now = time.time()
    # mtime is now; we pretend "now" is 180s later -> idle for 180s > 120
    assert decide_to_complete(f, threshold_seconds=120, _now=now + 180) is True


def test_decide_false_at_exact_threshold(tmp_path):
    """Exactly at the threshold is NOT idle (must be >= to complete)."""
    f = tmp_path / "edge.jsonl"
    f.write_text("{}\n")
    real_mtime = f.stat().st_mtime
    # exactly at threshold
    assert decide_to_complete(f, threshold_seconds=120, _now=real_mtime + 119) is False
    # just over threshold
    assert decide_to_complete(f, threshold_seconds=120, _now=real_mtime + 120) is True


def test_decide_respects_custom_threshold(tmp_path):
    f = tmp_path / "custom.jsonl"
    f.write_text("{}\n")
    now = time.time()
    assert decide_to_complete(f, threshold_seconds=30, _now=now + 29) is False
    assert decide_to_complete(f, threshold_seconds=30, _now=now + 31) is True


# ---------------------------------------------------------------------------
# Unit: find_transcript
# ---------------------------------------------------------------------------

def test_find_transcript_subagents_dir(tmp_path):
    """find_transcript locates a subagent jsonl file that mentions the agent."""
    # Mirror: ~/.claude/projects/-<label>/<session>/subagents/agent-xxx.jsonl
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    label = str(repo_root).replace("/", "-").lstrip("-")

    # Patch home so the claude projects dir lands in tmp_path
    fake_home = tmp_path / "home"
    projects_dir = fake_home / ".claude" / "projects" / f"-{label}"
    session_dir = projects_dir / "sess-abc123"
    subagents_dir = session_dir / "subagents"
    subagents_dir.mkdir(parents=True)

    agent_file = subagents_dir / "agent-001.jsonl"
    agent_file.write_text(
        json.dumps({"type": "system", "content": "agent my-test-agent task"}) + "\n"
    )

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        result = find_transcript("my-test-agent", repo_root=repo_root)

    assert result == agent_file


def test_find_transcript_returns_none_when_no_match(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    with patch("services.heartbeat_idle.Path.home", return_value=tmp_path / "empty_home"):
        result = find_transcript("no-such-agent", repo_root=repo_root)
    assert result is None


def test_find_transcript_legacy_markdown(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcripts = repo_root / "transcripts"
    transcripts.mkdir()
    md = transcripts / "legacy-agent.md"
    md.write_text("# Legacy Agent\nsome content\n")

    with patch("services.heartbeat_idle.Path.home", return_value=tmp_path / "empty_home"):
        result = find_transcript("legacy-agent", repo_root=repo_root)

    assert result == md


def test_find_transcript_prefers_freshest_subagent(tmp_path):
    """When two subagent files match, the freshest is returned."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    label = str(repo_root).replace("/", "-").lstrip("-")

    fake_home = tmp_path / "home"
    projects_dir = fake_home / ".claude" / "projects" / f"-{label}"

    older_dir = projects_dir / "sess-old" / "subagents"
    older_dir.mkdir(parents=True)
    older_file = older_dir / "agent-0.jsonl"
    older_file.write_text('{"type":"user","content":"my-agent start"}\n')

    newer_dir = projects_dir / "sess-new" / "subagents"
    newer_dir.mkdir(parents=True)
    newer_file = newer_dir / "agent-1.jsonl"
    newer_file.write_text('{"type":"user","content":"my-agent continue"}\n')

    # Force mtime ordering
    import os
    now = time.time()
    os.utime(older_file, (now - 300, now - 300))
    os.utime(newer_file, (now - 10, now - 10))

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        result = find_transcript("my-agent", repo_root=repo_root)

    assert result == newer_file


# ---------------------------------------------------------------------------
# Integration: register agent, simulate idle transcript, assert auto-complete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_agent_auto_complete_via_idle_detection(tmp_path):
    """
    Integration: register an agent, create a fake idle transcript,
    call decide_to_complete, assert it returns True, POST /complete,
    verify the row flips to completed.
    """
    import os
    from main import app

    agent_name = f"orphan-idle-{uuid.uuid4().hex[:8]}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/register",
            json={
                "name": agent_name,
                "model": "sonnet",
                "budget": 1,
                "task": "orphan idle test",
                "source": "claude-code",
            },
        )
        assert resp.status_code == 200

        list_resp = await client.get("/api/agents")
        agents = {a["name"]: a for a in list_resp.json().get("agents", [])}
        assert agents.get(agent_name, {}).get("status") == "running"

    # Create a fake transcript backdated 300s (well past 120s threshold)
    transcript = tmp_path / f"{agent_name}.jsonl"
    transcript.write_text('{"type":"assistant","content":"done"}\n')
    stale_time = time.time() - 300
    os.utime(transcript, (stale_time, stale_time))

    assert decide_to_complete(transcript, threshold_seconds=120) is True

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        complete_resp = await client.post(
            f"/api/agents/{agent_name}/complete",
            json={"summary": "Subagent exited, auto-completed by heartbeat idle detector"},
        )
        assert complete_resp.status_code == 200

        list_resp2 = await client.get("/api/agents")
        agents2 = {a["name"]: a for a in list_resp2.json().get("agents", [])}
        assert agents2.get(agent_name, {}).get("status") == "completed"


@pytest.mark.asyncio
async def test_active_agent_not_auto_completed(tmp_path):
    """Agent with a recently-touched transcript must NOT be auto-completed."""
    import os
    from main import app

    agent_name = f"active-idle-{uuid.uuid4().hex[:8]}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/agents/register",
            json={
                "name": agent_name,
                "model": "sonnet",
                "budget": 1,
                "task": "active test",
                "source": "claude-code",
            },
        )

    transcript = tmp_path / f"{agent_name}.jsonl"
    transcript.write_text('{"type":"assistant","content":"still working"}\n')
    recent = time.time() - 30
    os.utime(transcript, (recent, recent))

    assert decide_to_complete(transcript, threshold_seconds=120) is False

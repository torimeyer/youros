"""→2893: the agent transcript endpoint must return the agent's own log, never
the orchestrator's session.

Verified bug: for a harness-spawned agent (source claude-code) that registers
without a transcript_path, register-time linking (_link_session_jsonl) stores
the freshest TOP-LEVEL session JSONL under ~/.claude/projects/<label>/ into
meta["transcript_path"]. That file is the ORCHESTRATOR'S conversation, not the
agent's log. The resolver's transcript_path shortcut then returns it before the
subagents/agent-*.jsonl scan ever runs, so GET /api/agents/{name}/transcript
served someone else's transcript. Two different agents' "transcripts" differed
by exactly the length of their names (same content, different "name" field).

The agent's real log lives at
~/.claude/projects/<label>/<parent-session-id>/subagents/agent-<id>.jsonl.

Fix under test:
1. A transcript_path that points at a shared top-level session JSONL is never
   served as the agent's transcript unless the caller explicitly provided it
   at registration (transcript_path_source == "caller").
2. In that case resolution falls back to per-agent attribution (the
   subagents/agent-*.jsonl scan matched on the registered name).
3. When nothing can be attributed to the agent, the endpoint returns 404 with
   a clear detail. Never someone else's transcript.
4. Backend-spawned (source api) agents with transcripts/{name}.md keep working,
   and the existing 200 + empty contract for agents with no transcript at all
   is unchanged.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
import routers.agents as agents_module  # noqa: E402
from routers.agents import agent_metadata  # noqa: E402


ORCH_MARKER = "ORCH-MARKER-2893-only-in-the-orchestrator-session"
SUB_MARKER = "SUB-MARKER-2893-only-in-the-subagent-log"


@pytest.fixture(autouse=True)
def _reset_transcript_caches():
    """Cold resolver caches before and after every test (same idiom as
    test_agents.py): the resolver memoizes per name and per glob sweep, and
    tests reuse names across fresh tmp_paths."""
    agents_module._reset_transcript_resolver_cache()
    agents_module._reset_candidates_cache()
    agents_module._reset_meta_candidates_cache()
    agents_module._transcript_metrics_cache.clear()
    yield
    agents_module._reset_transcript_resolver_cache()
    agents_module._reset_candidates_cache()
    agents_module._reset_meta_candidates_cache()
    agents_module._transcript_metrics_cache.clear()


def _project_label(project_root: Path) -> str:
    return str(project_root).replace("/", "-").lstrip("-")


def _write_orchestrator_session(project_dir: Path, session_id: str) -> Path:
    """A top-level session JSONL directly under the project dir. This is the
    file _link_session_jsonl picks up: the orchestrator's own conversation."""
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{session_id}.jsonl"
    lines = [
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": f"orchestrator opening {ORCH_MARKER}"},
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"orchestrator reply {ORCH_MARKER}"}],
            },
        }),
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_subagent_jsonl(project_dir: Path, session_id: str, agent_name: str) -> Path:
    """The agent's real log: <project_dir>/<session>/subagents/agent-<id>.jsonl.
    Its first line is the spawn prompt, which embeds the register curl with the
    agent's name (the strict first-line match the resolver scan relies on)."""
    sub_dir = project_dir / session_id / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    p = sub_dir / "agent-afb286f7469e97553.jsonl"
    register_body = json.dumps({"name": agent_name, "task": "test task"})
    lines = [
        json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": (
                    "You are an agent. STEP 0 - register: curl -X POST "
                    "https://127.0.0.1:8000/api/agents/register -d '"
                    + register_body + "'"
                ),
            },
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"working now {SUB_MARKER}"}],
            },
        }),
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


async def _get_transcript(name: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(f"/api/agents/{name}/transcript")


# ---------------------------------------------------------------------------
# The bug: linked orchestrator session served as the agent's transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_jsonl_wins_over_linked_orchestrator_session(tmp_path, monkeypatch):
    """A harness-spawned agent whose transcript_path was register-time linked
    to the orchestrator's session JSONL must still get its OWN
    subagents/agent-*.jsonl log, found by registered name."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"

    orch = _write_orchestrator_session(project_dir, "e8224b5f-8d67-4b06-9493-af1397a06fb1")
    _write_subagent_jsonl(
        project_dir, "e8224b5f-8d67-4b06-9493-af1397a06fb1", "test-2893-worker"
    )

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata["test-2893-worker"] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        # What _link_session_jsonl stored: the orchestrator's session file.
        "transcript_path": str(orch),
    }
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp = await _get_transcript("test-2893-worker")
    finally:
        agent_metadata.pop("test-2893-worker", None)

    assert resp.status_code == 200
    data = resp.json()
    assert SUB_MARKER in data["content"], (
        "the agent's own subagents/agent-*.jsonl log must be served"
    )
    assert ORCH_MARKER not in data["content"], (
        "the orchestrator's session conversation leaked into the agent transcript"
    )


@pytest.mark.asyncio
async def test_refuses_orchestrator_session_when_no_per_agent_file(tmp_path, monkeypatch):
    """When the ONLY candidate is the linked orchestrator session and no file
    can be attributed to the agent, the endpoint returns 404 with a clear
    detail. Wrong data is worse than no data."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"

    orch = _write_orchestrator_session(project_dir, "e8224b5f-8d67-4b06-9493-af1397a06fb1")
    # No subagents/ file for this agent anywhere.

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata["test-2893-orphan"] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "transcript_path": str(orch),
    }
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp = await _get_transcript("test-2893-orphan")
    finally:
        agent_metadata.pop("test-2893-orphan", None)

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "test-2893-orphan" in detail
    assert ORCH_MARKER not in detail


@pytest.mark.asyncio
async def test_two_agents_linked_to_same_session_never_share_content(tmp_path, monkeypatch):
    """Yesterday's symptom: two agents' 'transcripts' were the same orchestrator
    conversation. With per-agent attribution, the agent with a real log gets
    its log, the one without gets 404. Nobody gets the shared session."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"
    session_id = "e8224b5f-8d67-4b06-9493-af1397a06fb1"

    orch = _write_orchestrator_session(project_dir, session_id)
    _write_subagent_jsonl(project_dir, session_id, "test-2893-alpha")

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    row = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "transcript_path": str(orch),
    }
    agent_metadata["test-2893-alpha"] = dict(row)
    agent_metadata["test-2893-beta"] = dict(row)
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp_a = await _get_transcript("test-2893-alpha")
            resp_b = await _get_transcript("test-2893-beta")
    finally:
        agent_metadata.pop("test-2893-alpha", None)
        agent_metadata.pop("test-2893-beta", None)

    assert resp_a.status_code == 200
    assert SUB_MARKER in resp_a.json()["content"]
    assert ORCH_MARKER not in resp_a.json()["content"]
    assert resp_b.status_code == 404


# ---------------------------------------------------------------------------
# Existing correct behavior that must keep working
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_spawned_md_transcript_still_served(tmp_path, monkeypatch):
    """Backend-spawned (source api) agents write transcripts/{name}.md under
    PROJECT_ROOT. That path must keep resolving exactly as before."""
    project_root = tmp_path / "repo"
    transcripts_dir = project_root / "transcripts"
    transcripts_dir.mkdir(parents=True)
    md_content = "api-spawned agent output line one\nline two\nreal work happened here\n" * 6
    (transcripts_dir / "test-2893-api-spawned.md").write_text(md_content)
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata["test-2893-api-spawned"] = {
        "source": "api",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "transcript_path": str(transcripts_dir / "test-2893-api-spawned.md"),
    }
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp = await _get_transcript("test-2893-api-spawned")
    finally:
        agent_metadata.pop("test-2893-api-spawned", None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == md_content
    assert data["empty"] is False


@pytest.mark.asyncio
async def test_explicit_caller_transcript_path_is_trusted(tmp_path, monkeypatch):
    """A transcript_path the CALLER provided at registration
    (transcript_path_source == 'caller') ranks first, even when it is shaped
    like a shared session JSONL. Explicit beats heuristics."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"

    session = _write_orchestrator_session(project_dir, "0000aaaa-1111-2222-3333-444455556666")

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata["test-2893-explicit"] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "transcript_path": str(session),
        "transcript_path_source": "caller",
    }
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp = await _get_transcript("test-2893-explicit")
    finally:
        agent_metadata.pop("test-2893-explicit", None)

    assert resp.status_code == 200
    assert ORCH_MARKER in resp.json()["content"]


@pytest.mark.asyncio
async def test_no_transcript_at_all_keeps_empty_contract(tmp_path, monkeypatch):
    """An agent with no transcript_path and nothing on disk keeps the existing
    200 + empty:true contract the Agents UI renders (absence, not error)."""
    project_root = tmp_path / "repo"
    (project_root / "transcripts").mkdir(parents=True)
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata["test-2893-nothing"] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resp = await _get_transcript("test-2893-nothing")
    finally:
        agent_metadata.pop("test-2893-nothing", None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["empty"] is True
    assert data["content"] == ""


# ---------------------------------------------------------------------------
# Provenance markers written at registration / link time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_marks_caller_provided_transcript_path(tmp_path):
    """POST /register with an explicit transcript_path stamps
    transcript_path_source='caller' so the endpoint can trust it."""
    log_path = tmp_path / "my-own-log.jsonl"
    log_path.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"), \
             patch.object(agents_module.chat_ack_bot, "start", lambda *a, **k: None):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post(
                "/api/agents/register",
                json={
                    "name": "test-2893-caller-path",
                    "task": "unique 2893 provenance check",
                    "source": "claude-code",
                    "transcript_path": str(log_path),
                },
            )
    try:
        assert resp.status_code == 200
        meta = agent_metadata["test-2893-caller-path"]
        assert meta["transcript_path"] == str(log_path)
        assert meta.get("transcript_path_source") == "caller"
    finally:
        agent_metadata.pop("test-2893-caller-path", None)


def test_link_session_jsonl_marks_session_link(tmp_path, monkeypatch):
    """_link_session_jsonl stamps transcript_path_source='session-link' so
    readers can tell a heuristic session link from a caller-provided path."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"
    _write_orchestrator_session(project_dir, "aaaabbbb-cccc-dddd-eeee-ffff00001111")

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    meta: dict = {}
    with patch("config.PROJECT_ROOT", project_root):
        linked = agents_module._link_session_jsonl(
            "test-2893-linked", meta, datetime.now(timezone.utc).isoformat()
        )

    assert linked is True
    assert meta["transcript_path"].endswith(".jsonl")
    assert meta.get("transcript_path_source") == "session-link"

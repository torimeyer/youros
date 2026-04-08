"""Tests for the agent workspace service and router."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_patched_service(tmp_path: Path):
    """Return an AgentWorkspaceService that reads/writes to tmp_path."""
    import services.agent_workspace as mod
    from services.agent_workspace import AgentWorkspaceService

    class PatchedService(AgentWorkspaceService):
        def _load(self_inner):
            f = tmp_path / "agent_workspace.json"
            if not f.exists():
                return []
            try:
                from services.agent_workspace import WorkspaceMessage
                return [WorkspaceMessage.from_dict(d) for d in json.loads(f.read_text())]
            except (json.JSONDecodeError, OSError):
                return []

        def _save(self_inner, messages):
            tmp_path.mkdir(parents=True, exist_ok=True)
            (tmp_path / "agent_workspace.json").write_text(
                json.dumps([m.to_dict() for m in messages], indent=2)
            )

    return PatchedService()


# ---------------------------------------------------------------------------
# Unit tests for the service
# ---------------------------------------------------------------------------

class TestAgentWorkspaceService:
    def test_post_message_stores_message(self, tmp_path):
        svc = make_patched_service(tmp_path)
        msg = svc.post_message("agent-a", "I found X", "finding")
        assert msg.agent_name == "agent-a"
        assert msg.content == "I found X"
        assert msg.message_type == "finding"
        assert msg.id
        assert msg.timestamp

    def test_get_messages_returns_all(self, tmp_path):
        svc = make_patched_service(tmp_path)
        svc.post_message("agent-a", "finding one", "finding")
        svc.post_message("agent-b", "result two", "result")
        msgs = svc.get_messages()
        assert len(msgs) == 2

    def test_get_messages_filters_by_agent(self, tmp_path):
        svc = make_patched_service(tmp_path)
        svc.post_message("agent-a", "finding one", "finding")
        svc.post_message("agent-b", "result two", "result")
        msgs = svc.get_messages(agent_name="agent-a")
        assert len(msgs) == 1
        assert msgs[0].agent_name == "agent-a"

    def test_get_messages_filters_since_id(self, tmp_path):
        svc = make_patched_service(tmp_path)
        m1 = svc.post_message("agent-a", "first", "finding")
        m2 = svc.post_message("agent-a", "second", "finding")
        svc.post_message("agent-a", "third", "finding")
        # since m1 should return m2 and m3
        msgs = svc.get_messages(since_id=m1.id)
        assert len(msgs) == 2
        assert msgs[0].content == "second"
        assert msgs[1].content == "third"

    def test_get_messages_since_unknown_id_returns_all(self, tmp_path):
        svc = make_patched_service(tmp_path)
        svc.post_message("agent-a", "first", "finding")
        msgs = svc.get_messages(since_id="no-such-id")
        assert len(msgs) == 1

    def test_clear_workspace_removes_all(self, tmp_path):
        svc = make_patched_service(tmp_path)
        svc.post_message("agent-a", "first", "finding")
        svc.post_message("agent-b", "second", "result")
        count = svc.clear_workspace()
        assert count == 2
        assert svc.get_messages() == []

    def test_clear_workspace_empty_returns_zero(self, tmp_path):
        svc = make_patched_service(tmp_path)
        count = svc.clear_workspace()
        assert count == 0

    def test_invalid_message_type_defaults_to_finding(self, tmp_path):
        svc = make_patched_service(tmp_path)
        msg = svc.post_message("agent-a", "content", "bogus_type")
        assert msg.message_type == "finding"

    def test_get_summary_empty(self, tmp_path):
        svc = make_patched_service(tmp_path)
        assert svc.get_summary() == ""

    def test_get_summary_has_content(self, tmp_path):
        svc = make_patched_service(tmp_path)
        svc.post_message("agent-a", "Found that X is true", "finding")
        svc.post_message("agent-b", "Result: Y equals 42", "result")
        summary = svc.get_summary()
        assert "agent-a" in summary
        assert "Found that X is true" in summary
        assert "agent-b" in summary
        assert "Result: Y equals 42" in summary
        assert "FINDING" in summary
        assert "RESULT" in summary

    def test_all_valid_message_types(self, tmp_path):
        svc = make_patched_service(tmp_path)
        for msg_type in ("finding", "question", "result", "context"):
            msg = svc.post_message("agent-x", "content", msg_type)
            assert msg.message_type == msg_type


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_messages_empty(client):
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.get_messages.return_value = []
        resp = await client.get("/api/workspace/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_post_message_success(client):
    from services.agent_workspace import WorkspaceMessage
    from datetime import datetime, timezone
    fake_msg = WorkspaceMessage(
        id="test-id",
        agent_name="test-agent",
        content="I found something",
        message_type="finding",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.post_message.return_value = fake_msg
        resp = await client.post("/api/workspace/messages", json={
            "agent_name": "test-agent",
            "content": "I found something",
            "message_type": "finding",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"]["agent_name"] == "test-agent"
    assert data["message"]["content"] == "I found something"


@pytest.mark.asyncio
async def test_post_message_missing_agent_name(client):
    with patch("routers.workspace.agent_workspace_service"):
        resp = await client.post("/api/workspace/messages", json={
            "agent_name": "",
            "content": "something",
            "message_type": "finding",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_message_missing_content(client):
    with patch("routers.workspace.agent_workspace_service"):
        resp = await client.post("/api/workspace/messages", json={
            "agent_name": "agent-a",
            "content": "",
            "message_type": "finding",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_message_invalid_type(client):
    with patch("routers.workspace.agent_workspace_service"):
        resp = await client.post("/api/workspace/messages", json={
            "agent_name": "agent-a",
            "content": "something",
            "message_type": "not_a_type",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_clear_messages(client):
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.clear_workspace.return_value = 5
        resp = await client.delete("/api/workspace/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cleared_count"] == 5
    assert data["result"] == "workspace cleared"


@pytest.mark.asyncio
async def test_get_summary_empty(client):
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.get_summary.return_value = ""
        mock_svc.get_messages.return_value = []
        resp = await client.get("/api/workspace/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_content"] is False
    assert data["message_count"] == 0
    assert data["summary"] == ""


@pytest.mark.asyncio
async def test_get_summary_with_content(client):
    from services.agent_workspace import WorkspaceMessage
    from datetime import datetime, timezone
    fake_msg = WorkspaceMessage(
        id="x",
        agent_name="agent-a",
        content="Found it",
        message_type="finding",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.get_summary.return_value = "Shared workspace context..."
        mock_svc.get_messages.return_value = [fake_msg]
        resp = await client.get("/api/workspace/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_content"] is True
    assert data["message_count"] == 1


@pytest.mark.asyncio
async def test_list_messages_with_filters(client):
    with patch("routers.workspace.agent_workspace_service") as mock_svc:
        mock_svc.get_messages.return_value = []
        resp = await client.get("/api/workspace/messages?since_id=abc&agent=agent-a")
    assert resp.status_code == 200
    mock_svc.get_messages.assert_called_once_with(since_id="abc", agent_name="agent-a")

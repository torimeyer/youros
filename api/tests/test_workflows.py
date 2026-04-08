"""Tests for the workflow CRUD and status tracking endpoints."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.fixture
def tmp_workflows_file(tmp_path):
    """Patch WORKFLOWS_FILE to a temp location so tests don't touch ~/.myos."""
    wf_file = tmp_path / "workflows.json"
    with patch("services.workflows.WORKFLOWS_FILE", wf_file), \
         patch("services.workflows.MYOS_DIR", tmp_path):
        yield wf_file


@pytest.fixture
def client(tmp_workflows_file):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_workflows_empty(client, tmp_workflows_file):
    async with client as c:
        r = await c.get("/api/workflows")
    assert r.status_code == 200
    assert r.json()["workflows"] == []


@pytest.mark.asyncio
async def test_create_workflow(client, tmp_workflows_file):
    payload = {
        "name": "My Pipeline",
        "steps": [
            {"agent_name": "researcher", "prompt": "Research topic X", "model": "sonnet", "budget": 1.0, "depends_on": []},
            {"agent_name": "writer", "prompt": "Write report on topic X", "model": "sonnet", "budget": 2.0, "depends_on": ["step-1"]},
        ],
    }
    async with client as c:
        r = await c.post("/api/workflows", json=payload)
    assert r.status_code == 200
    wf = r.json()["workflow"]
    assert wf["name"] == "My Pipeline"
    assert len(wf["steps"]) == 2
    assert wf["status"] == "pending"
    assert wf["id"]


@pytest.mark.asyncio
async def test_list_workflows_after_create(client, tmp_workflows_file):
    payload = {"name": "Pipeline A", "steps": [{"agent_name": "agent1", "prompt": "do X"}]}
    async with client as c:
        await c.post("/api/workflows", json=payload)
        r = await c.get("/api/workflows")
    assert r.status_code == 200
    workflows = r.json()["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["name"] == "Pipeline A"


@pytest.mark.asyncio
async def test_get_workflow_status(client, tmp_workflows_file):
    payload = {
        "name": "Status Check Pipeline",
        "steps": [{"agent_name": "alpha", "prompt": "step one"}],
    }
    async with client as c:
        create_r = await c.post("/api/workflows", json=payload)
        wf_id = create_r.json()["workflow"]["id"]
        r = await c.get(f"/api/workflows/{wf_id}/status")
    assert r.status_code == 200
    body = r.json()["workflow"]
    assert body["id"] == wf_id
    assert body["status"] == "pending"
    assert len(body["steps"]) == 1
    assert body["steps"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_workflow_status_not_found(client, tmp_workflows_file):
    async with client as c:
        r = await c.get("/api/workflows/nonexistent/status")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_workflow(client, tmp_workflows_file):
    payload = {"name": "Delete Me", "steps": [{"agent_name": "a", "prompt": "p"}]}
    async with client as c:
        create_r = await c.post("/api/workflows", json=payload)
        wf_id = create_r.json()["workflow"]["id"]
        del_r = await c.delete(f"/api/workflows/{wf_id}")
        assert del_r.status_code == 200
        list_r = await c.get("/api/workflows")
    assert list_r.json()["workflows"] == []


@pytest.mark.asyncio
async def test_delete_workflow_not_found(client, tmp_workflows_file):
    async with client as c:
        r = await c.delete("/api/workflows/doesnotexist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Service-level unit tests
# ---------------------------------------------------------------------------


def test_create_workflow_assigns_step_ids(tmp_workflows_file):
    from services.workflows import create_workflow, get_workflow
    wf = create_workflow("Test", [
        {"agent_name": "a1", "prompt": "do A"},
        {"agent_name": "a2", "prompt": "do B", "depends_on": ["step-1"]},
    ])
    assert wf["steps"][0]["id"] == "step-1"
    assert wf["steps"][1]["id"] == "step-2"
    assert wf["steps"][1]["depends_on"] == ["step-1"]


def test_create_workflow_persists(tmp_workflows_file):
    from services.workflows import create_workflow, list_workflows
    create_workflow("Persist Test", [{"agent_name": "x", "prompt": "go"}])
    workflows = list_workflows()
    assert len(workflows) == 1
    assert workflows[0]["name"] == "Persist Test"


def test_delete_workflow_returns_false_for_missing(tmp_workflows_file):
    from services.workflows import delete_workflow
    assert delete_workflow("no-such-id") is False


def test_get_workflow_status_returns_none_for_missing(tmp_workflows_file):
    from services.workflows import get_workflow_status
    assert get_workflow_status("phantom") is None


def test_step_defaults(tmp_workflows_file):
    from services.workflows import create_workflow
    wf = create_workflow("Defaults", [{"agent_name": "bot", "prompt": "hi"}])
    step = wf["steps"][0]
    assert step["model"] == "sonnet"
    assert step["budget"] == 2.0
    assert step["depends_on"] == []
    assert step["status"] == "pending"


# ---------------------------------------------------------------------------
# run_workflow unit test (mocked spawning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_workflow_marks_done(tmp_workflows_file):
    """run_workflow should mark steps done when agents exit cleanly."""
    from services.workflows import create_workflow, run_workflow, get_workflow_status

    wf = create_workflow("Run Test", [
        {"agent_name": "step-agent-1", "prompt": "do step 1"},
    ])

    # Mock spawn_agent and the resulting process
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("services.workflows.run_workflow", wraps=None) as _, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
         patch("routers.agents.active_agents", {}) as mock_active:

        async def fake_spawn(body):
            mock_active[body.name] = mock_proc
            return {"result": "ok"}

        mock_spawn.side_effect = fake_spawn

        result = await run_workflow(wf["id"])

    assert result["status"] == "done"
    assert result["steps"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_run_workflow_not_found(tmp_workflows_file):
    from services.workflows import run_workflow
    with pytest.raises(ValueError, match="not found"):
        await run_workflow("bad-id")


# ---------------------------------------------------------------------------
# PUT /api/workflows/{id} -- update existing workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_workflow(client, tmp_workflows_file):
    """PUT replaces name and steps on an existing workflow."""
    payload = {"name": "Old Name", "steps": [{"agent_name": "a", "prompt": "original"}]}
    async with client as c:
        create_r = await c.post("/api/workflows", json=payload)
        wf_id = create_r.json()["workflow"]["id"]

        update_payload = {
            "name": "New Name",
            "steps": [
                {"agent_name": "a", "prompt": "updated prompt"},
                {"agent_name": "b", "prompt": "second step"},
            ],
        }
        put_r = await c.put(f"/api/workflows/{wf_id}", json=update_payload)
        assert put_r.status_code == 200
        updated = put_r.json()["workflow"]
        assert updated["name"] == "New Name"
        assert len(updated["steps"]) == 2
        assert updated["steps"][0]["prompt"] == "updated prompt"

        # Confirm persisted
        status_r = await c.get(f"/api/workflows/{wf_id}/status")
    assert status_r.json()["workflow"]["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_workflow_not_found(client, tmp_workflows_file):
    """PUT on a missing workflow returns 404."""
    async with client as c:
        r = await c.put("/api/workflows/ghost", json={"name": "X", "steps": []})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/workflows/templates -- built-in automation templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_automation_templates(client, tmp_workflows_file):
    """GET /workflows/templates returns the built-in automation templates."""
    async with client as c:
        r = await c.get("/api/workflows/templates")
    assert r.status_code == 200
    body = r.json()
    assert "templates" in body
    templates = body["templates"]
    assert len(templates) == 4
    ids = {t["id"] for t in templates}
    assert ids == {
        "builtin-daily-standup",
        "builtin-weekly-review",
        "builtin-meeting-prep",
        "builtin-inbox-triage",
    }
    # Each template must have the fields the frontend relies on
    for tpl in templates:
        assert tpl["name"]
        assert tpl["description"]
        assert tpl["icon"]
        assert isinstance(tpl["steps"], list)
        assert len(tpl["steps"]) >= 1
        for step in tpl["steps"]:
            assert step["name"]
            assert step["prompt"]

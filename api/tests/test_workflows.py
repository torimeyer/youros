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
# GET /api/workflows/{id}/runs -- run history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_endpoint_empty_before_any_run(client, tmp_workflows_file):
    """A newly created workflow has no run history."""
    payload = {"name": "Fresh Pipeline", "steps": [{"agent_name": "bot", "prompt": "go"}]}
    async with client as c:
        create_r = await c.post("/api/workflows", json=payload)
        wf_id = create_r.json()["workflow"]["id"]
        runs_r = await c.get(f"/api/workflows/{wf_id}/runs")
    assert runs_r.status_code == 200
    assert runs_r.json()["runs"] == []


@pytest.mark.asyncio
async def test_runs_endpoint_not_found(client, tmp_workflows_file):
    """GET /runs on a missing workflow returns 404."""
    async with client as c:
        r = await c.get("/api/workflows/ghost/runs")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_runs_endpoint_records_run_result(client, tmp_workflows_file):
    """After a workflow completes, GET /runs shows one entry with the right data."""
    from unittest.mock import patch, AsyncMock, MagicMock

    payload = {
        "name": "Run History Test",
        "steps": [{"agent_name": "runner-bot", "prompt": "do the work"}],
    }
    async with client as c:
        create_r = await c.post("/api/workflows", json=payload)
        wf_id = create_r.json()["workflow"]["id"]

        # Simulate a clean agent execution
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
             patch("routers.agents.active_agents", {}) as mock_active:

            async def fake_spawn(body):
                mock_active[body.name] = mock_proc
                return {"result": "ok"}

            mock_spawn.side_effect = fake_spawn

            # Trigger the run and wait for it to complete synchronously
            from services.workflows import run_workflow
            await run_workflow(wf_id)

        # Now the runs endpoint must show one completed run
        runs_r = await c.get(f"/api/workflows/{wf_id}/runs")

    assert runs_r.status_code == 200
    runs = runs_r.json()["runs"]
    assert len(runs) == 1

    run = runs[0]
    assert run["status"] == "done"
    assert run["run_id"]
    assert run["completed_at"]
    # Duration must be recorded (may be 0.0 on fast machines)
    assert "duration_seconds" in run
    # Per-step data must be present
    assert len(run["steps"]) == 1
    assert run["steps"][0]["agent_name"] == "runner-bot"
    assert run["steps"][0]["status"] == "done"


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
    assert len(templates) == 10
    ids = {t["id"] for t in templates}
    assert ids == {
        "builtin-daily-standup",
        "builtin-weekly-review",
        "builtin-meeting-prep",
        "builtin-inbox-triage",
        "builtin-eod-recap",
        "builtin-stale-tasks",
        "builtin-project-status",
        "builtin-email-followup",
        "builtin-pre-release",
        "builtin-sprint-planning",
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


# ---------------------------------------------------------------------------
# Workflow step-agent cleanup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_agents_cancelled_when_workflow_completes(tmp_workflows_file):
    """When a workflow with 3 steps completes, all step agents are cancelled."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from services.workflows import create_workflow, run_workflow

    wf = create_workflow("Cleanup Test", [
        {"agent_name": "step-agent-a", "prompt": "do A"},
        {"agent_name": "step-agent-b", "prompt": "do B"},
        {"agent_name": "step-agent-c", "prompt": "do C"},
    ])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    # Inject fake running metadata so _cancel_step_agents has something to cancel
    from routers.agents import agent_metadata, _save_agent_state
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for name in ("step-agent-a", "step-agent-b", "step-agent-c"):
        agent_metadata[name] = {
            "status": "running",
            "spawned_at": now_iso,
            "last_heartbeat_at": now_iso,
            "budget": "2.0",
            "model": "sonnet",
            "source": "api",
        }
    _save_agent_state()

    with patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
         patch("routers.agents.active_agents", {}) as mock_active:

        async def fake_spawn(body):
            mock_active[body.name] = mock_proc
            # Record workflow_run_id as it would be in real registration
            if body.workflow_run_id and body.name in agent_metadata:
                agent_metadata[body.name]["workflow_run_id"] = body.workflow_run_id
            return {"result": "ok"}

        mock_spawn.side_effect = fake_spawn

        result = await run_workflow(wf["id"])

    assert result["status"] == "done"

    # All three step agents must be cancelled (not running)
    terminal_statuses = {"cancelled", "completed", "failed"}
    for name in ("step-agent-a", "step-agent-b", "step-agent-c"):
        meta = agent_metadata.get(name, {})
        assert meta.get("status") in terminal_statuses, (
            f"Agent '{name}' is still '{meta.get('status')}' after workflow finished"
        )
        if meta.get("status") == "cancelled":
            assert meta.get("terminated_reason") == "workflow ended"


@pytest.mark.asyncio
async def test_step_agents_cancelled_when_workflow_fails(tmp_workflows_file):
    """When a workflow step fails, remaining agents are also cancelled."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from services.workflows import create_workflow, run_workflow

    wf = create_workflow("Fail Cleanup Test", [
        {"agent_name": "fail-agent-x", "prompt": "fail here"},
    ])

    from routers.agents import agent_metadata, _save_agent_state
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    agent_metadata["fail-agent-x"] = {
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "budget": "2.0",
        "model": "sonnet",
        "source": "api",
    }
    _save_agent_state()

    mock_proc = MagicMock()
    mock_proc.returncode = 1  # non-zero exit = failure
    mock_proc.wait = AsyncMock(return_value=1)

    with patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
         patch("routers.agents.active_agents", {}) as mock_active:

        async def fake_spawn(body):
            mock_active[body.name] = mock_proc
            return {"result": "ok"}

        mock_spawn.side_effect = fake_spawn

        result = await run_workflow(wf["id"])

    assert result["status"] == "failed"
    meta = agent_metadata.get("fail-agent-x", {})
    # After failure the direct-cancel runs; running agents are cancelled
    assert meta.get("status") in {"cancelled", "failed", "completed"}


@pytest.mark.asyncio
async def test_reconcile_cancels_orphaned_step_agent(tmp_workflows_file):
    """An agent whose workflow is already terminal (>2 min ago) gets auto-cancelled by the reconcile pass."""
    from routers.agents import (
        agent_metadata,
        _save_agent_state,
        _reconcile_workflow_step_agents,
    )
    from datetime import datetime, timezone, timedelta

    # Create a workflow and mark it done more than 2 minutes ago
    from services.workflows import create_workflow, _load, _save, WF_DONE
    wf = create_workflow("Orphan Test", [{"agent_name": "orphan-step-bot", "prompt": "work"}])
    data = _load()
    data[wf["id"]]["status"] = WF_DONE
    data[wf["id"]]["completed_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    _save(data)

    # Register a step agent pointing at that workflow (already terminal)
    now_iso = datetime.now(timezone.utc).isoformat()
    agent_metadata["orphan-step-bot"] = {
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "budget": "2.0",
        "model": "sonnet",
        "source": "api",
        "workflow_run_id": wf["id"],
    }
    _save_agent_state()

    changed = _reconcile_workflow_step_agents()

    assert changed is True
    meta = agent_metadata["orphan-step-bot"]
    assert meta["status"] == "cancelled"
    assert meta["terminated_reason"] == "workflow ended"


def test_reconcile_does_not_cancel_user_spawned_agent(tmp_workflows_file):
    """An agent with no workflow_run_id must never be touched by the reconcile pass."""
    from routers.agents import (
        agent_metadata,
        _save_agent_state,
        _reconcile_workflow_step_agents,
    )
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    agent_metadata["plain-user-agent"] = {
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "budget": "2.0",
        "model": "sonnet",
        "source": "api",
        # No workflow_run_id
    }
    _save_agent_state()

    changed = _reconcile_workflow_step_agents()

    assert changed is False
    assert agent_metadata["plain-user-agent"]["status"] == "running"


@pytest.mark.asyncio
async def test_reconcile_does_not_cancel_agent_within_grace_window(tmp_workflows_file):
    """An orphaned step agent is not cancelled within the 2-minute grace window."""
    from routers.agents import (
        agent_metadata,
        _save_agent_state,
        _reconcile_workflow_step_agents,
    )
    from datetime import datetime, timezone, timedelta
    from services.workflows import create_workflow, _load, _save, WF_DONE

    wf = create_workflow("Grace Test", [{"agent_name": "grace-step-bot", "prompt": "go"}])
    data = _load()
    # Mark done only 30 seconds ago - within grace window
    data[wf["id"]]["status"] = WF_DONE
    data[wf["id"]]["completed_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    ).isoformat()
    _save(data)

    now_iso = datetime.now(timezone.utc).isoformat()
    agent_metadata["grace-step-bot"] = {
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "budget": "2.0",
        "model": "sonnet",
        "source": "api",
        "workflow_run_id": wf["id"],
    }
    _save_agent_state()

    changed = _reconcile_workflow_step_agents()

    assert changed is False
    assert agent_metadata["grace-step-bot"]["status"] == "running"


# ---------------------------------------------------------------------------
# Weekly review template runnable end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_review_workflow_runs_end_to_end(client, tmp_workflows_file):
    """POST /workflows/weekly-review/run materialises the template and runs it.

    This proves the live demo flow: the user asks to run "weekly-review" by
    its short alias and the API resolves it to the built-in template, saves
    it as a runnable workflow, and kicks off all three step agents.
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("routers.agents.spawn_agent", new_callable=AsyncMock), \
         patch("routers.agents.active_agents", {
             "closed-this-week": mock_proc,
             "still-open": mock_proc,
             "write-the-review": mock_proc,
         }):
        async with client as c:
            r = await c.post("/api/workflows/weekly-review/run")
            assert r.status_code == 200
            body = r.json()
            assert body["workflow"]["id"] == "builtin-weekly-review"
            assert body["workflow"]["name"] == "Weekly review"

            # The async run_workflow task was scheduled. Drive it to
            # completion synchronously so we can assert end-state.
            from services.workflows import run_workflow
            await run_workflow("builtin-weekly-review")

            status_r = await c.get("/api/workflows/builtin-weekly-review/status")
            assert status_r.status_code == 200
            wf = status_r.json()["workflow"]
            assert wf["status"] == "done"
            assert all(s["status"] == "done" for s in wf["steps"])


@pytest.mark.asyncio
async def test_weekly_review_step_agents_all_resolve(client, tmp_workflows_file):
    """All three step agents (closed-this-week, still-open, write-the-review)
    are resolvable from the template and run in dependency order."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("routers.agents.spawn_agent", new_callable=AsyncMock), \
         patch("routers.agents.active_agents", {
             "closed-this-week": mock_proc,
             "still-open": mock_proc,
             "write-the-review": mock_proc,
         }):
        async with client as c:
            # Use the full template id this time
            r = await c.post("/api/workflows/builtin-weekly-review/run")
            assert r.status_code == 200
            steps = r.json()["workflow"]["steps"]
            agent_names = [s["agent_name"] for s in steps]
            assert agent_names == ["closed-this-week", "still-open", "write-the-review"]
            # Steps must depend on the previous one so they run in order
            assert steps[0]["depends_on"] == []
            assert steps[1]["depends_on"] == ["step-1"]
            assert steps[2]["depends_on"] == ["step-2"]

            from services.workflows import run_workflow
            await run_workflow("builtin-weekly-review")

            status_r = await c.get("/api/workflows/builtin-weekly-review/status")
            wf = status_r.json()["workflow"]
            for step in wf["steps"]:
                assert step["status"] == "done", f"{step['agent_name']} did not resolve"

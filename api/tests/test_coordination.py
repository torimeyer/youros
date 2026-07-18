"""Tests for the /api/coordination HTTP endpoints."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_blockers_route_is_removed(client):
    """→2924: the blockers route is gone.

    GET /coordination/blockers auto-created a "[Blocker] ..." task for every
    result of an unscoped Jira query on every Dashboard mount, flooding the
    task list and starving the backend until Tasks polling timed out. The
    route and its task-creation side effect were removed outright, so a
    request must now 404.
    """
    resp = await client.get("/api/coordination/blockers")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dependencies_returns_empty_when_not_connected(client):
    with patch("routers.coordination.atlassian_service.is_connected", return_value=False):
        resp = await client.get("/api/coordination/dependencies/PROJ-99")
    assert resp.status_code == 200
    body = resp.json()
    # The response is a graph shape: {nodes: [], edges: []}
    assert "nodes" in body
    assert body["nodes"] == []
    assert body["edges"] == []

"""RED tests for narrative router — exec-update drafts (→1437).

Tests must FAIL before api/routers/narrative.py exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/narrative/sources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrative_sources_returns_list(client):
    """Sources endpoint returns an object with a 'sources' list."""
    resp = await client.get("/api/narrative/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert isinstance(data["sources"], list)


@pytest.mark.asyncio
async def test_narrative_sources_graceful_when_atlassian_disconnected(client):
    """When Atlassian is not connected, sources still returns 200 (not 500)."""
    with patch("services.atlassian.is_connected", return_value=False):
        resp = await client.get("/api/narrative/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data


@pytest.mark.asyncio
async def test_narrative_sources_includes_atlassian_when_connected(client):
    """When Atlassian is connected, Jira + Confluence sources appear."""
    mock_issues = [
        {"key": "PROJ-1", "summary": "Epic one", "type": "Epic", "status": "In Progress"}
    ]
    mock_pages = [
        {"id": "123", "title": "Strategy doc", "space": "TEAM", "url": "https://example.atlassian.net/wiki/..."}
    ]
    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=mock_issues)),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=mock_pages)),
    ):
        resp = await client.get("/api/narrative/sources")
    assert resp.status_code == 200
    sources = resp.json()["sources"]
    kinds = [s["kind"] for s in sources]
    assert "jira_issue" in kinds
    assert "confluence_page" in kinds


# ---------------------------------------------------------------------------
# POST /api/narrative/draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_narrative_draft_returns_required_fields(client, tmp_path):
    """POST /narrative/draft returns draft_id, markdown, source_refs."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path):
        resp = await client.post(
            "/api/narrative/draft",
            json={"audience": "exec", "window_days": 7, "source_ids": []},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "draft_id" in data
    assert "markdown" in data
    assert "source_refs" in data
    assert isinstance(data["source_refs"], list)


@pytest.mark.asyncio
async def test_narrative_draft_persists_to_disk(client, tmp_path):
    """Draft JSON is written to NARRATIVES_DIR/{draft_id}.json."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path):
        resp = await client.post(
            "/api/narrative/draft",
            json={"audience": "exec", "window_days": 7, "source_ids": []},
        )
    assert resp.status_code == 200
    data = resp.json()
    draft_file = tmp_path / f"{data['draft_id']}.json"
    assert draft_file.exists(), "Draft file should be persisted to disk"
    saved = json.loads(draft_file.read_text())
    assert saved["draft_id"] == data["draft_id"]
    assert "markdown" in saved


@pytest.mark.asyncio
async def test_narrative_draft_zero_sources_returns_placeholder(client, tmp_path):
    """Draft with no sources returns a placeholder markdown, not an error."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path):
        resp = await client.post(
            "/api/narrative/draft",
            json={"audience": "exec", "window_days": 7, "source_ids": []},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["markdown"]  # not empty
    assert isinstance(data["source_refs"], list)


@pytest.mark.asyncio
async def test_narrative_draft_creates_narratives_dir(client, tmp_path):
    """NARRATIVES_DIR is created if it doesn't exist."""
    narratives_dir = tmp_path / "narratives"
    assert not narratives_dir.exists()
    with patch("routers.narrative.NARRATIVES_DIR", narratives_dir):
        resp = await client.post(
            "/api/narrative/draft",
            json={"audience": "exec", "window_days": 7, "source_ids": []},
        )
    assert resp.status_code == 200
    assert narratives_dir.exists()

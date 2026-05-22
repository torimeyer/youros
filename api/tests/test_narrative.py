"""Tests for the /api/narrative HTTP endpoints."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock


@pytest.fixture(autouse=True)
def _redirect_narratives_dir(tmp_path, monkeypatch):
    """Redirect NARRATIVES_DIR to tmp so tests never touch ~/.myos/narratives/."""
    import routers.narrative as _nar
    fake_dir = tmp_path / "narratives"
    fake_dir.mkdir()
    monkeypatch.setattr(_nar, "NARRATIVES_DIR", fake_dir)
    monkeypatch.setenv("MYOS_DIR", str(tmp_path))
    yield


@pytest.mark.asyncio
async def test_list_drafts_returns_empty_when_no_files(client):
    resp = await client.get("/api/narrative/drafts")
    assert resp.status_code == 200
    assert resp.json() == {"drafts": []}


@pytest.mark.asyncio
async def test_get_draft_unknown_id_returns_404(client):
    resp = await client.get("/api/narrative/draft/nonexistent-draft-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_draft_persists_and_returns_id(client):
    fake_markdown = "## Weekly Update\nShipped login fix."
    with patch("routers.narrative._build_markdown_async",
               new_callable=AsyncMock, return_value=fake_markdown), \
         patch("routers.narrative.atlassian_service.is_connected", return_value=False):
        resp = await client.post("/api/narrative/draft",
                                  json={"audience": "exec", "window_days": 7})

    assert resp.status_code == 200
    body = resp.json()
    draft_id = body["draft_id"]
    assert draft_id
    assert body["markdown"] == fake_markdown
    assert isinstance(body["source_refs"], list)

    # Draft must be retrievable by the returned ID.
    get_resp = await client.get(f"/api/narrative/draft/{draft_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["draft_id"] == draft_id


@pytest.mark.asyncio
async def test_created_draft_appears_in_list(client):
    fake_markdown = "## Update\nContent."
    with patch("routers.narrative._build_markdown_async",
               new_callable=AsyncMock, return_value=fake_markdown), \
         patch("routers.narrative.atlassian_service.is_connected", return_value=False):
        post_resp = await client.post("/api/narrative/draft",
                                       json={"audience": "exec", "window_days": 7})
    draft_id = post_resp.json()["draft_id"]

    list_resp = await client.get("/api/narrative/drafts")
    assert list_resp.status_code == 200
    drafts = list_resp.json()["drafts"]
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft_id
    assert drafts[0]["audience"] == "exec"


@pytest.mark.asyncio
async def test_get_narrative_sources_returns_sources_key(client):
    with patch("routers.narrative.atlassian_service.is_connected", return_value=False):
        resp = await client.get("/api/narrative/sources")
    assert resp.status_code == 200
    assert "sources" in resp.json()
    assert isinstance(resp.json()["sources"], list)

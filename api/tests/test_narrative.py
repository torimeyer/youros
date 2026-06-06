"""Tests for the /api/narrative HTTP endpoints and get_ai_client() integration."""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def _redirect_narratives_dir(tmp_path, monkeypatch):
    """Redirect NARRATIVES_DIR to tmp so tests never touch ~/.myos/narratives/."""
    import routers.narrative as _nar
    fake_dir = tmp_path / "narratives"
    fake_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(_nar, "NARRATIVES_DIR", fake_dir)
    monkeypatch.setenv("MYOS_DIR", str(tmp_path))
    yield


def _make_llm_response(text: str) -> MagicMock:
    block = SimpleNamespace(type="text", text=text)
    resp = MagicMock()
    resp.content = [block]
    return resp


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


@pytest.mark.asyncio
async def test_narrative_draft_uses_get_ai_client(client):
    """create_narrative_draft calls get_ai_client(); with a real client returns 200."""
    mock_md = "## Week in review\n\nShipped the auth refactor."
    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=_make_llm_response(mock_md))

    with (
        patch("routers.narrative.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.narrative._gather_sources", new=AsyncMock(return_value=[])),
    ):
        resp = await client.post("/api/narrative/draft", json={"audience": "team", "window_days": 7})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_narrative_draft_falls_back_when_no_client(client):
    """When get_ai_client() returns None, draft still responds gracefully."""
    with (
        patch("routers.narrative.get_ai_client", new=AsyncMock(return_value=None)),
        patch("routers.narrative._gather_sources", new=AsyncMock(return_value=[])),
    ):
        resp = await client.post("/api/narrative/draft", json={"audience": "team", "window_days": 7})

    assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_narrative_uses_cli_client_when_always_subscription(client):
    """With ClaudeCliClient, narrative draft still returns 200."""
    from services.ai_backend import ClaudeCliClient
    mock_md = "## Week in review\n\nAll green."
    cli_client = ClaudeCliClient()
    cli_client.messages.create = AsyncMock(return_value=_make_llm_response(mock_md))

    with (
        patch("routers.narrative.get_ai_client", new=AsyncMock(return_value=cli_client)),
        patch("routers.narrative._gather_sources", new=AsyncMock(return_value=[])),
    ):
        resp = await client.post("/api/narrative/draft", json={"audience": "team", "window_days": 7})

    assert resp.status_code == 200

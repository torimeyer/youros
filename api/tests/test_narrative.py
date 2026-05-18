"""Tests for the narrative router — verifies get_ai_client() integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_llm_response(text: str) -> MagicMock:
    block = SimpleNamespace(type="text", text=text)
    resp = MagicMock()
    resp.content = [block]
    return resp


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

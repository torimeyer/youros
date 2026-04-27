from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_next_task_returns_message_when_suggestion_available(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.next_task = AsyncMock(return_value="→850 review prompts [P3]")
        resp = await client.get("/api/tasks/next")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"message": "→850 review prompts [P3]"}


@pytest.mark.asyncio
async def test_next_task_returns_fallback_when_empty(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.next_task = AsyncMock(return_value="")
        resp = await client.get("/api/tasks/next")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"message": "No open tasks right now."}


@pytest.mark.asyncio
async def test_next_task_returns_fallback_when_none(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.next_task = AsyncMock(return_value=None)
        resp = await client.get("/api/tasks/next")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"message": "No open tasks right now."}

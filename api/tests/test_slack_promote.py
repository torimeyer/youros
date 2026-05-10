"""Tests for POST /api/slack/triage/promote — Slack message to needle conversion."""

from unittest.mock import AsyncMock, patch

import pytest

from routers.slack import router  # noqa: F401  — fail loudly on import errors


# ---------------------------------------------------------------------------
# POST /api/slack/triage/promote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_not_connected(client):
    """Returns 401 when Slack is not connected."""
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post(
            "/api/slack/triage/promote",
            json={"channel_id": "C1", "ts": "123.456"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_promote_message_not_found(client):
    """Returns 404 when the ts doesn't match any message in the channel."""
    with patch("routers.slack.slack_service") as mock_svc, \
         patch("routers.slack.create_task"):
        mock_svc.is_connected.return_value = True
        mock_svc.fetch_messages = AsyncMock(return_value=[
            {"ts": "999.000", "user": "U1", "text": "Other message", "type": "message"},
        ])
        resp = await client.post(
            "/api/slack/triage/promote",
            json={"channel_id": "C1", "ts": "123.456"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_success(client):
    """200 with task_id; title from message text, body has permalink, source=slack."""
    mock_create = AsyncMock(return_value={"result": "→1234", "task_id": "1234"})
    with patch("routers.slack.slack_service") as mock_svc, \
         patch("routers.slack.create_task", mock_create):
        mock_svc.is_connected.return_value = True
        mock_svc.fetch_messages = AsyncMock(return_value=[
            {"ts": "123.456", "user": "U1", "text": "Ship the feature by Friday", "type": "message"},
        ])
        mock_svc.get_message_permalink = AsyncMock(
            return_value="https://slack.com/archives/C1/p123456"
        )
        resp = await client.post(
            "/api/slack/triage/promote",
            json={"channel_id": "C1", "ts": "123.456"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "1234"

    call_body = mock_create.call_args[0][0]
    assert "Ship the feature by Friday" in call_body.title
    assert "https://slack.com/archives/C1/p123456" in call_body.description
    assert call_body.source == "slack"


@pytest.mark.asyncio
async def test_promote_title_truncated_at_60_chars(client):
    """Long message text is truncated to 60 chars with trailing ellipsis."""
    long_text = "a" * 100
    mock_create = AsyncMock(return_value={"result": "→1235", "task_id": "1235"})
    with patch("routers.slack.slack_service") as mock_svc, \
         patch("routers.slack.create_task", mock_create):
        mock_svc.is_connected.return_value = True
        mock_svc.fetch_messages = AsyncMock(return_value=[
            {"ts": "123.456", "user": "U1", "text": long_text, "type": "message"},
        ])
        mock_svc.get_message_permalink = AsyncMock(return_value="")
        resp = await client.post(
            "/api/slack/triage/promote",
            json={"channel_id": "C1", "ts": "123.456"},
        )

    assert resp.status_code == 200
    call_body = mock_create.call_args[0][0]
    assert len(call_body.title) <= 60
    assert call_body.title.endswith("...")

"""Tests for Slack follow-up flagging endpoints and storage service."""

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connected_mock(mock_svc):
    mock_svc.is_connected.return_value = True
    mock_svc.get_message_permalink = AsyncMock(return_value="https://myworkspace.slack.com/archives/C1/p123456")


def _followup_payload(**overrides):
    base = {
        "channel_id": "C1",
        "channel_name": "general",
        "ts": "123.456",
        "user": "U1",
        "text": "Let us chat later about this.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# POST /api/slack/followups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_followup_not_connected(client, tmp_path):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/slack/followups", json=_followup_payload())

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_add_followup_success(client, tmp_path):
    followup_path = tmp_path / "slack_followups.json"

    with patch("routers.slack.slack_service") as mock_svc, \
         patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
         patch("services.slack_followups.MYOS_DIR", tmp_path):
        _connected_mock(mock_svc)
        resp = await client.post("/api/slack/followups", json=_followup_payload())

    assert resp.status_code == 200
    data = resp.json()
    assert data["channel_id"] == "C1"
    assert data["channel_name"] == "general"
    assert data["ts"] == "123.456"
    assert data["user"] == "U1"
    assert "id" in data
    assert "created_at" in data
    assert "permalink" in data


# ---------------------------------------------------------------------------
# GET /api/slack/followups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_followups_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/slack/followups")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_add_list_delete_cycle(client, tmp_path):
    """Full lifecycle: add a followup, list it, delete it, confirm it is gone."""
    followup_path = tmp_path / "slack_followups.json"

    with patch("routers.slack.slack_service") as mock_svc, \
         patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
         patch("services.slack_followups.MYOS_DIR", tmp_path):
        _connected_mock(mock_svc)

        # Add
        add_resp = await client.post("/api/slack/followups", json=_followup_payload())
        assert add_resp.status_code == 200
        followup_id = add_resp.json()["id"]

        # List
        list_resp = await client.get("/api/slack/followups")
        assert list_resp.status_code == 200
        items = list_resp.json()["followups"]
        assert len(items) == 1
        assert items[0]["id"] == followup_id

        # Delete
        del_resp = await client.request("DELETE", f"/api/slack/followups/{followup_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # Confirm gone
        list_after = await client.get("/api/slack/followups")
        assert list_after.json()["followups"] == []


# ---------------------------------------------------------------------------
# GET /api/slack/followups/count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/slack/followups/count")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_count_reflects_additions(client, tmp_path):
    followup_path = tmp_path / "slack_followups.json"

    with patch("routers.slack.slack_service") as mock_svc, \
         patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
         patch("services.slack_followups.MYOS_DIR", tmp_path):
        _connected_mock(mock_svc)

        # Count starts at zero
        resp = await client.get("/api/slack/followups/count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # Add two followups
        await client.post("/api/slack/followups", json=_followup_payload(ts="1.1"))
        await client.post("/api/slack/followups", json=_followup_payload(ts="1.2"))

        resp = await client.get("/api/slack/followups/count")
        assert resp.json()["count"] == 2


# ---------------------------------------------------------------------------
# DELETE /api/slack/followups/{id} -- idempotent (unknown id)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_unknown_id_returns_404(client, tmp_path):
    followup_path = tmp_path / "slack_followups.json"

    with patch("routers.slack.slack_service") as mock_svc, \
         patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
         patch("services.slack_followups.MYOS_DIR", tmp_path):
        mock_svc.is_connected.return_value = True

        resp = await client.request("DELETE", "/api/slack/followups/does-not-exist")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.request("DELETE", "/api/slack/followups/some-id")

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------

class TestFollowupsService:
    def test_empty_on_missing_file(self, tmp_path):
        with patch("services.slack_followups.FOLLOWUPS_PATH", tmp_path / "no_such.json"):
            from services.slack_followups import list_followups
            assert list_followups() == []

    def test_add_and_count(self, tmp_path):
        followup_path = tmp_path / "slack_followups.json"
        with patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
             patch("services.slack_followups.MYOS_DIR", tmp_path):
            from services.slack_followups import add_followup, count, list_followups
            row = add_followup("C1", "general", "1.0", "U1", "hello", "https://example.com")
            assert count() == 1
            rows = list_followups()
            assert rows[0]["id"] == row["id"]
            assert rows[0]["permalink"] == "https://example.com"

    def test_remove_returns_false_for_unknown(self, tmp_path):
        followup_path = tmp_path / "slack_followups.json"
        with patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
             patch("services.slack_followups.MYOS_DIR", tmp_path):
            from services.slack_followups import remove_followup
            assert remove_followup("not-a-real-id") is False

    def test_newest_first_order(self, tmp_path):
        followup_path = tmp_path / "slack_followups.json"
        with patch("services.slack_followups.FOLLOWUPS_PATH", followup_path), \
             patch("services.slack_followups.MYOS_DIR", tmp_path):
            from services.slack_followups import add_followup, list_followups
            add_followup("C1", "general", "1.0", "U1", "first", "")
            add_followup("C1", "general", "2.0", "U1", "second", "")
            rows = list_followups()
            assert rows[0]["ts"] == "2.0"
            assert rows[1]["ts"] == "1.0"

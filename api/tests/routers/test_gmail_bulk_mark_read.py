"""Tests for POST /gmail/messages/batch-mark-read (→2473)."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from routers.gmail import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_batch_mark_read_success(client):
    """All ids succeed: returns succeeded list and count."""
    with (
        patch("routers.gmail.is_authenticated", return_value=True),
        patch("routers.gmail.gmail_service.mark_read", new_callable=AsyncMock) as mock_mark,
    ):
        response = client.post(
            "/api/gmail/messages/batch-mark-read",
            json={"ids": ["abc123", "def456"]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == ["abc123", "def456"]
    assert data["failed"] == []
    assert data["count"] == 2
    assert mock_mark.call_count == 2


def test_batch_mark_read_partial_failure(client):
    """One id fails: that id lands in failed list; others succeed."""
    call_count = 0

    async def flaky_mark_read(message_id: str):
        nonlocal call_count
        call_count += 1
        if message_id == "bad_id":
            raise RuntimeError("Gmail API error")

    with (
        patch("routers.gmail.is_authenticated", return_value=True),
        patch("routers.gmail.gmail_service.mark_read", side_effect=flaky_mark_read),
    ):
        response = client.post(
            "/api/gmail/messages/batch-mark-read",
            json={"ids": ["good_id", "bad_id"]},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == ["good_id"]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["id"] == "bad_id"
    assert data["count"] == 1


def test_batch_mark_read_requires_auth(client):
    """Returns 401 when not authenticated."""
    with patch("routers.gmail.is_authenticated", return_value=False):
        response = client.post(
            "/api/gmail/messages/batch-mark-read",
            json={"ids": ["abc"]},
        )
    assert response.status_code == 401


def test_batch_mark_read_empty_ids(client):
    """Returns 400 when no ids are provided."""
    with patch("routers.gmail.is_authenticated", return_value=True):
        response = client.post(
            "/api/gmail/messages/batch-mark-read",
            json={"ids": []},
        )
    assert response.status_code == 400

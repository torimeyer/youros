"""Tests for GET /api/memory and PUT /api/memory."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_get_user_memory_returns_content(client):
    with patch("services.user_memory_store.read", return_value="# Preferences\n- Plain language"):
        resp = await client.get("/api/memory")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# Preferences\n- Plain language"


@pytest.mark.asyncio
async def test_get_user_memory_returns_empty_string_when_file_empty(client):
    with patch("services.user_memory_store.read", return_value=""):
        resp = await client.get("/api/memory")
    assert resp.status_code == 200
    assert resp.json()["content"] == ""


@pytest.mark.asyncio
async def test_put_user_memory_replaces_content(client):
    captured: dict = {}

    def fake_replace_all(text: str) -> None:
        captured["text"] = text

    with patch("services.user_memory_store.replace_all", side_effect=fake_replace_all):
        resp = await client.put("/api/memory", json={"content": "# Facts\n- I am a PM"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert captured["text"] == "# Facts\n- I am a PM"


@pytest.mark.asyncio
async def test_put_user_memory_requires_content_field(client):
    resp = await client.put("/api/memory", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_user_memory_accepts_empty_string(client):
    with patch("services.user_memory_store.replace_all") as mock_replace:
        resp = await client.put("/api/memory", json={"content": ""})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_replace.assert_called_once_with("")

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def settings_file(tmp_path):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"os_name": "TestOS", "dark_mode": True}))
    return sf


@pytest.mark.asyncio
async def test_agents_last_viewed_default_is_empty_string(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents_last_viewed" in data
    assert data["agents_last_viewed"] == ""


@pytest.mark.asyncio
async def test_agents_last_viewed_patch_persists(client, settings_file):
    ts = "2026-04-27T10:00:00Z"
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"agents_last_viewed": ts})
    assert resp.status_code == 200
    assert resp.json()["result"] == "updated"

    saved = json.loads(settings_file.read_text())
    assert saved["agents_last_viewed"] == ts


@pytest.mark.asyncio
async def test_agents_last_viewed_get_after_patch(client, settings_file):
    ts = "2026-04-27T15:30:00Z"
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"agents_last_viewed": ts})
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["agents_last_viewed"] == ts


@pytest.mark.asyncio
async def test_agents_last_viewed_patch_preserves_other_fields(client, settings_file):
    ts = "2026-04-27T12:00:00Z"
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"agents_last_viewed": ts})
        resp = await client.get("/api/settings")
    data = resp.json()
    assert data["agents_last_viewed"] == ts
    assert data["os_name"] == "TestOS"
    assert data["dark_mode"] is True

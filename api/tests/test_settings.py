import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def settings_file(tmp_path):
    """Create a temporary settings file with default values and patch SETTINGS_PATH."""
    sf = tmp_path / "settings.json"
    defaults = {
        "os_name": "ToriOS",
        "dark_mode": True,
        "accent_color": "blue",
        "anthropic_api_key": "",
        "gemini_api_key": "",
        "default_model": "@claude",
        "features": {
            "chat": True, "tasks": True, "hay": True,
            "agents": True, "projects": True, "docs": True,
            "transcripts": False,
        },
        "notifications": {
            "agent_complete": True, "agent_needs_input": True,
            "agent_failed": True, "approval_needed": True,
        },
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    sf.write_text(json.dumps(defaults, indent=2))
    return sf


@pytest.mark.asyncio
async def test_get_settings(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True
    assert data["accent_color"] == "blue"
    assert data["default_model"] == "@claude"


@pytest.mark.asyncio
async def test_put_settings(client, settings_file):
    new_settings = {
        "os_name": "CustomOS",
        "dark_mode": False,
        "accent_color": "red",
    }
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.put("/api/settings", json=new_settings)

    assert resp.status_code == 200
    assert resp.json()["result"] == "saved"

    # Verify persisted to file
    saved = json.loads(settings_file.read_text())
    assert saved["os_name"] == "CustomOS"
    assert saved["dark_mode"] is False
    assert saved["accent_color"] == "red"


@pytest.mark.asyncio
async def test_patch_settings(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"accent_color": "green"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated"

    # The other fields should remain unchanged
    saved = json.loads(settings_file.read_text())
    assert saved["accent_color"] == "green"
    assert saved["os_name"] == "ToriOS"
    assert saved["dark_mode"] is True


@pytest.mark.asyncio
async def test_default_settings_values(settings_file):
    data = json.loads(settings_file.read_text())
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True
    assert data["accent_color"] == "blue"
    assert data["anthropic_api_key"] == ""
    assert data["gemini_api_key"] == ""
    assert data["default_model"] == "@claude"
    assert data["features"]["chat"] is True
    assert data["features"]["transcripts"] is False
    assert data["notifications"]["agent_complete"] is True
    assert data["quiet_hours_enabled"] is True
    assert data["quiet_hours_start"] == "22:00"
    assert data["quiet_hours_end"] == "07:00"


@pytest.mark.asyncio
async def test_patch_preserves_other_fields(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"dark_mode": False})
        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["dark_mode"] is False
    assert data["os_name"] == "ToriOS"
    assert data["quiet_hours_enabled"] is True


# --- Feature key normalization regression tests ---
# These prevent the bug where backend stored lowercase feature keys ("tasks")
# but the frontend looked up TitleCase keys ("Tasks"), causing features to
# appear invisible to the UI.


@pytest.mark.asyncio
async def test_get_settings_normalizes_lowercase_feature_keys(client, settings_file):
    """Lowercase feature keys from old settings files must be normalized to
    TitleCase labels matching the frontend store."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    # The fixture uses lowercase keys. After normalization the API should
    # return TitleCase labels that the frontend expects.
    assert "Tasks" in features
    assert "Chat" in features
    assert "Hay/Ideas" in features
    assert "Agents" in features
    assert "Projects" in features
    assert "Docs" in features
    assert "Transcripts" in features
    # Old lowercase keys should NOT appear in the response
    assert "tasks" not in features
    assert "chat" not in features
    assert "hay" not in features


@pytest.mark.asyncio
async def test_patch_features_normalizes_lowercase_keys(client, settings_file):
    """Patching features with lowercase keys should normalize them to TitleCase."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "features": {"tasks": False, "chat": True}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert "tasks" not in features
    assert "chat" not in features


@pytest.mark.asyncio
async def test_patch_features_preserves_titlecase_keys(client, settings_file):
    """Features saved with TitleCase keys (from the UI) should be preserved."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "features": {"Tasks": False, "Chat": True, "Transcripts": True}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert features["Transcripts"] is True


@pytest.mark.asyncio
async def test_feature_values_survive_roundtrip(client, settings_file):
    """A feature disabled via the API must still be disabled when read back.
    This is the core regression test: before the fix, the frontend would
    look up 'Tasks' but the backend stored 'tasks', so a disabled feature
    appeared enabled."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        # Simulate the frontend disabling Tasks
        await client.patch("/api/settings", json={
            "features": {"Tasks": False}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False

    # Now simulate the old-style API writing lowercase keys
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        saved = json.loads(settings_file.read_text())
        saved["features"] = {"tasks": False, "chat": True, "transcripts": False}
        settings_file.write_text(json.dumps(saved))
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert features["Transcripts"] is False


@pytest.mark.asyncio
async def test_default_settings_schema_uses_titlecase_feature_keys():
    """The Settings schema defaults must use TitleCase keys matching the UI."""
    from models.schemas import Settings
    defaults = Settings()
    assert "Tasks" in defaults.features
    assert "Chat" in defaults.features
    assert "Hay/Ideas" in defaults.features
    assert "tasks" not in defaults.features
    assert "chat" not in defaults.features

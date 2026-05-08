"""Tests for ADHD mode endpoints."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")
    monkeypatch.setattr("services.settings_store.SETTINGS_PATH", settings_file)
    yield


class TestAdhdConfig:
    def test_get_defaults(self):
        r = client.get("/api/adhd/config")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert data["check_in_seconds"] == 30
        assert data["focus_mode"] is False

    def test_patch_enable(self):
        r = client.patch("/api/adhd/config", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True

        r2 = client.get("/api/adhd/config")
        assert r2.json()["enabled"] is True

    def test_patch_interval(self):
        r = client.patch("/api/adhd/config", json={"check_in_seconds": 45})
        assert r.status_code == 200
        assert r.json()["check_in_seconds"] == 45

    def test_patch_interval_clamped_low(self):
        r = client.patch("/api/adhd/config", json={"check_in_seconds": 5})
        assert r.status_code == 200
        assert r.json()["check_in_seconds"] == 10

    def test_patch_interval_clamped_high(self):
        r = client.patch("/api/adhd/config", json={"check_in_seconds": 300})
        assert r.status_code == 200
        assert r.json()["check_in_seconds"] == 120

    def test_patch_focus_mode(self):
        r = client.patch("/api/adhd/config", json={"focus_mode": True})
        assert r.status_code == 200
        assert r.json()["focus_mode"] is True

    def test_patch_ignores_unknown_keys(self):
        r = client.patch("/api/adhd/config", json={"enabled": True, "bogus": 42})
        assert r.status_code == 200
        assert "bogus" not in r.json()


class TestCheckIn:
    def test_check_in_empty(self):
        with patch("routers.agents.agent_metadata", {}):
            r = client.get("/api/adhd/check-in")
            assert r.status_code == 200
            data = r.json()
            assert data["running_count"] == 0
            assert data["agents"] == []

    def test_check_in_running_agent(self):
        meta = {
            "test-agent": {
                "status": "running",
                "task": "fixing bugs",
                "current_step": "running tests",
                "source": "user",
            }
        }
        with patch("routers.agents.agent_metadata", meta):
            r = client.get("/api/adhd/check-in")
            data = r.json()
            assert data["running_count"] == 1
            assert data["agents"][0]["name"] == "test-agent"
            assert data["agents"][0]["task"] == "fixing bugs"

    def test_check_in_skips_system_agents(self):
        meta = {
            "hook-agent": {
                "status": "running",
                "task": "system work",
                "source": "hook",
            }
        }
        with patch("routers.agents.agent_metadata", meta):
            r = client.get("/api/adhd/check-in")
            assert r.json()["running_count"] == 0


class TestAdhdPersistence:
    """Regression tests for ADHD mode server-side persistence.

    The two bugs caught here:
    1. ``adhd_mode`` had no schema default so GET /settings never returned it
       on a fresh install — the Settings page toggle always showed "off".
    2. PUT /settings with a partial body (e.g. {files_dir: null}) called
       settings_store.save(body) which *overwrites* settings.json, erasing
       ``adhd_mode`` and every other field not in that body.
    """

    def test_adhd_mode_included_in_settings_get_response_by_default(self):
        """GET /settings must include adhd_mode even before it has been set.

        Without a schema default, a fresh install never writes adhd_mode to
        settings.json and GET /settings never returns the field — so the
        Settings page toggle always reads ``undefined`` and defaults to false.
        """
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "adhd_mode" in data, (
            "adhd_mode missing from GET /settings — add it to the Settings schema with defaults"
        )
        assert data["adhd_mode"]["enabled"] is False
        assert "check_in_seconds" in data["adhd_mode"]
        assert "focus_mode" in data["adhd_mode"]

    def test_adhd_config_accessible_via_settings_endpoint(self):
        """PATCH /adhd/config should make the config readable through GET /settings.

        The Settings page hydrates its toggle state from GET /settings, not
        GET /adhd/config, so both endpoints must reflect the same stored value.
        """
        r = client.patch("/api/adhd/config", json={"enabled": True})
        assert r.status_code == 200

        r2 = client.get("/api/settings")
        data = r2.json()
        assert "adhd_mode" in data, "adhd_mode missing from GET /settings after PATCH /adhd/config"
        assert data["adhd_mode"]["enabled"] is True

    def test_adhd_config_survives_put_settings_partial_body(self):
        """Enabling ADHD mode must survive a subsequent PUT /settings with partial data.

        The Settings page calls PUT /settings with {files_dir: <value>} when the
        user changes their files folder.  That PUT must NOT wipe adhd_mode.
        Currently it calls settings_store.save(body) which overwrites the whole
        file — fix is to use settings_store.update(body) in the PUT handler.
        """
        r = client.patch("/api/adhd/config", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True

        # Simulate the user changing their files directory in Settings
        r2 = client.put("/api/settings", json={"files_dir": None})
        assert r2.status_code == 200

        # ADHD mode must still be on
        r3 = client.get("/api/adhd/config")
        assert r3.json()["enabled"] is True, (
            "adhd_mode was erased by PUT /settings — change PUT handler to use "
            "settings_store.update() instead of settings_store.save()"
        )


class TestContextRebuild:
    def test_context_rebuild_empty(self, tmp_path):
        with patch("routers.agents.agent_metadata", {}):
            r = client.get("/api/adhd/context-rebuild")
            assert r.status_code == 200
            data = r.json()
            assert data["active_agents"] == []
            assert data["recent_agents"] == []
            assert "next_step" in data

    def test_context_rebuild_with_running_agent(self):
        meta = {
            "build-agent": {
                "status": "running",
                "task": "building feature X",
                "current_step": "writing tests",
                "source": "user",
            }
        }
        with patch("routers.agents.agent_metadata", meta):
            r = client.get("/api/adhd/context-rebuild")
            data = r.json()
            assert len(data["active_agents"]) == 1
            assert "running" in data["next_step"].lower()

    def test_context_rebuild_next_step_no_activity(self):
        with patch("routers.agents.agent_metadata", {}):
            r = client.get("/api/adhd/context-rebuild")
            data = r.json()
            assert "no active work" in data["next_step"].lower() or "task list" in data["next_step"].lower()

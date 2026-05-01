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

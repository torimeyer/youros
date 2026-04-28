"""Tests for member-facing team home endpoint.

Covers: happy path, empty states, announcements file parsing, adoption summary.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import team_home as team_home_router
from services import team_catalog as catalog_svc
from services import enterprise_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path):
    """Minimal FastAPI app with only team_home router; all storage in tmp."""
    with (
        patch.object(catalog_svc, "CATALOG_BASE", tmp_path / "team_catalog"),
        patch.object(enterprise_store, "ENTERPRISE_PATH", tmp_path / "enterprise.json"),
    ):
        mini_app = FastAPI()
        mini_app.include_router(team_home_router.router, prefix="/api")
        yield TestClient(mini_app), tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_enterprise(tmp_path, members=None):
    if members is None:
        members = [
            {"id": "m1", "email": "admin@test.com", "role": "admin"},
            {"id": "m2", "email": "alice@test.com", "role": "member"},
            {"id": "m3", "email": "bob@test.com", "role": "member"},
        ]
    data = {
        "org": {"id": "test-org", "name": "Test Org", "admin_email": "admin@test.com"},
        "members": members,
        "policies": {},
    }
    (tmp_path / "enterprise.json").write_text(json.dumps(data))


def _publish(org, name, content="FROM auto\n"):
    return catalog_svc.publish_agentfile(org, name, content, "admin")


# ---------------------------------------------------------------------------
# Tests: endpoint shape
# ---------------------------------------------------------------------------


class TestTeamHomeShape:
    def test_returns_200(self, app_client):
        client, _ = app_client
        res = client.get("/api/team/home")
        assert res.status_code == 200

    def test_returns_expected_keys(self, app_client):
        client, _ = app_client
        body = client.get("/api/team/home").json()
        assert "team_picks" in body
        assert "announcements" in body
        assert "adoption_summary" in body

    def test_adoption_summary_has_expected_keys(self, app_client):
        client, _ = app_client
        summary = client.get("/api/team/home").json()["adoption_summary"]
        assert "active_members_this_week" in summary
        assert "top_skill" in summary


# ---------------------------------------------------------------------------
# Tests: team picks
# ---------------------------------------------------------------------------


class TestTeamPicks:
    def test_empty_catalog_returns_empty_list(self, app_client):
        client, _ = app_client
        assert client.get("/api/team/home").json()["team_picks"] == []

    def test_picks_capped_at_three(self, app_client):
        client, tmp_path = app_client
        for i in range(5):
            _publish("default", f"skill-{i}")
        picks = client.get("/api/team/home").json()["team_picks"]
        assert len(picks) == 3

    def test_picks_contain_published_entries(self, app_client):
        client, tmp_path = app_client
        _publish("default", "code-reviewer")
        picks = client.get("/api/team/home").json()["team_picks"]
        assert picks[0]["name"] == "code-reviewer"


# ---------------------------------------------------------------------------
# Tests: announcements
# ---------------------------------------------------------------------------


class TestAnnouncements:
    def test_empty_when_file_missing(self, app_client):
        client, _ = app_client
        assert client.get("/api/team/home").json()["announcements"] == []

    def test_loads_list_format(self, app_client):
        client, tmp_path = app_client
        home = tmp_path / ".myos" / "orgs" / "default" / "announcements.json"
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text(json.dumps([{"id": "a1", "title": "Hello"}]))

        with patch("routers.team_home.Path.home", return_value=tmp_path):
            with patch("routers.team_home._enterprise_org_id", return_value="default"):
                res = client.get("/api/team/home")
        assert res.status_code == 200

    def test_loads_dict_format_with_announcements_key(self, app_client):
        client, tmp_path = app_client
        home = tmp_path / ".myos" / "orgs" / "default" / "announcements.json"
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text(json.dumps({"announcements": [{"title": "Scoped"}]}))

        with patch("routers.team_home.Path.home", return_value=tmp_path):
            with patch("routers.team_home._enterprise_org_id", return_value="default"):
                res = client.get("/api/team/home")
        assert res.status_code == 200

    def test_returns_empty_on_corrupt_file(self, app_client):
        client, tmp_path = app_client
        home = tmp_path / ".myos" / "orgs" / "default" / "announcements.json"
        home.parent.mkdir(parents=True, exist_ok=True)
        home.write_text("not json {{")

        with patch("routers.team_home.Path.home", return_value=tmp_path):
            with patch("routers.team_home._enterprise_org_id", return_value="default"):
                res = client.get("/api/team/home")
        assert res.status_code == 200
        assert res.json()["announcements"] == []


# ---------------------------------------------------------------------------
# Tests: adoption summary
# ---------------------------------------------------------------------------


class TestAdoptionSummary:
    def test_top_skill_none_when_no_catalog(self, app_client):
        client, _ = app_client
        assert client.get("/api/team/home").json()["adoption_summary"]["top_skill"] is None

    def test_top_skill_is_most_recent_published_entry(self, app_client):
        client, tmp_path = app_client
        _publish("default", "first")
        _publish("default", "second")
        summary = client.get("/api/team/home").json()["adoption_summary"]
        assert summary["top_skill"] in ("first", "second")

    def test_active_members_excludes_admin(self, app_client):
        client, tmp_path = app_client
        _write_enterprise(tmp_path)
        count = client.get("/api/team/home").json()["adoption_summary"]["active_members_this_week"]
        assert count == 2  # alice + bob, not the admin

    def test_active_members_zero_when_no_enterprise(self, app_client):
        client, _ = app_client
        count = client.get("/api/team/home").json()["adoption_summary"]["active_members_this_week"]
        assert count == 0

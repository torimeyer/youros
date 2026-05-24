"""Tests for team resource catalog.

Covers: CRUD, versioning, rollback immutability, install path.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import team_catalog as svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog(tmp_path):
    """A catalog backed by a tmp dir so tests never touch ~/.myos."""
    with patch.object(svc, "CATALOG_BASE", tmp_path / "team_catalog"):
        yield


@pytest.fixture
def agentfiles_dir(tmp_path):
    """Redirect install destination to tmp."""
    fake_dir = tmp_path / "agentfiles"
    fake_dir.mkdir(parents=True, exist_ok=True)

    def _fake_home():
        return tmp_path

    with patch("services.team_catalog.Path.home", staticmethod(_fake_home)):
        yield fake_dir


# ---------------------------------------------------------------------------
# Service: publish
# ---------------------------------------------------------------------------


class TestPublishAgentfile:
    def test_first_publish_creates_v1(self, catalog):
        entry = svc.publish_agentfile("org1", "code-reviewer", "FROM auto\n", "admin")
        assert entry["version"] == 1
        assert entry["status"] == "published"
        assert entry["kind"] == "agentfile"
        assert entry["name"] == "code-reviewer"

    def test_second_publish_increments_version(self, catalog):
        svc.publish_agentfile("org1", "code-reviewer", "FROM auto\n", "admin")
        entry2 = svc.publish_agentfile("org1", "code-reviewer", "FROM opus\n", "admin")
        assert entry2["version"] == 2
        assert entry2["status"] == "published"

    def test_prior_version_marked_rolled_back_on_new_publish(self, catalog):
        e1 = svc.publish_agentfile("org1", "code-reviewer", "v1 content", "admin")
        svc.publish_agentfile("org1", "code-reviewer", "v2 content", "admin")

        data = svc._load("org1")
        assert data["entries"][e1["id"]]["status"] == "rolled_back"

    def test_versions_are_immutable(self, catalog):
        """Published content must not change when a new version is added."""
        e1 = svc.publish_agentfile("org1", "code-reviewer", "original", "admin")
        svc.publish_agentfile("org1", "code-reviewer", "updated", "admin")

        data = svc._load("org1")
        assert data["entries"][e1["id"]]["content"] == "original"

    def test_different_names_are_independent(self, catalog):
        a = svc.publish_agentfile("org1", "alpha", "a content", "admin")
        b = svc.publish_agentfile("org1", "beta", "b content", "admin")
        assert a["version"] == 1
        assert b["version"] == 1
        listed = svc.list_catalog("org1")
        assert len(listed) == 2


# ---------------------------------------------------------------------------
# Service: list
# ---------------------------------------------------------------------------


class TestListCatalog:
    def test_empty_catalog_returns_empty_list(self, catalog):
        assert svc.list_catalog("org-new") == []

    def test_only_published_entries_appear(self, catalog):
        e1 = svc.publish_agentfile("org1", "alpha", "content", "admin")
        # Add a second version, which demotes e1
        svc.publish_agentfile("org1", "alpha", "content v2", "admin")
        listed = svc.list_catalog("org1")
        # Only the current published version appears
        assert len(listed) == 1
        assert listed[0]["version"] == 2

    def test_list_all_versions_returns_all_rows(self, catalog):
        svc.publish_agentfile("org1", "alpha", "v1", "admin")
        svc.publish_agentfile("org1", "alpha", "v2", "admin")
        svc.publish_agentfile("org1", "alpha", "v3", "admin")
        versions = svc.list_all_versions("org1", "alpha")
        assert len(versions) == 3
        # Newest first
        assert versions[0]["version"] == 3


# ---------------------------------------------------------------------------
# Service: rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_creates_new_version_with_old_content(self, catalog):
        e1 = svc.publish_agentfile("org1", "alpha", "original content", "admin")
        svc.publish_agentfile("org1", "alpha", "new content", "admin")

        rolled = svc.rollback("org1", e1["id"])
        assert rolled is not None
        assert rolled["content"] == "original content"
        assert rolled["version"] == 3  # v1, v2, v3=rollback
        assert rolled["status"] == "published"

    def test_rollback_preserves_prior_versions(self, catalog):
        e1 = svc.publish_agentfile("org1", "alpha", "v1 content", "admin")
        svc.publish_agentfile("org1", "alpha", "v2 content", "admin")
        svc.rollback("org1", e1["id"])

        versions = svc.list_all_versions("org1", "alpha")
        assert len(versions) == 3

    def test_rollback_nonexistent_id_returns_none(self, catalog):
        result = svc.rollback("org1", "does-not-exist")
        assert result is None

    def test_only_one_published_after_rollback(self, catalog):
        e1 = svc.publish_agentfile("org1", "alpha", "v1", "admin")
        svc.publish_agentfile("org1", "alpha", "v2", "admin")
        svc.rollback("org1", e1["id"])

        listed = svc.list_catalog("org1")
        assert len(listed) == 1


# ---------------------------------------------------------------------------
# Service: install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_writes_content_to_agentfiles_dir(self, catalog, tmp_path):
        with patch("services.team_catalog.Path.home", return_value=tmp_path):
            entry = svc.publish_agentfile("org1", "my-agent", "FROM auto\n", "admin")
            result = svc.install_agentfile("org1", entry["id"])

        assert result is not None
        assert result["name"] == "my-agent"
        assert result["version"] == 1
        # Service writes to Path.home() / ".myos" / "agentfiles"
        installed_file = tmp_path / ".myos" / "agentfiles" / "my-agent.agent"
        assert installed_file.exists()
        assert installed_file.read_text() == "FROM auto\n"

    def test_install_nonexistent_entry_returns_none(self, catalog, tmp_path):
        with patch("services.team_catalog.Path.home", return_value=tmp_path):
            result = svc.install_agentfile("org1", "no-such-id")
        assert result is None

    def test_install_rolled_back_entry_returns_none(self, catalog, tmp_path):
        fake_dir = tmp_path / "agentfiles"
        fake_dir.mkdir()
        with patch("services.team_catalog.Path.home", return_value=tmp_path):
            e1 = svc.publish_agentfile("org1", "alpha", "v1", "admin")
            svc.publish_agentfile("org1", "alpha", "v2", "admin")
            result = svc.install_agentfile("org1", e1["id"])
        assert result is None


# ---------------------------------------------------------------------------
# Router: HTTP endpoints
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    import httpx
    from main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _patch_catalog_base(tmp_path):
    with patch.object(svc, "CATALOG_BASE", tmp_path / "team_catalog"):
        yield


class TestRouterList:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get("/api/team/catalog")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    @pytest.mark.asyncio
    async def test_list_after_publish(self, client):
        await client.post("/api/team/catalog/agentfiles", json={
            "name": "test-agent", "content": "FROM auto\n"
        })
        resp = await client.get("/api/team/catalog")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "test-agent"
        assert entries[0]["version"] == 1


class TestRouterPublish:
    @pytest.mark.asyncio
    async def test_publish_returns_entry(self, client):
        resp = await client.post("/api/team/catalog/agentfiles", json={
            "name": "my-agent", "content": "FROM opus\n"
        })
        assert resp.status_code == 200
        entry = resp.json()["entry"]
        assert entry["name"] == "my-agent"
        assert entry["version"] == 1
        assert entry["status"] == "published"

    @pytest.mark.asyncio
    async def test_publish_missing_name_returns_422(self, client):
        resp = await client.post("/api/team/catalog/agentfiles", json={"content": "x"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_publish_missing_content_returns_422(self, client):
        resp = await client.post("/api/team/catalog/agentfiles", json={"name": "x"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_second_publish_increments_version(self, client):
        await client.post("/api/team/catalog/agentfiles", json={"name": "agent", "content": "v1"})
        resp = await client.post("/api/team/catalog/agentfiles", json={"name": "agent", "content": "v2"})
        assert resp.json()["entry"]["version"] == 2


class TestRouterRollback:
    @pytest.mark.asyncio
    async def test_rollback_restores_old_content(self, client):
        r1 = await client.post("/api/team/catalog/agentfiles", json={"name": "x", "content": "v1"})
        e1_id = r1.json()["entry"]["id"]
        await client.post("/api/team/catalog/agentfiles", json={"name": "x", "content": "v2"})

        resp = await client.post(f"/api/team/catalog/{e1_id}/rollback")
        assert resp.status_code == 200
        rolled = resp.json()["entry"]
        assert rolled["content"] == "v1"
        assert rolled["version"] == 3

    @pytest.mark.asyncio
    async def test_rollback_missing_id_returns_404(self, client):
        resp = await client.post("/api/team/catalog/no-such-id/rollback")
        assert resp.status_code == 404


class TestRouterInstall:
    @pytest.mark.asyncio
    async def test_install_published_entry(self, client, tmp_path):
        fake_dir = tmp_path / "agentfiles"
        fake_dir.mkdir()
        r = await client.post("/api/team/catalog/agentfiles", json={"name": "inst-agent", "content": "FROM auto\n"})
        entry_id = r.json()["entry"]["id"]

        with patch("services.team_catalog.Path.home", return_value=tmp_path):
            resp = await client.post(f"/api/team/catalog/{entry_id}/install")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_install_nonexistent_returns_404(self, client):
        resp = await client.post("/api/team/catalog/no-such/install")
        assert resp.status_code == 404


class TestRouterVersionHistory:
    @pytest.mark.asyncio
    async def test_version_history_returns_all_versions(self, client):
        await client.post("/api/team/catalog/agentfiles", json={"name": "hist", "content": "v1"})
        await client.post("/api/team/catalog/agentfiles", json={"name": "hist", "content": "v2"})
        resp = await client.get("/api/team/catalog/hist/versions")
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert len(versions) == 2

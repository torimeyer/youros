"""Tests for the persistent notifications service and router."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(tmp_path: Path):
    """Return a NotificationsService backed by a temp directory."""
    import services.notifications as mod

    # Patch the module-level constants so nothing writes to ~/.myos
    with patch.object(mod, "MYOS_DIR", tmp_path), patch.object(
        mod, "NOTIFICATIONS_FILE", tmp_path / "notifications.json"
    ):
        from services.notifications import NotificationsService
        svc = NotificationsService()
        # Monkey-patch the instance to use tmp paths
        svc._myos_dir = tmp_path
        svc._file = tmp_path / "notifications.json"
        return svc, tmp_path / "notifications.json"


# ---------------------------------------------------------------------------
# Unit tests for the service
# ---------------------------------------------------------------------------

class TestNotificationsService:
    def _patched_service(self, tmp_path):
        import services.notifications as mod
        from services.notifications import NotificationsService

        class PatchedService(NotificationsService):
            def _load(self_inner):
                if not (tmp_path / "notifications.json").exists():
                    return []
                try:
                    raw = json.loads((tmp_path / "notifications.json").read_text())
                    from services.notifications import Notification
                    return [Notification.from_dict(d) for d in raw]
                except (json.JSONDecodeError, OSError):
                    return []

            def _save(self_inner, notifications):
                tmp_path.mkdir(parents=True, exist_ok=True)
                (tmp_path / "notifications.json").write_text(
                    json.dumps([n.to_dict() for n in notifications], indent=2)
                )

        return PatchedService()

    def test_add_and_list_all(self, tmp_path):
        svc = self._patched_service(tmp_path)
        n = svc.add(type="info", title="Hello", body="World")
        all_notifs = svc.list_all()
        assert len(all_notifs) == 1
        assert all_notifs[0].id == n.id
        assert all_notifs[0].title == "Hello"

    def test_unread_count_starts_at_one(self, tmp_path):
        svc = self._patched_service(tmp_path)
        svc.add(type="info", title="A", body="B")
        assert len(svc.list_unread()) == 1

    def test_mark_read_decrements_unread(self, tmp_path):
        svc = self._patched_service(tmp_path)
        n = svc.add(type="info", title="A", body="B")
        svc.mark_read(n.id)
        assert len(svc.list_unread()) == 0
        all_notifs = svc.list_all()
        assert all_notifs[0].read is True

    def test_mark_all_read(self, tmp_path):
        svc = self._patched_service(tmp_path)
        svc.add(type="info", title="A", body="B")
        svc.add(type="info", title="C", body="D")
        svc.mark_all_read()
        assert len(svc.list_unread()) == 0

    def test_delete(self, tmp_path):
        svc = self._patched_service(tmp_path)
        n = svc.add(type="info", title="A", body="B")
        result = svc.delete(n.id)
        assert result is True
        assert len(svc.list_all()) == 0

    def test_delete_nonexistent(self, tmp_path):
        svc = self._patched_service(tmp_path)
        result = svc.delete("does-not-exist")
        assert result is False

    def test_has_unread_of_type(self, tmp_path):
        svc = self._patched_service(tmp_path)
        svc.add(type="upgrade", title="Update", body="New version")
        assert svc.has_unread_of_type("upgrade") is True
        assert svc.has_unread_of_type("other") is False

    def test_mark_read_false_for_nonexistent(self, tmp_path):
        svc = self._patched_service(tmp_path)
        result = svc.mark_read("bogus-id")
        assert result is False


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notifications_list_empty(client, tmp_path):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.list_all.return_value = []
        resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_unread_count_endpoint(client, tmp_path):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.list_unread.return_value = [object(), object()]
        resp = await client.get("/api/notifications/unread/count")
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


@pytest.mark.asyncio
async def test_mark_read_endpoint_found(client):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.mark_read.return_value = True
        resp = await client.post("/api/notifications/some-id/read")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"


@pytest.mark.asyncio
async def test_mark_read_endpoint_not_found(client):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.mark_read.return_value = False
        resp = await client.post("/api/notifications/bad-id/read")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_read_endpoint(client):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.mark_all_read.return_value = None
        resp = await client.post("/api/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"


@pytest.mark.asyncio
async def test_delete_endpoint_found(client):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.delete.return_value = True
        resp = await client.delete("/api/notifications/some-id")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"


@pytest.mark.asyncio
async def test_delete_endpoint_not_found(client):
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.delete.return_value = False
        resp = await client.delete("/api/notifications/bad-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Agent completion fires a notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_complete_fires_notification(tmp_path):
    """When an agent calls /complete, a persistent notification with type='agent'
    must be created so the bell lights up in the UI."""
    from unittest.mock import AsyncMock, MagicMock
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers.agents import agent_metadata

    agent_metadata["notif-test-agent"] = {
        "spawned_at": "2026-04-08T10:00:00+00:00",
        "budget": "1.0",
        "model": "claude-sonnet-4-6",
        "source": "claude-code",
        "description": "Runs the notification test.",
    }

    fired: list[dict] = []

    def fake_add(**kwargs):
        fired.append(kwargs)
        n = MagicMock()
        n.id = "fake-notif-id"
        return n

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("services.notifications.notifications_service") as mock_notif_svc, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_notif_svc.add.side_effect = fake_add

                resp = await client.post("/api/agents/notif-test-agent/complete")
                assert resp.status_code == 200

        finally:
            agent_metadata.pop("notif-test-agent", None)

    assert len(fired) == 1, "Expected exactly one notification to be fired"
    assert fired[0]["type"] == "agent"
    assert "notif-test-agent" in fired[0]["title"]
    assert fired[0]["action_url"] == "/agents"

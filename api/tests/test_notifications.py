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

    def test_store_is_capped_at_max(self, tmp_path):
        """Adding more than MAX_NOTIFICATIONS drops the oldest entries."""
        from services.notifications import MAX_NOTIFICATIONS

        svc = self._patched_service(tmp_path)
        # Use unique targets so nothing dedupes. Add cap+25 entries.
        for i in range(MAX_NOTIFICATIONS + 25):
            svc.add(
                type="agent",
                title=f"Agent {i}",
                body="done",
                metadata={"agent_name": f"agent-{i}"},
            )
        all_notifs = svc.list_all()
        assert len(all_notifs) == MAX_NOTIFICATIONS
        # Newest stays at the top (insert(0)), oldest is dropped.
        assert all_notifs[0].title == f"Agent {MAX_NOTIFICATIONS + 24}"

    def test_load_trims_oversized_store_once(self, tmp_path):
        """Legacy stores that are already over the cap get trimmed on load."""
        import json as _json
        import services.notifications as mod
        from services.notifications import MAX_NOTIFICATIONS, NotificationsService

        # Write an oversized store directly to disk, bypassing the service.
        oversized = []
        for i in range(MAX_NOTIFICATIONS + 50):
            oversized.append({
                "id": f"id-{i}",
                "type": "agent",
                "title": f"T{i}",
                "body": "b",
                "action_label": None,
                "action_url": None,
                "read": False,
                "created_at": "2026-04-08T10:00:00+00:00",
                "metadata": {"agent_name": f"a-{i}"},
            })
        tmp_path.mkdir(parents=True, exist_ok=True)
        notif_file = tmp_path / "notifications.json"
        notif_file.write_text(_json.dumps(oversized))

        # Point the real module at the tmp file so the real _load trim runs.
        with patch.object(mod, "MYOS_DIR", tmp_path), patch.object(
            mod, "NOTIFICATIONS_FILE", notif_file
        ):
            svc = NotificationsService()
            loaded = svc.list_all()
            assert len(loaded) == MAX_NOTIFICATIONS
            # The trimmed version should have been written back to disk.
            on_disk = _json.loads(notif_file.read_text())
            assert len(on_disk) == MAX_NOTIFICATIONS

    def test_dedup_same_type_and_target_bumps_count(self, tmp_path):
        """Adding a duplicate (type, target) pair updates the existing one."""
        svc = self._patched_service(tmp_path)
        first = svc.add(
            type="agent",
            title="Agent done: foo",
            body="first",
            metadata={"agent_name": "foo"},
        )
        second = svc.add(
            type="agent",
            title="Agent done: foo",
            body="second",
            metadata={"agent_name": "foo"},
        )
        # Same underlying row, not two separate entries.
        assert first.id == second.id
        all_notifs = svc.list_all()
        assert len(all_notifs) == 1
        assert all_notifs[0].body == "second"
        assert all_notifs[0].metadata.get("count") == 2

    def test_dedup_only_bumps_unread_entries(self, tmp_path):
        """A previously-read duplicate does not get re-bumped. A new one appears."""
        svc = self._patched_service(tmp_path)
        first = svc.add(
            type="agent",
            title="Agent done: foo",
            body="first",
            metadata={"agent_name": "foo"},
        )
        svc.mark_read(first.id)
        svc.add(
            type="agent",
            title="Agent done: foo",
            body="second",
            metadata={"agent_name": "foo"},
        )
        all_notifs = svc.list_all()
        assert len(all_notifs) == 2

    def test_spec_complete_dedup_survives_read(self, tmp_path):
        """spec_complete notifications dedup even after the user reads them.

        Regression for the "Your feature is live" modal re-firing every time
        a completed spec's verify endpoint is hit again. The fix makes
        spec_complete a permanent-dedup type so a read notification still
        absorbs repeat fires.
        """
        svc = self._patched_service(tmp_path)
        first = svc.add(
            type="spec_complete",
            title="Your feature is live",
            body="auto close is built and ready to try.",
            target="spec_complete:docs/spec/auto-close.md",
            metadata={"spec_path": "docs/spec/auto-close.md"},
        )
        svc.mark_read(first.id)
        second = svc.add(
            type="spec_complete",
            title="Your feature is live",
            body="auto close is built and ready to try.",
            target="spec_complete:docs/spec/auto-close.md",
            metadata={"spec_path": "docs/spec/auto-close.md"},
        )
        all_notifs = svc.list_all()
        assert len(all_notifs) == 1, "read spec_complete must not re-fire"
        assert second.id == first.id, "second add must return the original row"
        assert int(all_notifs[0].metadata.get("count", 1)) == 2

    def test_dedup_different_targets_keeps_both(self, tmp_path):
        svc = self._patched_service(tmp_path)
        svc.add(
            type="agent",
            title="Agent done: foo",
            body="b",
            metadata={"agent_name": "foo"},
        )
        svc.add(
            type="agent",
            title="Agent done: bar",
            body="b",
            metadata={"agent_name": "bar"},
        )
        assert len(svc.list_all()) == 2

    def test_roadmap_ready_dedupes_across_different_targets(self, tmp_path):
        """Regression: the backend was fanning out a single user-visible
        roadmap completion into multiple notification rows because the
        upstream caller fires from several code paths, each with a
        different target (e.g. timestamped roadmap path vs. stable
        roadmap.md). With the singleton-event dedupe, the second add()
        collapses into the first one regardless of the target."""
        svc = self._patched_service(tmp_path)
        first = svc.add(
            type="roadmap_ready",
            title="Roadmap ready",
            body="Open roadmap.md.",
            target="roadmap:/path/one.md",
            metadata={"source_agent": "foo"},
        )
        second = svc.add(
            type="roadmap_ready",
            title="Roadmap ready",
            body="Open roadmap.md.",
            target="roadmap:/path/two.md",
            metadata={"source_agent": "foo"},
        )
        third = svc.add(
            type="roadmap_ready",
            title="Roadmap ready",
            body="Open roadmap.md.",
            target="roadmap:/path/three.md",
            metadata={"source_agent": "foo"},
        )

        assert first.id == second.id == third.id
        all_notifs = svc.list_all()
        assert len(all_notifs) == 1
        assert all_notifs[0].metadata.get("count") == 3

    def test_roadmap_ready_after_read_creates_new_row(self, tmp_path):
        """A fresh roadmap completion after the user read the previous
        one must still surface as a new toast."""
        svc = self._patched_service(tmp_path)
        first = svc.add(
            type="roadmap_ready",
            title="Roadmap ready",
            body="Open roadmap.md.",
            target="roadmap:/path/one.md",
        )
        svc.mark_read(first.id)
        svc.add(
            type="roadmap_ready",
            title="Roadmap ready",
            body="Open roadmap.md.",
            target="roadmap:/path/two.md",
        )

        all_notifs = svc.list_all()
        assert len(all_notifs) == 2
        # Exactly one unread row so the bell badge says "1".
        assert sum(1 for n in all_notifs if not n.read) == 1

    def test_explicit_target_param_dedupes(self, tmp_path):
        svc = self._patched_service(tmp_path)
        svc.add(type="sync", title="Synced", body="ok", target="settings")
        svc.add(type="sync", title="Synced", body="ok", target="settings")
        all_notifs = svc.list_all()
        assert len(all_notifs) == 1
        assert all_notifs[0].metadata.get("count") == 2


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

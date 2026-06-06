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
        # Permanent dedup returns the existing row without any mutation,
        # so count stays at 1 and created_at is not refreshed.
        assert int(all_notifs[0].metadata.get("count", 1)) == 1

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


# ---------------------------------------------------------------------------
# Infra-agent completion must NOT fire notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("infra_name", [
    "dupe-guard-abc123",
    "stale-complete-20260428",
    "reap-ghost-worker",
    "ghost-cleaner-0a1b2c",
])
async def test_infra_agent_complete_no_notification(tmp_path, infra_name):
    """Internal housekeeping agents (dupe-guard-*, stale-complete-*, reap-*, ghost-*)
    must NOT produce a completion toast — they are infra noise, not user work."""
    from unittest.mock import AsyncMock, MagicMock
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers.agents import agent_metadata

    agent_metadata[infra_name] = {
        "spawned_at": "2026-04-28T00:00:00+00:00",
        "budget": "0.1",
        "model": "claude-haiku-4-5-20251001",
        "source": "api",
        "description": "Internal housekeeping.",
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

                resp = await client.post(f"/api/agents/{infra_name}/complete")
                assert resp.status_code == 200

        finally:
            agent_metadata.pop(infra_name, None)

    assert fired == [], (
        f"Infra agent '{infra_name}' must not fire a notification, but got: {fired}"
    )


def test_notifications_store_isolated_from_real_home(tmp_path):
    """Regression: test runs were leaking roadmap_ready notifications into
    ~/.myos/notifications.json because the conftest did not redirect
    NOTIFICATIONS_FILE. After the fix, the module-level constant must point
    to a tmp path, never to the real user store."""
    import services.notifications as mod
    real_file = Path.home() / ".myos" / "notifications.json"
    assert mod.NOTIFICATIONS_FILE != real_file, (
        "NOTIFICATIONS_FILE still points at the real user store; "
        "the conftest autouse fixture failed to redirect it. "
        "Every roadmap test run would pollute ~/.myos/notifications.json."
    )


@pytest.mark.asyncio
async def test_roadmap_complete_notification_goes_to_isolated_store(tmp_path):
    """Roadmap agent /complete must write its roadmap_ready notification to
    the isolated store (the path NOTIFICATIONS_FILE currently points to),
    not hardcoded to ~/.myos/notifications.json."""
    from unittest.mock import AsyncMock
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers import agents as agents_module
    from routers.agents import agent_metadata
    import services.notifications as notif_mod

    agent_name = "roadmap-isolation-test-agent"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-15T10:00:00+00:00",
        "template": "Roadmap",
        "status": "running",
    }

    fake_files = tmp_path / "files"
    fake_files.mkdir(exist_ok=True)
    real_file = Path.home() / ".myos" / "notifications.json"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch.object(agents_module, "MYOS_FILES_DIR", fake_files), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "", "daemon_running": False, "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(
                    return_value={"message": "ok", "timestamp": "2026-04-15T10:01:00+00:00"}
                )

                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": "Q1 goals: build amazing things. Ship fast. Learn. Grow."},
                )
            assert resp.status_code == 200
        finally:
            agent_metadata.pop(agent_name, None)

    # Notification must be in the isolated store
    all_notifs = notif_mod.notifications_service.list_all()
    roadmap_notifs = [n for n in all_notifs if n.type == "roadmap_ready"]
    assert len(roadmap_notifs) == 1, (
        f"Expected exactly 1 roadmap_ready notification in the isolated store, got {len(roadmap_notifs)}"
    )

    # Real file must not have been created or modified by this test
    if real_file.exists():
        import json as _json
        real_data = _json.loads(real_file.read_text())
        paths_in_real = [
            n.get("metadata", {}).get("roadmap_path", "")
            for n in real_data
            if n.get("type") == "roadmap_ready"
        ]
        assert not any(str(tmp_path) in p for p in paths_in_real), (
            "A roadmap_ready notification pointing at the test tmp_path was "
            "found in the real ~/.myos/notifications.json — the isolation fix failed."
        )


@pytest.mark.asyncio
async def test_normal_user_agent_complete_fires_notification(tmp_path):
    """A normal user-spawned agent must still produce a completion notification
    after the infra-agent guard is in place."""
    from unittest.mock import AsyncMock, MagicMock
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers.agents import agent_metadata

    agent_name = "my-feature-agent-abc"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-28T00:00:00+00:00",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "source": "claude-code",
        "description": "Implements my feature.",
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

                resp = await client.post(f"/api/agents/{agent_name}/complete")
                assert resp.status_code == 200

        finally:
            agent_metadata.pop(agent_name, None)

    assert len(fired) == 1, (
        f"User agent '{agent_name}' must fire exactly one notification, but got: {fired}"
    )
    assert fired[0]["type"] == "agent"
    assert agent_name in fired[0]["title"]


# ---------------------------------------------------------------------------
# Stale roadmap_ready cleanup (root cause: no-roadmap false positive)
# ---------------------------------------------------------------------------


def test_stale_roadmap_ready_notification_pruned_when_file_missing(tmp_path):
    """A roadmap_ready notification whose roadmap_path no longer exists on disk
    must be silently removed from the store on the next load. This guards
    against stale toasts that linger after test leakage or a user deleting
    their roadmap file."""
    import services.notifications as mod
    from services.notifications import NotificationsService

    notif_file = tmp_path / "notifications.json"
    stale_path = tmp_path / "files" / "roadmap.md"  # deliberately NOT created

    stale_entry = {
        "id": "stale-roadmap-notif-001",
        "type": "roadmap_ready",
        "title": "Roadmap ready",
        "body": "Open roadmap.md.",
        "action_label": "Open roadmap",
        "action_url": f"/files?path={stale_path}",
        "read": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "metadata": {"roadmap_path": str(stale_path), "kind": "roadmap_ready"},
    }
    notif_file.write_text(json.dumps([stale_entry]))

    svc = NotificationsService()
    with patch.object(mod, "NOTIFICATIONS_FILE", notif_file), \
         patch.object(mod, "MYOS_DIR", tmp_path):
        result = svc.list_all()

    assert result == [], (
        f"Stale roadmap_ready notification must be pruned on load, got: {result}"
    )
    remaining = json.loads(notif_file.read_text())
    assert remaining == [], (
        "Stale entry must be removed from the persistent store, got: "
        f"{remaining}"
    )


def test_valid_roadmap_ready_notification_kept_when_file_exists(tmp_path):
    """A roadmap_ready notification whose roadmap_path exists on disk must NOT
    be pruned. Only stale entries (missing file) are removed."""
    import services.notifications as mod
    from services.notifications import NotificationsService

    notif_file = tmp_path / "notifications.json"
    real_path = tmp_path / "files" / "roadmap.md"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("# Roadmap\n\nQ1: ship things.\n")

    live_entry = {
        "id": "live-roadmap-notif-001",
        "type": "roadmap_ready",
        "title": "Roadmap ready",
        "body": "Open roadmap.md.",
        "action_label": "Open roadmap",
        "action_url": f"/files?path={real_path}",
        "read": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "metadata": {"roadmap_path": str(real_path), "kind": "roadmap_ready"},
    }
    notif_file.write_text(json.dumps([live_entry]))

    svc = NotificationsService()
    with patch.object(mod, "NOTIFICATIONS_FILE", notif_file), \
         patch.object(mod, "MYOS_DIR", tmp_path):
        result = svc.list_all()

    assert len(result) == 1, (
        f"Live roadmap_ready notification must NOT be pruned, got: {result}"
    )
    assert result[0].id == "live-roadmap-notif-001"


@pytest.mark.asyncio
async def test_non_roadmap_agent_complete_does_not_emit_roadmap_ready(tmp_path):
    """A non-roadmap agent completing must NOT emit a roadmap_ready
    notification, even when it produces a summary. This is the regression
    guard for the stale toast that appeared for users who never ran the
    Roadmap template."""
    from unittest.mock import AsyncMock, MagicMock
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers.agents import agent_metadata

    agent_name = "prd-agent-no-roadmap-xyz"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-29T00:00:00+00:00",
        "template": "PRD",
        "status": "running",
        "source": "claude-code",
        "template_produces_doc": True,
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

                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": "Wrote the PRD. Audience, key pages, success criteria covered."},
                )
            assert resp.status_code == 200
        finally:
            agent_metadata.pop(agent_name, None)

    roadmap_fired = [f for f in fired if f.get("type") == "roadmap_ready"]
    assert roadmap_fired == [], (
        f"Non-roadmap agent must not emit roadmap_ready notification, got: {roadmap_fired}"
    )

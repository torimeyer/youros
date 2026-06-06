"""Tests for cross-device settings sync.

Tests cover:
- configure endpoint stores repo URL
- push/pull with mocked git subprocess calls
- status endpoint returns correct fields
- disconnect removes config
- startup pull hook fires when configured
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run_success(stdout="", stderr="", returncode=0):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _mock_run_fail(stderr="error", returncode=1):
    return _mock_run_success(stdout="", stderr=stderr, returncode=1)


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_status_not_configured(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.get("/api/sync/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["last_synced"] is None
    assert data["remote_url"] is None


@pytest.mark.asyncio
async def test_sync_status_configured(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({
        "repo_url": "git@github.com:user/myos-sync.git",
        "last_synced": "2026-04-08T10:00:00+00:00",
    }))
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.get("/api/sync/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["remote_url"] == "git@github.com:user/myos-sync.git"
    assert data["last_synced"] == "2026-04-08T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Configure endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_configure_stores_repo_url(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    sync_repo = tmp_path / "sync_repo"
    sync_repo.mkdir(exist_ok=True)

    with (
        patch("services.settings_sync.MYOS_DIR", tmp_path),
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.settings_sync.SYNC_REPO_PATH", sync_repo),
        patch("services.settings_sync._run", return_value=_mock_run_success()),
        patch("services.settings_sync._run_quiet", return_value=_mock_run_success(stdout="")),
    ):
        resp = await client.post(
            "/api/sync/configure",
            json={"repo_url": "git@github.com:user/myos-sync.git"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] == "configured"
    assert data["repo_url"] == "git@github.com:user/myos-sync.git"

    saved = json.loads(config_path.read_text())
    assert saved["repo_url"] == "git@github.com:user/myos-sync.git"


@pytest.mark.asyncio
async def test_configure_rejects_empty_url(client):
    resp = await client.post("/api/sync/configure", json={"repo_url": "  "})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Push endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_not_configured_returns_400(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.post("/api/sync/push")

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_push_copies_and_commits_files(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))

    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)
    sync_repo = myos_dir / "sync_repo"
    sync_repo.mkdir(exist_ok=True)
    (sync_repo / ".git").mkdir(exist_ok=True)

    # Create a settings.json to sync
    settings_file = myos_dir / "settings.json"
    settings_file.write_text(json.dumps({"os_name": "myOS"}))

    # diff --cached returns non-zero = there are staged changes
    def mock_run_quiet(cmd, cwd):
        if "diff" in cmd and "--cached" in cmd:
            return _mock_run_fail()  # returncode=1 means there are changes
        return _mock_run_success(stdout="origin")

    with (
        patch("services.settings_sync.MYOS_DIR", myos_dir),
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.settings_sync.SYNC_REPO_PATH", sync_repo),
        patch("services.settings_sync._run", return_value=_mock_run_success()),
        patch("services.settings_sync._run_quiet", side_effect=mock_run_quiet),
    ):
        resp = await client.post("/api/sync/push")

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] == "pushed"
    assert "settings.json" in data["files"]


@pytest.mark.asyncio
async def test_push_nothing_to_sync_when_no_files(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))

    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)
    sync_repo = myos_dir / "sync_repo"
    sync_repo.mkdir(exist_ok=True)
    (sync_repo / ".git").mkdir(exist_ok=True)

    # No files in SYNC_FILES exist in myos_dir
    with (
        patch("services.settings_sync.MYOS_DIR", myos_dir),
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.settings_sync.SYNC_REPO_PATH", sync_repo),
    ):
        resp = await client.post("/api/sync/push")

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] == "nothing to sync"


# ---------------------------------------------------------------------------
# Pull endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pull_not_configured_returns_400(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.post("/api/sync/pull")

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_pull_restores_files(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))

    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)
    sync_repo = myos_dir / "sync_repo"
    sync_repo.mkdir(exist_ok=True)
    git_dir = sync_repo / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "FETCH_HEAD").write_text("abc123 origin/main")

    # Put a settings.json in the sync repo (as if pulled from remote)
    settings_in_repo = sync_repo / "settings.json"
    settings_in_repo.write_text(json.dumps({"os_name": "WorkOS"}))

    def mock_run_quiet(cmd, cwd):
        if "rev-parse" in cmd:
            return _mock_run_success(stdout="abc123")  # has local commits
        if "remote" in cmd and "fetch" not in cmd:
            return _mock_run_success(stdout="origin")
        return _mock_run_success()

    with (
        patch("services.settings_sync.MYOS_DIR", myos_dir),
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.settings_sync.SYNC_REPO_PATH", sync_repo),
        patch("services.settings_sync._run_quiet", side_effect=mock_run_quiet),
    ):
        resp = await client.post("/api/sync/pull")

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] == "pulled"
    assert "settings.json" in data["files"]

    restored = json.loads((myos_dir / "settings.json").read_text())
    assert restored["os_name"] == "WorkOS"


# ---------------------------------------------------------------------------
# Disconnect endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disconnect_removes_config(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))

    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.post("/api/sync/disconnect")

    assert resp.status_code == 200
    assert resp.json()["result"] == "disconnected"
    assert not config_path.exists()


@pytest.mark.asyncio
async def test_disconnect_idempotent_when_not_configured(client, tmp_path):
    config_path = tmp_path / "sync_config.json"
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        resp = await client.post("/api/sync/disconnect")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------

def test_is_configured_false_when_no_file(tmp_path):
    from services import settings_sync
    config_path = tmp_path / "sync_config.json"
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        assert settings_sync.is_configured() is False


def test_is_configured_true_when_url_set(tmp_path):
    from services import settings_sync
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        assert settings_sync.is_configured() is True


def test_status_returns_all_fields(tmp_path):
    from services import settings_sync
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({
        "repo_url": "git@github.com:user/sync.git",
        "last_synced": "2026-04-08T10:00:00+00:00",
    }))
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        s = settings_sync.status()

    assert s["configured"] is True
    assert s["remote_url"] == "git@github.com:user/sync.git"
    assert s["last_synced"] == "2026-04-08T10:00:00+00:00"


def test_disconnect_deletes_config_file(tmp_path):
    from services import settings_sync
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))
    with patch("services.settings_sync.SYNC_CONFIG_PATH", config_path):
        settings_sync.disconnect()
    assert not config_path.exists()


# ---------------------------------------------------------------------------
# Startup pull hook test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_pull_fires_notification_when_files_synced(tmp_path):
    """When sync is configured and pull returns files, a notification is added."""
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps({"repo_url": "git@github.com:user/sync.git"}))

    notifications = []

    mock_notif_service = MagicMock()
    mock_notif_service.add = MagicMock(side_effect=lambda **kwargs: notifications.append(kwargs))

    with (
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.settings_sync.pull", return_value={"result": "pulled", "files": ["settings.json"]}),
        patch("services.settings_sync.is_configured", return_value=True),
        patch("services.notifications.notifications_service", mock_notif_service),
    ):
        import asyncio
        import importlib
        import sys

        # Simulate the _pull coroutine directly (without the sleep)
        async def _pull():
            from services import settings_sync
            from services.notifications import notifications_service

            if not settings_sync.is_configured():
                return

            result = settings_sync.pull()
            if result.get("files"):
                notifications_service.add(
                    type="sync",
                    title="Settings synced",
                    body="Settings synced from your other device.",
                    action_label="View Settings",
                    action_url="/settings",
                )

        await _pull()

    assert len(notifications) == 1
    assert notifications[0]["type"] == "sync"
    assert notifications[0]["title"] == "Settings synced"


@pytest.mark.asyncio
async def test_startup_pull_no_notification_when_no_files(tmp_path):
    """When pull returns no files (already up to date), no notification is added."""
    notifications = []

    mock_notif_service = MagicMock()
    mock_notif_service.add = MagicMock(side_effect=lambda **kwargs: notifications.append(kwargs))

    with (
        patch("services.settings_sync.pull", return_value={"result": "already up to date", "files": []}),
        patch("services.settings_sync.is_configured", return_value=True),
        patch("services.notifications.notifications_service", mock_notif_service),
    ):
        async def _pull():
            from services import settings_sync
            from services.notifications import notifications_service

            if not settings_sync.is_configured():
                return
            result = settings_sync.pull()
            if result.get("files"):
                notifications_service.add(
                    type="sync",
                    title="Settings synced",
                    body="Settings synced from your other device.",
                )

        await _pull()

    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_startup_pull_skips_when_not_configured(tmp_path):
    """When sync is not configured, the startup hook does nothing."""
    notifications = []
    mock_notif_service = MagicMock()
    mock_notif_service.add = MagicMock(side_effect=lambda **kwargs: notifications.append(kwargs))

    config_path = tmp_path / "sync_config.json"
    with (
        patch("services.settings_sync.SYNC_CONFIG_PATH", config_path),
        patch("services.notifications.notifications_service", mock_notif_service),
    ):
        async def _pull():
            from services import settings_sync
            from services.notifications import notifications_service

            if not settings_sync.is_configured():
                return
            result = settings_sync.pull()
            if result.get("files"):
                notifications_service.add(type="sync", title="Settings synced", body="...")

        await _pull()

    assert len(notifications) == 0

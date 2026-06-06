"""Tests for the upgrade check service and router."""

import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests for upgrade_check service
# ---------------------------------------------------------------------------

class TestSemverTuple:
    def test_basic(self):
        from services.upgrade_check import _semver_tuple
        assert _semver_tuple("2.4.0") == (2, 4, 0)
        assert _semver_tuple("v2.5.1") == (2, 5, 1)
        assert _semver_tuple("1.0") == (1, 0, 0)

    def test_comparison(self):
        from services.upgrade_check import _semver_tuple
        assert _semver_tuple("2.5.0") > _semver_tuple("2.4.0")
        assert _semver_tuple("2.4.0") == _semver_tuple("2.4.0")
        assert _semver_tuple("3.0.0") > _semver_tuple("2.99.99")


class TestCacheHelpers:
    def test_save_and_load(self, tmp_path):
        from services import upgrade_check as mod
        cache_file = tmp_path / "upgrade_cache.json"
        with patch.object(mod, "UPGRADE_CACHE_FILE", cache_file), patch.object(
            mod, "MYOS_DIR", tmp_path
        ):
            mod._save_cache({"myos": {"behind": False}, "ostk": {"behind": True}})
            result = mod._load_cache()
            assert result is not None
            assert result["ostk"]["behind"] is True

    def test_expired_cache_returns_none(self, tmp_path):
        from services import upgrade_check as mod
        cache_file = tmp_path / "upgrade_cache.json"
        # Write a cache with a timestamp 2 hours ago
        old_ts = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        payload = {"cached_at": old_ts, "result": {"myos": {}, "ostk": {}}}
        cache_file.write_text(json.dumps(payload))
        with patch.object(mod, "UPGRADE_CACHE_FILE", cache_file):
            result = mod._load_cache()
            assert result is None


class TestCheckMyOS:
    @pytest.mark.asyncio
    async def test_up_to_date(self):
        from services import upgrade_check as mod
        mock_run = MagicMock()
        # fetch returns 0
        # rev-list returns "0"
        # describe returns some tag
        mock_run.side_effect = [
            MagicMock(returncode=0),           # fetch
            MagicMock(returncode=0, stdout="0"),  # rev-list
            MagicMock(returncode=0, stdout="v2.2.0-0-gabcdef"),  # describe local
            MagicMock(returncode=0, stdout="v2.2.0"),            # describe remote
        ]
        with patch("subprocess.run", mock_run):
            result = await mod.check_myos()
        assert result["behind"] is False
        assert result["commits_behind"] == 0

    @pytest.mark.asyncio
    async def test_behind(self):
        from services import upgrade_check as mod
        mock_run = MagicMock()
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="3"),
            MagicMock(returncode=0, stdout="v2.1.0-0-gabcdef"),
            MagicMock(returncode=0, stdout="v2.2.0"),
        ]
        with patch("subprocess.run", mock_run):
            result = await mod.check_myos()
        assert result["behind"] is True
        assert result["commits_behind"] == 3


def _make_async_client_mock(tag_name: str, status_code: int = 200):
    """Build a mock for httpx.AsyncClient used as an async context manager."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"tag_name": tag_name}

    mock_instance = MagicMock()
    mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)

    mock_class = MagicMock(return_value=mock_instance)
    return mock_class


class TestCheckOstk:
    @pytest.mark.asyncio
    async def test_up_to_date(self):
        from services import upgrade_check as mod
        mock_run = MagicMock(returncode=0, stdout="ostk 2.4.0\n")
        mock_client_cls = _make_async_client_mock("v2.4.0")
        with patch("subprocess.run", return_value=mock_run), \
             patch("services.upgrade_check.httpx.AsyncClient", mock_client_cls):
            result = await mod.check_ostk()
        assert result["current"] == "2.4.0"
        assert result["latest"] == "2.4.0"
        assert result["behind"] is False

    @pytest.mark.asyncio
    async def test_behind(self):
        from services import upgrade_check as mod
        mock_run = MagicMock(returncode=0, stdout="ostk 2.4.0\n")
        mock_client_cls = _make_async_client_mock("v2.5.0")
        with patch("subprocess.run", return_value=mock_run), \
             patch("services.upgrade_check.httpx.AsyncClient", mock_client_cls):
            result = await mod.check_ostk()
        assert result["current"] == "2.4.0"
        assert result["latest"] == "2.5.0"
        assert result["behind"] is True

    @pytest.mark.asyncio
    async def test_ostk_not_installed(self):
        from services import upgrade_check as mod
        mock_client_cls = _make_async_client_mock("v2.5.0")
        with patch("subprocess.run", side_effect=FileNotFoundError), \
             patch("services.upgrade_check.httpx.AsyncClient", mock_client_cls):
            result = await mod.check_ostk()
        assert result["current"] == "unknown"
        assert result["behind"] is False


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upgrade_status_endpoint(client):
    mock_result = {
        "myos": {"current": "v2.2.0-0-gabc", "latest": "v2.2.0", "behind": False, "commits_behind": 0},
        "ostk": {"current": "2.4.0", "latest": "2.5.0", "behind": True},
    }
    with patch("routers.upgrade.check_all", new=AsyncMock(return_value=mock_result)):
        resp = await client.get("/api/upgrade/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["myos"]["behind"] is False
    assert data["ostk"]["behind"] is True


@pytest.mark.asyncio
async def test_upgrade_run_myos(client):
    mock_result = {"success": True, "message": "myOS updated successfully. Restart myOS to apply the update."}
    with patch("routers.upgrade.run_upgrade", new=AsyncMock(return_value=mock_result)):
        resp = await client.post("/api/upgrade/run", json={"target": "myos"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_upgrade_run_ostk(client):
    mock_result = {"success": True, "message": "ostk updated to 2.5.0 successfully."}
    with patch("routers.upgrade.run_upgrade", new=AsyncMock(return_value=mock_result)):
        resp = await client.post("/api/upgrade/run", json={"target": "ostk"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_upgrade_run_both(client):
    mock_result = {"success": True, "message": "myOS updated. ostk updated."}
    with patch("routers.upgrade.run_upgrade", new=AsyncMock(return_value=mock_result)):
        resp = await client.post("/api/upgrade/run", json={"target": "both"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_upgrade_run_invalid_target(client):
    resp = await client.post("/api/upgrade/run", json={"target": "invalid"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Regression tests: daemon restart after ostk upgrade (version drift fix)
# ---------------------------------------------------------------------------

class TestReadAnchorPid:
    def test_returns_dict_when_valid(self, tmp_path):
        from services.upgrade_check import _read_anchor_pid
        anchor = {"pid": 12345, "ostk_version": "6.0.0"}
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text(json.dumps(anchor))
        result = _read_anchor_pid(tmp_path)
        assert result == anchor

    def test_returns_none_when_missing(self, tmp_path):
        from services.upgrade_check import _read_anchor_pid
        result = _read_anchor_pid(tmp_path)
        assert result is None

    def test_returns_none_on_bad_json(self, tmp_path):
        from services.upgrade_check import _read_anchor_pid
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text("not json")
        result = _read_anchor_pid(tmp_path)
        assert result is None


class TestRestartStaleDaemon:
    def test_kills_stale_daemon(self, tmp_path):
        """Stale daemon (old version) must receive SIGTERM after upgrade."""
        from services import upgrade_check as mod
        anchor = {"pid": 99999, "ostk_version": "6.0.0"}
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text(json.dumps(anchor))

        with patch.object(mod, "PROJECT_ROOT", tmp_path), \
             patch("os.kill") as mock_kill:
            mod._restart_stale_daemon("6.0.5")

        mock_kill.assert_called_once_with(99999, signal.SIGTERM)

    def test_skips_when_version_already_matches(self, tmp_path):
        """No kill when daemon already runs the new version."""
        from services import upgrade_check as mod
        anchor = {"pid": 99999, "ostk_version": "6.0.5"}
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text(json.dumps(anchor))

        with patch.object(mod, "PROJECT_ROOT", tmp_path), \
             patch("os.kill") as mock_kill:
            mod._restart_stale_daemon("6.0.5")

        mock_kill.assert_not_called()

    def test_skips_when_no_anchor_pid(self, tmp_path):
        """No kill when anchor.pid is absent (daemon not running)."""
        from services import upgrade_check as mod
        with patch.object(mod, "PROJECT_ROOT", tmp_path), \
             patch("os.kill") as mock_kill:
            mod._restart_stale_daemon("6.0.5")

        mock_kill.assert_not_called()

    def test_handles_dead_process_gracefully(self, tmp_path):
        """ProcessLookupError (daemon already stopped) must not propagate."""
        from services import upgrade_check as mod
        anchor = {"pid": 99999, "ostk_version": "6.0.0"}
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text(json.dumps(anchor))

        with patch.object(mod, "PROJECT_ROOT", tmp_path), \
             patch("os.kill", side_effect=ProcessLookupError):
            mod._restart_stale_daemon("6.0.5")  # must not raise


class TestRestartStaleDaemonIntegration:
    """_restart_stale_daemon is the single gate: binary version == daemon version."""

    def test_version_drift_is_detected(self, tmp_path):
        """The function must kill any PID whose ostk_version != the new binary version.

        This is the regression gate for the boot-splash/--version drift bug.
        The boot splash version comes from the running daemon; ``ostk --version``
        reads the binary on disk. They drift when the binary is upgraded but the
        daemon is not restarted. The fix: _restart_stale_daemon() is called after
        every successful install and sends SIGTERM to the stale process.
        """
        from services import upgrade_check as mod

        # Simulate daemon running at 6.0.0 while binary is now 6.0.5
        anchor = {"pid": 54321, "ostk_version": "6.0.0", "build_hash": "abc"}
        (tmp_path / ".ostk").mkdir(exist_ok=True)
        (tmp_path / ".ostk" / "anchor.pid").write_text(json.dumps(anchor))

        with patch.object(mod, "PROJECT_ROOT", tmp_path), \
             patch("os.kill") as mock_kill:
            mod._restart_stale_daemon("6.0.5")

        # Daemon must be terminated so next boot starts with 6.0.5
        mock_kill.assert_called_once_with(54321, signal.SIGTERM)

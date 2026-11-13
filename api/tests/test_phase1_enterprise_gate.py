"""Phase 1 regression tests for enterprise_enabled gate and Bucket 4 router purge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def test_settings_enterprise_enabled_defaults_false():
    """Settings schema and store default enterprise_enabled to False."""
    from models.schemas import Settings
    from services import settings_store

    s = Settings()
    assert s.enterprise_enabled is False
    assert settings_store.is_enterprise_enabled() is False


def test_is_enterprise_enabled_env_override(monkeypatch):
    """YOUROS_ENTERPRISE_ENABLED overrides settings.json when set."""
    from services import settings_store

    settings_store.settings_store.update({"enterprise_enabled": False})
    monkeypatch.setenv("YOUROS_ENTERPRISE_ENABLED", "1")
    assert settings_store.is_enterprise_enabled() is True

    settings_store.settings_store.update({"enterprise_enabled": True})
    monkeypatch.setenv("YOUROS_ENTERPRISE_ENABLED", "0")
    assert settings_store.is_enterprise_enabled() is False

    monkeypatch.delenv("YOUROS_ENTERPRISE_ENABLED", raising=False)
    assert settings_store.is_enterprise_enabled() is True


@pytest.mark.asyncio
async def test_settings_endpoint_roundtrips_enterprise_enabled(client):
    """GET/PATCH /api/settings exposes and persists enterprise_enabled."""
    r1 = await client.get("/api/settings")
    assert r1.status_code == 200
    assert r1.json()["enterprise_enabled"] is False

    r2 = await client.patch("/api/settings", json={"enterprise_enabled": True})
    assert r2.status_code == 200

    r3 = await client.get("/api/settings")
    assert r3.status_code == 200
    assert r3.json()["enterprise_enabled"] is True


@pytest.mark.asyncio
async def test_core_mode_unmounts_enterprise_and_purged_routers(client):
    """With enterprise_enabled=False, enterprise, /admin, and Bucket 4 routers return 404 while Core stays 200."""
    # Enterprise / Work extension endpoints must 404 in Core mode
    for path in (
        "/admin",
        "/admin/members",
        "/api/enterprise",
        "/api/org/settings",
        "/api/portfolio",
        "/api/atlassian/status",
        "/api/slack/status",
        "/api/team",
    ):
        resp = await client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path} in Core mode, got {resp.status_code}"

    # Purged Bucket 4 routers must 404 unconditionally
    for purged in (
        "/api/imessage/status",
        "/api/gems",
        "/api/adventures/templates",
    ):
        resp = await client.get(purged)
        assert resp.status_code == 404, f"Expected 404 for purged {purged}, got {resp.status_code}"

    # Core GitHub PR linkage router remains mounted
    gh_resp = await client.get("/api/github/status")
    assert gh_resp.status_code == 200


@pytest.mark.asyncio
async def test_core_startup_skips_enterprise_and_gmail_background_loops(monkeypatch):
    """Core startup must not start Atlassian sync, Portfolio scheduler, or Gmail unread polling."""
    import main as main_mod

    monkeypatch.setenv("YOUROS_ENTERPRISE_ENABLED", "0")
    assert not hasattr(main_mod, "schedule_gmail_unread_notification"), (
        "schedule_gmail_unread_notification should be removed from main.py"
    )

    with (
        patch.object(main_mod, "schedule_atlassian_sync", new_callable=AsyncMock) as mock_atl,
        patch("services.portfolio_health.start_portfolio_scheduler", new_callable=AsyncMock) as mock_port,
    ):
        await main_mod._start_optional_enterprise_schedulers(lambda t: t)
        mock_atl.assert_not_called()
        mock_port.assert_not_called()

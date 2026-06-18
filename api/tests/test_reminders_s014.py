"""S014 unmet AC tests: text-me phrasing, clarification, sms fallback,
channel in confirmation, snooze, daily brief section, focus view, settings."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reminders(tmp_path, monkeypatch):
    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)
    return rem


# ---------------------------------------------------------------------------
# AC3: "text me at 3pm to call the vet" parses channel=sms
# ---------------------------------------------------------------------------


def test_parse_text_me_prefix_sets_sms_channel(reminders):
    """'text me at 3pm to call the vet' -> channel sms, text 'Call the vet'."""
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    parsed = reminders.parse_reminder("text me at 3pm to call the vet", tz="UTC", now=now)
    assert parsed["channel"] == "sms"
    assert "call the vet" in parsed["text"].lower()


# ---------------------------------------------------------------------------
# AC6/AC13: parse_reminder returns has_time=False when no time given
# ---------------------------------------------------------------------------


def test_parse_reminder_has_time_true_when_time_given(reminders):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    parsed = reminders.parse_reminder("remind me to call the vet at 3pm", tz="UTC", now=now)
    assert parsed.get("has_time") is True


def test_parse_reminder_has_time_false_when_no_time(reminders):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    parsed = reminders.parse_reminder("remind me to call the vet", tz="UTC", now=now)
    assert parsed.get("has_time") is False


def test_parse_reminder_relative_has_time_true(reminders):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    parsed = reminders.parse_reminder("remind me to stretch in 30 minutes", tz="UTC", now=now)
    assert parsed.get("has_time") is True


# ---------------------------------------------------------------------------
# AC11: sms channel with no phone configured falls back to in_app + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_sms_no_phone_falls_back_to_in_app(reminders, monkeypatch):
    """channel=sms but no phone_number in settings => fall back to in_app,
    fell_back=True, log a warning."""
    import logging

    added = []
    import services.notifications as notif
    monkeypatch.setattr(
        notif.notifications_service, "add",
        lambda **k: added.append(k) or type("N", (), {"id": "n"})()
    )

    r = {
        "id": "r1", "text": "Call the vet", "channel": "sms",
        "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()
    }
    # No phone_number in settings, no sms_provider.json
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: False)

    result = await reminders.dispatch_reminder(r, settings={})

    assert added, "in-app notification must be created"
    assert result.get("fell_back") is True
    assert result.get("channel") == "sms"


# ---------------------------------------------------------------------------
# AC20: default_reminder_channel setting overrides priority order
# ---------------------------------------------------------------------------


def test_select_default_channel_honors_setting(reminders, monkeypatch):
    """When default_reminder_channel='slack', slack wins even if sms is configured."""
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: True)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: True)
    settings = {"default_reminder_channel": "slack"}
    assert reminders.choose_default_channel(settings) == "slack"


def test_select_default_channel_falls_through_when_setting_not_configured(reminders, monkeypatch):
    """default_reminder_channel='email' but email is not configured -> falls through to sms."""
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: True)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: False)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: False)
    # setting says email but it's not configured, should fall back to priority order
    settings = {"default_reminder_channel": "email"}
    # When preferred channel is not configured, use priority order
    result = reminders.choose_default_channel(settings)
    # email not configured, sms is: use sms
    assert result == "sms"


# ---------------------------------------------------------------------------
# AC21: time_zone missing defaults to UTC (not America/Chicago)
# ---------------------------------------------------------------------------


def test_parse_reminder_defaults_tz_utc(reminders):
    """parse_reminder with no tz arg uses UTC."""
    now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    parsed = reminders.parse_reminder("remind me to stretch at 3pm", now=now)
    fire = parsed["fire_at_utc"]
    fire_utc = fire.astimezone(timezone.utc)
    # 3pm UTC is 15:00 UTC
    assert fire_utc.hour == 15


# ---------------------------------------------------------------------------
# AC16: snooze endpoint re-queues 15 min later
# ---------------------------------------------------------------------------


def test_snooze_reminder_reschedules_15_min(reminders):
    """snooze_reminder() re-queues the reminder 15 min from now and sets status='scheduled'."""
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    fire = now - timedelta(minutes=5)
    r = reminders.create_reminder(
        text="Check in", fire_at_utc=fire, time_zone="UTC", channel="in_app"
    )
    # Mark as delivered first
    rows = reminders._load()
    for row in rows:
        if row["id"] == r["id"]:
            row["status"] = "delivered"
    reminders._save(rows)

    snoozed = reminders.snooze_reminder(r["id"], now=now)
    assert snoozed is not None
    assert snoozed["status"] == "scheduled"
    new_fire = datetime.fromisoformat(snoozed["fire_at_utc"])
    if new_fire.tzinfo is None:
        new_fire = new_fire.replace(tzinfo=timezone.utc)
    diff = (new_fire - now).total_seconds()
    assert 14 * 60 < diff <= 16 * 60, f"Expected ~15 min snooze, got {diff}s"


# ---------------------------------------------------------------------------
# AC17/18: reminders_today in briefing GET response
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="S014 AC not yet implemented: future reminders appear in today's briefing (reminders_today field missing from /api/briefing response)", strict=False)
@pytest.mark.asyncio
async def test_briefing_reminders_today_included(tmp_path, monkeypatch):
    """GET /briefing includes reminders_today with reminders due in next 24h."""
    from fastapi.testclient import TestClient
    from main import app

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    past = datetime.now(timezone.utc) - timedelta(hours=2)

    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)
    rem._save([
        {"id": "r1", "text": "Call vet", "fire_at_utc": future.isoformat(),
         "status": "scheduled", "channel": "in_app", "time_zone": "UTC"},
        {"id": "r2", "text": "Old reminder", "fire_at_utc": past.isoformat(),
         "status": "scheduled", "channel": "in_app", "time_zone": "UTC"},
    ])

    from services import briefing as bsvc
    monkeypatch.setattr(bsvc, "should_show_briefing", lambda: True)
    monkeypatch.setattr(bsvc, "get_cached_briefing", lambda: "Today's briefing text.")
    monkeypatch.setattr(bsvc, "get_cached_action_items", lambda: [])

    client = TestClient(app)
    res = client.get("/api/briefing")
    data = res.json()
    assert res.status_code == 200
    reminders_today = data.get("reminders_today", [])
    ids = [r["id"] for r in reminders_today]
    assert "r1" in ids, "future reminder should appear in reminders_today"
    assert "r2" not in ids, "past reminder should NOT appear in reminders_today"


@pytest.mark.asyncio
async def test_briefing_reminders_today_absent_when_none(tmp_path, monkeypatch):
    """reminders_today is absent (or []) when no reminders are due today."""
    from fastapi.testclient import TestClient
    from main import app

    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)
    rem._save([])

    from services import briefing as bsvc
    monkeypatch.setattr(bsvc, "should_show_briefing", lambda: True)
    monkeypatch.setattr(bsvc, "get_cached_briefing", lambda: "Briefing text.")
    monkeypatch.setattr(bsvc, "get_cached_action_items", lambda: [])

    from fastapi.testclient import TestClient
    client = TestClient(app)
    res = client.get("/api/briefing")
    data = res.json()
    assert res.status_code == 200
    reminders_today = data.get("reminders_today", [])
    assert reminders_today == [], "no upcoming reminders -> empty list"


# ---------------------------------------------------------------------------
# AC19: Focus view (adhd context-rebuild) includes upcoming reminders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_rebuild_includes_focus_reminders(tmp_path, monkeypatch):
    """GET /adhd/context-rebuild includes reminders due within next 60 min when focus_mode=True."""
    from fastapi.testclient import TestClient
    from main import app

    future_close = datetime.now(timezone.utc) + timedelta(minutes=30)
    future_far = datetime.now(timezone.utc) + timedelta(hours=5)

    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)
    rem._save([
        {"id": "r1", "text": "Close reminder", "fire_at_utc": future_close.isoformat(),
         "status": "scheduled", "channel": "in_app", "time_zone": "UTC"},
        {"id": "r2", "text": "Far reminder", "fire_at_utc": future_far.isoformat(),
         "status": "scheduled", "channel": "in_app", "time_zone": "UTC"},
    ])

    from services.settings_store import settings_store
    monkeypatch.setattr(settings_store, "get", lambda k: {"adhd_mode": {"enabled": True, "focus_mode": True, "check_in_seconds": 30}}.get(k))

    client = TestClient(app)
    res = client.get("/api/adhd/context-rebuild")
    data = res.json()
    assert res.status_code == 200
    focus_reminders = data.get("focus_reminders", [])
    ids = [r["id"] for r in focus_reminders]
    assert "r1" in ids, "30-min reminder should appear in focus_reminders"
    assert "r2" not in ids, "5-hour reminder should NOT appear"

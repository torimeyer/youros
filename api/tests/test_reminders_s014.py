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


# ---------------------------------------------------------------------------
# AC12/AC13/AC21: _handle_remind_me chat protocol — confirmation and clarification
#
# Invariant: when a user says "remind me to X at TIME" in chat, yourOS replies
# with plain-language confirmation (text, local time, channel) before the turn
# ends — no AI round-trip required.
# ---------------------------------------------------------------------------


class _FakeWS:
    """Minimal WebSocket stand-in that captures send_json calls."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def tokens(self) -> list[str]:
        return [m["data"] for m in self.messages if m.get("type") == "token"]

    def done_count(self) -> int:
        return sum(1 for m in self.messages if m.get("type") == "done")


@pytest.mark.asyncio
async def test_chat_reminder_confirmation_includes_text_time_channel(tmp_path, monkeypatch):
    """AC12: reminder created via chat -> reply contains reminder text, local time, and channel."""
    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)

    import services.settings_store as ss_mod
    monkeypatch.setattr(ss_mod.settings_store, "get", lambda k, *a: "America/New_York" if k in ("time_zone", "timezone") else None)

    from routers.chat import _handle_remind_me

    ws = _FakeWS()
    handled = await _handle_remind_me("remind me to call the vet at 3pm", ws)

    assert handled is True, "_handle_remind_me should return True"
    assert ws.done_count() == 1, "exactly one 'done' frame expected"
    token_text = " ".join(ws.tokens())
    assert "call the vet" in token_text.lower(), f"reminder text missing from reply: {token_text!r}"
    assert "3" in token_text, f"time missing from reply: {token_text!r}"
    assert any(
        label in token_text.lower()
        for label in ("text message", "email", "slack", "imessage", "notification")
    ), f"channel label missing from reply: {token_text!r}"


@pytest.mark.asyncio
async def test_chat_reminder_no_time_sends_clarification(tmp_path, monkeypatch):
    """AC13: when no time is in the input, yourOS asks for the time rather than creating a reminder."""
    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)

    import services.settings_store as ss_mod
    monkeypatch.setattr(ss_mod.settings_store, "get", lambda k, *a: "UTC" if k in ("time_zone", "timezone") else None)

    from routers.chat import _handle_remind_me

    ws = _FakeWS()
    handled = await _handle_remind_me("remind me to take my meds", ws)

    assert handled is True, "_handle_remind_me should return True"
    assert ws.done_count() == 1
    token_text = " ".join(ws.tokens())
    assert "time" in token_text.lower() or "when" in token_text.lower(), (
        f"clarification should mention time/when, got: {token_text!r}"
    )
    # No reminder should be created
    rows = rem._load()
    assert rows == [], "no reminder should be persisted when no time is given"


@pytest.mark.asyncio
async def test_chat_reminder_no_timezone_notes_utc(tmp_path, monkeypatch):
    """AC21: when time_zone is missing from settings, confirmation mentions UTC."""
    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)

    import services.settings_store as ss_mod
    monkeypatch.setattr(ss_mod.settings_store, "get", lambda k, *a: None)

    from routers.chat import _handle_remind_me

    ws = _FakeWS()
    handled = await _handle_remind_me("remind me to stretch at 2pm", ws)

    assert handled is True
    token_text = " ".join(ws.tokens())
    assert "utc" in token_text.lower(), (
        f"UTC note missing from confirmation when no timezone set, got: {token_text!r}"
    )


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

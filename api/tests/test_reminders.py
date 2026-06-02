"""Tests for the reminders service: parse, timezone, firing, channels.

These cover the core promise of the "remind me" feature: a person says
"remind me to X at TIME", it fires at the right wall-clock moment in their
own time zone, on the channel they picked (or the best default), exactly
once, and surfaces in the app even if the outside channel fails.

Covered:
- parse_reminder() pulls the what, the when, and the optional channel
- the fire time is the correct UTC instant for the person's time zone
- a one-time reminder fires once and never double-fires after a restart
- the firing loop delivers on the chosen channel AND records in-app
- default-channel selection follows the configured fallback order
- if the chosen channel send fails, it falls back and still records in-app
- each channel send path (sms/slack/email) is exercised with senders mocked
- CRUD: create / list upcoming / cancel
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def reminders(tmp_path, monkeypatch):
    """Point the reminders store at a tmp file and return the module."""
    import services.reminders as rem
    store_path = tmp_path / "reminders.json"
    monkeypatch.setattr(rem, "REMINDERS_PATH", store_path)
    return rem


# ---------------------------------------------------------------------------
# Parsing: what / when / how
# ---------------------------------------------------------------------------


def test_parse_extracts_text_and_time(reminders):
    """'remind me to call the dentist at 3pm' -> text + a 3pm fire time."""
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    parsed = reminders.parse_reminder(
        "remind me to call the dentist at 3pm",
        tz="America/Chicago",
        now=now,
    )
    assert parsed["text"] == "Call the dentist"
    # 3pm today in Chicago.
    fire_local = parsed["fire_at_utc"].astimezone(ZoneInfo("America/Chicago"))
    assert fire_local.hour == 15
    assert fire_local.minute == 0
    assert fire_local.date() == now.date()
    assert parsed["channel"] == "default"


def test_parse_picks_channel_text(reminders):
    """'...text me' resolves to the sms channel."""
    now = datetime(2026, 6, 1, 6, 0, tzinfo=ZoneInfo("America/Chicago"))
    parsed = reminders.parse_reminder(
        "remind me to take the trash out tomorrow at 7am, text me",
        tz="America/Chicago",
        now=now,
    )
    assert parsed["text"] == "Take the trash out"
    assert parsed["channel"] == "sms"
    fire_local = parsed["fire_at_utc"].astimezone(ZoneInfo("America/Chicago"))
    assert fire_local.hour == 7
    assert fire_local.date() == (now + timedelta(days=1)).date()


def test_parse_picks_channel_slack(reminders):
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    parsed = reminders.parse_reminder(
        "remind me to stand up at 2pm in slack",
        tz="America/Chicago",
        now=now,
    )
    assert parsed["channel"] == "slack"
    assert "stand up" in parsed["text"].lower()


def test_parse_time_already_passed_rolls_to_tomorrow(reminders):
    """If the clock time already passed today, schedule it for tomorrow."""
    now = datetime(2026, 6, 1, 16, 0, tzinfo=ZoneInfo("America/Chicago"))  # 4pm
    parsed = reminders.parse_reminder(
        "remind me to call mom at 3pm",
        tz="America/Chicago",
        now=now,
    )
    fire_local = parsed["fire_at_utc"].astimezone(ZoneInfo("America/Chicago"))
    assert fire_local.hour == 15
    assert fire_local.date() == (now + timedelta(days=1)).date()


# ---------------------------------------------------------------------------
# Time zone -> correct UTC instant (the core of "fires at the right time")
# ---------------------------------------------------------------------------


def test_local_time_converts_to_correct_utc_instant(reminders):
    """3pm Chicago (CDT, UTC-5 in June) is 20:00 UTC."""
    now = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    parsed = reminders.parse_reminder(
        "remind me to ship it at 3pm",
        tz="America/Chicago",
        now=now,
    )
    fire_utc = parsed["fire_at_utc"].astimezone(timezone.utc)
    assert fire_utc.hour == 20  # 15:00 CDT + 5 = 20:00 UTC
    assert fire_utc.tzinfo == timezone.utc


def test_different_zones_yield_different_utc_instants(reminders):
    now_ny = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    now_la = datetime(2026, 6, 1, 6, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    p_ny = reminders.parse_reminder("remind me to X at 3pm", tz="America/New_York", now=now_ny)
    p_la = reminders.parse_reminder("remind me to X at 3pm", tz="America/Los_Angeles", now=now_la)
    # 3pm in two zones three hours apart must be three hours apart in UTC.
    delta = p_la["fire_at_utc"] - p_ny["fire_at_utc"]
    assert delta == timedelta(hours=3)


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


def test_create_and_list_upcoming(reminders):
    fire = datetime.now(timezone.utc) + timedelta(hours=2)
    r = reminders.create_reminder(
        text="Call the dentist",
        fire_at_utc=fire,
        time_zone="America/Chicago",
        channel="email",
    )
    assert r["id"]
    assert r["status"] == "scheduled"
    upcoming = reminders.list_upcoming()
    assert any(x["id"] == r["id"] for x in upcoming)


def test_cancel_reminder(reminders):
    fire = datetime.now(timezone.utc) + timedelta(hours=2)
    r = reminders.create_reminder(
        text="Stretch", fire_at_utc=fire, time_zone="UTC", channel="in_app"
    )
    assert reminders.cancel_reminder(r["id"]) is True
    stored = [x for x in reminders.list_all() if x["id"] == r["id"]][0]
    assert stored["status"] == "cancelled"
    # Cancelled reminders are not "upcoming".
    assert not any(x["id"] == r["id"] for x in reminders.list_upcoming())


# ---------------------------------------------------------------------------
# Firing: due detection + no double-fire after restart
# ---------------------------------------------------------------------------


def test_due_reminders_only_returns_past_undelivered(reminders):
    now = datetime.now(timezone.utc)
    past = reminders.create_reminder(
        text="past", fire_at_utc=now - timedelta(minutes=1), time_zone="UTC", channel="in_app"
    )
    future = reminders.create_reminder(
        text="future", fire_at_utc=now + timedelta(hours=1), time_zone="UTC", channel="in_app"
    )
    due_ids = {r["id"] for r in reminders.due_reminders(now=now)}
    assert past["id"] in due_ids
    assert future["id"] not in due_ids


@pytest.mark.asyncio
async def test_one_time_reminder_fires_once_no_double_fire(reminders, monkeypatch):
    """A delivered reminder is never returned as due again, even after a
    fresh load of the store (simulating a backend restart)."""
    now = datetime.now(timezone.utc)
    r = reminders.create_reminder(
        text="ping", fire_at_utc=now - timedelta(seconds=30), time_zone="UTC", channel="in_app"
    )

    sent = []

    async def fake_dispatch(reminder, settings):
        sent.append(reminder["id"])
        return {"channel": "in_app", "ok": True}

    monkeypatch.setattr(reminders, "dispatch_reminder", fake_dispatch)

    fired1 = await reminders.fire_due_reminders(now=now)
    assert len(fired1) == 1
    assert sent == [r["id"]]

    # Simulate a restart: nothing in memory, the store is reloaded from disk.
    # The delivered marker must keep it from firing again.
    fired2 = await reminders.fire_due_reminders(now=now + timedelta(minutes=1))
    assert fired2 == []
    assert sent == [r["id"]]  # still only fired once

    stored = [x for x in reminders.list_all() if x["id"] == r["id"]][0]
    assert stored["status"] == "delivered"
    assert stored["delivered_at"]


# ---------------------------------------------------------------------------
# Default channel selection (fallback order)
# ---------------------------------------------------------------------------


def test_default_channel_order_prefers_sms_when_configured(reminders, monkeypatch):
    settings = {
        "phone_number": "+15551234567",
        "slack_user_id": "U123",
        "reminder_email": "me@example.com",
    }
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: True)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: True)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: True)
    assert reminders.choose_default_channel(settings) == "sms"


def test_default_channel_falls_to_slack_then_email_then_in_app(reminders, monkeypatch):
    settings = {}
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: False)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: True)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: True)
    assert reminders.choose_default_channel(settings) == "slack"

    monkeypatch.setattr(reminders, "_slack_configured", lambda s: False)
    assert reminders.choose_default_channel(settings) == "email"

    monkeypatch.setattr(reminders, "_email_configured", lambda s: False)
    assert reminders.choose_default_channel(settings) == "in_app"


# ---------------------------------------------------------------------------
# Channel dispatch: each path + in-app always recorded + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_in_app_records_notification(reminders, monkeypatch):
    added = []

    def fake_add(**kwargs):
        added.append(kwargs)
        return type("N", (), {"id": "n1"})()

    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", fake_add)

    r = {"id": "r1", "text": "Call the dentist", "channel": "in_app",
         "time_zone": "America/Chicago",
         "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={})
    assert result["ok"] is True
    assert result["channel"] == "in_app"
    # In-app record always created.
    assert added, "expected a notification to be recorded"
    assert "Call the dentist" in (added[0]["title"] + added[0]["body"])


@pytest.mark.asyncio
async def test_dispatch_email_uses_email_sender_and_records_in_app(reminders, monkeypatch):
    sent = {}

    async def fake_send_email(to, subject, body):
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = body
        return {"ok": True}

    monkeypatch.setattr(reminders, "send_email_reminder", fake_send_email)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: True)

    added = []
    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", lambda **k: added.append(k) or type("N", (), {"id": "n"})())

    r = {"id": "r1", "text": "Pay rent", "channel": "email",
         "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={"reminder_email": "me@example.com"})
    assert result["ok"] is True
    assert result["channel"] == "email"
    assert sent["to"] == "me@example.com"
    assert "Pay rent" in sent["subject"] or "Pay rent" in sent["body"]
    assert added  # in-app record still created


@pytest.mark.asyncio
async def test_dispatch_slack_opens_dm_and_posts(reminders, monkeypatch):
    calls = {}

    async def fake_send_slack(slack_user_id, text):
        calls["user"] = slack_user_id
        calls["text"] = text
        return {"ok": True}

    monkeypatch.setattr(reminders, "send_slack_reminder", fake_send_slack)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: True)
    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", lambda **k: type("N", (), {"id": "n"})())

    r = {"id": "r1", "text": "Standup", "channel": "slack",
         "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={"slack_user_id": "U999"})
    assert result["ok"] is True
    assert result["channel"] == "slack"
    assert calls["user"] == "U999"
    assert "Standup" in calls["text"]


@pytest.mark.asyncio
async def test_dispatch_sms_uses_sms_sender(reminders, monkeypatch):
    calls = {}

    async def fake_send_sms(to_number, body):
        calls["to"] = to_number
        calls["body"] = body
        return {"ok": True}

    monkeypatch.setattr(reminders, "send_sms_reminder", fake_send_sms)
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: True)
    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", lambda **k: type("N", (), {"id": "n"})())

    r = {"id": "r1", "text": "Trash", "channel": "sms",
         "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={"phone_number": "+15551112222"})
    assert result["ok"] is True
    assert result["channel"] == "sms"
    assert calls["to"] == "+15551112222"
    assert "Trash" in calls["body"]


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_chosen_channel_fails(reminders, monkeypatch):
    """If the chosen channel send raises/fails, the reminder still records
    in-app and the result reports the fallback + a plain-language note."""
    async def failing_email(to, subject, body):
        raise RuntimeError("Gmail not connected")

    monkeypatch.setattr(reminders, "send_email_reminder", failing_email)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: True)

    added = []
    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", lambda **k: added.append(k) or type("N", (), {"id": "n"})())

    r = {"id": "r1", "text": "Important", "channel": "email",
         "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={"reminder_email": "me@example.com"})
    # The outside channel failed, but in-app still recorded and we report it.
    assert added, "in-app record must exist even when outside channel fails"
    assert result["in_app_recorded"] is True
    assert result.get("fell_back") is True
    assert result.get("error")


@pytest.mark.asyncio
async def test_default_channel_resolved_at_fire_time(reminders, monkeypatch):
    """A reminder created with channel='default' resolves to the best
    configured channel when it fires, and reports which one it used."""
    monkeypatch.setattr(reminders, "_sms_configured", lambda s: False)
    monkeypatch.setattr(reminders, "_slack_configured", lambda s: False)
    monkeypatch.setattr(reminders, "_email_configured", lambda s: True)

    sent = {}

    async def fake_send_email(to, subject, body):
        sent["to"] = to
        return {"ok": True}

    monkeypatch.setattr(reminders, "send_email_reminder", fake_send_email)
    import services.notifications as notif
    monkeypatch.setattr(notif.notifications_service, "add", lambda **k: type("N", (), {"id": "n"})())

    r = {"id": "r1", "text": "X", "channel": "default",
         "time_zone": "UTC", "fire_at_utc": datetime.now(timezone.utc).isoformat()}
    result = await reminders.dispatch_reminder(r, settings={"reminder_email": "me@example.com"})
    assert result["channel"] == "email"
    assert sent["to"] == "me@example.com"


# ---------------------------------------------------------------------------
# Repeating reminders (reuse the weekday machinery)
# ---------------------------------------------------------------------------


def test_repeating_weekday_reschedules_after_fire(reminders):
    """A weekday-repeating reminder, once delivered, gets a fresh fire time
    on the next matching weekday so it fires again."""
    # Monday 2026-06-01 08:00 Chicago.
    tz = "America/Chicago"
    fire = datetime(2026, 6, 1, 8, 0, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    r = reminders.create_reminder(
        text="Stretch", fire_at_utc=fire, time_zone=tz, channel="in_app",
        repeat={"kind": "weekdays", "hour": 8, "minute": 0},
    )
    next_fire = reminders.next_fire_for_repeat(r, after=fire)
    assert next_fire is not None
    nxt_local = next_fire.astimezone(ZoneInfo(tz))
    # Next weekday after Monday is Tuesday, still 8am local.
    assert nxt_local.hour == 8
    assert nxt_local.weekday() == 1  # Tuesday

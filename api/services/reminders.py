"""Reminder service: parse, store, schedule, fire, and deliver.

Reminders live in ~/.youros/reminders.json. The scheduler loop is started
once in main.py's lifespan and polls every 10 seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import services.notifications as notif
import services.imessage as imessage
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

_MYOS_DIR = youros_home()
REMINDERS_PATH = _MYOS_DIR / "reminders.json"

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    if not REMINDERS_PATH.exists():
        return []
    try:
        return json.loads(REMINDERS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save(rows: list[dict]) -> None:
    REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_PATH.write_text(json.dumps(rows, default=str, indent=2))


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

_HOUR_12_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)
_HOUR_24_RE = re.compile(r"\b(?:at\s+)?(\d{1,2}):(\d{2})\b")
_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+)\s+(minute|hour|min|hr)s?\b",
    re.IGNORECASE,
)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_CHANNEL_RE = re.compile(
    r"\b(text\s+me|via\s+sms|in\s+slack|via\s+slack|email\s+me|via\s+email)\b",
    re.IGNORECASE,
)
# "text me at 3pm to call the vet" — leading "text me" phrasing (AC3)
_TEXT_ME_PREFIX_RE = re.compile(r"^text\s+me\b", re.IGNORECASE)
_REMIND_ME_RE = re.compile(r"remind\s+me\s+to\s+(.+)", re.IGNORECASE)
# "text me at TIME to X" body extraction
_TEXT_ME_BODY_RE = re.compile(r"^text\s+me\b.*?\bto\s+(.+)", re.IGNORECASE)


def _parse_hm(text: str) -> Optional[tuple[int, int]]:
    m = _HOUR_12_RE.search(text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        ampm = m.group(3).lower()
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return h, mn
    m = _HOUR_24_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _strip_meta(raw: str) -> str:
    t = _CHANNEL_RE.sub("", raw)
    t = _TOMORROW_RE.sub("", t)
    t = _RELATIVE_RE.sub("", t)
    t = _HOUR_12_RE.sub("", t)
    t = _HOUR_24_RE.sub("", t)
    t = re.sub(r"\bat\s*,?\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bat\s+,", ",", t, flags=re.IGNORECASE)
    t = re.sub(r"[,\s]+$", "", t.strip())
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t.capitalize() if t else raw.strip().capitalize()


def parse_reminder(
    text: str,
    *,
    tz: str = "UTC",
    now: Optional[datetime] = None,
) -> dict:
    """Parse a natural-language reminder phrase into {text, fire_at_utc, channel, has_time}.

    has_time is True when a specific time was found in the input; False means
    the caller should ask the user for a time rather than silently defaulting.
    """
    zone = ZoneInfo(tz)
    if now is None:
        now = datetime.now(zone)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    local_now = now.astimezone(zone)

    channel = "default"
    # "text me at TIME to X" prefix sets sms channel (AC3)
    if _TEXT_ME_PREFIX_RE.match(text):
        channel = "sms"
    else:
        cm = _CHANNEL_RE.search(text)
        if cm:
            kw = cm.group(1).lower().replace(" ", "")
            if "text" in kw or "sms" in kw:
                channel = "sms"
            elif "slack" in kw:
                channel = "slack"
            elif "email" in kw:
                channel = "email"

    is_tomorrow = bool(_TOMORROW_RE.search(text))
    rel_m = _RELATIVE_RE.search(text)

    # Extract reminder body text
    if _TEXT_ME_PREFIX_RE.match(text):
        body_m = _TEXT_ME_BODY_RE.match(text)
        raw_body = body_m.group(1) if body_m else text
    else:
        body_m = _REMIND_ME_RE.search(text)
        raw_body = body_m.group(1) if body_m else text
    clean = _strip_meta(raw_body)

    if rel_m:
        val = int(rel_m.group(1))
        unit = rel_m.group(2).lower()
        if unit.startswith("hour") or unit == "hr":
            delta = timedelta(hours=val)
        else:
            delta = timedelta(minutes=val)
        fire_utc = now.astimezone(timezone.utc) + delta
        return {
            "text": clean,
            "fire_at_utc": fire_utc,
            "channel": channel,
            "has_time": True,
        }

    hm = _parse_hm(text)
    has_time = hm is not None
    h, mn = hm if hm else (9, 0)

    target_date = local_now.date()
    if is_tomorrow:
        target_date += timedelta(days=1)
    else:
        candidate = datetime(target_date.year, target_date.month, target_date.day, h, mn, tzinfo=zone)
        if candidate <= local_now:
            target_date += timedelta(days=1)

    fire_local = datetime(target_date.year, target_date.month, target_date.day, h, mn, tzinfo=zone)
    return {
        "text": clean,
        "fire_at_utc": fire_local.astimezone(timezone.utc),
        "channel": channel,
        "has_time": has_time,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_reminder(
    *,
    text: str,
    fire_at_utc: datetime,
    time_zone: str = "UTC",
    channel: str = "default",
    repeat: Optional[dict] = None,
    conversation_tab_id: Optional[str] = None,
) -> dict:
    rows = _load()
    r: dict = {
        "id": str(uuid.uuid4()),
        "text": text,
        "fire_at_utc": fire_at_utc.isoformat() if isinstance(fire_at_utc, datetime) else fire_at_utc,
        "time_zone": time_zone,
        "channel": channel,
        "repeat": repeat,
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if conversation_tab_id:
        r["conversation_tab_id"] = conversation_tab_id
    rows.append(r)
    _save(rows)
    return r


def list_all() -> list[dict]:
    return _load()


def list_upcoming() -> list[dict]:
    return [r for r in _load() if r.get("status") == "scheduled"]


def cancel_reminder(reminder_id: str) -> bool:
    rows = _load()
    found = False
    for r in rows:
        if r["id"] == reminder_id:
            r["status"] = "cancelled"
            found = True
            break
    _save(rows)
    return found


def snooze_reminder(reminder_id: str, *, now: Optional[datetime] = None, minutes: int = 15) -> Optional[dict]:
    """Re-queue a reminder N minutes from now. Returns the updated row or None if not found."""
    if now is None:
        now = datetime.now(timezone.utc)
    rows = _load()
    for r in rows:
        if r["id"] == reminder_id:
            r["fire_at_utc"] = (now + timedelta(minutes=minutes)).isoformat()
            r["status"] = "scheduled"
            r.pop("delivered_at", None)
            _save(rows)
            return r
    return None


def due_reminders(*, now: Optional[datetime] = None) -> list[dict]:
    if now is None:
        now = datetime.now(timezone.utc)
    result = []
    for r in _load():
        if r.get("status") != "scheduled":
            continue
        fire = datetime.fromisoformat(r["fire_at_utc"])
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=timezone.utc)
        if fire <= now:
            result.append(r)
    return result


# ---------------------------------------------------------------------------
# Channel senders (monkeypatchable for tests)
# ---------------------------------------------------------------------------

async def send_email_reminder(to: str, subject: str, body: str) -> dict:
    try:
        from services import gmail_reply
        creds = await asyncio.get_event_loop().run_in_executor(None, gmail_reply.get_credentials)
        if creds:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: gmail_reply._send_email_raw(to, subject, body)
            )
            return {"ok": True}
    except Exception:
        pass
    return {"ok": False, "error": "email channel not available"}


async def send_slack_reminder(user_id: str, text: str) -> dict:
    try:
        from services import slack as slack_svc
        data = await slack_svc._slack_post("conversations.open", {"users": user_id})
        channel_id = (data.get("channel") or {}).get("id")
        if channel_id:
            await slack_svc.post_message(channel_id, f"Reminder: {text}")
            return {"ok": True}
    except Exception:
        pass
    return {"ok": False, "error": "Slack channel not available"}


async def send_sms_reminder(to: str, body: str) -> dict:
    try:
        from services import sms as sms_svc
        if sms_svc.is_configured():
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sms_svc.send_sms(to, body)
            )
            return result if isinstance(result, dict) else {"ok": bool(result)}
    except Exception:
        pass
    return {"ok": False, "error": "SMS channel not available"}


async def send_imessage_reminder(to: str, text: str) -> dict:
    try:
        if imessage.is_available()["available"]:
            await imessage.send_message(to, f"Reminder: {text}")
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "iMessage channel not available"}


def _sms_configured(settings: dict) -> bool:
    return (youros_home() / "sms_provider.json").exists() and bool(settings.get("phone_number"))


def _imessage_configured(settings: dict) -> bool:
    # iMessage doesn't need a setting if available on macOS, 
    # but we might want to check for a phone/email in settings too.
    # For now, availability + phone_number is a good signal.
    return imessage.is_available()["available"] and bool(settings.get("phone_number"))


def _slack_configured(settings: dict) -> bool:
    return bool(settings.get("slack_user_id"))


def _email_configured(settings: dict) -> bool:
    return bool(settings.get("reminder_email"))


def choose_default_channel(settings: dict) -> str:
    return _select_default_channel(settings)


def _select_default_channel(settings: dict) -> str:
    # AC20: honor explicit override from settings.json
    preferred = settings.get("default_reminder_channel")
    if preferred:
        _CONFIGURED = {
            "sms": _sms_configured,
            "imessage": _imessage_configured,
            "slack": _slack_configured,
            "email": _email_configured,
            "in_app": lambda s: True,
        }
        checker = _CONFIGURED.get(preferred)
        if checker and checker(settings):
            return preferred
        # preferred channel is not configured; fall through to priority order

    if _sms_configured(settings):
        return "sms"
    if _imessage_configured(settings):
        return "imessage"
    if _slack_configured(settings):
        return "slack"
    if _email_configured(settings):
        return "email"
    return "in_app"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def dispatch_reminder(reminder: dict, *, settings: dict) -> dict:
    """Deliver a reminder. Always records in-app; falls back if outside channel fails."""
    text = reminder["text"]
    channel = reminder.get("channel", "default")
    if channel == "default":
        channel = _select_default_channel(settings)

    tab_id = reminder.get("conversation_tab_id")
    action_url = f"/?tab={tab_id}" if tab_id else None
    notif.notifications_service.add(
        type="reminder",
        title=f"Reminder: {text}",
        body=text,
        action_url=action_url,
        metadata={"reminder_id": reminder.get("id"), "channel": channel},
    )

    outside_ok = False
    outside_error = None

    if channel == "in_app":
        outside_ok = True
    elif channel == "email":
        to = settings.get("reminder_email", "")
        if to:
            try:
                res = await send_email_reminder(to, f"Reminder: {text}", text)
                outside_ok = res.get("ok", False)
                outside_error = res.get("error")
            except Exception as exc:
                outside_error = str(exc)
    elif channel == "slack":
        uid = settings.get("slack_user_id", "")
        if uid:
            try:
                res = await send_slack_reminder(uid, text)
                outside_ok = res.get("ok", False)
                outside_error = res.get("error")
            except Exception as exc:
                outside_error = str(exc)
    elif channel == "sms":
        phone = settings.get("phone_number", "")
        if not phone or not _sms_configured(settings):
            # AC11: sms requested but not configured — fall back to in_app
            logger.warning(
                "Reminder %s: channel=sms but SMS is not configured (no phone_number or sms_provider.json); falling back to in_app",
                reminder.get("id"),
            )
            outside_error = "SMS not configured: no phone number set"
        elif phone:
            try:
                res = await send_sms_reminder(phone, text)
                outside_ok = res.get("ok", False)
                outside_error = res.get("error")
            except Exception as exc:
                outside_error = str(exc)
    elif channel == "imessage":
        phone = settings.get("phone_number", "")
        if phone:
            try:
                res = await send_imessage_reminder(phone, text)
                outside_ok = res.get("ok", False)
                outside_error = res.get("error")
            except Exception as exc:
                outside_error = str(exc)

    result: dict = {"channel": channel, "ok": True, "in_app": True, "in_app_recorded": True}
    if not outside_ok and outside_error:
        result["fell_back"] = True
        result["error"] = outside_error
        result["fallback_note"] = f"Delivery via {channel} failed ({outside_error}); saved in-app."
    return result


# ---------------------------------------------------------------------------
# Fire loop
# ---------------------------------------------------------------------------

async def fire_due_reminders(*, now: Optional[datetime] = None) -> list[dict]:
    """Fire all due reminders; mark each delivered immediately after."""
    if now is None:
        now = datetime.now(timezone.utc)

    due = due_reminders(now=now)
    if not due:
        return []

    settings: dict = {}
    try:
        settings_path = youros_home() / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
    except Exception:
        pass

    fired = []
    for reminder in due:
        try:
            await dispatch_reminder(reminder, settings=settings)
        except Exception:
            pass

        rows = _load()
        for r in rows:
            if r["id"] == reminder["id"]:
                r["delivered_at"] = datetime.now(timezone.utc).isoformat()
                if r.get("repeat"):
                    fire_dt = datetime.fromisoformat(r["fire_at_utc"])
                    if fire_dt.tzinfo is None:
                        fire_dt = fire_dt.replace(tzinfo=timezone.utc)
                    nxt = next_fire_for_repeat(r, after=fire_dt)
                    r["fire_at_utc"] = nxt.isoformat()
                    r["status"] = "scheduled"
                else:
                    r["status"] = "delivered"
                break
        _save(rows)
        fired.append(reminder)

    return fired


# ---------------------------------------------------------------------------
# Repeat support
# ---------------------------------------------------------------------------

def next_fire_for_repeat(reminder: dict, *, after: datetime) -> datetime:
    """Compute the next fire time for a repeating reminder."""
    repeat = reminder.get("repeat") or {}
    kind = repeat.get("kind", "weekdays")
    tz = reminder.get("time_zone", "UTC")
    h = repeat.get("hour", 8)
    mn = repeat.get("minute", 0)
    zone = ZoneInfo(tz)
    local_after = after.astimezone(zone)
    candidate = local_after.date() + timedelta(days=1)
    while True:
        if kind == "weekdays" and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
            continue
        break
    return datetime(candidate.year, candidate.month, candidate.day, h, mn, tzinfo=zone).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

async def start_reminder_scheduler() -> asyncio.Task:
    """Start a background loop that fires due reminders every 10 seconds."""
    async def _loop() -> None:
        while True:
            try:
                await fire_due_reminders()
            except Exception:
                pass
            await asyncio.sleep(10)

    return asyncio.create_task(_loop())

"""REST API for reminders."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import services.reminders as reminders_svc
from services import recent_deletes

router = APIRouter(tags=["reminders"])


class CreateReminderRequest(BaseModel):
    text: str
    fire_at_utc: Optional[str] = None
    natural: Optional[str] = None
    time_zone: str = "UTC"
    channel: str = "default"
    repeat: Optional[dict] = None


@router.post("/reminders")
async def create_reminder(body: CreateReminderRequest):
    if body.fire_at_utc:
        try:
            fire = datetime.fromisoformat(body.fire_at_utc)
            if fire.tzinfo is None:
                fire = fire.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "Invalid fire_at_utc format")
        text = body.text
    elif body.natural:
        parsed = reminders_svc.parse_reminder(body.natural, tz=body.time_zone)
        fire = parsed["fire_at_utc"]
        text = parsed["text"]
        if body.channel == "default" and parsed.get("channel") != "default":
            body = body.model_copy(update={"channel": parsed["channel"]})
    else:
        raise HTTPException(400, "Provide fire_at_utc or natural")

    r = reminders_svc.create_reminder(
        text=text,
        fire_at_utc=fire,
        time_zone=body.time_zone,
        channel=body.channel,
        repeat=body.repeat,
    )
    return r


@router.get("/reminders")
async def list_reminders(upcoming_only: bool = True):
    if upcoming_only:
        return reminders_svc.list_upcoming()
    return reminders_svc.list_all()


@router.delete("/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: str):
    # Record a tombstone before cancelling so an automation that re-creates
    # the same reminder seconds later does not resurrect a ghost row in the
    # UI (→ feedback_delete_records.md). Key on both the id and the text.
    existing = next(
        (r for r in reminders_svc.list_all() if r.get("id") == reminder_id),
        None,
    )
    reminders_svc.cancel_reminder(reminder_id)
    recent_deletes.record_id(f"reminder:{reminder_id}")
    if existing and existing.get("text"):
        recent_deletes.record(existing["text"])
    return {"ok": True}


class SnoozeRequest(BaseModel):
    minutes: int = 15


@router.post("/reminders/{reminder_id}/snooze")
async def snooze_reminder(reminder_id: str, body: Optional[SnoozeRequest] = None):
    """Snooze a reminder. Re-queues it and removes the current notification.

    Pass {minutes: 15} (default), {minutes: 60} (1 hour), or {minutes: 1440} (1 day).
    """
    import services.notifications as notif_svc
    minutes = (body.minutes if body else None) or 15
    updated = reminders_svc.snooze_reminder(reminder_id, minutes=minutes)
    if updated is None:
        raise HTTPException(404, "Reminder not found")
    # Remove the existing in-app notification for this reminder so it doesn't stay visible.
    for n in notif_svc.notifications_service.list_all():
        meta = n.metadata or {}
        if meta.get("reminder_id") == reminder_id:
            notif_svc.notifications_service.delete(n.id)
            break
    return updated


@router.post("/reminders/parse")
async def parse_reminder_endpoint(body: dict):
    text = body.get("text", "")
    tz = body.get("time_zone", "UTC")
    parsed = reminders_svc.parse_reminder(text, tz=tz)
    parsed["fire_at_utc"] = parsed["fire_at_utc"].isoformat()
    return parsed

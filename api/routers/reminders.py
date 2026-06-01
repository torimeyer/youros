"""REST API for reminders."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import services.reminders as reminders_svc

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
    reminders_svc.cancel_reminder(reminder_id)
    return {"ok": True}


@router.post("/reminders/parse")
async def parse_reminder_endpoint(body: dict):
    text = body.get("text", "")
    tz = body.get("time_zone", "UTC")
    parsed = reminders_svc.parse_reminder(text, tz=tz)
    parsed["fire_at_utc"] = parsed["fire_at_utc"].isoformat()
    return parsed

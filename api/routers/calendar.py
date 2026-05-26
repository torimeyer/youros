"""Google Calendar integration endpoints."""

from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional

from services.google_auth import get_email, is_authenticated
from services import calendar as calendar_service
from services import connections_cache
from services.calendar_events import calendar_events as _calendar_event_bus

router = APIRouter(tags=["calendar"])

# Key used for the in-memory TTL cache of the status payload.
_CALENDAR_STATUS_CACHE_KEY = "calendar_auth_status"

# Default number of days to look ahead when no range is requested. Picked
# to match historic behavior from before the day/week/month range
# selector landed, so existing callers keep getting a 7-day window.
_DEFAULT_EVENTS_DAYS = 7

# Allowed values for the ``days`` query param. The dashboard offers a
# Day/Week/Month range selector that maps to these three values; other
# numbers in the 1..30 range are clamped to one of these so we never
# fan out to an unbounded window.
_ALLOWED_EVENT_DAYS = {1, 7, 30}


class CreateEventBody(BaseModel):
    summary: str
    start: str
    end: Optional[str] = None
    all_day: bool = False
    description: str = ""
    location: str = ""


def _compute_calendar_status() -> dict:
    """Pure function that resolves the Calendar status payload from disk."""
    authed = is_authenticated()
    email = get_email() if authed else None
    reauth = False
    if authed:
        # Fast path: check scope from the saved token instead of calling
        # the Google Calendar API. The actual events are fetched by
        # /calendar/events, so we skip the duplicate round trip here.
        try:
            from services.google_auth import has_calendar_scope
            if not has_calendar_scope():
                reauth = True
        except Exception:
            pass
    return {
        "authenticated": authed,
        "needs_reauth": reauth,
        "email": email,
    }


@router.get("/calendar/auth/status")
async def calendar_auth_status():
    """Return whether the user has connected Google Calendar.

    - authenticated: True if a Google token exists.
    - needs_reauth: True if the token exists but the calendar scope is missing.
    - email: the connected account email, if available.

    Payload is served from an in-memory TTL cache so repeat polls within
    the same minute return in sub-millisecond time.
    """
    return connections_cache.get_or_compute(
        _CALENDAR_STATUS_CACHE_KEY,
        _compute_calendar_status,
        ttl=120.0,
    )


@router.get("/calendar/events")
async def calendar_events(days: int = Query(_DEFAULT_EVENTS_DAYS, ge=1, le=30)):
    """Return upcoming events within the next ``days`` days.

    The ``days`` query param accepts 1 (today), 7 (week, default) or
    30 (month). Any other value in the 1..30 range is clamped to one
    of those three buckets so the on-disk cache stays predictable.

    Returns 401 if not authenticated. Returns 403 with needs_reauth=true if
    the calendar scope is missing on the current token.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Calendar.")

    # Clamp arbitrary values to one of the three supported windows so
    # a caller cannot bypass the cache by passing days=8.
    if days not in _ALLOWED_EVENT_DAYS:
        if days <= 1:
            days = 1
        elif days <= 7:
            days = 7
        else:
            days = 30

    try:
        if days == _DEFAULT_EVENTS_DAYS:
            # Default 7-day window uses the on-disk cache so existing
            # callers (Calendar page, dashboard) keep their fast path.
            events = await calendar_service.get_upcoming_events(days=days)
        else:
            # Day and Month windows skip the cache. The cache is keyed
            # only by date and would otherwise return the wrong window.
            # These calls are infrequent (user-initiated range change)
            # so the extra Google round trip is acceptable.
            events = await calendar_service.fetch_events_uncached(days=days)
    except Exception as exc:
        msg = str(exc).lower()
        if "accessnotconfigured" in msg or "has not been used" in msg:
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": False, "api_not_enabled": True, "message": "Google Calendar API is not enabled in your Google Cloud project. Enable it in Google Cloud Console, then wait a minute and reload."},
            ) from exc
        if "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": True, "message": str(exc)},
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not load calendar events: {exc}",
        ) from exc

    return {"events": events, "days": days}


@router.post("/calendar/events")
async def calendar_create_event(body: CreateEventBody):
    """Create a new event on the user's Google Calendar.

    Returns the created event (id, summary, start, end, htmlLink).
    Returns 401 if not authenticated.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Calendar.")

    if not body.summary.strip():
        raise HTTPException(status_code=400, detail="Event title cannot be empty.")

    try:
        event = await calendar_service.create_event(
            summary=body.summary.strip(),
            start=body.start,
            end=body.end,
            all_day=body.all_day,
            description=body.description,
            location=body.location,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
            raise HTTPException(
                status_code=403,
                detail={
                    "needs_reauth": True,
                    "message": (
                        "Calendar write access has not been granted. "
                        "Reconnect your Google account to allow creating events."
                    ),
                },
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not create the calendar event: {exc}",
        ) from exc

    return {
        "ok": True,
        "event": {
            "id": event.get("id"),
            "summary": event.get("summary"),
            "start": event.get("start"),
            "end": event.get("end"),
            "htmlLink": event.get("htmlLink"),
        },
    }


@router.delete("/calendar/events/{event_id}")
async def calendar_delete_event(event_id: str):
    """Delete a single event from the user's primary Google Calendar.

    Returns 401 if not authenticated. Returns 403 with needs_reauth=true
    when the calendar write scope is missing. Treats Google "Not Found"
    errors as success so a stale frontend cannot keep prompting for an
    event the backend has already removed.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Calendar.")
    if not event_id.strip():
        raise HTTPException(status_code=400, detail="Event id is required.")

    try:
        await calendar_service.delete_event(event_id)
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            # Already gone server-side. Treat as success so the UI can
            # converge without surfacing a confusing error.
            calendar_service._clear_cache()
            return {"ok": True, "already_deleted": True}
        if "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
            raise HTTPException(
                status_code=403,
                detail={
                    "needs_reauth": True,
                    "message": (
                        "Calendar write access has not been granted. "
                        "Reconnect your Google account to allow deleting events."
                    ),
                },
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete the calendar event: {exc}",
        ) from exc

    return {"ok": True}


@router.post("/calendar/sync")
async def calendar_sync():
    """Clear the events cache and re-fetch from Google Calendar."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Calendar.")

    calendar_service._clear_cache()

    try:
        events = await calendar_service.get_upcoming_events(days=7)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {exc}",
        ) from exc

    return {"ok": True, "count": len(events)}


async def _build_calendar_data(days: int = 7) -> dict:
  """Helper to fetch current calendar data snapshot."""
  if not is_authenticated():
    return {"events": []}
  try:
    if days == 7:
      events = await calendar_service.get_upcoming_events(days=days)
    else:
      events = await calendar_service.fetch_events_uncached(days=days)
    return {"events": events}
  except Exception:
    return {"events": []}


@router.websocket("/ws/calendar/events")
async def websocket_calendar_events(websocket: WebSocket):
  """WebSocket endpoint for real-time calendar events.
  
  On connect, sends snapshot of upcoming events. Then pushes deltas
  when calendar changes. Falls back to HTTP polling if connection closes.
  """
  await websocket.accept()
  try:
    data = await _build_calendar_data()
    await websocket.send_json({"type": "snapshot", "events": data.get("events", [])})
    
    async with _calendar_event_bus.subscribe() as queue:
      while True:
        try:
          msg = await asyncio.wait_for(queue.get(), timeout=15.0)
          await websocket.send_json(msg)
        except asyncio.TimeoutError:
          await websocket.send_json({"type": "ping"})
  except WebSocketDisconnect:
    pass
  except Exception as e:
    import logging
    logging.error(f"Calendar WS error: {e}")
  finally:
    try:
      await websocket.close()
    except Exception:
      pass

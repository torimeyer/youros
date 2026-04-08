"""Google Calendar integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.google_auth import get_email, is_authenticated
from services import calendar as calendar_service

router = APIRouter(tags=["calendar"])


@router.get("/calendar/auth/status")
async def calendar_auth_status():
    """Return whether the user has connected Google Calendar.

    - authenticated: True if a Google token exists.
    - needs_reauth: True if the token exists but the calendar scope is missing.
    - email: the connected account email, if available.
    """
    authed = is_authenticated()
    email = get_email() if authed else None
    reauth = False
    if authed:
        try:
            reauth = await calendar_service.needs_reauth()
        except Exception:
            reauth = False
    return {
        "authenticated": authed,
        "needs_reauth": reauth,
        "email": email,
    }


@router.get("/calendar/events")
async def calendar_events():
    """Return upcoming events for the next 7 days.

    Returns 401 if not authenticated. Returns 403 with needs_reauth=true if
    the calendar scope is missing on the current token.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Calendar.")

    try:
        events = await calendar_service.get_upcoming_events(days=7)
    except Exception as exc:
        msg = str(exc).lower()
        if "insufficient" in msg or "403" in msg:
            raise HTTPException(
                status_code=403,
                detail={"needs_reauth": True, "message": str(exc)},
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not load calendar events: {exc}",
        ) from exc

    return {"events": events}


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

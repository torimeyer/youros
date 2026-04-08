"""Google Calendar service.

Fetches events from the user's primary calendar and caches them locally.
Cache lives in ~/.myos/calendar_cache/ -- never inside the repo.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.google_auth import get_credentials, is_authenticated

MYOS_DIR = Path.home() / ".myos"
CALENDAR_CACHE_DIR = MYOS_DIR / "calendar_cache"
EVENTS_CACHE_PATH = CALENDAR_CACHE_DIR / "events.json"

# 15 minutes TTL for the events cache.
_CACHE_TTL_SECONDS = 900


def _ensure_dirs() -> None:
    CALENDAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _build_calendar_service():
    """Build an authenticated Google Calendar API service object."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Google API client is not available on this server."
        ) from exc

    tokens = get_credentials()
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=None,
        client_secret=None,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _load_cache() -> list[dict] | None:
    """Return cached events if they exist and are less than 15 minutes old."""
    if not EVENTS_CACHE_PATH.exists():
        return None
    age = time.time() - EVENTS_CACHE_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(EVENTS_CACHE_PATH.read_text())
    except Exception:
        return None


def _save_cache(events: list[dict]) -> None:
    """Persist events to the cache file."""
    _ensure_dirs()
    EVENTS_CACHE_PATH.write_text(json.dumps(events))


def _clear_cache() -> None:
    """Remove the cached events file."""
    if EVENTS_CACHE_PATH.exists():
        EVENTS_CACHE_PATH.unlink(missing_ok=True)


def _fetch_events_sync(days: int = 7) -> list[dict]:
    """Synchronous call to the Calendar API."""
    now = datetime.now(tz=timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    service = _build_calendar_service()
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
            fields="items(id,summary,start,end,location,htmlLink,hangoutLink,colorId)",
        )
        .execute()
    )
    return result.get("items", [])


async def get_upcoming_events(days: int = 7) -> list[dict]:
    """Return upcoming events for the next *days* days.

    Checks the on-disk cache first (15 min TTL).  On cache miss, calls the
    Calendar API in a thread so the async event loop is not blocked.
    """
    cached = _load_cache()
    if cached is not None:
        return cached

    events = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_events_sync(days)
    )
    _save_cache(events)
    return events


async def get_today_events() -> list[dict]:
    """Return events starting today (local calendar day)."""
    all_events = await get_upcoming_events(days=1)
    today_str = datetime.now().strftime("%Y-%m-%d")
    result = []
    for ev in all_events:
        start = ev.get("start", {})
        # All-day events use "date"; timed events use "dateTime".
        start_val = start.get("dateTime") or start.get("date") or ""
        if start_val.startswith(today_str):
            result.append(ev)
    return result


async def needs_reauth() -> bool:
    """Return True if the Calendar scope is missing (403 insufficientPermissions).

    Makes a minimal API call and checks for the specific error.  Any other
    error is treated as False (not a scope problem) to avoid spurious reauth
    prompts.
    """
    if not is_authenticated():
        return False
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _fetch_events_sync(days=1)
        )
        return False
    except Exception as exc:
        msg = str(exc).lower()
        # Only flag as needs_reauth for OAuth scope errors, not API-not-enabled errors
        if "accessnotconfigured" in msg or "has not been used" in msg:
            return False
        return "insufficientpermissions" in msg or "insufficient authentication scopes" in msg

"""Google Calendar service.

Fetches events from the user's primary calendar and caches them locally.
Cache lives in ~/.myos/calendar_cache/ -- never inside the repo.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.atomic_io import atomic_write_text
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
    client_config = {}
    try:
        from services.google_auth import _load_client_config
        client_config = _load_client_config()
    except Exception:
        pass
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config.get("client_id"),
        client_secret=client_config.get("client_secret"),
    )
    return build("calendar", "v3", credentials=creds)


def _load_cache() -> list[dict] | None:
    """Return cached events if they exist, are less than 15 minutes old,
    and were fetched for today's date.

    The cache stores ``{"fetched_date": "YYYY-MM-DD", "events": [...]}``.
    If the local calendar day has changed since the cache was written,
    the events are stale (they cover a different 7-day window) and must
    be re-fetched. This prevents the Calendar page from showing events
    from a previous month when the server stays running across midnight.
    """
    if not EVENTS_CACHE_PATH.exists():
        return None
    age = time.time() - EVENTS_CACHE_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    try:
        raw = json.loads(EVENTS_CACHE_PATH.read_text())
    except Exception:
        return None
    # Support both old format (bare list) and new format (dict with date).
    if isinstance(raw, list):
        # Old format without date metadata. Treat as expired so we
        # re-fetch with the current date range.
        return None
    if not isinstance(raw, dict):
        return None
    today_str = datetime.now().strftime("%Y-%m-%d")
    if raw.get("fetched_date") != today_str:
        return None
    events = raw.get("events")
    # Defensive: if a previous version persisted an empty list, do not
    # serve it. Force a re-fetch so a brief earlier-today blip cannot
    # leave the Calendar page blank for the rest of the day.
    if not events:
        return None
    return events


def _save_cache(events: list[dict]) -> None:
    """Persist events with the fetch date so we can detect stale caches.

    Skip persisting an EMPTY events list. A transient zero-result fetch
    (network hiccup, momentary scope blip, or simply caught between two
    real syncs) would otherwise be pinned for the 15-minute TTL and the
    Calendar page would render blank even though the user has events.
    Letting the next request hit the API fresh on a zero result is the
    right tradeoff. If the calendar truly is empty, the next call still
    returns [] in well under a second. Regression guard for the
    "Calendar tab shows nothing" demo bug.
    """
    if not events:
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    payload = {"fetched_date": today_str, "events": events}
    atomic_write_text(EVENTS_CACHE_PATH, json.dumps(payload))


def _clear_cache() -> None:
    """Remove the cached events file."""
    if EVENTS_CACHE_PATH.exists():
        EVENTS_CACHE_PATH.unlink(missing_ok=True)


def _fetch_events_sync(days: int = 7) -> list[dict]:
    """Synchronous call to the Calendar API.

    ``time_min`` is the START of TODAY in local time, not ``now``. The
    old code used ``datetime.now(tz=utc).isoformat()`` which, at 9:51
    PM Central, meant timeMin was 2:51 AM UTC **tomorrow**. Google's
    Calendar API then filtered out every event that started earlier
    today (the Fox soccer game at 11:30 AM Central would stay in the
    result only because it was tomorrow; anything on today's Central
    day before 7 PM was silently dropped). Regression guard for
    needle 285.
    """
    # Local midnight today as the lower bound so events from earlier
    # today are still returned. Upper bound: end of day ``days - 1``
    # from now in local time (so days=1 means "today only").
    now_local = datetime.now()
    today_local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = today_local_midnight.astimezone().isoformat()
    time_max = (today_local_midnight + timedelta(days=days)).astimezone().isoformat()

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

async def fetch_events_uncached(days: int = 7) -> list[dict]:
    """Fetch events for the next *days* days, bypassing the on-disk cache.

    The shared cache is keyed by date only, not by window size. Day/Month
    range requests must skip it so a 7-day cache hit does not get served
    back as a 1-day or 30-day result. Cache writes are also skipped here
    so a Day fetch cannot poison the default 7-day cache.
    """
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_events_sync(days)
    )




async def get_today_events() -> list[dict]:
    """Return events starting today (local calendar day).

    Filters on the LOCAL calendar day the caller is currently living
    in. The old code compared raw ISO prefixes from the event start,
    which silently mismatches when the event string uses UTC (``Z``
    suffix) or when the caller's local tz differs from the event tz.
    Now both sides go through the same local-day conversion.
    Regression guard for needle 285.
    """
    all_events = await get_upcoming_events(days=1)
    today_local = datetime.now()
    today_key = today_local.strftime("%Y-%m-%d")
    result = []
    for ev in all_events:
        start = ev.get("start", {})
        if start.get("date"):
            # All-day event: the date is already a calendar day.
            if start["date"] == today_key:
                result.append(ev)
            continue
        start_val = start.get("dateTime") or ""
        if not start_val:
            continue
        try:
            parsed = datetime.fromisoformat(start_val.replace("Z", "+00:00"))
        except ValueError:
            if start_val.startswith(today_key):
                result.append(ev)
            continue
        local_start = parsed.astimezone()
        if local_start.strftime("%Y-%m-%d") == today_key:
            result.append(ev)
    return result


async def create_event(
    *,
    summary: str,
    start: str,
    end: str | None = None,
    all_day: bool = False,
    description: str = "",
    location: str = "",
) -> dict:
    """Create a new event on the user's primary Google Calendar.

    Parameters:
        summary: Event title (required).
        start: Start date or datetime. For timed events use ISO format
               like ``2026-04-28T09:00:00``. For all-day events use
               ``YYYY-MM-DD``.
        end: End date or datetime. Defaults to one hour after start for
             timed events or the next day for all-day events.
        all_day: When True, ``start`` and ``end`` are treated as dates.
        description: Optional event description.
        location: Optional location text.

    Returns the created event dict from the Calendar API.
    """
    import copy
    from datetime import datetime as _dt, timedelta as _td

    body: dict = {"summary": summary}

    if description:
        body["description"] = description
    if location:
        body["location"] = location

    if all_day or (len(start) == 10 and "T" not in start):
        # All-day event: use date fields.
        body["start"] = {"date": start}
        if end:
            body["end"] = {"date": end}
        else:
            # Default end: the next day.
            try:
                d = _dt.strptime(start, "%Y-%m-%d")
                body["end"] = {"date": (d + _td(days=1)).strftime("%Y-%m-%d")}
            except ValueError:
                body["end"] = {"date": start}
    else:
        # Timed event: use dateTime fields.
        # Always include timeZone so Google never rejects with
        # "Missing time zone definition for start time."
        import time as _time
        local_tz = _time.tzname[0]
        # Prefer IANA zone from the TZ env var or fall back to a
        # fixed offset derived from the UTC offset right now.
        iana_tz = os.environ.get("TZ", "")
        if not iana_tz or "/" not in iana_tz:
            try:
                from datetime import timezone as _tz
                offset = _dt.now(_tz.utc).astimezone().strftime("%z")
                iana_tz = f"Etc/GMT{'+' if offset[0] == '-' else '-'}{int(offset[1:3])}" if offset else "America/Chicago"
            except Exception:
                iana_tz = "America/Chicago"
        body["start"] = {"dateTime": start, "timeZone": iana_tz}
        if end:
            body["end"] = {"dateTime": end, "timeZone": iana_tz}
        else:
            # Default end: one hour after start.
            try:
                parsed = _dt.fromisoformat(start)
                body["end"] = {"dateTime": (parsed + _td(hours=1)).isoformat(), "timeZone": iana_tz}
            except ValueError:
                body["end"] = {"dateTime": start, "timeZone": iana_tz}

    def _insert_sync():
        service = _build_calendar_service()
        return (
            service.events()
            .insert(calendarId="primary", body=body)
            .execute()
        )

    event = await asyncio.get_event_loop().run_in_executor(None, _insert_sync)

    # Bust the cache so the new event shows up immediately.
    _clear_cache()

    return event


async def delete_event(event_id: str) -> None:
    """Delete a single event from the user's primary Google Calendar.

    Raises whatever the Google client raises (typically HttpError) so the
    caller can map 404 to "already gone" and 403 to a reauth prompt.
    """
    def _delete_sync():
        service = _build_calendar_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()

    await asyncio.get_event_loop().run_in_executor(None, _delete_sync)
    _clear_cache()


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

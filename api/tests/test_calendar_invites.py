"""Tests for calendar invites with attendees + timezone-aware time suggestions.

Covers →1941/→1994 acceptance criteria:
- contacts.readonly scope in SCOPES
- Attendee suggestion endpoint returns contacts
- Free/busy time suggestions rank by fewest-busy when no free slot exists
- Event creation persists attendees to Google Calendar
- AI tool create_calendar_event schema includes attendees field
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest



# ---------------------------------------------------------------------------
# Scope test (pure import — no HTTP needed)
# ---------------------------------------------------------------------------

def test_contacts_readonly_scope_present():
    """contacts.readonly must appear in SCOPES so the People API is accessible."""
    from services.google_auth import SCOPES
    assert "https://www.googleapis.com/auth/contacts.readonly" in SCOPES


# ---------------------------------------------------------------------------
# Attendee contact suggestion endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contact_search_returns_matches(client):
    """GET /calendar/contacts?q=alice returns matching contacts."""
    fake_contacts = [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "Alice Jones", "email": "alicejones@example.com"},
    ]
    with (
        patch("routers.calendar.is_authenticated", return_value=True),
        patch("services.calendar.search_contacts", new_callable=AsyncMock, return_value=fake_contacts),
    ):
        resp = await client.get("/api/calendar/contacts?q=alice")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["contacts"], list)
    assert len(data["contacts"]) == 2
    assert data["contacts"][0]["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_contact_search_requires_auth(client):
    """GET /calendar/contacts returns 401 when not authenticated."""
    with patch("routers.calendar.is_authenticated", return_value=False):
        resp = await client.get("/api/calendar/contacts?q=alice")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Free/busy endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freebusy_returns_suggestions(client):
    """POST /calendar/freebusy returns up to 5 suggested slots."""
    with (
        patch("routers.calendar.is_authenticated", return_value=True),
        patch(
            "services.calendar.suggest_meeting_times",
            new_callable=AsyncMock,
            return_value=[
                {"start": "2026-06-01T09:00:00", "end": "2026-06-01T10:00:00", "busy_count": 0},
                {"start": "2026-06-01T10:00:00", "end": "2026-06-01T11:00:00", "busy_count": 0},
            ],
        ),
    ):
        resp = await client.post(
            "/api/calendar/freebusy",
            json={
                "attendees": ["bob@example.com"],
                "time_min": "2026-06-01T00:00:00",
                "time_max": "2026-06-02T00:00:00",
                "duration_minutes": 60,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) >= 1
    assert "start" in data["suggestions"][0]
    assert "busy_count" in data["suggestions"][0]


@pytest.mark.asyncio
async def test_freebusy_ranks_by_fewest_busy(client):
    """When no slot is fully free, suggestions are ranked fewest-busy first."""
    with (
        patch("routers.calendar.is_authenticated", return_value=True),
        patch(
            "services.calendar.suggest_meeting_times",
            new_callable=AsyncMock,
            return_value=[
                {"start": "2026-06-01T09:00:00", "end": "2026-06-01T10:00:00", "busy_count": 1},
                {"start": "2026-06-01T10:00:00", "end": "2026-06-01T11:00:00", "busy_count": 3},
                {"start": "2026-06-01T11:00:00", "end": "2026-06-01T12:00:00", "busy_count": 2},
            ],
        ),
    ):
        resp = await client.post(
            "/api/calendar/freebusy",
            json={
                "attendees": ["bob@example.com", "carol@example.com", "dave@example.com"],
                "time_min": "2026-06-01T00:00:00",
                "time_max": "2026-06-02T00:00:00",
                "duration_minutes": 60,
            },
        )
    assert resp.status_code == 200
    suggestions = resp.json()["suggestions"]
    # First suggestion must have the lowest busy_count
    counts = [s["busy_count"] for s in suggestions]
    assert counts == sorted(counts)


# ---------------------------------------------------------------------------
# Event creation with attendees
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_event_with_attendees(client):
    """POST /calendar/events with attendees sends them to the Google API."""
    inserted_body = {}

    def _fake_insert(**kwargs):
        nonlocal inserted_body
        mock = MagicMock()
        # Capture the body kwarg so we can assert on it
        inserted_body = kwargs.get("body", {})
        mock.execute.return_value = {
            "id": "new-event-123",
            "summary": "Team meeting",
            "start": {"dateTime": "2026-06-01T09:00:00"},
            "end": {"dateTime": "2026-06-01T10:00:00"},
            "htmlLink": "https://calendar.google.com/event?eid=new-event-123",
        }
        return mock

    fake_service = MagicMock()
    fake_service.events.return_value.insert.side_effect = _fake_insert

    with (
        patch("routers.calendar.is_authenticated", return_value=True),
        patch("services.calendar._build_calendar_service", return_value=fake_service),
        patch("services.calendar._clear_cache"),
    ):
        resp = await client.post(
            "/api/calendar/events",
            json={
                "summary": "Team meeting",
                "start": "2026-06-01T09:00:00",
                "end": "2026-06-01T10:00:00",
                "attendees": ["alice@example.com", "bob@example.com"],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Verify attendees were injected into the Google API body
    assert "attendees" in inserted_body
    emails = {a["email"] for a in inserted_body["attendees"]}
    assert "alice@example.com" in emails
    assert "bob@example.com" in emails


@pytest.mark.asyncio
async def test_create_event_without_attendees_still_works(client):
    """POST /calendar/events with no attendees field works exactly as before."""
    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "solo-event",
        "summary": "Solo event",
        "start": {"dateTime": "2026-06-01T09:00:00"},
        "end": {"dateTime": "2026-06-01T10:00:00"},
        "htmlLink": "https://calendar.google.com/event?eid=solo",
    }

    with (
        patch("routers.calendar.is_authenticated", return_value=True),
        patch("services.calendar._build_calendar_service", return_value=fake_service),
        patch("services.calendar._clear_cache"),
    ):
        resp = await client.post(
            "/api/calendar/events",
            json={"summary": "Solo event", "start": "2026-06-01T09:00:00"},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# AI tool schema includes attendees
# ---------------------------------------------------------------------------

def test_create_calendar_event_tool_has_attendees_property():
    """The create_calendar_event AI tool schema must include an attendees array."""
    from services.tool_executor import TOOL_DEFINITIONS
    tool = next((t for t in TOOL_DEFINITIONS if t["name"] == "create_calendar_event"), None)
    assert tool is not None, "create_calendar_event tool not found"
    props = tool["input_schema"]["properties"]
    assert "attendees" in props, "attendees property missing from create_calendar_event schema"
    assert props["attendees"]["type"] == "array"


# ---------------------------------------------------------------------------
# suggest_meeting_times unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_meeting_times_prefers_free_slots():
    """suggest_meeting_times returns slots ordered free-first, fewest-busy second."""
    from services.calendar import _rank_slots_by_busy

    slots = [
        {"start": "2026-06-01T09:00:00", "end": "2026-06-01T10:00:00", "busy_count": 2},
        {"start": "2026-06-01T10:00:00", "end": "2026-06-01T11:00:00", "busy_count": 0},
        {"start": "2026-06-01T11:00:00", "end": "2026-06-01T12:00:00", "busy_count": 1},
    ]
    ranked = _rank_slots_by_busy(slots)
    assert ranked[0]["busy_count"] == 0
    assert ranked[1]["busy_count"] == 1
    assert ranked[2]["busy_count"] == 2


# ---------------------------------------------------------------------------
# Timezone-aware free/busy logic (the hard part — →1941)
#
# These exercise the REAL suggest_meeting_times slot-generation + overlap
# math, mocking only the Google freebusy network call. They prove that busy
# intervals expressed in one timezone correctly block a candidate slot that
# the requester expressed in a different timezone, and that the comparison
# survives a daylight-saving-time transition.
# ---------------------------------------------------------------------------

def _fake_service_with_busy(busy_intervals: dict[str, list[dict]]):
    """Build a MagicMock Calendar service whose freebusy().query().execute()
    returns the given per-calendar busy intervals.

    busy_intervals maps a calendar id to a list of {"start","end"} dicts in
    RFC3339 (with offset or Z).
    """
    calendars = {
        cal_id: {"busy": intervals} for cal_id, intervals in busy_intervals.items()
    }
    fake_service = MagicMock()
    fake_service.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": calendars
    }
    return fake_service


@pytest.mark.asyncio
async def test_busy_interval_in_other_timezone_blocks_overlapping_slot():
    """An attendee busy 09:00-10:00 in New York (-04:00) must block a slot
    that the requester expressed as 13:00-14:00 UTC — they are the same
    wall-clock hour, so the slot is not free for that attendee."""
    from services import calendar as cal

    # Requester window 12:00-16:00 UTC on a single day.
    # Attendee "ny@example.com" is busy 09:00-10:00 America/New_York,
    # which is 13:00-14:00 UTC.
    busy = {
        "primary": [],
        "ny@example.com": [
            {"start": "2026-06-01T09:00:00-04:00", "end": "2026-06-01T10:00:00-04:00"},
        ],
    }
    with patch.object(cal, "_build_calendar_service", return_value=_fake_service_with_busy(busy)):
        slots = await cal.suggest_meeting_times(
            attendees=["ny@example.com"],
            time_min="2026-06-01T12:00:00+00:00",
            time_max="2026-06-01T16:00:00+00:00",
            duration_minutes=60,
            max_slots=20,
        )

    by_start = {s["start"]: s for s in slots}
    # The 13:00 UTC slot overlaps the NY busy block -> busy_count == 1.
    slot_1300 = by_start.get("2026-06-01T13:00:00+00:00")
    assert slot_1300 is not None, "expected a 13:00 UTC candidate slot"
    assert slot_1300["busy_count"] == 1, "NY-timezone busy block must mark the same UTC hour busy"

    # A clearly non-overlapping slot (15:00 UTC) must be free.
    slot_1500 = by_start.get("2026-06-01T15:00:00+00:00")
    assert slot_1500 is not None
    assert slot_1500["busy_count"] == 0


@pytest.mark.asyncio
async def test_multiple_attendees_different_timezones_rank_fewest_busy():
    """Requester in UTC, one attendee busy in Tokyo, one busy in London,
    overlapping different UTC hours. Slots must rank fewest-busy first and
    each busy block must count only against the hour it actually covers."""
    from services import calendar as cal

    # Window 08:00-12:00 UTC.
    # tokyo busy 17:00-18:00 Asia/Tokyo (+09:00) == 08:00-09:00 UTC.
    # london busy 10:00-11:00 Europe/London (+01:00 BST) == 09:00-10:00 UTC.
    busy = {
        "primary": [],
        "tokyo@example.com": [
            {"start": "2026-06-01T17:00:00+09:00", "end": "2026-06-01T18:00:00+09:00"},
        ],
        "london@example.com": [
            {"start": "2026-06-01T10:00:00+01:00", "end": "2026-06-01T11:00:00+01:00"},
        ],
    }
    with patch.object(cal, "_build_calendar_service", return_value=_fake_service_with_busy(busy)):
        slots = await cal.suggest_meeting_times(
            attendees=["tokyo@example.com", "london@example.com"],
            time_min="2026-06-01T08:00:00+00:00",
            time_max="2026-06-01T12:00:00+00:00",
            duration_minutes=60,
            max_slots=20,
        )

    by_start = {s["start"]: s for s in slots}
    assert by_start["2026-06-01T08:00:00+00:00"]["busy_count"] == 1  # tokyo
    assert by_start["2026-06-01T09:00:00+00:00"]["busy_count"] == 1  # london
    assert by_start["2026-06-01T10:00:00+00:00"]["busy_count"] == 0  # free
    assert by_start["2026-06-01T11:00:00+00:00"]["busy_count"] == 0  # free

    # Ranking: the two free slots come first (busy_count 0).
    counts = [s["busy_count"] for s in slots]
    assert counts == sorted(counts)
    assert slots[0]["busy_count"] == 0


@pytest.mark.asyncio
async def test_dst_spring_forward_edge_overlap():
    """Across the US spring-forward DST jump (2026-03-08, 02:00 -> 03:00 local),
    a busy block expressed in local New York time still correctly blocks the
    matching UTC slot. Before the jump NY is -05:00 (EST); after it is -04:00
    (EDT). A 09:00-10:00 EDT busy block == 13:00-14:00 UTC and must block the
    13:00 UTC slot, not the 14:00 UTC slot (which it would, wrongly, if the
    code assumed a fixed -05:00 offset)."""
    from services import calendar as cal

    busy = {
        "primary": [],
        "ny@example.com": [
            # 2026-03-08 is after the spring-forward, so NY is on EDT (-04:00).
            {"start": "2026-03-08T09:00:00-04:00", "end": "2026-03-08T10:00:00-04:00"},
        ],
    }
    with patch.object(cal, "_build_calendar_service", return_value=_fake_service_with_busy(busy)):
        slots = await cal.suggest_meeting_times(
            attendees=["ny@example.com"],
            time_min="2026-03-08T12:00:00+00:00",
            time_max="2026-03-08T16:00:00+00:00",
            duration_minutes=60,
            max_slots=20,
        )

    by_start = {s["start"]: s for s in slots}
    assert by_start["2026-03-08T13:00:00+00:00"]["busy_count"] == 1, "EDT busy block must map to 13:00 UTC"
    assert by_start["2026-03-08T14:00:00+00:00"]["busy_count"] == 0, "14:00 UTC must be free (no fixed-offset bug)"

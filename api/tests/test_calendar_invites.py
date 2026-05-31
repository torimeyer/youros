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

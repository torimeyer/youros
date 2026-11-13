"""Phase 1 regression tests for clean morning briefing degradation and Core URLs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_briefing_degrades_cleanly_to_tasks_when_google_unauthenticated():
    """When Google auth is off, _gather_briefing_facts returns task-only facts with zero errors."""
    from services import briefing

    fake_tasks = [
        {
            "id": "3001",
            "title": "Ship Phase 1 gate",
            "priority": "P1",
            "status": "open",
            "created_at": "2026-11-12T10:00:00+00:00",
        },
    ]
    with (
        patch("services.google_auth.is_authenticated", return_value=False),
        patch("services.calendar.get_today_events", new=AsyncMock()) as mock_cal,
        patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=fake_tasks)),
        patch("services.ostk.ostk.get_compounds", new=AsyncMock(return_value=[])),
    ):
        facts = await briefing._gather_briefing_facts()

    mock_cal.assert_not_called()
    assert facts["events"] == []
    assert len(facts["priority_tasks"]) == 1
    assert facts["priority_tasks"][0]["title"] == "Ship Phase 1 gate"

    text = briefing._render_briefing_from_facts(facts)
    assert "Ship Phase 1 gate" in text
    assert "Your calendar is clear today." in text


@pytest.mark.asyncio
async def test_briefing_enriches_from_calendar_and_avoids_dead_consumer_routes(tmp_path):
    """When Google auth is on, briefing enriches from Calendar and action items never link to deleted /gmail or /calendar routes."""
    from services import briefing

    future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    fake_events = [
        {"id": "ev1", "summary": "Architecture Sync", "start": {"dateTime": future_iso}},
    ]
    fake_emails = [
        {
            "id": "m1",
            "thread_id": "t1",
            "from": "Alex <alex@acme-builders.dev>",
            "from_name": "Alex",
            "from_email": "alex@acme-builders.dev",
            "subject": "Quick question about the spec?",
        },
    ]
    with (
        patch("services.google_auth.is_authenticated", return_value=True),
        patch("services.calendar.get_today_events", new=AsyncMock(return_value=fake_events)),
        patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=[])),
        patch("services.ostk.ostk.get_compounds", new=AsyncMock(return_value=[])),
    ):
        facts = await briefing._gather_briefing_facts()

    assert len(facts["events"]) == 1
    assert facts["events"][0]["title"] == "Architecture Sync"
    text = briefing._render_briefing_from_facts(facts)
    assert "Architecture Sync" in text

    state_path = tmp_path / "briefing_state.json"
    with (
        patch.object(briefing, "BRIEFING_STATE_PATH", state_path),
        patch.object(briefing, "_fetch_tasks_for_action_items", new=AsyncMock(return_value=[])),
        patch.object(briefing, "_fetch_unread_emails_for_action_items", new=AsyncMock(return_value=fake_emails)),
        patch.object(briefing, "_fetch_events_for_action_items", new=AsyncMock(return_value=fake_events)),
    ):
        items = await briefing.generate_action_items()

    urls = [item.get("action_url", "") for item in items]
    assert len(urls) == 2
    for url in urls:
        assert not url.startswith("/gmail"), f"Dead /gmail route leaked into briefing action_url: {url}"
        assert not url.startswith("/calendar"), f"Dead /calendar route leaked into briefing action_url: {url}"

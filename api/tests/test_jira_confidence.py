"""Tests for →2113: Jira confidence/risk auto-populate from activity signals."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── signal computation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_issue_signals_all_active():
    """All signals active returns correct dict."""
    from services.atlassian import compute_issue_signals

    with (
        patch("services.atlassian._get_running_agents_mentioning", new=AsyncMock(return_value=True)),
        patch("services.atlassian._count_recent_commits", new=AsyncMock(return_value=3)),
        patch("services.atlassian._count_closed_tasks", new=AsyncMock(return_value=2)),
    ):
        signals = await compute_issue_signals("PROJ-42")

    assert signals["has_active_agent"] is True
    assert signals["recent_commits"] == 3
    assert signals["completed_tasks"] == 2


@pytest.mark.asyncio
async def test_compute_issue_signals_no_activity():
    """No signals → all zeros/False."""
    from services.atlassian import compute_issue_signals

    with (
        patch("services.atlassian._get_running_agents_mentioning", new=AsyncMock(return_value=False)),
        patch("services.atlassian._count_recent_commits", new=AsyncMock(return_value=0)),
        patch("services.atlassian._count_closed_tasks", new=AsyncMock(return_value=0)),
    ):
        signals = await compute_issue_signals("PROJ-99")

    assert signals == {"has_active_agent": False, "recent_commits": 0, "completed_tasks": 0}


# ── confidence / risk mapping ─────────────────────────────────────────────────

@pytest.mark.parametrize("signals,expected_confidence,expected_risk", [
    # Active agent, no commits → High confidence, High risk
    ({"has_active_agent": True,  "recent_commits": 0, "completed_tasks": 0}, "High",   "High"),
    # Active agent + many commits → High confidence, Low risk
    ({"has_active_agent": True,  "recent_commits": 5, "completed_tasks": 0}, "High",   "Low"),
    # Commits > 2, no agent → High confidence, Low risk
    ({"has_active_agent": False, "recent_commits": 3, "completed_tasks": 0}, "High",   "Low"),
    # Exactly 1 commit → High confidence, Medium risk
    ({"has_active_agent": False, "recent_commits": 1, "completed_tasks": 0}, "High",   "Medium"),
    # Only closed tasks → Medium confidence, High risk
    ({"has_active_agent": False, "recent_commits": 0, "completed_tasks": 1}, "Medium", "High"),
    # Nothing at all → Low confidence, High risk
    ({"has_active_agent": False, "recent_commits": 0, "completed_tasks": 0}, "Low",    "High"),
])
def test_signals_to_values(signals, expected_confidence, expected_risk):
    from services.atlassian import _signals_to_values
    result = _signals_to_values(signals)
    assert result["confidence"] == expected_confidence, f"confidence mismatch for {signals}"
    assert result["risk"] == expected_risk, f"risk mismatch for {signals}"


# ── sync-signals endpoint ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_signals_skips_when_not_configured():
    """Returns updated=False when Jira is not connected — never raises."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    import services.atlassian as svc

    with patch.object(svc, "is_connected", return_value=False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/atlassian/issues/PROJ-1/sync-signals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] is False
    assert "reason" in body


@pytest.mark.asyncio
async def test_sync_signals_skips_when_fields_not_found():
    """Returns updated=False when confidence/risk fields aren't on this Jira instance."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    import services.atlassian as svc

    with (
        patch.object(svc, "is_connected", return_value=True),
        patch.object(svc, "compute_issue_signals", new=AsyncMock(return_value={
            "has_active_agent": True, "recent_commits": 1, "completed_tasks": 0,
        })),
        patch.object(svc, "discover_custom_field_ids", new=AsyncMock(return_value={})),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/atlassian/issues/PROJ-1/sync-signals")

    assert resp.status_code == 200
    assert resp.json()["updated"] is False


@pytest.mark.asyncio
async def test_sync_signals_writes_correct_fields():
    """Endpoint writes confidence+risk using discovered field IDs, returns updated=True."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    import services.atlassian as svc

    captured: dict = {}

    async def fake_update(issue_key, fields):
        captured.update(fields)

    with (
        patch.object(svc, "is_connected", return_value=True),
        patch.object(svc, "compute_issue_signals", new=AsyncMock(return_value={
            "has_active_agent": True, "recent_commits": 3, "completed_tasks": 0,
        })),
        patch.object(svc, "discover_custom_field_ids", new=AsyncMock(return_value={
            "confidence": "customfield_10100",
            "risk": "customfield_10101",
        })),
        patch.object(svc, "update_issue_fields", new=AsyncMock(side_effect=fake_update)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/atlassian/issues/PROJ-1/sync-signals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] is True
    # 3 commits → High confidence, Low risk
    assert body["fields"]["confidence"] == "High"
    assert body["fields"]["risk"] == "Low"
    # Correct custom field IDs written
    assert captured["customfield_10100"] == {"value": "High"}
    assert captured["customfield_10101"] == {"value": "Low"}

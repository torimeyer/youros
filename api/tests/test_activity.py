from unittest.mock import AsyncMock, patch

import pytest


SAMPLE_EVENTS = [
    {"timestamp": "2026-04-06T17:42:46Z", "event": "task.added", "detail": "→087 Build activity timeline"},
    {"timestamp": "2026-04-06T17:42:47Z", "event": "task.closed", "detail": "→044 reason: none"},
    {"timestamp": "2026-04-06T17:45:12Z", "event": "needle.linked", "detail": 'relation="blocks" source="→001" target="→002"'},
    {"timestamp": "2026-04-06T17:45:20Z", "event": "needle.activated", "detail": 'needle="→088"'},
    {"timestamp": "2026-04-06T17:50:00Z", "event": "agent.spawned", "detail": "worker-1"},
]


@pytest.mark.asyncio
async def test_activity_returns_events(client):
    """The activity endpoint should return a list of events."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=SAMPLE_EVENTS)
        resp = await client.get("/api/activity")

    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "count" in data
    assert data["count"] == 5


@pytest.mark.asyncio
async def test_activity_events_have_required_fields(client):
    """Each event should include timestamp, event, label, category, and detail."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=SAMPLE_EVENTS[:1])
        resp = await client.get("/api/activity")

    event = resp.json()["events"][0]
    assert "timestamp" in event
    assert "event" in event
    assert "label" in event
    assert "category" in event
    assert "detail" in event


@pytest.mark.asyncio
async def test_activity_task_events_labeled_correctly(client):
    """Task events should have plain-language labels."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T17:42:46Z", "event": "task.added", "detail": "→087 Build timeline"},
        ])
        resp = await client.get("/api/activity")

    event = resp.json()["events"][0]
    assert event["label"] == "Task created"
    assert event["category"] == "task"


@pytest.mark.asyncio
async def test_activity_agent_events_labeled_correctly(client):
    """Agent events should have plain-language labels."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T18:00:00Z", "event": "agent.spawned", "detail": "worker-1"},
        ])
        resp = await client.get("/api/activity")

    event = resp.json()["events"][0]
    assert event["label"] == "Agent started"
    assert event["category"] == "agent"


@pytest.mark.asyncio
async def test_activity_returns_newest_first(client):
    """Events should be returned with newest first."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T10:00:00Z", "event": "task.added", "detail": "→001 First"},
            {"timestamp": "2026-04-06T11:00:00Z", "event": "task.added", "detail": "→002 Second"},
            {"timestamp": "2026-04-06T12:00:00Z", "event": "task.added", "detail": "→003 Third"},
        ])
        resp = await client.get("/api/activity")

    events = resp.json()["events"]
    assert events[0]["detail"] == "→003 Third"
    assert events[-1]["detail"] == "→001 First"


@pytest.mark.asyncio
async def test_activity_respects_last_param(client):
    """The last query parameter should be forwarded to get_history."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[])
        resp = await client.get("/api/activity?last=10")

    assert resp.status_code == 200
    mock_ostk.get_history.assert_called_once_with(last=10, target=None)


@pytest.mark.asyncio
async def test_activity_respects_target_param(client):
    """The target query parameter should be forwarded to get_history."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[])
        resp = await client.get("/api/activity?target=%E2%86%92087")

    assert resp.status_code == 200
    mock_ostk.get_history.assert_called_once_with(last=50, target="→087")


@pytest.mark.asyncio
async def test_activity_empty_history(client):
    """Activity should return empty list when there is no history."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[])
        resp = await client.get("/api/activity")

    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_activity_unknown_event_gets_fallback_label(client):
    """Unknown event types should get a formatted fallback label."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T18:00:00Z", "event": "custom.thing", "detail": "something"},
        ])
        resp = await client.get("/api/activity")

    event = resp.json()["events"][0]
    assert event["label"] == "Custom Thing"
    assert event["category"] == "other"



@pytest.mark.asyncio
async def test_activity_filters_hidden_events(client):
    """Noise events like chat.completion and tack.unknown should be filtered out."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T18:00:00Z", "event": "task.added", "detail": "real event"},
            {"timestamp": "2026-04-06T18:00:01Z", "event": "chat.completion", "detail": "noise"},
            {"timestamp": "2026-04-06T18:00:02Z", "event": "tool.bash", "detail": "noise"},
            {"timestamp": "2026-04-06T18:00:03Z", "event": "tack.unknown", "detail": "input=bg-white"},
            {"timestamp": "2026-04-06T18:00:04Z", "event": "heartbeat_injected", "detail": "noise"},
        ])
        resp = await client.get("/api/activity")

    data = resp.json()
    assert data["count"] == 1
    assert data["events"][0]["event"] == "task.added"


@pytest.mark.asyncio
async def test_activity_tack_resolved_has_readable_detail(client):
    """tack.resolved events should have human-readable detail text."""
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[
            {"timestamp": "2026-04-06T18:00:00Z", "event": "tack.resolved", "detail": "input=:compile suggestions=[]"},
        ])
        resp = await client.get("/api/activity")

    event = resp.json()["events"][0]
    assert event["label"] == "Command resolved"
    assert event["category"] == "system"
    assert event["detail"].startswith("Ran: ")


@pytest.mark.asyncio
async def test_activity_known_events_all_have_labels(client):
    """Every event type in EVENT_LABELS should produce a human-readable label."""
    from routers.activity import EVENT_LABELS
    for event_type, label in EVENT_LABELS.items():
        assert label != event_type, f"{event_type} label is just the raw type"
        assert label[0].isupper(), f"{event_type} label should start with a capital letter"


# --- OstkService.get_history unit tests ---


def test_parse_history_line():
    """The parser should extract timestamp, event, and detail from a history line."""
    from services.ostk import OstkService

    line = "[2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management"
    result = OstkService._parse_history_line(line)

    assert result is not None
    assert result["timestamp"] == "2026-04-06T17:42:46Z"
    assert result["event"] == "task.added"
    assert result["detail"] == "→091 Use ostk secret management"


def test_parse_history_line_needle_linked():
    """The parser should handle needle.linked events with extra details."""
    from services.ostk import OstkService

    line = '[2026-04-06T17:45:12Z] needle.linked     relation="blocks" source="→001" target="→002"'
    result = OstkService._parse_history_line(line)

    assert result is not None
    assert result["event"] == "needle.linked"
    assert "blocks" in result["detail"]


def test_parse_history_line_invalid():
    """The parser should return None for unparseable lines."""
    from services.ostk import OstkService

    assert OstkService._parse_history_line("") is None
    assert OstkService._parse_history_line("some random text") is None
    assert OstkService._parse_history_line("---") is None


def test_parse_history_line_new_timestamp_format():
    """Parser must handle the current ostk format: microseconds + +00:00 timezone."""
    from services.ostk import OstkService

    # Current format from `ostk os history`
    line = '[2026-04-14T22:30:45.665455+00:00] agent.spawned     name="fix-activity-insights" model="claude-sonnet-4-6" budget="2.0"'
    result = OstkService._parse_history_line(line)

    assert result is not None, "Parser returned None for new timestamp format (Bug 1)"
    assert result["timestamp"] == "2026-04-14T22:30:45.665455+00:00"
    assert result["event"] == "agent.spawned"
    assert "fix-activity-insights" in result["detail"]


def test_parse_history_line_completed_event():
    """Parser must handle agent.completed events which have no trailing detail."""
    from services.ostk import OstkService

    line = '[2026-04-14T22:30:51.915896+00:00] agent.completed   name="summarizer-bot"'
    result = OstkService._parse_history_line(line)

    assert result is not None
    assert result["event"] == "agent.completed"
    assert "summarizer-bot" in result["detail"]


@pytest.mark.asyncio
async def test_get_history_parses_new_timestamp_format():
    """get_history must return rows for the current ostk output format (Bug 1 regression)."""
    from services.ostk import OstkService

    service = OstkService()
    sample_output = (
        '[2026-04-14T22:30:45.665455+00:00] agent.spawned     name="fix-activity-insights" model="claude-sonnet-4-6" budget="2.0"\n'
        '[2026-04-14T22:30:51.915896+00:00] agent.completed   name="summarizer-bot"\n'
    )
    with patch.object(service, "_run", new_callable=AsyncMock, return_value=sample_output):
        result = await service.get_history(last=10)

    # Before the fix this returned [] because the regex didn't match +00:00 format
    assert len(result) == 2, f"Expected 2 events, got {len(result)} (Bug 1: regex mismatch)"
    assert result[0]["event"] == "agent.spawned"
    assert result[1]["event"] == "agent.completed"


@pytest.mark.asyncio
async def test_get_history_calls_ostk_cli():
    """get_history should call ostk os history with the right args."""
    from services.ostk import OstkService

    service = OstkService()
    sample_output = (
        "[2026-04-06T17:42:46Z] task.added        →087 Build timeline\n"
        "[2026-04-06T17:42:47Z] task.closed       →044 reason: none\n"
    )
    with patch.object(service, "_run", new_callable=AsyncMock, return_value=sample_output):
        result = await service.get_history(last=10)

    assert len(result) == 2
    assert result[0]["event"] == "task.added"
    assert result[1]["event"] == "task.closed"


@pytest.mark.asyncio
async def test_get_history_with_target():
    """get_history should pass target to the CLI."""
    from services.ostk import OstkService

    service = OstkService()
    with patch.object(service, "_run", new_callable=AsyncMock, return_value="") as mock_run:
        await service.get_history(target="→087")

    mock_run.assert_called_once_with("os", "history", "→087")


@pytest.mark.asyncio
async def test_get_history_handles_error():
    """get_history should return empty list on OstkError."""
    from services.ostk import OstkService, OstkError

    service = OstkService()
    with patch.object(service, "_run", new_callable=AsyncMock, side_effect=OstkError("boom")):
        result = await service.get_history(last=10)

    assert result == []


# ---------------------------------------------------------------------------
# Regression: dedupe_audit_log cleanup endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedupe_audit_log_removes_duplicates(tmp_path):
    """POST /api/activity/dedupe-audit must collapse duplicate agent.completed
    rows within the default 60-second window, leaving one per agent."""
    import json
    from pathlib import Path

    audit_path = tmp_path / "audit.jsonl"
    # Write 4 agent.completed rows for "summarizer-bot" within 60 seconds,
    # plus one for a different agent and one non-terminal event.
    rows = [
        {"event": "task.added", "detail": "→001 something", "timestamp": "2026-04-14T17:50:00+00:00"},
        {"event": "agent.completed", "name": "summarizer-bot", "timestamp": "2026-04-14T17:54:00.143908+00:00"},
        {"event": "agent.completed", "name": "summarizer-bot", "timestamp": "2026-04-14T17:54:01.433174+00:00"},
        {"event": "agent.completed", "name": "summarizer-bot", "timestamp": "2026-04-14T17:54:10.769528+00:00"},
        {"event": "agent.completed", "name": "summarizer-bot", "timestamp": "2026-04-14T17:54:12.179658+00:00"},
        {"event": "agent.completed", "name": "other-bot", "timestamp": "2026-04-14T17:54:05+00:00"},
    ]
    audit_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    with patch("routers.activity.OSTK_DIR", tmp_path), \
         patch("routers.activity.invalidate_audit_cache"):
        from routers.activity import dedupe_audit_log
        result = await dedupe_audit_log(window_seconds=60)

    assert result["removed"] == 3, f"Expected 3 removed, got {result}"

    cleaned = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
    summarizer_rows = [r for r in cleaned if r.get("name") == "summarizer-bot" and r.get("event") == "agent.completed"]
    other_rows = [r for r in cleaned if r.get("name") == "other-bot"]
    task_rows = [r for r in cleaned if r.get("event") == "task.added"]

    assert len(summarizer_rows) == 1, f"Expected 1 summarizer-bot row, got {len(summarizer_rows)}"
    assert len(other_rows) == 1, "other-bot row must be kept"
    assert len(task_rows) == 1, "non-terminal event must be kept"


@pytest.mark.asyncio
async def test_dedupe_audit_log_no_duplicates_no_change(tmp_path):
    """POST /api/activity/dedupe-audit returns removed=0 when there are
    no duplicates in the window."""
    import json

    audit_path = tmp_path / "audit.jsonl"
    rows = [
        {"event": "agent.completed", "name": "bot-a", "timestamp": "2026-04-14T17:54:00+00:00"},
        {"event": "agent.completed", "name": "bot-a", "timestamp": "2026-04-14T17:55:05+00:00"},  # >60s later
        {"event": "agent.completed", "name": "bot-b", "timestamp": "2026-04-14T17:54:30+00:00"},
    ]
    original_content = "\n".join(json.dumps(r) for r in rows) + "\n"
    audit_path.write_text(original_content)

    with patch("routers.activity.OSTK_DIR", tmp_path), \
         patch("routers.activity.invalidate_audit_cache"):
        from routers.activity import dedupe_audit_log
        result = await dedupe_audit_log(window_seconds=60)

    assert result["removed"] == 0
    assert audit_path.read_text() == original_content

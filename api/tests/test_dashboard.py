from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _make_task(id, title, priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status}


@pytest.mark.asyncio
async def test_dashboard_returns_all_fields(client):
    mock_tasks = [
        _make_task("t-1", "Build UI", "P0", "open"),
        _make_task("t-2", "Fix bug", "P1", "open"),
        _make_task("t-3", "Done item", "P2", "closed"),
    ]
    mock_hay = {"clusters": [{"name": "design", "count": 3, "items": []}], "unclustered": ["idea1"]}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="daemon running")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert "counts" in data
    assert "focus" in data
    assert "recent_tasks" in data
    assert "hay_count" in data
    assert "ostk_status" in data


@pytest.mark.asyncio
async def test_dashboard_counts_computed_correctly(client):
    mock_tasks = [
        _make_task("t-1", "A", "P0", "open"),
        _make_task("t-2", "B", "P1", "open"),
        _make_task("t-3", "C", "P2", "open"),
        _make_task("t-4", "D", "P0", "open"),
        _make_task("t-5", "E", "P1", "closed"),
        _make_task("t-6", "F", "P2", "closed"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    counts = resp.json()["counts"]
    assert counts["open"] == 4
    assert counts["closed"] == 2
    assert counts["p0"] == 2
    assert counts["p1"] == 1
    assert counts["p2"] == 1


@pytest.mark.asyncio
async def test_dashboard_focus_sorted_by_priority(client):
    # Focus returns ALL visible open tasks sorted by priority so the
    # widget body always agrees with the "N open" count in the header.
    # P0 comes first, then P1, P2, P3, then anything without a priority.
    mock_tasks = [
        _make_task("t-3", "Nice to have", "P2", "open"),
        _make_task("t-1", "Critical fix", "P0", "open"),
        _make_task("t-2", "Important feature", "P1", "open"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    data = resp.json()
    focus_ids = [f["id"] for f in data["focus"]]
    # All three visible open tasks appear in focus, sorted P0 -> P1 -> P2.
    assert focus_ids == ["t-1", "t-2", "t-3"]
    # And the open count agrees with the number of focus rows when the
    # total fits under the 4-row cap.
    assert data["counts"]["open"] == len(data["focus"]) == 3


@pytest.mark.asyncio
async def test_dashboard_focus_body_matches_open_count(client):
    # Regression: when the only open tasks are P2 (no P0 or P1), the
    # "N open" count and the body list must still agree. Previously the
    # count said "2 open" and the body said "No focus tasks right now."
    mock_tasks = [
        _make_task("t-1", "Write docs", "P2", "open"),
        _make_task("t-2", "Chase vendor", "P2", "open"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    data = resp.json()
    assert data["counts"]["open"] == 2
    assert len(data["focus"]) == 2
    assert {f["id"] for f in data["focus"]} == {"t-1", "t-2"}


@pytest.mark.asyncio
async def test_dashboard_focus_includes_unprioritized_tasks(client):
    # A task with no priority set should still appear in focus when it
    # is the only open task, so the body never stays empty while the
    # count shows a non-zero number.
    mock_tasks = [
        {"id": "t-1", "title": "Unlabeled task", "status": "open"},
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    data = resp.json()
    assert data["counts"]["open"] == 1
    assert len(data["focus"]) == 1
    assert data["focus"][0]["id"] == "t-1"


@pytest.mark.asyncio
async def test_dashboard_focus_limited_to_4(client):
    mock_tasks = [
        _make_task(f"t-{i}", f"Task {i}", "P0", "open")
        for i in range(10)
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    focus = resp.json()["focus"]
    assert len(focus) <= 4


@pytest.mark.asyncio
async def test_dashboard_hay_count_field_present(client):
    # hay_count is kept in the response for schema back-compat, but the
    # frontend never reads it, so /api/dashboard no longer spawns the
    # hay subprocess. The field just reports 0.
    mock_tasks = []

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/dashboard")

    assert resp.json()["hay_count"] == 0


@pytest.mark.asyncio
async def test_dashboard_ostk_status_field_present(client):
    # ostk_status is kept in the response for schema back-compat, but the
    # frontend never reads it, so /api/dashboard no longer spawns the
    # status subprocess. The field is an empty string.
    mock_tasks = []

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/dashboard")

    assert "ostk_status" in resp.json()


@pytest.mark.asyncio
async def test_dashboard_skips_os_status_and_list_hay_subprocesses(client):
    # Regression: the Today's Focus card kept saying "Loading..." for
    # several seconds because /api/dashboard used to spawn ostk os status
    # and ostk work hay subprocesses in parallel on every call. Neither
    # field is read by the frontend, so this test enforces that the
    # endpoint no longer calls those two methods.
    mock_tasks = [
        _make_task("t-1", "Build UI", "P0", "open"),
    ]

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="daemon running")
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    # list_tasks stays. os_status and list_hay must never be called.
    assert mock_ostk.list_tasks.await_count == 1
    assert mock_ostk.os_status.await_count == 0
    assert mock_ostk.list_hay.await_count == 0


@pytest.mark.asyncio
async def test_dashboard_recent_tasks_limited_to_5(client):
    mock_tasks = [
        _make_task(f"t-{i}", f"Task {i}", "P1", "open")
        for i in range(10)
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert len(resp.json()["recent_tasks"]) <= 5


@pytest.mark.asyncio
async def test_dashboard_handles_ostk_task_error(client):
    """When task listing fails, dashboard should still return with empty data."""
    from services.ostk import OstkError

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(side_effect=OstkError("offline"))
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    assert resp.json()["counts"]["open"] == 0


# --- GET /api/dashboard/summary ---


@pytest.mark.asyncio
async def test_dashboard_summary_returns_bullets(client, tmp_path):
    """The summary endpoint should return a list of bullet strings."""
    mock_tasks = [
        _make_task("t-1", "Fix bug", "P0", "open"),
        _make_task("t-2", "Add feature", "P1", "open"),
    ]

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert "bullets" in data
    assert isinstance(data["bullets"], list)
    assert len(data["bullets"]) > 0


@pytest.mark.asyncio
async def test_dashboard_summary_bullets_limited_to_5(client, tmp_path):
    """Summary should return at most 5 bullets."""
    mock_tasks = [
        _make_task(f"t-{i}", f"Task {i}", "P0", "open")
        for i in range(10)
    ]

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.list_hay = AsyncMock(
            return_value={"clusters": [], "unclustered": ["idea1", "idea2"]}
        )

        resp = await client.get("/api/dashboard/summary")

    data = resp.json()
    assert len(data["bullets"]) <= 5


@pytest.mark.asyncio
async def test_dashboard_summary_mentions_open_tasks(client, tmp_path):
    """Summary should mention the open task count."""
    mock_tasks = [
        _make_task("t-1", "Task A", "P1", "open"),
        _make_task("t-2", "Task B", "P1", "open"),
        _make_task("t-3", "Task C", "P2", "open"),
    ]

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    bullets = resp.json()["bullets"]
    # One bullet should mention "3 tasks still open"
    assert any("3 tasks still open" in b for b in bullets)


@pytest.mark.asyncio
async def test_dashboard_summary_mentions_p0_tasks(client, tmp_path):
    """Summary should call out P0 priorities by title."""
    mock_tasks = [
        _make_task("t-1", "Critical fix", "P0", "open"),
    ]

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    bullets = resp.json()["bullets"]
    assert any("Critical fix" in b for b in bullets)


@pytest.mark.asyncio
async def test_dashboard_summary_handles_no_tasks(client, tmp_path):
    """Summary should work even when there are zero tasks."""
    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    assert resp.status_code == 200
    bullets = resp.json()["bullets"]
    assert any("0 tasks still open" in b for b in bullets)


@pytest.mark.asyncio
async def test_dashboard_summary_handles_task_error(client, tmp_path):
    """Summary should handle OstkError gracefully."""
    from services.ostk import OstkError

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(side_effect=OstkError("offline"))
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    assert resp.status_code == 200
    bullets = resp.json()["bullets"]
    # With no tasks, it should still produce bullets
    assert len(bullets) > 0


@pytest.mark.asyncio
async def test_dashboard_summary_agent_activity_from_audit(client, tmp_path):
    """Summary should report agent activity from the audit log."""
    import json
    from datetime import datetime, timezone

    # Use local date and construct UTC timestamps that fall within the
    # local calendar day. This matters when the UTC date has rolled ahead
    # (e.g. it is evening CDT but already tomorrow in UTC).
    today_str = datetime.now().strftime("%Y-%m-%d")
    # Noon local time in UTC: guaranteed to be on the local calendar day.
    local_noon = datetime.strptime(f"{today_str} 12:00:00", "%Y-%m-%d %H:%M:%S")
    noon_utc = local_noon.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    one_pm_utc = local_noon.replace(hour=13).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    half_past_utc = local_noon.replace(hour=12, minute=30).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audit_path = tmp_path / "audit.jsonl"
    audit_entries = [
        {"event": "agent.spawned", "name": "worker-1", "model": "sonnet", "timestamp": noon_utc},
        {"event": "agent.spawned", "name": "worker-2", "model": "sonnet", "timestamp": one_pm_utc},
        {"event": "agent.completed", "name": "worker-1", "timestamp": half_past_utc},
    ]
    audit_path.write_text("\n".join(json.dumps(e) for e in audit_entries) + "\n")

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch("routers.dashboard.OSTK_DIR", tmp_path):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})

        resp = await client.get("/api/dashboard/summary")

    bullets = resp.json()["bullets"]
    # Should mention "2 started" and "1 finished"
    agent_bullet = [b for b in bullets if "Agents today" in b]
    assert len(agent_bullet) == 1
    assert "2 started" in agent_bullet[0]
    assert "1 finished" in agent_bullet[0]


def test_is_local_today_converts_utc_to_local():
    """_is_local_today should convert UTC timestamps to local time before
    comparing the date string, so events near UTC midnight are correctly
    classified by the local calendar day."""
    from routers.dashboard import _is_local_today
    from datetime import datetime, timezone

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Noon local time converted to UTC is unambiguously on the local date.
    local_noon = datetime.strptime(f"{today_str} 12:00:00", "%Y-%m-%d %H:%M:%S")
    noon_utc = local_noon.astimezone(timezone.utc).isoformat()
    assert _is_local_today(noon_utc, today_str) is True

    # A timestamp from 30 hours ago should NOT match today.
    from datetime import timedelta
    old_ts = (local_noon - timedelta(hours=30)).astimezone(timezone.utc).isoformat()
    assert _is_local_today(old_ts, today_str) is False

    # Edge cases.
    assert _is_local_today("", today_str) is False
    assert _is_local_today("garbage", today_str) is False


# --- GET /api/dashboard/compounds ---


@pytest.mark.asyncio
async def test_dashboard_compounds_returns_top_and_all(client):
    """The compounds endpoint should return top and all fields."""
    mock_compounds = [
        {"id": "→042", "title": "Build smart focus", "blocks_count": 5},
        {"id": "→017", "title": "Fix auth redirect", "blocks_count": 2},
    ]

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_compounds = AsyncMock(return_value=mock_compounds)
        resp = await client.get("/api/dashboard/compounds")

    assert resp.status_code == 200
    data = resp.json()
    assert "top" in data
    assert "all" in data
    assert data["top"]["id"] == "→042"
    assert data["top"]["blocks_count"] == 5
    assert len(data["all"]) == 2


@pytest.mark.asyncio
async def test_dashboard_compounds_top_is_highest_leverage(client):
    """The top recommendation should be the task that unblocks the most."""
    mock_compounds = [
        {"id": "→010", "title": "Task A", "blocks_count": 8},
        {"id": "→011", "title": "Task B", "blocks_count": 3},
        {"id": "→012", "title": "Task C", "blocks_count": 1},
    ]

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_compounds = AsyncMock(return_value=mock_compounds)
        resp = await client.get("/api/dashboard/compounds")

    data = resp.json()
    assert data["top"]["id"] == "→010"
    assert data["top"]["blocks_count"] == 8


@pytest.mark.asyncio
async def test_dashboard_compounds_empty_when_no_dependencies(client):
    """When there are no blocking dependencies, top should be null."""
    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_compounds = AsyncMock(return_value=[])
        resp = await client.get("/api/dashboard/compounds")

    data = resp.json()
    assert data["top"] is None
    assert data["all"] == []


@pytest.mark.asyncio
async def test_dashboard_compounds_handles_ostk_error(client):
    """When ostk fails, compounds should return empty gracefully."""
    from services.ostk import OstkError

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_compounds = AsyncMock(side_effect=OstkError("offline"))
        resp = await client.get("/api/dashboard/compounds")

    assert resp.status_code == 200
    data = resp.json()
    assert data["top"] is None
    assert data["all"] == []


# --- OstkService._parse_compounds ---


def test_parse_compounds_with_entries():
    """Parser should extract id, title, and blocks_count from CLI output."""
    from services.ostk import OstkService

    output = """:compounds - highest compounding work

  →042  Build smart focus  (blocks 5)
  →017  Fix auth redirect  (blocks 2)
  →003  Refactor settings page  (blocks 1)
"""
    result = OstkService._parse_compounds(output)
    assert len(result) == 3
    assert result[0] == {"id": "→042", "title": "Build smart focus", "blocks_count": 5}
    assert result[1] == {"id": "→017", "title": "Fix auth redirect", "blocks_count": 2}
    assert result[2] == {"id": "→003", "title": "Refactor settings page", "blocks_count": 1}


def test_parse_compounds_empty():
    """Parser should return empty list when no dependencies found."""
    from services.ostk import OstkService

    output = """:compounds - highest compounding work

  (no blocking dependencies found)"""
    result = OstkService._parse_compounds(output)
    assert result == []


def test_parse_compounds_sorts_by_blocks_count():
    """Parser should sort results so highest blocks_count comes first."""
    from services.ostk import OstkService

    output = """:compounds - highest compounding work

  →003  Low priority  (blocks 1)
  →042  High priority  (blocks 9)
  →017  Mid priority  (blocks 4)
"""
    result = OstkService._parse_compounds(output)
    assert result[0]["blocks_count"] == 9
    assert result[1]["blocks_count"] == 4
    assert result[2]["blocks_count"] == 1


def test_parse_compounds_single_block():
    """Parser should handle singular 'block' vs 'blocks'."""
    from services.ostk import OstkService

    output = """:compounds - highest compounding work

  →001  Only one  (block 1)
"""
    result = OstkService._parse_compounds(output)
    assert len(result) == 1
    assert result[0]["blocks_count"] == 1


# --- GET /api/dashboard/diff ---


@pytest.mark.asyncio
async def test_dashboard_diff_returns_all_fields(client):
    """The diff endpoint should return files, needles, audit events, and total."""
    mock_diff = {
        "files_changed": ["src/main.py"],
        "needles_filed": [{"id": "\u2192089", "priority": "P1", "title": "Build session diff"}],
        "audit_events": [{"count": 3, "event": "task.added"}],
        "audit_total": 3,
    }

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_session_diff = AsyncMock(return_value=mock_diff)
        resp = await client.get("/api/dashboard/diff")

    assert resp.status_code == 200
    data = resp.json()
    assert "files_changed" in data
    assert "needles_filed" in data
    assert "audit_events" in data
    assert "audit_total" in data


@pytest.mark.asyncio
async def test_dashboard_diff_returns_correct_data(client):
    """The diff endpoint should pass through the parsed ostk output."""
    mock_diff = {
        "files_changed": ["api/main.py", "app/src/Dashboard.tsx"],
        "needles_filed": [
            {"id": "\u2192083", "priority": "P1", "title": "Add task dependencies"},
            {"id": "\u2192084", "priority": "P0", "title": "Build smart focus"},
        ],
        "audit_events": [
            {"count": 16, "event": "task.added"},
            {"count": 4, "event": "needle.activated"},
        ],
        "audit_total": 22,
    }

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_session_diff = AsyncMock(return_value=mock_diff)
        resp = await client.get("/api/dashboard/diff")

    data = resp.json()
    assert len(data["files_changed"]) == 2
    assert data["files_changed"][0] == "api/main.py"
    assert len(data["needles_filed"]) == 2
    assert data["needles_filed"][0]["title"] == "Add task dependencies"
    assert data["audit_total"] == 22
    assert len(data["audit_events"]) == 2


@pytest.mark.asyncio
async def test_dashboard_diff_handles_ostk_error(client):
    """When ostk os diff fails, the endpoint should return empty data."""
    from services.ostk import OstkError

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_session_diff = AsyncMock(side_effect=OstkError("no boot found"))
        resp = await client.get("/api/dashboard/diff")

    assert resp.status_code == 200
    data = resp.json()
    assert data["files_changed"] == []
    assert data["needles_filed"] == []
    assert data["audit_events"] == []
    assert data["audit_total"] == 0


@pytest.mark.asyncio
async def test_dashboard_diff_empty_session(client):
    """When nothing has happened, all lists should be empty."""
    mock_diff = {
        "files_changed": [],
        "needles_filed": [],
        "audit_events": [],
        "audit_total": 0,
    }

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.get_session_diff = AsyncMock(return_value=mock_diff)
        resp = await client.get("/api/dashboard/diff")

    data = resp.json()
    assert data["files_changed"] == []
    assert data["needles_filed"] == []
    assert data["audit_events"] == []
    assert data["audit_total"] == 0


# --- OstkService._parse_session_diff ---


def test_parse_session_diff_full_output():
    """Parser should extract files, needles, audit events, and total."""
    from services.ostk import OstkService

    output = """## Files changed since boot

  src/main.py
  app/Dashboard.tsx

## Needles filed this session

  [P1] \u219283  Add task dependencies and blocking relationships
  [P1] \u219284  Build smart focus recommendation using compounds
  [P0] \u219285  Critical fix

## Audit events this session

    16  task.added
     4  needle.activated
     2  needle.linked
  (22 total events)

## Active staging

  (no active.tack \u2014 no intent forming)"""

    svc = OstkService()
    result = svc._parse_session_diff(output)

    assert result["files_changed"] == ["src/main.py", "app/Dashboard.tsx"]
    assert len(result["needles_filed"]) == 3
    assert result["needles_filed"][0]["priority"] == "P1"
    assert result["needles_filed"][0]["id"] == "\u219283"
    assert result["needles_filed"][0]["title"] == "Add task dependencies and blocking relationships"
    assert result["needles_filed"][2]["priority"] == "P0"
    assert len(result["audit_events"]) == 3
    assert result["audit_events"][0] == {"count": 16, "event": "task.added"}
    assert result["audit_events"][1] == {"count": 4, "event": "needle.activated"}
    assert result["audit_total"] == 22


def test_parse_session_diff_no_data():
    """Parser should handle output with no tracking data."""
    from services.ostk import OstkService

    output = """## Files changed since boot

  (gen_table.jsonl not found \u2014 no tracking data)

## Needles filed this session

  (0 needle(s) added)

## Audit events this session

  (0 total events)

## Active staging

  (no active.tack \u2014 no intent forming)"""

    svc = OstkService()
    result = svc._parse_session_diff(output)

    assert result["files_changed"] == []
    assert result["needles_filed"] == []
    assert result["audit_events"] == []
    assert result["audit_total"] == 0


def test_parse_session_diff_only_needles():
    """Parser should handle output with needles but no files or audit events."""
    from services.ostk import OstkService

    output = """## Files changed since boot

  (gen_table.jsonl not found \u2014 no tracking data)

## Needles filed this session

  [P1] \u219289  Build session diff view
  (1 needle(s) added)

## Audit events this session

     1  task.added
  (1 total events)

## Active staging

  (no active.tack \u2014 no intent forming)"""

    svc = OstkService()
    result = svc._parse_session_diff(output)

    assert result["files_changed"] == []
    assert len(result["needles_filed"]) == 1
    assert result["needles_filed"][0]["title"] == "Build session diff view"
    assert result["audit_events"] == [{"count": 1, "event": "task.added"}]
    assert result["audit_total"] == 1

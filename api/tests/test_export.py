"""Tests for the /api/export endpoints.

Covers:
- tasks export as markdown
- tasks export as csv
- status filter (open vs closed)
- timeline export honors the days window
- labels export returns all labels
- invalid format returns 400
- empty dataset returns an empty file
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_TASKS = [
    {
        "id": "→001",
        "title": "Fix login bug",
        "priority": "P0",
        "status": "open",
        "description": "Login fails for users with apostrophes",
        "created_at": "2026-04-01T10:00:00Z",
    },
    {
        "id": "→002",
        "title": "Add dark mode",
        "priority": "P1",
        "status": "open",
        "description": "Toggle in settings",
        "created_at": "2026-04-02T11:00:00Z",
    },
    {
        "id": "→003",
        "title": "Old completed task",
        "priority": "P2",
        "status": "closed",
        "description": "Already done",
        "created_at": "2026-03-01T09:00:00Z",
    },
]


def _now_iso(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Tasks export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_tasks_markdown(client):
    """GET /api/export/tasks?format=markdown returns markdown with the right headers."""
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=SAMPLE_TASKS)
        with patch("routers.export.task_labels_store") as mock_tls, \
             patch("routers.export.labels_store") as mock_ls:
            mock_tls.get_labels_for_task.return_value = []
            mock_ls.list_labels.return_value = []
            resp = await client.get("/api/export/tasks?format=markdown&status=open")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "tasks-open" in cd
    assert ".md" in cd

    body = resp.text
    # Title and section
    assert "# Open tasks" in body
    # Closed task should be filtered out
    assert "Fix login bug" in body
    assert "Add dark mode" in body
    assert "Old completed task" not in body
    # Markdown checkbox format
    assert "- [ ] **→001**" in body


@pytest.mark.asyncio
async def test_export_tasks_csv(client):
    """GET /api/export/tasks?format=csv returns CSV with header and one row per task."""
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=SAMPLE_TASKS)
        with patch("routers.export.task_labels_store") as mock_tls, \
             patch("routers.export.labels_store") as mock_ls:
            mock_tls.get_labels_for_task.return_value = []
            mock_ls.list_labels.return_value = []
            resp = await client.get("/api/export/tasks?format=csv&status=all")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd

    body = resp.text
    lines = body.strip().splitlines()
    # Header + 3 task rows
    assert len(lines) == 4
    assert lines[0] == "id,title,priority,status,description,created_at,labels"
    assert "Fix login bug" in lines[1]
    assert "Add dark mode" in lines[2]
    assert "Old completed task" in lines[3]


@pytest.mark.asyncio
async def test_export_tasks_open_filter(client):
    """status=open should only export open tasks."""
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=SAMPLE_TASKS)
        with patch("routers.export.task_labels_store") as mock_tls, \
             patch("routers.export.labels_store") as mock_ls:
            mock_tls.get_labels_for_task.return_value = []
            mock_ls.list_labels.return_value = []
            resp = await client.get("/api/export/tasks?format=csv&status=open")

    assert resp.status_code == 200
    body = resp.text
    lines = body.strip().splitlines()
    # Header + 2 open task rows (closed task excluded)
    assert len(lines) == 3
    assert "Fix login bug" in body
    assert "Add dark mode" in body
    assert "Old completed task" not in body


@pytest.mark.asyncio
async def test_export_tasks_closed_filter(client):
    """status=closed should only export closed tasks."""
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=SAMPLE_TASKS)
        with patch("routers.export.task_labels_store") as mock_tls, \
             patch("routers.export.labels_store") as mock_ls:
            mock_tls.get_labels_for_task.return_value = []
            mock_ls.list_labels.return_value = []
            resp = await client.get("/api/export/tasks?format=markdown&status=closed")

    assert resp.status_code == 200
    body = resp.text
    assert "# Closed tasks" in body
    assert "Old completed task" in body
    assert "Fix login bug" not in body
    assert "Add dark mode" not in body
    # Closed tasks render with [x]
    assert "- [x] **→003**" in body


# ---------------------------------------------------------------------------
# Timeline export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_timeline_respects_days(client):
    """timeline export should only include events within the requested window."""
    events = [
        {"timestamp": _now_iso(0), "event": "task.added", "detail": "→100 Recent"},
        {"timestamp": _now_iso(2), "event": "task.added", "detail": "→099 Two days ago"},
        {"timestamp": _now_iso(20), "event": "task.added", "detail": "→050 Way old"},
    ]
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=events)
        resp = await client.get("/api/export/timeline?format=markdown&days=7")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    cd = resp.headers.get("content-disposition", "")
    assert "timeline-7d" in cd

    body = resp.text
    assert "# Activity timeline (last 7 days)" in body
    assert "→100 Recent" in body
    assert "→099 Two days ago" in body
    # Outside the window
    assert "→050 Way old" not in body


# ---------------------------------------------------------------------------
# Labels export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_labels_returns_all(client):
    """labels export should list every label with its task count."""
    fake_labels = [
        {"id": "l1", "name": "Bug", "color": "#ef4444"},
        {"id": "l2", "name": "Docs", "color": "#3b82f6"},
        {"id": "l3", "name": "Empty", "color": "#22c55e"},
    ]
    fake_assignments = {
        "→001": ["l1"],
        "→002": ["l1", "l2"],
    }
    with patch("routers.export.labels_store") as mock_ls, \
         patch("routers.export.task_labels_store") as mock_tls:
        mock_ls.list_labels.return_value = fake_labels
        mock_tls.get_all_assignments.return_value = fake_assignments

        # CSV
        resp_csv = await client.get("/api/export/labels?format=csv")
        # Markdown
        resp_md = await client.get("/api/export/labels?format=markdown")

    assert resp_csv.status_code == 200
    assert resp_csv.headers["content-type"].startswith("text/csv")
    csv_body = resp_csv.text
    csv_lines = csv_body.strip().splitlines()
    assert csv_lines[0] == "id,name,color,task_count"
    # Header + 3 labels
    assert len(csv_lines) == 4
    assert "Bug" in csv_body
    assert "Docs" in csv_body
    assert "Empty" in csv_body
    # Task counts: Bug=2, Docs=1, Empty=0
    assert ",2" in csv_lines[1]
    assert ",1" in csv_lines[2]
    assert ",0" in csv_lines[3]

    assert resp_md.status_code == 200
    md_body = resp_md.text
    assert "# Labels" in md_body
    assert "**Bug** (2 tasks)" in md_body
    assert "**Docs** (1 task)" in md_body
    assert "**Empty** (0 tasks)" in md_body


# ---------------------------------------------------------------------------
# Errors / edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_invalid_format_returns_400(client):
    """An unknown format should return a 400 with a clear error."""
    resp = await client.get("/api/export/tasks?format=pdf&status=open")
    assert resp.status_code == 400
    assert "format must be" in resp.json()["detail"]

    resp2 = await client.get("/api/export/timeline?format=csv&days=7")
    assert resp2.status_code == 400

    resp3 = await client.get("/api/export/labels?format=xml")
    assert resp3.status_code == 400


@pytest.mark.asyncio
async def test_export_tasks_empty_dataset(client):
    """An empty task list should still produce a valid file (no crash)."""
    with patch("routers.export.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        with patch("routers.export.task_labels_store") as mock_tls, \
             patch("routers.export.labels_store") as mock_ls:
            mock_tls.get_labels_for_task.return_value = []
            mock_ls.list_labels.return_value = []
            resp_md = await client.get("/api/export/tasks?format=markdown&status=open")
            resp_csv = await client.get("/api/export/tasks?format=csv&status=open")

    assert resp_md.status_code == 200
    assert "# Open tasks" in resp_md.text
    assert "No tasks to export" in resp_md.text

    assert resp_csv.status_code == 200
    csv_lines = resp_csv.text.strip().splitlines()
    # Just the header
    assert len(csv_lines) == 1
    assert csv_lines[0].startswith("id,title")

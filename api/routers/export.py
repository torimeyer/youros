"""Export endpoints.

Download tasks, timeline activity, and labels as plain-text files
(markdown or CSV) so they can live outside myOS.

This is intentionally separate from /api/shares. Shares produce a link
that points back into myOS. Export produces a file that the browser
downloads to disk and can be opened or sent anywhere.

Endpoints:
- GET /api/export/tasks?format={markdown|csv}&status={open|closed|all}
- GET /api/export/timeline?format={markdown}&days={N}
- GET /api/export/labels?format={csv|markdown}
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services.labels_store import labels_store
from services.ostk import ostk, OstkError
from services.task_labels_store import task_labels_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALLOWED_TASK_FORMATS = {"markdown", "csv"}
ALLOWED_TIMELINE_FORMATS = {"markdown"}
ALLOWED_LABEL_FORMATS = {"markdown", "csv"}
ALLOWED_TASK_STATUS = {"open", "closed", "all"}


def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _label_name_lookup() -> dict[str, str]:
    """Map label id to display name for use in exports."""
    return {l.get("id", ""): l.get("name", "") for l in labels_store.list_labels()}


def _build_tasks_markdown(tasks: list[dict], status: str) -> str:
    """Render the tasks list as a checkbox markdown document."""
    label_names = _label_name_lookup()
    lines: list[str] = []
    title = {
        "open": "Open tasks",
        "closed": "Closed tasks",
        "all": "All tasks",
    }.get(status, "Tasks")

    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"Exported {_today_stamp()} from myOS")
    lines.append("")

    if not tasks:
        lines.append("_No tasks to export._")
        lines.append("")
        return "\n".join(lines)

    for t in tasks:
        is_closed = (t.get("status") or "").lower() == "closed"
        box = "[x]" if is_closed else "[ ]"
        task_id = t.get("id", "")
        title_text = t.get("title", "") or ""
        priority = t.get("priority", "") or ""

        header = f"- {box} **{task_id}** {title_text}"
        if priority:
            header += f" _({priority})_"
        lines.append(header)

        description = (t.get("description") or "").strip()
        if description:
            for desc_line in description.splitlines():
                lines.append(f"    {desc_line}")

        label_ids = task_labels_store.get_labels_for_task(task_id)
        if label_ids:
            names = [label_names.get(lid, lid) for lid in label_ids if label_names.get(lid, lid)]
            if names:
                lines.append(f"    Labels: {', '.join(names)}")

        created = t.get("created_at") or ""
        if created:
            lines.append(f"    Created: {created}")
        lines.append("")

    return "\n".join(lines)


def _build_tasks_csv(tasks: list[dict]) -> str:
    """Render the tasks list as CSV with the agreed columns."""
    label_names = _label_name_lookup()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id",
        "title",
        "priority",
        "status",
        "description",
        "created_at",
        "labels",
    ])
    for t in tasks:
        task_id = t.get("id", "") or ""
        label_ids = task_labels_store.get_labels_for_task(task_id)
        names = [label_names.get(lid, lid) for lid in label_ids if label_names.get(lid, lid)]
        writer.writerow([
            task_id,
            t.get("title", "") or "",
            t.get("priority", "") or "",
            t.get("status", "") or "",
            (t.get("description") or "").replace("\n", " ").strip(),
            t.get("created_at", "") or "",
            "; ".join(names),
        ])
    return buf.getvalue()


def _build_timeline_markdown(events: list[dict], days: int) -> str:
    """Render activity events as a plain-text markdown report."""
    lines: list[str] = []
    lines.append(f"# Activity timeline (last {days} day{'s' if days != 1 else ''})")
    lines.append("")
    lines.append(f"Exported {_today_stamp()} from myOS")
    lines.append("")

    if not events:
        lines.append("_No activity in this period._")
        lines.append("")
        return "\n".join(lines)

    for ev in events:
        ts = ev.get("timestamp", "") or ""
        event_type = ev.get("event", "") or ""
        detail = ev.get("detail", "") or ""
        line = f"- `{ts}` **{event_type}**"
        if detail:
            line += f" {detail}"
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def _filter_events_by_days(events: list[dict], days: int) -> list[dict]:
    """Keep only events whose timestamp is within the last `days` days."""
    if days <= 0:
        return []
    cutoff_seconds = days * 24 * 60 * 60
    now = datetime.now(timezone.utc)
    keep: list[dict] = []
    for ev in events:
        ts = ev.get("timestamp", "")
        if not ts:
            continue
        try:
            event_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        delta = (now - event_dt).total_seconds()
        if 0 <= delta <= cutoff_seconds:
            keep.append(ev)
    return keep


def _count_tasks_per_label() -> dict[str, int]:
    counts: dict[str, int] = {}
    for label_ids in task_labels_store.get_all_assignments().values():
        for lid in label_ids:
            counts[lid] = counts.get(lid, 0) + 1
    return counts


def _build_labels_markdown(labels: list[dict], counts: dict[str, int]) -> str:
    lines: list[str] = []
    lines.append("# Labels")
    lines.append("")
    lines.append(f"Exported {_today_stamp()} from myOS")
    lines.append("")
    if not labels:
        lines.append("_No labels yet._")
        lines.append("")
        return "\n".join(lines)
    for label in labels:
        name = label.get("name", "") or ""
        lid = label.get("id", "") or ""
        color = label.get("color", "") or ""
        count = counts.get(lid, 0)
        lines.append(f"- **{name}** ({count} task{'s' if count != 1 else ''}) `{color}`")
    lines.append("")
    return "\n".join(lines)


def _build_labels_csv(labels: list[dict], counts: dict[str, int]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "color", "task_count"])
    for label in labels:
        lid = label.get("id", "") or ""
        writer.writerow([
            lid,
            label.get("name", "") or "",
            label.get("color", "") or "",
            counts.get(lid, 0),
        ])
    return buf.getvalue()


def _attachment_response(body: str, filename: str, media_type: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/export/tasks")
async def export_tasks(
    format: str = Query(default="markdown"),
    status: str = Query(default="open"),
):
    """Download tasks as a markdown or CSV file."""
    if format not in ALLOWED_TASK_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="format must be 'markdown' or 'csv'",
        )
    if status not in ALLOWED_TASK_STATUS:
        raise HTTPException(
            status_code=400,
            detail="status must be 'open', 'closed', or 'all'",
        )

    try:
        all_tasks = await ostk.list_tasks()
    except OstkError as e:
        logger.warning("Could not load tasks for export: %s", e)
        all_tasks = []

    if status == "all":
        tasks = all_tasks
    else:
        tasks = [t for t in all_tasks if (t.get("status") or "").lower() == status]

    stamp = _today_stamp()
    if format == "markdown":
        body = _build_tasks_markdown(tasks, status)
        filename = f"tasks-{status}-{stamp}.md"
        return _attachment_response(body, filename, "text/markdown; charset=utf-8")

    body = _build_tasks_csv(tasks)
    filename = f"tasks-{status}-{stamp}.csv"
    return _attachment_response(body, filename, "text/csv; charset=utf-8")


@router.get("/export/timeline")
async def export_timeline(
    format: str = Query(default="markdown"),
    days: int = Query(default=7, ge=1, le=365),
):
    """Download the activity timeline as a markdown report."""
    if format not in ALLOWED_TIMELINE_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="format must be 'markdown'",
        )

    try:
        raw_events = await ostk.get_history(last=500)
    except OstkError as e:
        logger.warning("Could not load timeline for export: %s", e)
        raw_events = []

    events = _filter_events_by_days(raw_events, days)
    # newest first
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    body = _build_timeline_markdown(events, days)
    filename = f"timeline-{days}d-{_today_stamp()}.md"
    return _attachment_response(body, filename, "text/markdown; charset=utf-8")


@router.get("/export/labels")
async def export_labels(format: str = Query(default="markdown")):
    """Download all labels with their task counts."""
    if format not in ALLOWED_LABEL_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="format must be 'markdown' or 'csv'",
        )

    labels = labels_store.list_labels()
    counts = _count_tasks_per_label()

    stamp = _today_stamp()
    if format == "markdown":
        body = _build_labels_markdown(labels, counts)
        filename = f"labels-{stamp}.md"
        return _attachment_response(body, filename, "text/markdown; charset=utf-8")

    body = _build_labels_csv(labels, counts)
    filename = f"labels-{stamp}.csv"
    return _attachment_response(body, filename, "text/csv; charset=utf-8")

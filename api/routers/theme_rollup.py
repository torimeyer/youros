"""Theme rollup endpoint (spec S009 Track C, →2618).

Rolls the user's work up into the org's strategic themes: tasks and
projects are grouped by their theme tag (spec S009 Track 0.2), every
theme from the org list gets a bucket, and anything untagged lands in
a catch-all bucket so nothing disappears. Each row carries a risk flag
in plain words: "overdue" (past its due date), "blocked" (waiting on
work that is still open), "quiet" (no update in 7+ days), or "none".

The Jira section is read-only and scoped to the current user's own
assigned tickets (the "## DECISION (2026-07-09)" section of the spec
pins this scope). When Atlassian is not configured or errors, the
endpoint still returns the local rollup with an empty, disconnected
Jira section: degrade, never fail.

The user-visible page is "Portfolio" and themes match the "Theme"
wording on the Tasks and Projects pages. This module is separate from
routers/portfolio.py (the Executive Summary), which reads a Jira KR
tree via ~/.youros/portfolio.json and writes confidence back.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter

from services import atlassian
from services import enterprise_store
from services.pillar_store import pillar_store

_log = logging.getLogger(__name__)

router = APIRouter(tags=["themes"])

# A row goes "quiet" when nothing has happened to it for this long.
QUIET_AFTER_DAYS = 7

# Worst first. A bucket reports the worst risk across its rows.
_RISK_ORDER = ("overdue", "blocked", "quiet", "none")


def _parse_when(value) -> Optional[datetime]:
    """Parse an ISO timestamp or date string. Returns None when unparseable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def task_risk(task: dict, open_ids: set, now: Optional[datetime] = None) -> str:
    """Risk flag for one task row.

    overdue: its due date is in the past.
    blocked: it waits on at least one task that is still open.
    quiet:   nothing has happened to it in QUIET_AFTER_DAYS or more.
    none:    everything looks fine.

    Kernel tasks only carry a due date or update timestamp when someone
    set one, so every field read here is defensive: a missing or
    unparseable value simply never trips its flag.
    """
    now = now or datetime.now(timezone.utc)

    due = _parse_when(task.get("due") or task.get("due_date"))
    if due and due < now:
        return "overdue"

    blockers = list(task.get("depends_on") or []) + list(task.get("blocked_by") or [])
    if any(b in open_ids for b in blockers):
        return "blocked"

    last_activity = _parse_when(
        task.get("updated_at") or task.get("updated") or task.get("created_at")
    )
    if last_activity and now - last_activity >= timedelta(days=QUIET_AFTER_DAYS):
        return "quiet"

    return "none"


def project_risk(project: dict, now: Optional[datetime] = None) -> str:
    """Risk flag for one project row.

    Projects are folders: they have no due dates or blockers, so the
    only signal is activity. quiet when the folder has not changed in
    QUIET_AFTER_DAYS or more.
    """
    now = now or datetime.now(timezone.utc)
    last_modified = _parse_when(project.get("last_modified"))
    if last_modified and now - last_modified >= timedelta(days=QUIET_AFTER_DAYS):
        return "quiet"
    return "none"


def _worst(risks) -> str:
    for level in _RISK_ORDER:
        if level in risks:
            return level
    return "none"


def build_rollup(
    tasks: list,
    projects: list,
    pillars: list,
    task_pillars: dict,
    now: Optional[datetime] = None,
) -> dict:
    """Group tasks and projects into theme buckets with per-row risk flags.

    Bucket order: the org pillars list first, then tags that only exist
    on items (kept visible so an edited org list never hides tagged
    work), then the catch-all bucket (name None) for untagged items.
    The catch-all bucket is always present and always last, so the
    empty state is a single bucket rather than an error.
    """
    now = now or datetime.now(timezone.utc)
    open_ids = {str(t.get("id") or "") for t in tasks if t.get("status") != "closed"}

    names = list(pillars)
    seen = set(names)
    extras = set()
    for t in tasks:
        tag = task_pillars.get(str(t.get("id") or ""))
        if tag and tag not in seen:
            extras.add(tag)
    for p in projects:
        tag = p.get("pillar")
        if tag and tag not in seen:
            extras.add(tag)
    names += sorted(extras)

    buckets = {name: {"name": name, "projects": [], "tasks": []} for name in names}
    catch_all = {"name": None, "projects": [], "tasks": []}

    for p in projects:
        row = {
            "name": p.get("name"),
            "risk": project_risk(p, now=now),
            "last_modified": p.get("last_modified"),
        }
        buckets.get(p.get("pillar"), catch_all)["projects"].append(row)

    for t in tasks:
        tid = str(t.get("id") or "")
        row = {
            "id": tid,
            "title": t.get("title") or "",
            "risk": task_risk(t, open_ids, now=now),
        }
        buckets.get(task_pillars.get(tid), catch_all)["tasks"].append(row)

    themes = [buckets[name] for name in names] + [catch_all]
    for theme in themes:
        theme["project_count"] = len(theme["projects"])
        theme["task_count"] = len(theme["tasks"])
        theme["risk"] = _worst(
            [r["risk"] for r in theme["projects"]]
            + [r["risk"] for r in theme["tasks"]]
        )

    return {"themes": themes}


# ── data loading seams (patched directly in tests) ──────────────────────────

async def _load_tasks() -> list:
    """Open tasks as the Tasks page serves them, reusing the Tasks page
    handler so its default filters (no session bookkeeping rows, no
    acceptance-criteria checklist rows, no test data) stay consistent
    between pages. Empty on any failure so the page still renders."""
    from routers.tasks import list_tasks

    try:
        payload = await list_tasks()
        return payload.get("tasks", [])
    except Exception as exc:  # noqa: BLE001 - rollup must degrade, never fail
        _log.warning("theme rollup: could not list tasks: %s", exc)
        return []


async def _load_projects() -> list:
    """Projects with their theme tags, reusing the Projects page listing
    so tags and last-modified timestamps stay consistent between pages."""
    from routers.projects import list_projects

    try:
        payload = await list_projects()
        return payload.get("projects", [])
    except Exception as exc:  # noqa: BLE001 - rollup must degrade, never fail
        _log.warning("theme rollup: could not list projects: %s", exc)
        return []


def _load_pillars() -> list:
    return enterprise_store.get_org_lists().get("pillars", [])


def _load_task_pillars() -> dict:
    return pillar_store.get_all("tasks")


async def _jira_lane() -> dict:
    """Read-only Jira section: the current user's assigned tickets.

    Reuses the existing atlassian.list_assigned_issues() reader; no new
    Jira query (the spec's DECISION section pins scope to "my tickets"
    until Team Mode lands). Never raises: with zero Atlassian
    configuration, or when Jira errors mid-request, the portfolio
    returns the local rollup with an empty, disconnected section.
    """
    try:
        if not atlassian.is_connected():
            return {"connected": False, "tickets": []}
        tickets = await atlassian.list_assigned_issues()
        return {"connected": True, "tickets": tickets}
    except Exception as exc:  # noqa: BLE001 - degrade, never fail
        _log.warning("theme rollup: Jira section unavailable: %s", exc)
        return {"connected": False, "tickets": []}


@router.get("/themes/rollup")
async def get_theme_rollup() -> dict:
    """Roll tasks into projects and projects into themes, with risk flags.

    Response shape:
      themes: ordered buckets, one per theme plus a final catch-all
              (name null) for untagged work. Each bucket lists project
              rows and task rows with a risk flag each, row counts, and
              the bucket's worst risk.
      jira:   {connected, tickets} for the current user's assigned
              tickets; empty and disconnected when Atlassian is not set
              up or unreachable.
    """
    tasks = await _load_tasks()
    projects = await _load_projects()
    rollup = build_rollup(tasks, projects, _load_pillars(), _load_task_pillars())
    rollup["jira"] = await _jira_lane()
    return rollup

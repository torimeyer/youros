import asyncio
import json
import re as _re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import OSTK_DIR
from services.ostk import ostk, OstkError
from services.labels_store import labels_store
from services.task_labels_store import task_labels_store
from services.task_visibility import (
    is_e2e_task,
    is_session_task,
    is_visible_task,
)
from services.event_bus import bus as _event_bus, DASHBOARD_SNAPSHOT

_SERVER_START = time.time()

router = APIRouter(tags=["dashboard"])


def _is_local_today(ts_str: str, today_str: str) -> bool:
    """Return True if the ISO timestamp falls on the local calendar date.

    Timestamps in the audit log are UTC. A naive ``ts.startswith(today_str)``
    comparison uses the UTC date embedded in the string, which drifts from
    the local date when the user's timezone is behind UTC (e.g. CDT is UTC-5,
    so after 7 PM local the UTC date is already tomorrow). This helper
    converts the timestamp to local time before comparing the date string.
    """
    if not ts_str:
        return False
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d") == today_str
    except (ValueError, TypeError):
        # Fall back to prefix match for malformed timestamps.
        return ts_str[:10] == today_str


# Back-compat alias for tests and any callers that imported the private
# helper from this module. The canonical implementation now lives in
# services.task_visibility.
def _is_session_task(t: dict) -> bool:
    return is_session_task(t)


@router.get("/dashboard")
async def get_dashboard():
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    # Active tasks include both open and in_progress (needle 277+280+283).
    # Session tasks (auto-filed by the SessionStart hook) and e2e smoke
    # leftovers (titles starting with "e2e-") are excluded from user-facing
    # counts and focus so the Dashboard agrees with the Tasks page default
    # view. Everything runs through the shared is_visible_task helper.
    open_tasks = [
        t for t in all_tasks
        if t.get("status") != "closed"
        and not is_session_task(t)
        and not is_e2e_task(t)
    ]
    closed_tasks = [t for t in all_tasks if t.get("status") == "closed"]

    p0 = [t for t in open_tasks if t.get("priority") == "P0"]
    p1 = [t for t in open_tasks if t.get("priority") == "P1"]
    p2 = [t for t in open_tasks if t.get("priority") == "P2"]
    p3 = [t for t in open_tasks if t.get("priority") == "P3"]

    # status/hay fields kept in response for back-compat
    status_result = ""
    hay_result = {"clusters": [], "unclustered": []}

    # Build focus list from ALL visible open tasks, sorted by priority.
    # The open count in the header and this body list must describe the
    # same set of tasks so the widget never reads "N open" with an empty
    # "No focus tasks" body. A task without a P0/P1 label still shows up
    # when it is the only open task the user has. Capped at 4 rows so the
    # widget stays scannable on smaller screens; the "N open" badge tells
    # the user when there is more to see on the Tasks page.
    _priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    sorted_open = sorted(
        open_tasks,
        key=lambda t: _priority_rank.get(t.get("priority") or "", 4),
    )
    focus = []
    for t in sorted_open[:4]:
        focus.append({
            "title": t.get("title", ""),
            "id": t.get("id", ""),
            "priority": t.get("priority") or "P2",
        })

    return {
        "counts": {
            "open": len(open_tasks),
            "closed": len(closed_tasks),
            "p0": len(p0),
            "p1": len(p1),
            "p2": len(p2),
            "p3": len(p3),
        },
        "focus": focus,
        "recent_tasks": [
            {"id": t.get("id"), "title": t.get("title"), "priority": t.get("priority")}
            for t in open_tasks[:5]
        ],
        "hay_count": len(hay_result.get("unclustered", [])) + sum(
            c.get("count", 0) for c in hay_result.get("clusters", [])
        ) if isinstance(hay_result, dict) else 0,
        "ostk_status": status_result if isinstance(status_result, str) else "unavailable",
    }


@router.get("/dashboard/compounds")
async def get_dashboard_compounds():
    """Return the highest-leverage task that unblocks the most other work.

    Uses ``ostk compounds`` to find tasks where finishing one thing
    lets several other tasks move forward. The response includes the
    full sorted list so the UI can show the top recommendation.
    """
    try:
        compounds = await ostk.get_compounds()
    except OstkError:
        compounds = []

    top = compounds[0] if compounds else None

    return {
        "top": top,
        "all": compounds,
    }


@router.get("/dashboard/summary")
async def get_dashboard_summary():
    """Generate a plain-text day summary from today's activity."""
    # Use local time so "today" matches the user's calendar day, not UTC.
    # When it is evening in the US, UTC has already rolled to the next
    # day, which would exclude the entire local morning and afternoon.
    today_str = datetime.now().strftime("%Y-%m-%d")
    bullets: list[str] = []

    # 1. Gather tasks
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    open_tasks = [t for t in all_tasks if t.get("status") == "open"]
    p0_tasks = [t for t in open_tasks if t.get("priority") == "P0"]
    closed_today = [
        t for t in all_tasks
        if t.get("status") == "closed"
        and _is_local_today(t.get("closed_at", ""), today_str)
    ]

    # 2. Read agent activity from audit.jsonl through the shared
    #    parse cache so we do not re-read + re-parse the 400 KB file
    #    on every /api/dashboard request.
    from services.ostk import read_audit_entries
    agents_spawned_today = 0
    agents_completed_today = 0
    for entry in read_audit_entries(OSTK_DIR / "audit.jsonl"):
        ts = entry.get("timestamp", "")
        if not _is_local_today(ts, today_str):
            continue
        ev = entry.get("event", "")
        if ev == "agent.spawned":
            agents_spawned_today += 1
        elif ev in ("agent.completed", "agent.failed"):
            agents_completed_today += 1

    # Build bullets
    if closed_today:
        bullets.append(f"You closed {len(closed_today)} task{'s' if len(closed_today) != 1 else ''} today. Nice work!")
    else:
        bullets.append("No tasks closed yet today.")

    bullets.append(f"{len(open_tasks)} task{'s' if len(open_tasks) != 1 else ''} still open.")

    if p0_tasks:
        # UAT item 5: a single P0 task with a huge multi-line title turned the
        # "Top priority" line into a wall of text. Collapse whitespace, clamp
        # each title on a word boundary, and show at most the top 2 (with a
        # "+N more" tail) so the bullet always stays one tidy line.
        def _clamp_title(raw: str, limit: int = 60) -> str:
            s = " ".join((raw or "Untitled").split())
            if len(s) <= limit:
                return s
            cut = s[:limit].rsplit(" ", 1)[0] or s[:limit]
            return cut.rstrip(".,;:- ") + "…"

        p0_titles = [_clamp_title(t.get("title", "Untitled")) for t in p0_tasks[:2]]
        line = "Top priority: " + ", ".join(p0_titles)
        extra = len(p0_tasks) - len(p0_titles)
        if extra > 0:
            line += f" (+{extra} more)"
        bullets.append(line)

    if agents_spawned_today > 0 or agents_completed_today > 0:
        parts = []
        if agents_spawned_today > 0:
            parts.append(f"{agents_spawned_today} started")
        if agents_completed_today > 0:
            parts.append(f"{agents_completed_today} finished")
        bullets.append(f"Agents today: {', '.join(parts)}.")
    else:
        bullets.append("No agents were used today.")

    return {"bullets": bullets[:5]}


@router.get("/dashboard/insights")
async def get_dashboard_insights():
    """Analyze open tasks across labels and find patterns.

    Returns a list of plain-language insight strings about task
    distribution, staleness, and label performance.
    """
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    open_tasks = [t for t in all_tasks if t.get("status") == "open"]
    closed_tasks = [t for t in all_tasks if t.get("status") == "closed"]

    if not all_tasks:
        return {"insights": ["No tasks found yet. Create some tasks to see insights here."]}

    insights: list[str] = []

    # Build label name lookup
    all_labels = labels_store.list_labels()
    label_names: dict[str, str] = {
        l.get("id", ""): l.get("name", "Unnamed") for l in all_labels
    }
    all_assignments = task_labels_store.get_all_assignments()

    # Count open tasks per label
    label_open_counts: dict[str, int] = {}
    for t in open_tasks:
        tid = t.get("id", "")
        for lid in all_assignments.get(tid, []):
            label_open_counts[lid] = label_open_counts.get(lid, 0) + 1

    if label_open_counts:
        top_label_id = max(label_open_counts, key=lambda k: label_open_counts[k])
        top_name = label_names.get(top_label_id, top_label_id)
        top_count = label_open_counts[top_label_id]
        insights.append(
            f"Most open tasks are in \"{top_name}\" ({top_count} task{'s' if top_count != 1 else ''})."
        )

    # Find which label closes tasks fastest (by average open duration)
    label_durations: dict[str, list[float]] = {}
    now_ts = datetime.now(timezone.utc).timestamp()
    for t in closed_tasks:
        tid = t.get("id", "")
        created = t.get("created_at", "")
        closed = t.get("closed_at", "")
        if not created or not closed:
            continue
        try:
            c_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            d_ts = datetime.fromisoformat(closed.replace("Z", "+00:00")).timestamp()
            duration_days = (d_ts - c_ts) / 86400
        except (ValueError, TypeError):
            continue
        for lid in all_assignments.get(tid, []):
            label_durations.setdefault(lid, []).append(duration_days)

    if label_durations:
        fastest_label = min(
            label_durations,
            key=lambda k: sum(label_durations[k]) / len(label_durations[k]),
        )
        avg_days = sum(label_durations[fastest_label]) / len(label_durations[fastest_label])
        fast_name = label_names.get(fastest_label, fastest_label)
        insights.append(
            f"\"{fast_name}\" tasks close fastest (average {avg_days:.1f} day{'s' if avg_days != 1 else ''})."
        )

    # Find stale open tasks (open > 7 days)
    stale_count = 0
    for t in open_tasks:
        created = t.get("created_at", "")
        if not created:
            continue
        try:
            c_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            if (now_ts - c_ts) > 7 * 86400:
                stale_count += 1
        except (ValueError, TypeError):
            continue

    if stale_count > 0:
        insights.append(
            f"{stale_count} task{'s have' if stale_count != 1 else ' has'} been open for over 7 days."
        )

    # Priority distribution insight
    p0 = sum(1 for t in open_tasks if t.get("priority") == "P0")
    p1 = sum(1 for t in open_tasks if t.get("priority") == "P1")
    p2 = sum(1 for t in open_tasks if t.get("priority") == "P2")
    if p0 > 0 and p0 >= p1:
        insights.append(
            f"You have {p0} urgent (P0) task{'s' if p0 != 1 else ''} right now. Consider focusing there first."
        )

    # Unlabeled tasks
    unlabeled = sum(1 for t in open_tasks if not all_assignments.get(t.get("id", "")))
    if unlabeled > 0 and all_labels:
        insights.append(
            f"{unlabeled} open task{'s' if unlabeled != 1 else ''} {'have' if unlabeled != 1 else 'has'} no labels. Labeling helps spot patterns."
        )

    if not insights:
        insights.append("Everything looks balanced. No standout patterns right now.")

    return {"insights": insights}


@router.get("/dashboard/diff")
async def get_session_diff():
    """Return what changed since the last session boot.

    Calls ``ostk os diff`` and returns structured data about files
    modified, tasks filed, and audit events this session.
    """
    try:
        diff = await ostk.get_session_diff()
    except OstkError:
        diff = {
            "files_changed": [],
            "needles_filed": [],
            "audit_events": [],
            "audit_total": 0,
        }
    return diff


async def _build_dashboard_data() -> dict:
    """Build snapshot of dashboard state: active agents, tasks, system health."""
    from routers.agents import agent_metadata
    from services.agent_filters import is_user_spawned_agent

    agents_count = sum(
        1
        for name, meta in agent_metadata.items()
        if is_user_spawned_agent({"name": name, **meta})
        and meta.get("status") == "running"
    )

    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    open_tasks = [t for t in all_tasks if t.get("status") != "closed" and not is_session_task(t) and not is_e2e_task(t)]

    return {
        "agents_count": agents_count,
        "tasks_count": len(open_tasks),
        "system_uptime": int(time.time() - _SERVER_START),
        "last_sync_at": datetime.now(timezone.utc).isoformat(),
    }


async def _publish_dashboard_state() -> None:
    """Recompute dashboard data and push to WS subscribers. Best-effort."""
    try:
        data = await _build_dashboard_data()
        await _event_bus.publish(DASHBOARD_SNAPSHOT, data)
    except Exception:
        pass


@router.get("/coordination/dashboard")
async def get_coordination_dashboard():
    """Fallback HTTP endpoint for dashboard data when WS is unavailable."""
    return await _build_dashboard_data()


@router.websocket("/ws/dashboard/data")
async def dashboard_data_ws(websocket: WebSocket) -> None:
    """Push dashboard state to clients in real time.

    On connect: sends one snapshot frame with current data.
    On updates: sends fresh snapshot.
    Keepalive ping every 15 seconds.
    """
    await websocket.accept()

    async def _periodic_publish() -> None:
        while True:
            await asyncio.sleep(5)
            await _publish_dashboard_state()

    periodic_task = asyncio.create_task(_periodic_publish())
    try:
        data = await _build_dashboard_data()
        await websocket.send_json({"type": "snapshot", **data})
        # →2946: subscribes to the consolidated bus; only dashboard domain
        # events surface here so the frame shape is unchanged for clients.
        async with _event_bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    if not event.type.startswith("dashboard."):
                        continue
                    await websocket.send_json(
                        {"type": event.type.split(".", 1)[1], **event.payload}
                    )
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        periodic_task.cancel()

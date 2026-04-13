"""Briefing service.

Generates a short daily briefing using Claude whenever the user asks for
it. State lives in ~/.myos/briefing_state.json so it never touches the
repo and survives git pulls.

Also generates structured action items alongside the text briefing. Each
action item has a type, label, action_url, and context so the dashboard
can render one-click buttons.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json
from services.settings_store import settings_store

MYOS_DIR = Path.home() / ".myos"
BRIEFING_STATE_PATH = MYOS_DIR / "briefing_state.json"


def _load_state() -> dict:
    import services.briefing as _self
    state_path: Path = _self.BRIEFING_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    import services.briefing as _self
    state_path: Path = _self.BRIEFING_STATE_PATH
    atomic_write_json(state_path, state)


def _today_str() -> str:
    import services.briefing as _self
    return _self.datetime.now().strftime("%Y-%m-%d")


def should_show_briefing() -> bool:
    """Return True when the briefing should be shown right now.

    Returns False when the briefing setting is disabled or when the user
    has already dismissed today's briefing. There is no time-of-day gate:
    the briefing can be requested at any hour.
    """
    # Respect the setting (defaults to True for new installs via schema default)
    if not settings_store.get("briefing_enabled", True):
        return False

    state = _load_state()
    today = _today_str()

    # Already dismissed today
    if state.get("dismissed_date") == today:
        return False

    # Cached briefing for today exists, serve it
    if state.get("last_shown") == today and state.get("briefing"):
        return True

    # Not shown yet today, show it
    if state.get("last_shown") != today:
        return True

    return False


def get_cached_briefing() -> Optional[str]:
    """Return today's cached briefing text, or None if not yet generated."""
    state = _load_state()
    today = _today_str()
    if state.get("last_shown") == today and state.get("briefing"):
        return state["briefing"]
    return None


def _is_active_task(t: dict) -> bool:
    """A task is 'active' for briefing purposes if it is anything other
    than closed. ostk emits 'open' and 'in_progress' as distinct
    statuses. The briefing used to filter strictly for status == 'open'
    and silently skipped every in_progress P0, so Tori's highest
    priority work was invisible the moment an agent picked it up.
    Regression guard for needle 280. Same rule as Tasks.tsx isActiveTask.
    """
    return t.get("status") != "closed"


async def _task_count_changed() -> bool:
    """Return True if the number of active P0/P1 tasks differs from when the briefing was cached."""
    from services.ostk import ostk, OstkError
    state = _load_state()
    cached_count = state.get("task_count")
    if cached_count is None:
        return False  # no baseline, treat as fresh
    try:
        # Pull every task (no status filter) so in_progress rows still
        # count toward the priority tally. Filtering in Python lets us
        # treat open and in_progress as the same bucket.
        all_tasks = await ostk.list_tasks()
        current = len([
            t for t in all_tasks
            if _is_active_task(t) and t.get("priority") in ("P0", "P1")
        ])
        return current != cached_count
    except OstkError:
        return False


async def generate_briefing() -> str:
    """Generate a briefing using Claude and cache it.

    First calls ostk to get a local activity summary (fast, no LLM cost).
    Then enriches with calendar events, open tasks, and compounds.
    Falls back to the full Claude-only approach if ostk data is empty.
    """
    from services.ostk import ostk, OstkError

    # Gather context pieces
    context_parts: list[str] = []

    # Start with ostk activity summary (local, fast, free)
    try:
        activity_summary = await ostk.get_activity_summary()
        if activity_summary.strip():
            context_parts.append(f"ostk activity summary:\n{activity_summary}")
    except (OstkError, Exception):
        pass

    # Calendar events (best effort)
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import calendar as cal_service
            events = await cal_service.get_today_events()
            if events:
                parts = []
                for ev in events:
                    title = ev.get("summary", "Untitled")
                    start = (ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or "")
                    if start:
                        try:
                            dt = datetime.fromisoformat(start)
                            time_str = dt.strftime("%-I:%M %p").lower()
                            parts.append(f"{time_str}: {title}")
                        except Exception:
                            parts.append(title)
                    else:
                        parts.append(title)
                context_parts.append("Today's meetings: " + ", ".join(parts))
            else:
                context_parts.append("No meetings on your calendar today.")
    except Exception:
        pass

    # Open P0 and P1 tasks, sorted by priority then age (best effort).
    # IMPORTANT: pull every task (no status filter) so in_progress rows
    # still show up. ostk returns both 'open' and 'in_progress' as
    # distinct statuses and the briefing must treat both as active.
    p0p1_count = 0
    try:
        all_tasks = await ostk.list_tasks()
        active_tasks = [t for t in all_tasks if _is_active_task(t)]
        priority_order = {"P0": 0, "P1": 1}
        priority_tasks = sorted(
            [t for t in active_tasks if t.get("priority") in ("P0", "P1")],
            key=lambda t: (
                priority_order.get(t.get("priority", "P1"), 1),
                t.get("created_at", "9999"),  # oldest first
            ),
        )
        p0p1_count = len(priority_tasks)

        # Compute compound scores (which tasks unblock the most).
        # Use the same active-task filter so an in_progress task can
        # still show up as high-leverage.
        open_ids = {t.get("id", "") for t in active_tasks}
        blocks_graph: dict[str, set] = {}
        for t in all_tasks:
            tid = t.get("id", "")
            for bid in (t.get("blocks") or []):
                blocks_graph.setdefault(tid, set()).add(bid)
            for did in (t.get("depends_on") or []):
                blocks_graph.setdefault(did, set()).add(tid)
        compound_scores: dict[str, int] = {}
        for tid in open_ids:
            visited: set = set()
            queue = list(blocks_graph.get(tid, set()))
            while queue:
                nid = queue.pop(0)
                if nid in visited or nid == tid:
                    continue
                visited.add(nid)
                if nid in open_ids:
                    queue.extend(blocks_graph.get(nid, set()))
            count = len(visited & open_ids)
            if count > 0:
                compound_scores[tid] = count

        if priority_tasks:
            today = datetime.now(timezone.utc)
            task_lines = []
            for t in priority_tasks[:10]:
                age = ""
                created = t.get("created_at", "")
                if created:
                    try:
                        days = (today - datetime.fromisoformat(created)).days
                        age = f" (open {days}d)" if days > 0 else " (new today)"
                    except Exception:
                        pass
                unblocks = compound_scores.get(t.get("id", ""), 0)
                unblocks_str = f" [unblocks {unblocks} tasks]" if unblocks > 0 else ""
                task_lines.append(
                    f"  [{t.get('priority')}] {t.get('title', 'Untitled')}{age}{unblocks_str}"
                )
            context_parts.append("Top open tasks (sorted by priority, then oldest first):\n" + "\n".join(task_lines))
        else:
            context_parts.append("No high-priority tasks open right now.")
    except OstkError:
        pass

    # High-leverage tasks (compounds) that unblock the most other work
    try:
        compounds = await ostk.get_compounds()
        if compounds:
            top = compounds[0]
            context_parts.append(
                f"Highest-leverage task: \"{top.get('title', 'Untitled')}\" "
                f"(finishing it unblocks {top.get('blocks_count', 0)} other "
                f"{'task' if top.get('blocks_count', 0) == 1 else 'tasks'})"
            )
    except OstkError:
        pass

    # Yesterday's activity (closed tasks, agents run)
    try:
        from datetime import timedelta
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        all_tasks_full = await ostk.list_tasks()
        closed_yesterday = [
            t for t in all_tasks_full
            if t.get("status") == "closed"
            and (t.get("closed_at", "") or "").startswith(yesterday_str)
        ]
        if closed_yesterday:
            context_parts.append(
                f"Yesterday you closed {len(closed_yesterday)} task(s): "
                + ", ".join(t.get("title", "Untitled") for t in closed_yesterday[:3])
            )
        else:
            context_parts.append("No tasks were closed yesterday.")
    except Exception:
        pass

    context_block = "\n\n".join(context_parts) if context_parts else "No context available."

    today_display = datetime.now().strftime("%A, %B %-d")
    prompt = (
        f"Today is {today_display}. Here is the user's workspace context:\n\n"
        f"{context_block}\n\n"
        "Write a short briefing in 2 short paragraphs, separated by blank lines. "
        "Plain factual language, no jargon, no bullet points, no motivational phrases.\n\n"
        "Paragraph 1: What is on the calendar today (if anything) and a quick "
        "recap of what was completed yesterday.\n\n"
        "Paragraph 2: The single most important task to work on and why. "
        "Use these rules to pick it: if the context includes a highest-leverage task "
        "that unblocks multiple other tasks, that is the top recommendation regardless "
        "of priority. Otherwise, P0 always beats P1. Among the same priority, "
        "the task that has been open the longest is the most important. If a task is "
        "a bug that users are hitting, it beats a feature request at the same priority "
        "and age. Name the specific task.\n\n"
        "Keep it concise and factual. Do not use em-dashes. Do not use encouraging or "
        "motivational language. Do not assume a time of day, so avoid phrases like "
        "good morning, this morning, or your morning."
    )

    briefing = await _call_claude(prompt)

    # Cache it
    state = _load_state()
    state["last_shown"] = _today_str()
    state["briefing"] = briefing
    state["task_count"] = p0p1_count
    state.pop("dismissed_date", None)
    _save_state(state)

    return briefing


async def _call_claude(prompt: str) -> str:
    """Make a single non-streaming call to Claude and return the text response."""
    messages = [{"role": "user", "content": prompt}]

    # Try the local claude CLI for a non-streaming call
    from services import claude_code_provider
    if await claude_code_provider.is_claude_code_available():
        try:
            result = await _call_via_cli(prompt)
            if result:
                return result
        except Exception:
            pass

    # Fall back to Anthropic API key
    api_key = ""
    try:
        from services.ostk import ostk
        api_key = await ostk.secret_get("ANTHROPIC_API_KEY") or ""
    except Exception:
        pass

    if not api_key:
        api_key = settings_store.get("anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        return (
            "Your workspace is ready. "
            "Check your top tasks and have a great day."
        )

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=messages,
    )
    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    return " ".join(text_blocks).strip()


async def _call_via_cli(prompt: str) -> str:
    """Use the local claude CLI binary for a non-streaming single call."""
    import asyncio
    import shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        return ""

    from services.claude_code_provider import _build_subprocess_env

    env = _build_subprocess_env()
    args = [
        claude_path,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        "sonnet",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""

    text = stdout.decode("utf-8", errors="replace").strip()
    # claude -p --output-format json returns a JSON object with a "result" field
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result = data.get("result") or data.get("content") or ""
            if isinstance(result, str):
                return result.strip()
            if isinstance(result, list):
                parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
                return " ".join(parts).strip()
    except (json.JSONDecodeError, AttributeError):
        pass

    return text


def dismiss_briefing() -> None:
    """Mark today's briefing as dismissed so it does not show again today."""
    state = _load_state()
    state["dismissed_date"] = _today_str()
    _save_state(state)


def get_cached_action_items() -> list[dict] | None:
    """Return cached action items, or None if not yet generated.

    Action items are generated alongside the briefing. They are valid
    as long as they exist in state. They get refreshed on the next
    briefing generation cycle.
    """
    state = _load_state()
    return state.get("action_items")


async def generate_action_items() -> list[dict]:
    """Generate structured action items from tasks, emails, and calendar.

    Each item has: type, label, action_url, context.
    Types: reply_email, close_task, prep_meeting, review_agent.
    """
    items: list[dict] = []

    # 1. Overdue or stale tasks that could be closed
    try:
        from services.ostk import ostk, OstkError
        all_tasks = await ostk.list_tasks()
        today = datetime.now(timezone.utc)
        for t in all_tasks:
            if t.get("status") == "closed":
                continue
            task_id = t.get("id", "")
            title = t.get("title", "Untitled")
            priority = t.get("priority", "")
            created = t.get("created_at", "")
            if created:
                try:
                    days_open = (today - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
                except Exception:
                    days_open = 0
            else:
                days_open = 0
            # Suggest closing tasks that are P0/P1 and open > 14 days
            if priority in ("P0", "P1") and days_open > 14:
                items.append({
                    "type": "close_task",
                    "label": f"Review: {title}",
                    "action_url": f"/api/tasks/{task_id}",
                    "context": f"This {priority} task has been open for {days_open} days. Check if it can be closed.",
                })
                if len(items) >= 5:
                    break
    except Exception:
        pass

    # 2. Unread emails that might need a reply
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import gmail as gmail_service
            unread = await gmail_service.get_unread_summary()
            for msg in unread[:3]:
                subject = msg.get("subject", "No subject")
                sender = msg.get("from", "Unknown sender")
                msg_id = msg.get("id", "")
                items.append({
                    "type": "reply_email",
                    "label": f"Reply to: {subject[:60]}",
                    "action_url": f"/gmail?thread={msg_id}",
                    "context": f"From {sender}.",
                })
    except Exception:
        pass

    # 3. Upcoming meetings that might need prep
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import calendar as cal_service
            events = await cal_service.get_today_events()
            now = datetime.now(timezone.utc)
            for ev in events[:2]:
                summary = ev.get("summary", "Untitled meeting")
                start_str = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or ""
                if start_str:
                    try:
                        start_dt = datetime.fromisoformat(start_str)
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=timezone.utc)
                        # Only suggest prep for meetings that have not started yet
                        if start_dt > now:
                            time_label = start_dt.strftime("%-I:%M %p").lower()
                            items.append({
                                "type": "prep_meeting",
                                "label": f"Prep for: {summary[:50]}",
                                "action_url": "/calendar",
                                "context": f"Starts at {time_label}.",
                            })
                    except Exception:
                        pass
    except Exception:
        pass

    # 4. Check for recent agent runs that need review
    try:
        from services import agent_patterns
        runs = agent_patterns.analyze_runs()
        failed_runs = [r for r in runs if r["status"] == "failed"][:2]
        for run in failed_runs:
            items.append({
                "type": "review_agent",
                "label": f"Review failed: {run['name'][:40]}",
                "action_url": "/agents",
                "context": f"Agent {run['name']} failed. Check the transcript.",
            })
    except Exception:
        pass

    # Cap at 6 items total so the dashboard stays clean
    items = items[:6]

    # Cache results
    state = _load_state()
    state["action_items"] = items
    _save_state(state)

    return items

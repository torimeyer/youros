"""Briefing service.

Generates a short daily briefing using Claude whenever the user asks for
it. State lives in ~/.myos/briefing_state.json so it never touches the
repo and survives git pulls.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


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


async def generate_briefing() -> str:
    """Generate a briefing using Claude and cache it.

    Pulls in today's calendar events, open P0/P1 tasks, and yesterday's
    activity summary. Returns a 3-5 sentence plain-language briefing.
    """
    from services.ostk import ostk, OstkError

    # Gather context pieces
    context_parts: list[str] = []

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

    # Open P0 and P1 tasks, sorted by priority then age (best effort)
    try:
        all_tasks = await ostk.list_tasks(status="open")
        priority_order = {"P0": 0, "P1": 1}
        priority_tasks = sorted(
            [t for t in all_tasks if t.get("priority") in ("P0", "P1")],
            key=lambda t: (
                priority_order.get(t.get("priority", "P1"), 1),
                t.get("created_at", "9999"),  # oldest first
            ),
        )
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
                task_lines.append(
                    f"  [{t.get('priority')}] {t.get('title', 'Untitled')}{age}"
                )
            context_parts.append("Top open tasks (sorted by priority, then oldest first):\n" + "\n".join(task_lines))
        else:
            context_parts.append("No high-priority tasks open right now.")
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
        "Write a short briefing (3-5 sentences, plain conversational language, "
        "no jargon, no bullet points). Mention what is on the calendar if anything. "
        "Highlight the single most important task to work on using these rules: "
        "P0 always beats P1. Among the same priority, the task that has been open "
        "the longest is the most important (it is overdue). If a task is a bug that "
        "users are hitting, it beats a feature request at the same priority and age. "
        "Name the specific task you picked. Give one encouraging note. "
        "Keep it warm and brief. Do not use em-dashes. Do not assume a time of day, "
        "so avoid phrases like good morning, this morning, or your morning."
    )

    briefing = await _call_claude(prompt)

    # Cache it
    state = _load_state()
    state["last_shown"] = _today_str()
    state["briefing"] = briefing
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

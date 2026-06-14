"""Meeting prep service.

Given a calendar event, generates a short briefing by pulling relevant
Drive files, open P0/P1 tasks, and asking Claude to summarize.

Cache lives in ~/.youros/meeting_prep_cache/ -- never inside the repo.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
PREP_CACHE_DIR = MYOS_DIR / "meeting_prep_cache"


def _ensure_dirs() -> None:
    PREP_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(event_id: str) -> Path:
    # Sanitize event_id so it is safe as a filename.
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", event_id)
    return PREP_CACHE_DIR / f"{safe}.txt"


def _load_cache(event_id: str, event_start_iso: str) -> Optional[str]:
    """Return cached briefing if it still exists and the meeting has not started."""
    path = _cache_path(event_id)
    if not path.exists():
        return None
    # Invalidate when the meeting has started.
    try:
        start_dt = datetime.fromisoformat(event_start_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if datetime.now(tz=timezone.utc) >= start_dt:
            return None
    except Exception:
        pass
    return path.read_text()


def _save_cache(event_id: str, text: str) -> None:
    _ensure_dirs()
    _cache_path(event_id).write_text(text)


def _minutes_until(event_start_iso: str) -> int:
    """Return minutes until the event starts (negative if already started)."""
    try:
        start_dt = datetime.fromisoformat(event_start_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        delta = start_dt - datetime.now(tz=timezone.utc)
        return int(delta.total_seconds() / 60)
    except Exception:
        return 0


def _keywords_from_title(title: str) -> list[str]:
    """Return meaningful keywords from an event title."""
    stop = {
        "a", "an", "the", "and", "or", "of", "in", "on", "for",
        "with", "to", "at", "from", "by", "is", "are", "be",
        "meeting", "call", "sync", "check", "review", "discussion",
        "prep", "intro", "catch", "up",
    }
    words = re.findall(r"[a-zA-Z0-9]+", title)
    return [w for w in words if w.lower() not in stop and len(w) > 2]


async def _fetch_drive_files(keyword: str) -> list[dict]:
    """Search Drive for files matching keyword. Returns empty list on any error."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from routers.drive import _fetch_drive_files as _drive_search
        return await _drive_search(q=keyword)
    except Exception:
        return []


async def _fetch_open_tasks(priorities: list[str] | None = None) -> list[dict]:
    """Return open tasks filtered to the given priorities."""
    try:
        from services.ostk import ostk
        tasks = await ostk.list_tasks(status="open")
        if priorities:
            tasks = [t for t in tasks if t.get("priority") in priorities]
        return tasks
    except Exception:
        return []


async def _call_claude(prompt: str) -> str:
    """Call Claude non-streaming and return the text.

    Uses the Anthropic API key path when available, otherwise tries the
    local claude binary as a subprocess (non-streaming, -p flag).
    """
    # Try API key first.
    from services.ostk_secrets import get_anthropic_key
    api_key = await get_anthropic_key()

    if api_key:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception:
            pass

    # Fall back to local claude binary.
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            env = {k: v for k, v in os.environ.items() if k not in {
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY"
            }}
            proc = await asyncio.create_subprocess_exec(
                claude_bin, "-p", prompt, "--model", "sonnet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            return stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            pass

    return "Could not generate a briefing right now. No Claude connection available."


async def generate_prep(event: dict) -> str:
    """Generate a meeting prep briefing for the given calendar event.

    Steps:
    1. Search Drive for files related to the event title.
    2. Fetch open P0/P1 tasks.
    3. Ask Claude to write a 3-5 sentence prep brief.
    4. Cache and return the result.

    Returns cached text if a valid cache entry exists.
    """
    event_id = event.get("id", "unknown")
    title = event.get("summary") or "Untitled meeting"
    start_iso = (
        event.get("start", {}).get("dateTime")
        or event.get("start", {}).get("date")
        or ""
    )

    # Cache hit.
    cached = _load_cache(event_id, start_iso)
    if cached:
        return cached

    minutes = _minutes_until(start_iso)
    time_str = f"in {minutes} minutes" if minutes > 0 else "starting now"

    attendees: list[str] = [
        a.get("email", a.get("displayName", ""))
        for a in event.get("attendees", [])
        if a.get("email") or a.get("displayName")
    ]

    # Drive search: use first meaningful keyword from the title.
    keywords = _keywords_from_title(title)
    drive_files: list[dict] = []
    if keywords:
        files = await _fetch_drive_files(keywords[0])
        drive_files = files[:5]  # keep it short

    # Tasks.
    tasks = await _fetch_open_tasks(priorities=["P0", "P1"])

    # Build prompt.
    file_lines = (
        "\n".join(f"- {f.get('name', 'Unnamed')}" for f in drive_files)
        if drive_files
        else "None found."
    )
    task_lines = (
        "\n".join(f"- [{t.get('priority')}] {t.get('title', '')}" for t in tasks[:10])
        if tasks
        else "None."
    )
    attendee_str = ", ".join(attendees) if attendees else "no attendees listed"

    prompt = (
        f"I have a meeting called '{title}' {time_str} with {attendee_str}.\n\n"
        f"Relevant Drive files:\n{file_lines}\n\n"
        f"My open P0/P1 tasks:\n{task_lines}\n\n"
        "Write a 3-5 sentence meeting prep brief. "
        "Focus on what I should know going in, any open tasks that might come up, "
        "and what I might want to have ready. "
        "Plain language, no jargon, no em-dashes."
    )

    briefing = await _call_claude(prompt)
    _save_cache(event_id, briefing)
    return briefing

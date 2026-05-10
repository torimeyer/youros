"""Smart email triage service.

Reads unread Gmail, classifies each message against the user's current tasks
and calendar, and returns categorized results with suggested actions.
Cache lives at ~/.myos/gmail_cache/triage_result.json -- never inside the repo.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from services.atomic_io import atomic_write_text
from services.briefing import is_actionable_for_reply
from services.task_source_store import task_source_store

MYOS_DIR = Path.home() / ".myos"
GMAIL_CACHE_DIR = MYOS_DIR / "gmail_cache"
TRIAGE_CACHE_PATH = GMAIL_CACHE_DIR / "triage_result.json"

_TRIAGE_CACHE_TTL = 300  # 5 minutes
_TRIAGE_MODEL = "claude-sonnet-4-20250514"

CATEGORIES = ("action_needed", "task_worthy", "informational", "noise")


def _ensure_dirs() -> None:
    GMAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_triage_cache() -> Optional[dict]:
    if not TRIAGE_CACHE_PATH.exists():
        return None
    age = time.time() - TRIAGE_CACHE_PATH.stat().st_mtime
    if age > _TRIAGE_CACHE_TTL:
        return None
    try:
        data = json.loads(TRIAGE_CACHE_PATH.read_text())
        return data if data else None
    except Exception:
        return None


def _save_triage_cache(result: dict) -> None:
    _ensure_dirs()
    atomic_write_text(TRIAGE_CACHE_PATH, json.dumps(result))


def _load_last_triage() -> Optional[dict]:
    """Load triage result regardless of TTL (for the /last endpoint)."""
    if not TRIAGE_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(TRIAGE_CACHE_PATH.read_text())
        return data if data else None
    except Exception:
        return None


async def _get_ostk_tasks() -> list:
    """Fetch open tasks via ostk. Returns empty list on failure."""
    try:
        from services.ostk import ostk
        return await ostk.list_tasks(status="open")
    except Exception:
        return []


async def _get_calendar_events() -> list:
    """Fetch today's calendar events. Returns empty list on failure."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services import calendar as cal_service
        return await cal_service.get_today_events() or []
    except Exception:
        return []


def _build_context_summary(tasks: list, events: list) -> str:
    """Build a compact context string for the Claude prompt."""
    parts = []
    if tasks:
        task_lines = [
            f"- {t.get('title', 'Untitled')} ({t.get('priority', 'P2')})"
            for t in tasks[:10]
        ]
        parts.append("Open tasks:\n" + "\n".join(task_lines))
    if events:
        event_lines = []
        for ev in events[:5]:
            title = ev.get("summary", "Untitled")
            start = (
                ev.get("start", {}).get("dateTime")
                or ev.get("start", {}).get("date")
                or ""
            )
            event_lines.append(f"- {title}" + (f" at {start}" if start else ""))
        parts.append("Today's calendar:\n" + "\n".join(event_lines))
    return "\n\n".join(parts) if parts else "No current tasks or meetings."


def _build_triage_prompt(messages: list, context: str) -> str:
    """Build the batch classification prompt."""
    msg_lines = []
    for i, msg in enumerate(messages):
        sender = msg.get("from_name") or msg.get("from_email", "unknown")
        email = msg.get("from_email", "")
        subject = msg.get("subject", "(no subject)")
        snippet = (msg.get("snippet") or "")[:200]
        msg_lines.append(
            f"[{i}] From: {sender} <{email}>\n"
            f"    Subject: {subject}\n"
            f"    Snippet: {snippet}"
        )

    messages_block = "\n\n".join(msg_lines)

    return (
        "You are a smart email triage assistant. Classify each email below into exactly one category.\n\n"
        f"Context about the user's current work:\n{context}\n\n"
        f"Emails to classify:\n{messages_block}\n\n"
        "Return a JSON array with one object per email, same order as input. "
        "Each object must have:\n"
        '- "index": the email number (0-based)\n'
        '- "category": one of "action_needed", "task_worthy", "informational", "noise"\n'
        '- "reason": one short sentence explaining the classification\n\n'
        "Categories:\n"
        "- action_needed: a real human sent this and expects a reply or decision\n"
        "- task_worthy: contains a clear action item related to the user's work\n"
        "- informational: useful to know but no reply needed (updates, FYI notes)\n"
        "- noise: marketing, automated notifications, shipping alerts, receipts\n\n"
        'If "task_worthy", also include:\n'
        '- "task_title": a short task title under 80 characters\n'
        '- "task_description": one sentence describing what to do\n\n'
        "Return ONLY the JSON array, no other text."
    )


async def _call_claude_batch(prompt: str) -> Optional[list]:
    """Send the batch triage prompt to Claude. Returns a parsed list or None."""
    try:
        from services.chat_providers import _resolve_api_key
    except ImportError:
        return None

    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=_TRIAGE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return None

    text = ""
    for block in response.content or []:
        if getattr(block, "type", "") == "text":
            text = getattr(block, "text", "")
            break

    if not text:
        return None

    text = text.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner)

    try:
        return json.loads(text)
    except Exception:
        return None


async def triage_inbox() -> dict:
    """Classify unread inbox messages and return categorized results.

    Returns a dict with:
      - ok: bool
      - messages: list of classified message dicts
      - summary: counts per category
      - triaged_at: ISO timestamp
    """
    from services import gmail as gmail_service
    from services.google_auth import is_authenticated

    if not is_authenticated():
        return {
            "ok": False,
            "error": "Gmail is not connected.",
            "messages": [],
            "summary": {cat: 0 for cat in CATEGORIES},
        }

    # Fetch unread messages and context in parallel.
    unread_msgs, tasks, events = await asyncio.gather(
        gmail_service.get_unread_summary(max_results=20),
        _get_ostk_tasks(),
        _get_calendar_events(),
    )

    if not unread_msgs:
        result = {
            "ok": True,
            "messages": [],
            "summary": {cat: 0 for cat in CATEGORIES},
            "triaged_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_triage_cache(result)
        return result

    # Pre-filter: flag which messages pass the sophisticated no-reply /
    # transactional filter. Claude can still classify freely, but we will
    # downgrade any "action_needed" verdict for messages that fail this
    # filter to "informational" rather than surfacing a reply card.
    actionable_ids = {
        msg["id"] for msg in unread_msgs if is_actionable_for_reply(msg)
    }

    context = _build_context_summary(tasks, events)
    prompt = _build_triage_prompt(unread_msgs, context)
    classifications = await _call_claude_batch(prompt)

    # Build the output list.
    classified_messages = []
    summary = {cat: 0 for cat in CATEGORIES}

    for i, msg in enumerate(unread_msgs):
        # Find Claude's classification for this index.
        classification = None
        if classifications:
            for c in classifications:
                if isinstance(c, dict) and c.get("index") == i:
                    classification = c
                    break

        category = (classification or {}).get("category", "informational")

        # Honour the pre-filter: if the message cannot plausibly need a
        # human reply, don't surface it as action_needed.
        if category == "action_needed" and msg["id"] not in actionable_ids:
            category = "informational"

        if category not in CATEGORIES:
            category = "informational"

        summary[category] = summary.get(category, 0) + 1

        classified = {
            **msg,
            "category": category,
            "reason": (classification or {}).get("reason", ""),
        }

        if category == "task_worthy" and classification:
            classified["task_title"] = classification.get(
                "task_title", f"Follow up: {msg.get('subject', '')}"
            )
            classified["task_description"] = classification.get("task_description", "")

        classified_messages.append(classified)

    result = {
        "ok": True,
        "messages": classified_messages,
        "summary": summary,
        "triaged_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_triage_cache(result)
    return result


async def create_task_from_email(msg: dict) -> dict:
    """Create an ostk task from a task_worthy classified message."""
    from services.ostk import ostk, OstkError
    from services.task_labeling import schedule_auto_labels, extract_task_id

    message_id = msg.get("id", "")
    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{message_id}" if message_id else ""

    title = msg.get("task_title") or f"Follow up: {msg.get('subject', 'email')}"
    base_description = msg.get("task_description") or (
        f"From {msg.get('from_name') or msg.get('from_email', 'unknown')}: "
        f"{msg.get('snippet', '')}"
    )
    description = f"{gmail_link}\n\n{base_description}" if gmail_link else base_description

    try:
        add_result = await ostk.add_task(title, "P1", description=description)
    except OstkError as exc:
        return {"ok": False, "error": str(exc)}

    task_id = extract_task_id(add_result)
    schedule_auto_labels(task_id, title, description)
    if task_id and message_id:
        task_source_store.set_source(task_id, "email", message_id)
    return {"ok": True, "task_id": task_id, "title": title}


async def archive_message(message_id: str) -> dict:
    """Mark a message as read (archive it from the unread view)."""
    try:
        from services import gmail as gmail_service
        await gmail_service.mark_read(message_id)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

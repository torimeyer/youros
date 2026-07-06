"""Auto-fix handlers for task health issues.

Handler registry maps issue_type -> async handler(task_id, task) -> dict.
Each handler returns {"fixed": True, "details": "..."} or {"fixed": False, "reason": "..."}.

Supported issue types at launch:
  no_description  Generate a 1-2 sentence plain-language description using Claude.
  no_labels       Apply auto-labels via the existing schedule_auto_labels path.
  no_priority     Set priority to P2 if not already set.
  *               Everything else returns {"fixable": False}.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional
import json

import anthropic

from services.chat_providers import _resolve_api_key
from services.ostk import ostk, OstkError
from services.task_labeling import apply_auto_labels

logger = logging.getLogger(__name__)

_DESCRIPTION_MODEL = "claude-sonnet-4-6"


async def _generate_description(title: str) -> Optional[str]:
    """Call Claude to generate a 1-2 sentence plain-language description from a task title.

    Returns the generated description string, or None if no API key is available
    or the call fails.
    """
    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return None
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system = (
        "You are a task description writer. Write a clear, plain-language description "
        "for the given task title. Use 1-2 sentences. No jargon. No em-dashes. "
        "Be specific and actionable. Return only the description text, nothing else."
    )
    user = f"Task title: {title.strip()}"
    try:
        response = await client.messages.create(
            model=_DESCRIPTION_MODEL,
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("task_autofix description generation error: %s", exc)
        return None
    if not response.content:
        return None
    for block in response.content:
        if getattr(block, "type", "") == "text":
            return (getattr(block, "text", "") or "").strip() or None
    return None


async def _save_description(task_id: str, description: str) -> None:
    """Write description into issues.jsonl for the given task ID."""
    issues_path = Path(ostk.cwd) / ".ostk" / "needles" / "issues.jsonl"
    if not issues_path.exists():
        raise OstkError("issues.jsonl not found")
    lines = issues_path.read_text().strip().splitlines()
    found = False
    updated = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("id") == task_id:
            entry["description"] = description
            found = True
        updated.append(json.dumps(entry, ensure_ascii=False))
    if not found:
        raise OstkError(f"task '{task_id}' not found in issues.jsonl")
    issues_path.write_text("\n".join(updated) + "\n")


# --- individual handlers ---

async def _fix_no_description(task_id: str, task: dict) -> dict:
    """Generate a description from the task title and save it."""
    title = (task.get("title") or "").strip()
    if not title:
        return {"fixed": False, "reason": "task has no title to generate description from"}
    desc = await _generate_description(title)
    if not desc:
        return {"fixed": False, "reason": "no Anthropic API key configured or generation failed"}
    try:
        await _save_description(task_id, desc)
    except OstkError as exc:
        return {"fixed": False, "reason": str(exc)}
    return {"fixed": True, "details": f"Added description: {desc}"}


async def _fix_no_labels(task_id: str, task: dict) -> dict:
    """Apply auto-labels to the task."""
    title = (task.get("title") or "").strip()
    description = (task.get("description") or "").strip()
    try:
        await asyncio.wait_for(
            apply_auto_labels(task_id, title, description),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        return {"fixed": False, "reason": "label generation timed out"}
    except Exception as exc:
        return {"fixed": False, "reason": str(exc)}
    return {"fixed": True, "details": "Auto-labels applied"}


async def _fix_no_priority(task_id: str, task: dict) -> dict:
    """Default to P2 if no priority is set."""
    current = (task.get("priority") or "").strip()
    if current:
        return {"fixed": False, "reason": f"task already has priority {current}"}
    try:
        await ostk.update_task_priority(task_id, "P2")
    except OstkError as exc:
        return {"fixed": False, "reason": str(exc)}
    return {"fixed": True, "details": "Priority set to P2"}


async def _fix_stub(task_id: str, task: dict) -> dict:
    """Stub for issue types with no auto-fix yet."""
    return {"fixable": False}


# Registry: issue_type -> handler
_REGISTRY: dict[str, Any] = {
    "no_description": _fix_no_description,
    "no_labels": _fix_no_labels,
    "no_priority": _fix_no_priority,
}


async def fix_issue(issue_type: str, task_id: str, task: dict) -> dict:
    """Dispatch to the registered handler for the given issue type.

    Returns the handler's result dict. Unknown issue types return {"fixable": False}.
    """
    handler = _REGISTRY.get(issue_type, _fix_stub)
    return await handler(task_id, task)

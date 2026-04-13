"""Cross-integration context linker.

When a task is viewed, scans recent emails (Gmail), upcoming events
(Calendar), and Drive files for content related to the task by keyword
matching against the task title and description.

Results are cached in memory with a 5-minute TTL so repeated views
do not hammer the integrations. Cache lives in-process only (not on
disk) because the data is ephemeral and cheap to regenerate.
"""

from __future__ import annotations

import re
import time
from typing import Optional


# In-memory cache: task_id -> (timestamp, result)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_get(task_id: str) -> Optional[dict]:
    entry = _cache.get(task_id)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _cache[task_id]
        return None
    return data


def _cache_set(task_id: str, data: dict) -> None:
    _cache[task_id] = (time.time(), data)


def _extract_keywords(title: str, description: str = "") -> list[str]:
    """Extract meaningful keywords from a task title and description.

    Drops common stop words and short tokens so matching is useful.
    """
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "it", "this", "that",
        "be", "as", "are", "was", "were", "been", "has", "have", "had",
        "do", "does", "did", "not", "no", "will", "would", "could",
        "should", "may", "might", "can", "shall", "need", "must",
        "add", "fix", "update", "create", "make", "set", "get",
    }
    text = f"{title} {description}".lower()
    # Split on non-alphanumeric characters
    tokens = re.findall(r"[a-z0-9]+", text)
    # Keep tokens that are at least 3 chars and not stop words
    keywords = [t for t in tokens if len(t) >= 3 and t not in stop_words]
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:10]  # cap at 10 keywords


def _text_matches(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in the text. Higher = better match."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw in lower)


async def get_linked_context(
    task_id: str,
    title: str,
    description: str = "",
) -> dict:
    """Return emails, events, and files related to the given task.

    Shape::

        {
            "emails": [{"id": str, "subject": str, "from": str, "snippet": str}],
            "events": [{"id": str, "summary": str, "start": str}],
            "files": [{"id": str, "name": str, "mimeType": str, "webViewLink": str}],
        }
    """
    cached = _cache_get(task_id)
    if cached is not None:
        return cached

    keywords = _extract_keywords(title, description)
    if not keywords:
        empty: dict = {"emails": [], "events": [], "files": []}
        _cache_set(task_id, empty)
        return empty

    result: dict = {"emails": [], "events": [], "files": []}

    # 1. Scan recent emails
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import gmail as gmail_service
            messages = await gmail_service.get_unread_summary()
            for msg in messages:
                text = f"{msg.get('subject', '')} {msg.get('snippet', '')} {msg.get('from', '')}"
                score = _text_matches(text, keywords)
                if score >= 2:
                    result["emails"].append({
                        "id": msg.get("id", ""),
                        "subject": msg.get("subject", "No subject"),
                        "from": msg.get("from", ""),
                        "snippet": (msg.get("snippet", "") or "")[:120],
                    })
            result["emails"] = result["emails"][:5]
    except Exception:
        pass

    # 2. Scan upcoming calendar events
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import calendar as cal_service
            events = await cal_service.get_upcoming_events(days=7)
            for ev in events:
                text = f"{ev.get('summary', '')} {ev.get('location', '')}"
                score = _text_matches(text, keywords)
                if score >= 1:
                    start_str = (
                        ev.get("start", {}).get("dateTime")
                        or ev.get("start", {}).get("date")
                        or ""
                    )
                    result["events"].append({
                        "id": ev.get("id", ""),
                        "summary": ev.get("summary", "Untitled"),
                        "start": start_str,
                    })
            result["events"] = result["events"][:5]
    except Exception:
        pass

    # 3. Scan Drive files (use cached file list if available)
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from routers.drive import _load_file_list_cache
            files = _load_file_list_cache(allow_stale=True)
            if files:
                for f in files:
                    text = f.get("name", "")
                    score = _text_matches(text, keywords)
                    if score >= 1:
                        result["files"].append({
                            "id": f.get("id", ""),
                            "name": f.get("name", "Untitled"),
                            "mimeType": f.get("mimeType", ""),
                            "webViewLink": f.get("webViewLink", ""),
                        })
                result["files"] = result["files"][:5]
    except Exception:
        pass

    _cache_set(task_id, result)
    return result

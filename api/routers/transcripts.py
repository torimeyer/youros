"""Transcripts router: reads Claude Code session files from disk."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.atomic_io import atomic_write_json
from services import session_task_map

router = APIRouter(tags=["transcripts"])

# Patterns that identify mailbox/registration boilerplate lines.
# A first-message line matching any of these is skipped when deriving a title.
_MAILBOX_BOILERPLATE_RE = re.compile(
    r"(?i)"
    r"(##\s*(agent\s+registration|mailbox)|"
    r"register\s+immediately|"
    r"step\s+0\s*:|"
    r"curl.*agents/register|"
    r"heartbeat.*every|"
    r"mailbox\s+check(?:ing)?|"
    r"nudge.*poll|"
    r"post\s*/api/agents)"
)

MYOS_DIR = Path.home() / ".myos"
TITLE_CACHE_PATH = MYOS_DIR / "transcript_titles.json"


def _load_title_cache() -> dict[str, str]:
    try:
        return json.loads(TITLE_CACHE_PATH.read_text()) if TITLE_CACHE_PATH.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_title_cache(cache: dict[str, str]) -> None:
    # Atomic to avoid a crash mid-save wiping the title cache.
    atomic_write_json(TITLE_CACHE_PATH, cache)


async def _save_title_cache_async(cache: dict[str, str]) -> None:
    """Thread-offloaded title cache write so the async title generator
    does not block the event loop on the fsync. Used from
    :func:`_generate_titles_background` which runs from the
    /api/transcripts handler.
    """
    await asyncio.to_thread(_save_title_cache, cache)

# Claude Code stores session index files and transcript JSONL files in these locations.
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# The torios project transcript folder. Claude Code uses the absolute path
# with slashes replaced by dashes as the folder name.
from config import PROJECT_ROOT

# Compute the Claude Code project folder name from the actual project root.
TORIOS_PROJECT_DIR = PROJECTS_DIR / str(PROJECT_ROOT).replace("/", "-").lstrip("-")


def _skip_mailbox_boilerplate(text: str) -> str:
    """Return the meaningful portion of a prompt text, skipping mailbox/registration blocks.

    Strategy:
    1. Locate the '## Agent registration' heading.
    2. Scan forward for the NEXT top-level '##' heading that is NOT itself
       a registration/mailbox heading. Everything under that heading is the
       real task text.
    3. Return the first non-empty non-code prose line from that section.
    4. If no such second heading exists but there is text after all the
       boilerplate lines are exhausted, return that text.
    5. If the input has no registration heading at all, return it unchanged.
    """
    lines = text.splitlines()

    # Find the line index of the mailbox block start
    mailbox_start: Optional[int] = None
    for i, line in enumerate(lines):
        if re.match(r"(?i)^##\s*agent\s+registration", line.strip()):
            mailbox_start = i
            break

    if mailbox_start is None:
        return text

    # Scan lines after the mailbox heading for the next top-level ## heading
    # that is NOT itself mailbox boilerplate.
    for i in range(mailbox_start + 1, len(lines)):
        line = lines[i].strip()
        if re.match(r"^##\s+", line) and not _MAILBOX_BOILERPLATE_RE.search(line):
            # Found the real task section. Return its first prose line.
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if not candidate:
                    continue
                if candidate.startswith("#"):
                    continue
                if candidate.startswith("`") or candidate.startswith("curl") or candidate.startswith("POST "):
                    continue
                return candidate
            # Heading found but no prose lines under it
            return ""

    # No secondary heading found. Try to find any non-boilerplate prose line
    # after the mailbox block.
    for i in range(mailbox_start + 1, len(lines)):
        candidate = lines[i].strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            continue
        if candidate.startswith("`") or candidate.startswith("curl") or candidate.startswith("POST "):
            continue
        if _MAILBOX_BOILERPLATE_RE.search(candidate):
            continue
        # Skip lines that are clearly still part of registration prose
        if re.search(r"(?i)(register|heartbeat|mailbox|nudge|agents page|before doing)", candidate):
            continue
        return candidate

    return ""


def _extract_context(jsonl_path: Path, max_messages: int = 5) -> str:
    """Extract the first few user messages for context, skipping mailbox boilerplate."""
    parts: list[str] = []
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user" or "message" not in entry:
                    continue
                msg = entry["message"]
                content = msg.get("content", "")
                text = ""
                if isinstance(content, str):
                    text = content.strip()
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "").strip()
                            break
                        elif isinstance(part, str) and part.strip():
                            text = part.strip()
                            break
                text = _skip_mailbox_boilerplate(text)
                if text:
                    parts.append(text[:300])
                    if len(parts) >= max_messages:
                        break
    except OSError:
        pass
    return "\n---\n".join(parts)


async def _generate_title(context: str) -> str:
    """Call the AI backend to generate a short summary title from transcript context."""
    from services.ai_backend import get_ai_client
    client = await get_ai_client()
    if client is None:
        return ""
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=30,
                messages=[{
                    "role": "user",
                    "content": (
                        "Below are the first few messages from a coding session. "
                        "Write a short title (5-8 words max) that summarizes what was worked on. "
                        "No quotes, no punctuation at the end, just the title.\n\n"
                        f"{context}"
                    ),
                }],
            ),
            timeout=10.0,
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                return (getattr(block, "text", "") or "").strip().strip('"\'.')
    except Exception:
        pass
    return ""


async def _generate_titles_background(items: list[tuple[str, Path]]) -> None:
    """Generate titles for transcripts that don't have one cached yet."""
    cache = _load_title_cache()
    for session_id, jsonl_path in items:
        if session_id in cache:
            continue
        context = _extract_context(jsonl_path)
        if not context:
            continue
        title = await _generate_title(context)
        if title:
            cache[session_id] = title
    await _save_title_cache_async(cache)


def _find_all_project_dirs() -> list[Path]:
    """Return all Claude Code project directories that have JSONL session files."""
    dirs = []
    if PROJECTS_DIR.exists():
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and any(d.glob("*.jsonl")):
                dirs.append(d)
    return dirs


def _session_index() -> dict[str, dict]:
    """Build a lookup of sessionId -> session metadata from the sessions directory."""
    index: dict[str, dict] = {}
    if not SESSIONS_DIR.exists():
        return index
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sid = data.get("sessionId")
            if sid:
                index[sid] = data
        except (json.JSONDecodeError, OSError):
            continue
    return index


def _format_timestamp(ts_ms: Optional[int] = None, ts_iso: Optional[str] = None) -> str:
    """Convert a timestamp (millis or ISO string) to a readable string."""
    try:
        if ts_ms:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M")
        if ts_iso:
            # Handle ISO strings with or without trailing Z
            cleaned = ts_iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        pass
    return ""


def _extract_first_user_message(jsonl_path: Path) -> str:
    """Read the JSONL and return the first meaningful user message text (truncated).

    Skips mailbox/registration boilerplate. If the first user message is entirely
    boilerplate, reads subsequent messages until a meaningful line is found.
    Caps output at 200 characters.
    """
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") == "user" and "message" in entry:
                    msg = entry["message"]
                    content = msg.get("content", "")
                    raw_text = ""
                    if isinstance(content, str) and content.strip():
                        raw_text = content.strip()
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                candidate = part.get("text", "").strip()
                                if candidate:
                                    raw_text = candidate
                                    break
                            elif isinstance(part, str) and part.strip():
                                raw_text = part.strip()
                                break

                    if not raw_text:
                        continue

                    # Strip mailbox boilerplate and pick the real content
                    cleaned = _skip_mailbox_boilerplate(raw_text)
                    # If the entire first message was boilerplate, skip to next message
                    if not cleaned:
                        continue

                    return cleaned[:200] if len(cleaned) > 200 else cleaned
    except OSError:
        pass
    return ""


def _derive_title(jsonl_path: Path, spawn_task: str = "") -> str:
    """Derive a display title for a transcript without calling an LLM.

    Rules (in priority order):
    1. First meaningful prose line after mailbox boilerplate, capped at 60 chars.
    2. spawn_task field from the agent row.
    3. Empty string (caller falls back to session name or session ID prefix).
    """
    first = _extract_first_user_message(jsonl_path)
    if first:
        return first[:60]
    if spawn_task:
        return spawn_task[:60]
    return ""


def _count_messages(jsonl_path: Path) -> dict[str, int]:
    """Count user and assistant messages in a transcript JSONL file.

    Reads only the type field from each line to stay fast on large files.
    """
    user_count = 0
    assistant_count = 0
    tool_count = 0
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = entry.get("type", "")
                if t == "user":
                    # Only count real user messages, not tool results
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        user_count += 1
                    elif isinstance(content, list):
                        # Tool results have tool_result items; skip those
                        has_text = any(
                            isinstance(p, str) or (isinstance(p, dict) and p.get("type") == "text")
                            for p in content
                        )
                        if has_text:
                            user_count += 1
                elif t == "assistant":
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "tool_use":
                                    tool_count += 1
                    assistant_count += 1
    except OSError:
        pass
    return {"user": user_count, "assistant": assistant_count, "tool_calls": tool_count}


def _transcript_contains_text(jsonl_path: Path, query: str) -> bool:
    """Check whether any message in a transcript JSONL file contains the query text.

    Case-insensitive search through user and assistant message content.
    """
    query_lower = query.lower()
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                if entry_type not in ("user", "assistant"):
                    continue

                msg = entry.get("message", {})
                content = msg.get("content", "")

                # Extract text from content (can be string or list of parts)
                if isinstance(content, str):
                    if query_lower in content.lower():
                        return True
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, str):
                            if query_lower in part.lower():
                                return True
                        elif isinstance(part, dict):
                            text = part.get("text", "")
                            if isinstance(text, str) and query_lower in text.lower():
                                return True
    except OSError:
        pass
    return False


def _parse_started_at(started_at_str: str) -> Optional[datetime]:
    """Parse a started_at string (YYYY-MM-DD HH:MM) into a datetime."""
    if not started_at_str:
        return None
    try:
        return datetime.strptime(started_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _matches_date_range(started_at_str: str, date_range: str) -> bool:
    """Check whether a transcript's start time falls within the given date range.

    Supported ranges: today, week, month, all.
    """
    if date_range == "all":
        return True

    dt = _parse_started_at(started_at_str)
    if dt is None:
        # If we cannot parse the date, include it only for 'all'
        return False

    now = datetime.now(tz=timezone.utc)

    if date_range == "today":
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt >= start_of_day
    elif date_range == "week":
        start_of_week = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return dt >= start_of_week
    elif date_range == "month":
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return dt >= start_of_month

    return True


@router.get("/transcripts")
async def list_transcripts(
    search: Optional[str] = Query(None, description="Search through message text"),
    date_range: Optional[str] = Query(None, description="Filter by date range: today, week, month, all"),
    kind: Optional[str] = Query(None, description="Filter by session kind (e.g. interactive, task)"),
):
    """List all available session transcripts, with optional search and filters."""
    session_index = _session_index()
    title_cache = _load_title_cache()
    transcripts = []
    needs_title: list[tuple[str, Path]] = []

    # Scan all project directories for JSONL session files
    project_dirs = _find_all_project_dirs()

    for project_dir in project_dirs:
        # Derive a project label from the directory name
        dir_name = project_dir.name  # e.g. "-Users-alice-claude-myproject"
        # Convert back to a readable path
        project_label = dir_name.lstrip("-").replace("-", "/")

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        try:
            jsonl_files = sorted(project_dir.glob("*.jsonl"), key=_safe_mtime, reverse=True)
        except OSError:
            continue

        for jsonl_file in jsonl_files:
            session_id = jsonl_file.stem
            meta = session_index.get(session_id, {})

            # Kind filter: check early before expensive operations
            session_kind = meta.get("kind", "unknown")
            if kind and session_kind != kind:
                continue

            started_at = _format_timestamp(ts_ms=meta.get("startedAt"))

            # Date range filter: check early before expensive operations
            if date_range and date_range != "all":
                if not _matches_date_range(started_at, date_range):
                    continue

            # Search filter: scan message content (most expensive, do last)
            if search:
                if not _transcript_contains_text(jsonl_file, search):
                    continue

            first_message = _extract_first_user_message(jsonl_file)
            try:
                file_size = jsonl_file.stat().st_size
            except OSError:
                file_size = 0
            counts = _count_messages(jsonl_file)

            # Use cached AI title > session name > derived title (boilerplate-skipped) > session ID
            name = title_cache.get(session_id, "")
            if not name:
                name = meta.get("name", "")
            if not name:
                # Derive from first meaningful message, skipping mailbox boilerplate
                name = _derive_title(jsonl_file)
            if not name and first_message:
                name = first_message[:60]
            if not name:
                name = f"Session {session_id[:8]}"
            # Always cap at 60 chars
            name = name[:60]

            if session_id not in title_cache:
                needs_title.append((session_id, jsonl_file))

            transcripts.append({
                "session_id": session_id,
                "name": name,
                "project": project_label,
                "cwd": meta.get("cwd", ""),
                "started_at": started_at,
                "kind": session_kind,
                "entrypoint": meta.get("entrypoint", ""),
                "first_message": first_message,
                "message_counts": counts,
                "file_size_bytes": file_size,
            })

    # Generate titles in the background for transcripts that don't have one
    if needs_title:
        asyncio.create_task(_generate_titles_background(needs_title[:10]))

    return {"transcripts": transcripts, "total": len(transcripts)}


@router.get("/transcripts/{session_id}")
async def get_transcript(session_id: str, limit: int = 100, offset: int = 0):
    """Get the messages from a specific session transcript.

    Returns user and assistant messages in order, skipping internal/system entries.
    The limit and offset parameters control pagination.
    """
    # Find the JSONL file across all project directories
    jsonl_path = None
    for project_dir in _find_all_project_dirs():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            jsonl_path = candidate
            break

    if not jsonl_path:
        raise HTTPException(status_code=404, detail="Transcript not found")

    session_index = _session_index()
    meta = session_index.get(session_id, {})

    messages = []
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                timestamp = entry.get("timestamp", "")

                if entry_type == "user" and "message" in entry:
                    # Claude Code stamps automated re-injections (system
                    # reminders, image-source placeholders, wakeup pings)
                    # with ``isMeta: true``. These are runtime scaffolding,
                    # not things the user actually typed, so scrolling up
                    # should not show the same "Claude Code opening"
                    # banner 100+ times. Drop them at read time so the
                    # on-disk JSONL stays an untouched audit log.
                    if entry.get("isMeta") is True:
                        continue

                    msg = entry["message"]
                    content = msg.get("content", "")
                    text = ""

                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        # Combine text parts, note tool results
                        parts = []
                        for part in content:
                            if isinstance(part, str):
                                parts.append(part)
                            elif isinstance(part, dict):
                                if part.get("type") == "text":
                                    parts.append(part.get("text", ""))
                                elif part.get("type") == "tool_result":
                                    # Summarize tool result
                                    result_content = part.get("content", "")
                                    if isinstance(result_content, str) and result_content.strip():
                                        preview = result_content[:300]
                                        parts.append(f"[Tool output: {preview}]")
                        text = "\n".join(parts)

                    if text.strip():
                        cleaned = text.strip()
                        # Collapse consecutive identical user bubbles into
                        # one. In long sessions the same "60s check: idle"
                        # style ping can fire dozens of times in a row and
                        # is not useful to render separately on scroll-up.
                        if (
                            messages
                            and messages[-1]["role"] == "user"
                            and messages[-1]["text"] == cleaned
                        ):
                            continue
                        messages.append({
                            "role": "user",
                            "text": cleaned,
                            "timestamp": _format_timestamp(ts_iso=timestamp) if timestamp else "",
                        })

                elif entry_type == "assistant" and "message" in entry:
                    msg = entry["message"]
                    content = msg.get("content", [])
                    text_parts = []
                    tool_uses = []

                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                                elif part.get("type") == "tool_use":
                                    tool_name = part.get("name", "unknown")
                                    tool_input = part.get("input", {})
                                    # Show a brief summary of what tool was called
                                    summary = f"Used {tool_name}"
                                    if isinstance(tool_input, dict):
                                        if "command" in tool_input:
                                            cmd = tool_input["command"]
                                            if len(cmd) > 120:
                                                cmd = cmd[:120] + "..."
                                            summary = f"Ran: {cmd}"
                                        elif "file_path" in tool_input:
                                            summary = f"Read: {tool_input['file_path']}"
                                        elif "pattern" in tool_input:
                                            summary = f"Searched for: {tool_input['pattern']}"
                                    tool_uses.append(summary)
                            elif isinstance(part, str):
                                text_parts.append(part)
                    elif isinstance(content, str):
                        text_parts.append(content)

                    text = "\n".join(text_parts).strip()
                    if text or tool_uses:
                        messages.append({
                            "role": "assistant",
                            "text": text,
                            "tool_uses": tool_uses,
                            "timestamp": _format_timestamp(ts_iso=timestamp) if timestamp else "",
                        })

    except OSError:
        raise HTTPException(status_code=500, detail="Could not read transcript file")

    # Apply pagination
    total = len(messages)
    paginated = messages[offset:offset + limit]

    return {
        "session_id": session_id,
        "name": meta.get("name", ""),
        "cwd": meta.get("cwd", ""),
        "started_at": _format_timestamp(ts_ms=meta.get("startedAt")),
        "kind": meta.get("kind", ""),
        "total_messages": total,
        "messages": paginated,
    }


@router.post("/transcripts/backfill-titles")
async def backfill_transcript_titles():
    """Re-derive titles for transcripts whose cached title looks like boilerplate.

    Checks every entry in the title cache. If a title starts with
    'Agent registration' (case-insensitive) it is deleted so the next
    /transcripts fetch will re-derive it from the actual message content.
    Does NOT mutate source JSONL files. Safe to call multiple times.
    """
    cache = _load_title_cache()
    removed: list[str] = []

    boilerplate_title_re = re.compile(
        r"(?i)^agent\s+registration"
    )

    for session_id, title in list(cache.items()):
        if boilerplate_title_re.match(title):
            del cache[session_id]
            removed.append(session_id)

    if removed:
        _save_title_cache(cache)

    return {
        "removed": len(removed),
        "message": (
            f"Cleared {len(removed)} boilerplate title(s) from cache. "
            "They will be re-derived on the next transcript fetch."
            if removed
            else "No boilerplate titles found in cache."
        ),
    }


# ---- Session-task links ----------------------------------------------------
#
# These endpoints let the SessionStart hook (or any other caller) connect a
# Claude Code session UUID to an already-filed task so the Tasks page can
# show a "View transcript" link on the session row and count how many tasks
# were created during that session.


class _LinkTaskBody(BaseModel):
    task_id: str


@router.post("/sessions/{session_id}/link-task")
async def link_session_task(session_id: str, body: _LinkTaskBody):
    """Attach an existing task_id to this Claude Code session.

    The task becomes the "session task" for this session: the row on the
    Tasks page that represents the session itself. Used by the
    SessionStart hook right after it files its auto task, so later
    renders can link back to the transcript.
    """
    if not session_id or not body.task_id:
        raise HTTPException(status_code=422, detail="session_id and task_id are required")
    session_task_map.link_session_to_task(session_id, body.task_id)
    return {"session_id": session_id, "task_id": body.task_id, "linked": True}


class _LinkChildBody(BaseModel):
    task_id: str
    parent_session_id: str


@router.post("/sessions/{session_id}/link-child-task")
async def link_child_task_endpoint(session_id: str, body: _LinkChildBody):
    """Record that ``task_id`` was created during ``session_id``.

    The session_id in the path and body must match so the record is
    unambiguous. This powers the "N tasks created in this session"
    count shown on the session row.
    """
    if body.parent_session_id != session_id:
        raise HTTPException(
            status_code=422,
            detail="parent_session_id in body does not match session_id in path",
        )
    if not body.task_id:
        raise HTTPException(status_code=422, detail="task_id is required")
    session_task_map.link_child_task(body.task_id, session_id)
    return {
        "session_id": session_id,
        "task_id": body.task_id,
        "child_task_count": session_task_map.count_children(session_id),
    }


@router.get("/sessions/{session_id}/child-tasks")
async def get_child_tasks(session_id: str):
    """Return the count of tasks created during this session.

    Lightweight companion to ``/link-child-task`` so the Tasks page can
    refresh the count without refetching every task row.
    """
    return {
        "session_id": session_id,
        "count": session_task_map.count_children(session_id),
    }

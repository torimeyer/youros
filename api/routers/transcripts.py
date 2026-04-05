"""Transcripts router: reads Claude Code session files from disk."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["transcripts"])

# Claude Code stores session index files and transcript JSONL files in these locations.
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# The torios project transcript folder. Claude Code uses the absolute path
# with slashes replaced by dashes as the folder name.
from config import PROJECT_ROOT

# Compute the Claude Code project folder name from the actual project root.
TORIOS_PROJECT_DIR = PROJECTS_DIR / str(PROJECT_ROOT).replace("/", "-").lstrip("-")


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
    """Read the JSONL and return the first user message text (truncated)."""
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
                    if isinstance(content, str) and content.strip():
                        text = content.strip()
                        return text[:200] if len(text) > 200 else text
                    if isinstance(content, list):
                        # content can be a list of parts (tool results, etc.)
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part.get("text", "").strip()
                                if text:
                                    return text[:200] if len(text) > 200 else text
                            elif isinstance(part, str) and part.strip():
                                text = part.strip()
                                return text[:200] if len(text) > 200 else text
    except OSError:
        pass
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
    transcripts = []

    # Scan all project directories for JSONL session files
    project_dirs = _find_all_project_dirs()

    for project_dir in project_dirs:
        # Derive a project label from the directory name
        dir_name = project_dir.name  # e.g. "-Users-torimeyer-claude-torios"
        # Convert back to a readable path
        project_label = dir_name.lstrip("-").replace("-", "/")

        for jsonl_file in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
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
            file_size = jsonl_file.stat().st_size
            counts = _count_messages(jsonl_file)

            # Use the session name if available, otherwise use a snippet of the first message
            name = meta.get("name", "")
            if not name and first_message:
                name = first_message[:80]
            if not name:
                name = f"Session {session_id[:8]}"

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
                        messages.append({
                            "role": "user",
                            "text": text.strip(),
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

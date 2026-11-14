"""Live sessions endpoint.

Reads .ostk/sessions/*/events.jsonl to report which ostk sessions are
currently active, idle, or stale. The endpoint only reads the last few
lines of each file so it stays fast even with long-running sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from config import OSTK_DIR
from services.ostk import get_effective_root
import services.runtime_sessions as _runtime_sessions_svc
from services.runtime_sessions import (
    discover_external_runtime_sessions as _default_discover_external_runtime_sessions,
    reset_runtime_sessions_cache,
)
from services.session_events import bus as _session_events_bus

discover_external_runtime_sessions = _default_discover_external_runtime_sessions

router = APIRouter(tags=["sessions"])

_DEFAULT_SESSIONS_DIR = OSTK_DIR / "sessions"
SESSIONS_DIR = _DEFAULT_SESSIONS_DIR


def _effective_sessions_dir() -> Path:
    """Return SESSIONS_DIR when explicitly patched by tests, else <effective_root>/.ostk/sessions."""
    if SESSIONS_DIR != _DEFAULT_SESSIONS_DIR:
        return SESSIONS_DIR
    return get_effective_root() / ".ostk" / "sessions"

# Thresholds
ACTIVE_CUTOFF_MINUTES = 5
IDLE_CUTOFF_MINUTES = 30

# Stuck detection: a session that went quiet between these two windows
# is "stuck" — quiet longer than finishing naturally, not yet idle long.
STUCK_MIN_SECONDS = 90
STUCK_MAX_SECONDS = 600  # 10 min

# Conflict detection: two distinct sessions writing the same file within this
# window triggers a conflict alert. Informational only, never blocks anything.
CONFLICT_WINDOW_MINUTES = 15

# File-writing tools whose input carries a "path" key
_FILE_WRITE_TOOLS = frozenset({"fs_ops", "edit", "write", "fs_write", "Write", "Edit"})

# How many bytes to read from the end of each events.jsonl to find recent events.
TAIL_BYTES = 8192

# The backend identifies itself to ostk as "myos-api", so ostk writes a new
# session directory for every uvicorn worker boot (myos-api-1, myos-api-2, ...).
# These are not real user sessions. The backend is the thing rendering the
# sidebar, so it should never count itself.
BACKEND_SESSION_PREFIX = "myos-api-"


def _is_backend_self_session(session_id: str) -> bool:
    """True when the session id belongs to the backend itself.

    Each uvicorn reload boots a new worker that opens its own ostk session,
    leaving the old session row behind as a zombie. Filtering by prefix
    handles every past, present, and future worker generation.
    """
    return session_id.startswith(BACKEND_SESSION_PREFIX)


def _tail_lines(path: Path, nbytes: int = TAIL_BYTES) -> list[str]:
    """Read the last *nbytes* of a file and return complete lines."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size == 0:
        return []
    read_size = min(size, nbytes)
    try:
        with open(path, "rb") as f:
            f.seek(-read_size, 2)
            chunk = f.read(read_size).decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.split("\n")
    # First line may be partial if we seeked into the middle of one.
    if read_size < size:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def _parse_event(line: str) -> Optional[dict]:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    ts = obj.get("ts")
    kind = obj.get("kind", "")
    data = obj.get("data", {})
    tool = data.get("tool", "")
    summary_parts = []
    if kind:
        summary_parts.append(kind)
    if tool:
        summary_parts.append(tool)
    return {
        "type": ": ".join(summary_parts) if summary_parts else obj.get("type", "event"),
        "timestamp": ts,
    }


def _extract_activity_and_files(events: list[dict]) -> tuple[str, list[str]]:
    """Derive a human-readable activity line and recent file paths from session events.

    Scans the event list in reverse so the most-recent tool call wins.
    Returns (activity_line, file_paths_up_to_3).
    """
    activity = ""
    seen_paths: list[str] = []
    seen_set: set[str] = set()

    _TOOL_LABELS = {
        "bash": "Running command",
        "shell": "Running command",
        "search": "Searching",
        "fs_ops": "Writing files",
        "edit": "Editing file",
        "write": "Writing file",
        "fs_write": "Writing file",
        "Write": "Writing file",
        "Edit": "Editing file",
        "read": "Reading file",
        "fs_read": "Reading file",
        "Read": "Reading file",
        "needle": "Managing tasks",
        "tack": "Running tack",
        "recall": "Searching memory",
        "spawn": "Spawning process",
        "interact": "Interacting with process",
    }

    for ev in reversed(events):
        data = ev.get("data", {})
        if not isinstance(data, dict):
            continue
        tool = data.get("tool", "")
        raw_input = data.get("input", "")

        # Build activity line from most recent event
        if not activity and tool:
            label = _TOOL_LABELS.get(tool, tool)
            # Append a short snippet from input
            if isinstance(raw_input, str) and raw_input:
                snippet = raw_input[:60].strip()
                if snippet:
                    activity = f"{label}: {snippet}"
                else:
                    activity = label
            elif isinstance(raw_input, dict):
                snippet = str(raw_input.get("cmd") or raw_input.get("path") or "")[:60]
                activity = f"{label}: {snippet}" if snippet else label
            else:
                activity = label

        # Collect file paths from write-tool calls
        if tool in _FILE_WRITE_TOOLS and len(seen_paths) < 3:
            path_str = ""
            if isinstance(raw_input, str):
                try:
                    parsed = json.loads(raw_input)
                    path_str = parsed.get("path", "")
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
            elif isinstance(raw_input, dict):
                path_str = raw_input.get("path", "")
            if path_str and path_str not in seen_set:
                seen_paths.append(path_str)
                seen_set.add(path_str)

        if activity and len(seen_paths) >= 3:
            break

    return activity, seen_paths


def _is_stuck(last_active: datetime, now: datetime) -> bool:
    """True when a session went quiet recently enough to be suspicious.

    A session that finished naturally goes idle gradually; one that stalled
    mid-work tends to go quiet abruptly after being active. We flag the
    window between STUCK_MIN_SECONDS (90s) and STUCK_MAX_SECONDS (10 min).
    Beyond 10 min it is just idle, not stuck. Informational only.
    """
    age_seconds = (now - last_active).total_seconds()
    return STUCK_MIN_SECONDS < age_seconds < STUCK_MAX_SECONDS


def _detect_file_conflicts(
    sessions_dir: Path,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Find files written by two or more distinct sessions within CONFLICT_WINDOW_MINUTES.

    Reads per-session event files (same source as _get_sessions). Two writes to
    the same path by different session IDs within the window = conflict. Self-
    conflicts (same session twice) and non-file-write tools are excluded.

    Runs synchronously — must be called inside asyncio.to_thread.

    Returns a list of dicts:
        {"path": str, "session_ids": [str, ...], "last_write_times": {id: iso_str}}
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=CONFLICT_WINDOW_MINUTES)

    if not sessions_dir.is_dir():
        return []

    # path -> {session_id: latest_write_datetime_within_window}
    path_writes: dict[str, dict[str, datetime]] = {}

    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue
        sid = entry.name
        if _is_backend_self_session(sid):
            continue
        events_file = entry / "events.jsonl"
        if not events_file.exists():
            continue

        lines = _tail_lines(events_file, nbytes=16384)
        for ln in lines:
            try:
                ev = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            ts_str = ev.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            data = ev.get("data", {})
            if not isinstance(data, dict):
                continue
            tool = data.get("tool", "")
            if tool not in _FILE_WRITE_TOOLS:
                continue
            raw_input = data.get("input", "")
            path_str = ""
            if isinstance(raw_input, str):
                try:
                    parsed = json.loads(raw_input)
                    path_str = parsed.get("path", "")
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
            elif isinstance(raw_input, dict):
                path_str = raw_input.get("path", "")
            if not path_str:
                continue
            session_writes = path_writes.setdefault(path_str, {})
            existing = session_writes.get(sid)
            if existing is None or ts > existing:
                session_writes[sid] = ts

    conflicts = []
    for path_str, session_writes in path_writes.items():
        if len(session_writes) < 2:
            continue
        conflicts.append({
            "path": path_str,
            "session_ids": list(session_writes.keys()),
            "last_write_times": {
                sid: ts.isoformat()
                for sid, ts in session_writes.items()
            },
        })
    return conflicts


def _get_sessions() -> list[dict]:
    sessions_dir = _effective_sessions_dir()
    if not sessions_dir.is_dir():
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=IDLE_CUTOFF_MINUTES)
    results = []

    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue
        events_file = entry / "events.jsonl"
        if not events_file.exists():
            continue

        lines = _tail_lines(events_file)
        if not lines:
            continue

        # Parse the last several events
        parsed = []
        for ln in lines[-20:]:
            ev = _parse_event(ln)
            if ev and ev.get("timestamp"):
                parsed.append(ev)

        if not parsed:
            continue

        # Determine last active time
        last_ts_str = parsed[-1]["timestamp"]
        try:
            last_active = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        if last_active < cutoff:
            continue

        active_threshold = now - timedelta(minutes=ACTIVE_CUTOFF_MINUTES)
        status = "active" if last_active >= active_threshold else "idle"

        recent_events = [
            {"type": e["type"], "timestamp": e["timestamp"]}
            for e in parsed[-5:]
        ]

        # Parse raw events for enrichment (activity, recent_files)
        raw_parsed = []
        for ln in lines[-30:]:
            try:
                raw_parsed.append(json.loads(ln))
            except (json.JSONDecodeError, ValueError):
                continue

        activity, recent_files = _extract_activity_and_files(raw_parsed)
        stuck = _is_stuck(last_active, now)

        results.append({
            "session_id": entry.name,
            "last_active": last_ts_str,
            "status": status,
            "recent_events": recent_events,
            "activity": activity,
            "recent_files": recent_files,
            "stuck": stuck,
            "runtime": _infer_session_runtime(entry.name),
            "pid": _extract_pid_from_session_id(entry.name),
        })

    # Sort: active first, then by most recent
    results.sort(key=lambda s: s["last_active"], reverse=True)
    return results


_ORIG_GET_SESSIONS = _get_sessions


def _claude_code_transcript_sessions() -> list[dict]:
    """Infer live Claude Code sessions from transcript file mtimes.

    Claude Code writes every session's events to a JSONL file under
    ``~/.claude/projects/<repo>/<session-id>.jsonl``. A file whose
    mtime is within the idle window means that session is alive right
    now, even if it never ran our SessionStart hook (which only fires
    for sessions started AFTER the hook was wired up in
    ``.claude/settings.json``). Without this fallback, existing tabs
    that were open before the wiring stayed invisible to the sidebar
    "sessions" counter.
    """
    from pathlib import Path as _P
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    projects_root = _P.home() / ".claude" / "projects"
    if not projects_root.is_dir():
        return []

    # Match the current repo's cwd so we only report sessions that are
    # actually editing this project. Claude Code encodes cwd as "-" for
    # each path separator.
    cwd_tag = str(get_effective_root()).replace("/", "-")
    project_dir = projects_root / cwd_tag
    if not project_dir.is_dir():
        return []

    now = _dt.now(_tz.utc)
    idle_cutoff = now - _td(minutes=IDLE_CUTOFF_MINUTES)
    active_cutoff = now - _td(minutes=ACTIVE_CUTOFF_MINUTES)
    results: list[dict] = []
    for jsonl in project_dir.glob("*.jsonl"):
        try:
            mtime = _dt.fromtimestamp(jsonl.stat().st_mtime, tz=_tz.utc)
        except OSError:
            continue
        if mtime < idle_cutoff:
            continue
        status = "active" if mtime >= active_cutoff else "idle"
        sid = jsonl.stem[:10]
        results.append({
            "session_id": f"claude-code-{sid}",
            "last_active": mtime.isoformat(),
            "status": status,
            "recent_events": [],
            "runtime": "claude",
            "external": True,
            "_transcript_only": True,
        })
    return results


def _agent_sessions() -> list[dict]:
    """Synthesize session records from the live agent registry.

    The ``.ostk/sessions/`` directory only reflects sessions that wrote
    ostk events. External Claude Code tabs and chat turns never
    touch that directory, so they were invisible to the sidebar's
    "sessions" counter even when they were clearly open. Using the
    agent registry as a secondary source fixes that: every registered
    agent with a recent heartbeat counts as a live session.
    """
    from routers.agents import agent_metadata, STALE_AGENT_TIMEOUT_SECONDS

    now = datetime.now(timezone.utc)
    active_window = timedelta(minutes=ACTIVE_CUTOFF_MINUTES)
    idle_window = timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS)
    results: list[dict] = []
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        heartbeat_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        if not isinstance(heartbeat_raw, str):
            continue
        try:
            heartbeat = datetime.fromisoformat(heartbeat_raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        age = now - heartbeat
        if age > idle_window:
            continue
        status = "active" if age <= active_window else "idle"
        record: dict = {
            "session_id": name,
            "last_active": heartbeat_raw,
            "status": status,
            "recent_events": [],
        }
        for field in ("task", "current_step", "prompt", "spawned_at"):
            value = meta.get(field)
            if value:
                record[field] = value
        results.append(record)
    return results


def cleanup_stale_backend_sessions() -> int:
    """Mark older myos-api-* session directories as terminated.

    Every uvicorn reload leaves its old session folder behind with
    events.jsonl still recent enough to count. On backend startup we run
    this once so only the newest generation (the one this worker owns)
    stays active in ostk's view. We touch an "events.jsonl" sentinel
    that back-dates the last event so the listing endpoint's 30-minute
    window naturally drops the old row on the next read.

    Returns the number of session directories swept. Safe to call even
    when the sessions directory does not exist.
    """
    sessions_dir = _effective_sessions_dir()
    if not sessions_dir.is_dir():
        return 0

    backend_dirs = [
        entry for entry in sessions_dir.iterdir()
        if entry.is_dir() and _is_backend_self_session(entry.name)
    ]
    if len(backend_dirs) <= 1:
        return 0

    # Keep the newest one (highest mtime), retire the rest by back-dating
    # their events.jsonl mtime past the idle window.
    backend_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=IDLE_CUTOFF_MINUTES + 5))
    stale_epoch = stale_cutoff.timestamp()
    swept = 0
    for stale_dir in backend_dirs[1:]:
        events_file = stale_dir / "events.jsonl"
        if not events_file.exists():
            continue
        try:
            os.utime(events_file, (stale_epoch, stale_epoch))
            swept += 1
        except OSError:
            continue
    return swept


_PID_SUFFIX_RE = re.compile(r"(?:-p|^claude-pid-)(\d+)$")


def _extract_pid_from_session_id(session_id: str) -> Optional[int]:
    if not session_id:
        return None
    match = _PID_SUFFIX_RE.search(session_id)
    if not match:
        return None
    try:
        pid_val = int(match.group(1))
        return pid_val if pid_val > 0 else None
    except ValueError:
        return None


def _infer_session_runtime(session_id: str, meta: Optional[dict] = None) -> str:
    if isinstance(meta, dict):
        probe = " ".join(
            str(meta.get(k) or "") for k in ("runtime", "provider", "model", "source")
        ).lower()
        if "gemini" in probe:
            return "gemini"
        if any(tok in probe for tok in ("claude", "anthropic", "sonnet", "opus", "haiku")):
            return "claude"
    lowered = (session_id or "").lower()
    if "gemini" in lowered:
        return "gemini"
    if "claude" in lowered:
        return "claude"
    return "ostk"


def _session_type(session_id: str) -> str:
    if session_id.startswith("claude-code-"):
        return "claude-code"
    if session_id.startswith("agent-"):
        return "agent"
    if session_id.startswith("chat-"):
        return "chat"
    return "ostk"


def _should_discover_external(include_external: Optional[bool] = None) -> bool:
    """Determine whether host Claude/Gemini runtime discovery should run.

    When tests patch SESSIONS_DIR (or _get_sessions) to an isolated fixture
    without explicitly enabling include_external or patching
    discover_external_runtime_sessions, skip real host process scanning so
    synthetic assertions stay deterministic.
    """
    if include_external is True:
        return True
    if include_external is False:
        return False
    if discover_external_runtime_sessions is not _default_discover_external_runtime_sessions:
        return True
    if (
        _runtime_sessions_svc.discover_external_runtime_sessions
        is not _default_discover_external_runtime_sessions
    ):
        return True
    return SESSIONS_DIR == _DEFAULT_SESSIONS_DIR and _get_sessions is _ORIG_GET_SESSIONS


def _resolve_discover_callable():
    if discover_external_runtime_sessions is not _default_discover_external_runtime_sessions:
        return discover_external_runtime_sessions
    return _runtime_sessions_svc.discover_external_runtime_sessions


def _merge_external_sessions(
    sessions: list[dict],
    external_sessions: list[dict],
    now_utc: Optional[datetime] = None,
) -> list[dict]:
    """Deduplicate and merge discovered external runtime sessions into *sessions*.

    If an existing .ostk/sessions/ (or registered agent) entry shares the same
    id/sessionId, pid (including claude-code-p<pid>), or name, enrich that row
    with runtime, kind, and pid rather than emitting a duplicate row.
    """
    now_ref = now_utc or datetime.now(timezone.utc)
    merged = [dict(row) for row in sessions]

    for ext in external_sessions:
        if not isinstance(ext, dict):
            continue
        ext_id = str(ext.get("id") or ext.get("session_id") or "").strip()
        ext_name = str(ext.get("name") or "").strip()
        if _is_backend_self_session(ext_id) or _is_backend_self_session(ext_name):
            continue

        raw_ext_pid = ext.get("pid")
        ext_pid = (
            raw_ext_pid
            if isinstance(raw_ext_pid, int) and not isinstance(raw_ext_pid, bool) and raw_ext_pid > 0
            else None
        )

        matched_row: Optional[dict] = None
        for row in merged:
            row_id = str(row.get("id") or row.get("session_id") or "").strip()
            row_name = str(row.get("name") or "").strip()
            raw_row_pid = row.get("pid")
            row_pid = (
                raw_row_pid
                if isinstance(raw_row_pid, int) and not isinstance(raw_row_pid, bool)
                else _extract_pid_from_session_id(row_id)
            )

            id_match = bool(
                ext_id
                and row_id
                and (
                    row_id.lower() == ext_id.lower()
                    or (len(ext_id) >= 10 and row_id.lower() == f"claude-code-{ext_id[:10]}".lower())
                )
            )
            pid_match = ext_pid is not None and row_pid is not None and row_pid == ext_pid
            name_match = bool(
                (ext_name and row_id and row_id.lower() == ext_name.lower())
                or (ext_name and row_name and row_name.lower() == ext_name.lower())
                or (ext_id and row_name and row_name.lower() == ext_id.lower())
            )
            if id_match or pid_match or name_match:
                matched_row = row
                break

        if matched_row is not None:
            if ext.get("runtime"):
                matched_row["runtime"] = ext["runtime"]
            if ext.get("kind"):
                matched_row["kind"] = ext["kind"]
            if ext_pid is not None:
                matched_row["pid"] = ext_pid
            if ext.get("cwd") and not matched_row.get("cwd"):
                matched_row["cwd"] = ext["cwd"]
            if ext.get("project_slug") and not matched_row.get("project_slug"):
                matched_row["project_slug"] = ext["project_slug"]
            if matched_row.get("_transcript_only"):
                matched_row["external"] = True
            else:
                matched_row["external"] = False
            continue

        last_event_at = str(
            ext.get("last_event_at")
            or ext.get("last_active_at")
            or ext.get("last_active")
            or now_ref.isoformat()
        )
        raw_age = ext.get("age_seconds")
        if isinstance(raw_age, (int, float)) and not isinstance(raw_age, bool):
            age_seconds = max(0, int(raw_age))
        else:
            try:
                ev_dt = datetime.fromisoformat(last_event_at.replace("Z", "+00:00"))
                age_seconds = max(0, int((now_ref - ev_dt).total_seconds()))
            except (ValueError, TypeError):
                age_seconds = 0

        display_name = ext_name or ext_id
        merged.append({
            "id": ext_id or display_name,
            "session_id": ext_id or display_name,
            "name": display_name,
            "label": str(ext.get("label") or display_name),
            "type": str(ext.get("runtime") or "external"),
            "runtime": str(ext.get("runtime") or _infer_session_runtime(ext_id)),
            "kind": str(ext.get("kind") or "interactive"),
            "pid": ext_pid,
            "cwd": ext.get("cwd"),
            "project_slug": ext.get("project_slug"),
            "external": True,
            "started_at": ext.get("started_at"),
            "last_active_at": last_event_at,
            "last_event_at": last_event_at,
            "last_active": last_event_at,
            "age_seconds": age_seconds,
            "status": str(ext.get("status") or "active"),
            "activity": str(ext.get("activity") or ""),
            "recent_files": list(ext.get("recent_files") or []),
            "recent_events": list(ext.get("recent_events") or []),
            "stuck": bool(ext.get("stuck", False)),
        })

    for row in merged:
        row.pop("_transcript_only", None)

    merged.sort(
        key=lambda s: str(
            s.get("last_active_at") or s.get("last_event_at") or s.get("last_active") or ""
        ),
        reverse=True,
    )
    return merged


# Coordination snapshot cache (→1738). The gather below walks the ostk
# sessions dir and the Claude Code transcript dir (thousands of files) and
# reads per-session event logs. Running it inline on the event loop for every
# poll / WS connect / agent mutation blocked the loop for seconds and starved
# every other request (health, agents, chat provider-detection) -> HTTP 502/000
# cascades. We offload the sync filesystem work to a thread AND cache it with a
# short TTL + single-flight, so a burst of callers shares one off-loop scan.
_COORD_TTL_SECONDS = 3.0
_coord_cache: Optional[dict] = None
_coord_cache_ts: float = 0.0
_coord_refresh_lock = asyncio.Lock()


def _gather_sessions_and_events_sync(
    meta_snapshot: dict, nudge_snapshot: dict
) -> tuple[list[dict], list[dict], list[dict]]:
    """Synchronous, filesystem-heavy gather. MUST run off the event loop.

    Operates only on immutable snapshots of the agent/nudge maps so it is
    safe to call from a worker thread.
    """
    now_utc = datetime.now(timezone.utc)
    # --- sessions ---
    raw_sessions = _get_sessions()
    seen: set[str] = {s["session_id"] for s in raw_sessions}
    for extra in _agent_sessions():
        if extra["session_id"] not in seen:
            raw_sessions.append(extra)
            seen.add(extra["session_id"])
    for extra in _claude_code_transcript_sessions():
        if extra["session_id"] not in seen:
            raw_sessions.append(extra)
            seen.add(extra["session_id"])
    raw_sessions = [s for s in raw_sessions if not _is_backend_self_session(s["session_id"])]
    raw_sessions.sort(key=lambda s: s["last_active"], reverse=True)

    sessions = []
    for s in raw_sessions:
        sid = s["session_id"]
        meta = meta_snapshot.get(sid, {})
        task = meta.get("task") or ""
        label = task or sid
        # Enrichment fields: compute stuck from last_active if not already set
        # by _get_sessions(). Agent/transcript sessions don't carry it.
        activity = s.get("activity", "")
        recent_files = s.get("recent_files", [])
        la_iso = s["last_active"]
        try:
            la_dt = datetime.fromisoformat(la_iso.replace("Z", "+00:00"))
            age_seconds = max(0, int((now_utc - la_dt).total_seconds()))
            computed_stuck = _is_stuck(la_dt, now_utc)
        except (ValueError, TypeError, KeyError):
            age_seconds = 0
            computed_stuck = False
        stuck = s["stuck"] if "stuck" in s else computed_stuck
        raw_pid = s.get("pid")
        pid_val = (
            raw_pid
            if isinstance(raw_pid, int) and not isinstance(raw_pid, bool)
            else _extract_pid_from_session_id(sid)
        )
        row_entry = {
            "id": sid,
            "session_id": sid,
            "name": task or sid,
            "label": label,
            "type": _session_type(sid),
            "runtime": s.get("runtime") or _infer_session_runtime(sid, meta),
            "kind": s.get("kind") or "interactive",
            "pid": pid_val,
            "cwd": s.get("cwd") or meta.get("worktree_path") or meta.get("cwd"),
            "external": bool(s.get("external", False)),
            "started_at": meta.get("spawned_at"),
            "last_active_at": la_iso,
            "last_event_at": la_iso,
            "age_seconds": age_seconds,
            "status": s["status"],
            "activity": activity,
            "recent_files": recent_files,
            "stuck": stuck,
        }
        if s.get("_transcript_only"):
            row_entry["_transcript_only"] = True
        sessions.append(row_entry)

    # --- recent coordination events (nudges across all agents, last 20) ---
    all_events: list[dict] = []
    for agent_name, nudges in nudge_snapshot.items():
        for nudge in nudges:
            all_events.append({
                "from_session": "user",
                "to_session": agent_name,
                "message": nudge.get("message", ""),
                "timestamp": nudge.get("timestamp", ""),
                "kind": nudge.get("kind", "user_message"),
            })
    all_events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    events = all_events[:20]

    conflicts = _detect_file_conflicts(_effective_sessions_dir(), now=now_utc)
    return sessions, events, conflicts


async def _build_coordination_data(include_external: Optional[bool] = None) -> dict:
    """Assemble the full coordination snapshot (sessions, locks, events).

    The filesystem-heavy session/event gather is offloaded to a worker thread
    so the event loop stays responsive while it runs.
    """
    from services.ostk import ostk
    from routers.agents import agent_metadata, nudge_history

    # Snapshot the in-memory maps on the loop (cheap) so the worker thread
    # never iterates a dict another coroutine is mutating.
    meta_snapshot = dict(agent_metadata)
    nudge_snapshot = {k: list(v) for k, v in nudge_history.items()}

    sessions, events, conflicts = await asyncio.to_thread(
        _gather_sessions_and_events_sync, meta_snapshot, nudge_snapshot
    )

    if _should_discover_external(include_external):
        discover_fn = _resolve_discover_callable()
        external_sessions = await discover_fn()
        sessions = _merge_external_sessions(sessions, external_sessions)
    else:
        for row in sessions:
            row.pop("_transcript_only", None)

    # --- locks (already async) ---
    try:
        raw_locks = await ostk.list_locks()
    except Exception:
        raw_locks = []

    locks = []
    for lk in raw_locks:
        locks.append({
            "lock_name": lk.get("name", ""),
            "held_by_session": lk.get("holder") or lk.get("held_by", ""),
            "started_at": lk.get("created_at") or lk.get("started_at"),
            "paths": lk.get("paths", []),
        })

    return {
        "sessions": sessions,
        "locks": locks,
        "events": events,
        "conflicts": conflicts,
    }


async def _coordination_snapshot() -> dict:
    """Short-TTL, single-flight wrapper around _build_coordination_data.

    Concurrent callers within the TTL share one cached result and only one
    refresh runs at a time, bounding the expensive gather to about once per
    TTL regardless of how many pollers, WS clients, or mutations fire.
    """
    global _coord_cache, _coord_cache_ts
    now = time.monotonic()
    if _coord_cache is not None and (now - _coord_cache_ts) < _COORD_TTL_SECONDS:
        return _coord_cache
    async with _coord_refresh_lock:
        now = time.monotonic()
        if _coord_cache is not None and (now - _coord_cache_ts) < _COORD_TTL_SECONDS:
            return _coord_cache
        data = await _build_coordination_data()
        _coord_cache = data
        _coord_cache_ts = time.monotonic()
        return data


async def _publish_session_state() -> None:
    """Recompute coordination data and push to WS subscribers. Best-effort."""
    global _coord_cache, _coord_cache_ts
    try:
        data = await _build_coordination_data()
        _coord_cache = data
        _coord_cache_ts = time.monotonic()
        await _session_events_bus.publish("snapshot", data)
    except Exception:
        pass  # never block mutation endpoints


@router.websocket("/ws/sessions/state")
async def sessions_state_ws(websocket: WebSocket) -> None:
    """Push coordination snapshot to clients in real time.

    On connect: sends one snapshot frame with current data.
    On every agent mutation (register, complete, cancel): sends a fresh snapshot.
    Keepalive ping every 15 seconds.
    """
    await websocket.accept()
    try:
        data = await _coordination_snapshot()
        await websocket.send_json({"type": "snapshot", **data})
        async with _session_events_bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    await websocket.send_json({"type": event.type, **event.payload})
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@router.get("/sessions")
async def list_sessions(include_external: Optional[bool] = Query(default=None)):
    """Unified Mission Control sessions feed (->3121).

    Merges ostk/agent sessions with ground-truth external Claude (`claude agents
    --json`) and Gemini (`~/.gemini/tmp/*/chats/session-*.jsonl`) sessions,
    deduplicating by session ID, PID, or name.
    """
    from routers.agents import agent_metadata, nudge_history

    meta_snapshot = dict(agent_metadata)
    nudge_snapshot = {k: list(v) for k, v in nudge_history.items()}

    sessions, _, conflicts = await asyncio.to_thread(
        _gather_sessions_and_events_sync, meta_snapshot, nudge_snapshot
    )

    if _should_discover_external(include_external):
        discover_fn = _resolve_discover_callable()
        external_sessions = await discover_fn()
        sessions = _merge_external_sessions(sessions, external_sessions)
    else:
        for row in sessions:
            row.pop("_transcript_only", None)

    return {
        "sessions": sessions,
        "conflicts": conflicts,
        "count": len(sessions),
        "active_count": sum(1 for s in sessions if s.get("status") == "active"),
        "idle_count": sum(1 for s in sessions if s.get("status") == "idle"),
        "external_count": sum(1 for s in sessions if s.get("external")),
    }


@router.get("/sessions/coordination")
async def get_coordination(include_external: Optional[bool] = Query(default=None)):
    """Visible coordination panel: sessions, locks, and recent inter-session events."""
    if include_external is not None:
        return await _build_coordination_data(include_external=include_external)
    return await _coordination_snapshot()


def _build_active_sessions() -> dict:
    """Synchronous body of GET /api/sessions/active.

    Runs ALL of the disk-walking work: _get_sessions() opens+tails every
    .ostk/sessions/*/events.jsonl and json.loads each tail line, and
    _claude_code_transcript_sessions() globs+stat()s every Claude Code
    transcript. On a busy box (dozens of sessions/transcripts) this is
    ~tens of ms per call, and the live frontend polls it from several
    sockets at once. Run on the event-loop thread (as it was), each poll
    blocked the single uvicorn loop long enough to starve every other
    request and WebSocket publish -- the ->2018 live freeze.

    This is pulled into a plain sync function so get_active_sessions can
    hand it to asyncio.to_thread() and keep the loop free, mirroring the
    offload the specs scan got in be3496a8.
    """
    sessions = _get_sessions()
    seen = {s["session_id"] for s in sessions}
    for extra in _agent_sessions():
        if extra["session_id"] not in seen:
            sessions.append(extra)
            seen.add(extra["session_id"])
    # Also pick up every Claude Code session that is writing its
    # transcript right now, even if it never called /register.
    for extra in _claude_code_transcript_sessions():
        if extra["session_id"] not in seen:
            sessions.append(extra)
            seen.add(extra["session_id"])
    # Drop zombie backend-self sessions. Uvicorn hot reloads leave behind
    # one myos-api-NNN row per worker generation. The backend should not
    # count itself in the user's sidebar.
    sessions = [s for s in sessions if not _is_backend_self_session(s["session_id"])]
    # Re-sort newest first after the merge.
    sessions.sort(key=lambda s: s["last_active"], reverse=True)
    return {
        "sessions": sessions,
        "count": len(sessions),
        "active_count": sum(1 for s in sessions if s["status"] == "active"),
        "idle_count": sum(1 for s in sessions if s["status"] == "idle"),
    }


# ---------------------------------------------------------------------------
# Phase D: Cross-session digest (→2455)
# ---------------------------------------------------------------------------

_DIGEST_TTL_SECONDS = 10.0
_digest_cache: Optional[dict] = None
_digest_cache_ts: float = 0.0
_digest_refresh_lock = asyncio.Lock()


def _build_digest_sync(sessions_dir: Path) -> list[dict]:
    """Build a per-session summary of TODAY's activity from event files.

    Synchronous filesystem read — must be called inside asyncio.to_thread.

    Returns a list of dicts:
        {
            "session_id": str,
            "label": str,
            "activity_count": int,        # events with today's UTC timestamp
            "files_touched": list[str],   # unique file paths written today
            "recent_activity": str,       # description of the most recent tool call
        }
    Only sessions that have at least one event today (UTC midnight) are included.
    """
    if not sessions_dir.is_dir():
        return []

    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    results: list[dict] = []

    for entry in sessions_dir.iterdir():
        if not entry.is_dir():
            continue
        sid = entry.name
        if _is_backend_self_session(sid):
            continue
        events_file = entry / "events.jsonl"
        if not events_file.exists():
            continue

        # Read ALL lines (not just tail) for a full-day picture
        try:
            raw_lines = events_file.read_text(errors="replace").splitlines()
        except OSError:
            continue

        activity_count = 0
        files_touched: list[str] = []
        seen_files: set[str] = set()
        recent_activity = ""

        for ln in raw_lines:
            if not ln.strip():
                continue
            try:
                ev = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            ts_str = ev.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ts < today_utc:
                continue

            activity_count += 1

            data = ev.get("data", {})
            if not isinstance(data, dict):
                continue
            tool = data.get("tool", "")
            raw_input = data.get("input", "")

            # Collect file paths from write tools
            if tool in _FILE_WRITE_TOOLS:
                path_str = ""
                if isinstance(raw_input, str):
                    try:
                        parsed = json.loads(raw_input)
                        path_str = parsed.get("path", "")
                    except (json.JSONDecodeError, ValueError, AttributeError):
                        pass
                elif isinstance(raw_input, dict):
                    path_str = raw_input.get("path", "")
                if path_str and path_str not in seen_files:
                    files_touched.append(path_str)
                    seen_files.add(path_str)

        if activity_count == 0:
            continue

        # Derive recent_activity from the last event in today's window
        for ln in reversed(raw_lines):
            if not ln.strip():
                continue
            try:
                ev = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            ts_str = ev.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ts < today_utc:
                continue
            data = ev.get("data", {})
            if not isinstance(data, dict):
                break
            tool = data.get("tool", "")
            if tool:
                _TOOL_LABELS = {
                    "bash": "Running command",
                    "shell": "Running command",
                    "search": "Searching",
                    "fs_ops": "Writing files",
                    "edit": "Editing file",
                    "write": "Writing file",
                    "fs_write": "Writing file",
                    "Write": "Writing file",
                    "Edit": "Editing file",
                    "read": "Reading file",
                    "fs_read": "Reading file",
                    "Read": "Reading file",
                    "needle": "Managing tasks",
                }
                label = _TOOL_LABELS.get(tool, tool)
                raw_input = data.get("input", "")
                snippet = ""
                if isinstance(raw_input, str) and raw_input:
                    snippet = raw_input[:60].strip()
                elif isinstance(raw_input, dict):
                    snippet = str(raw_input.get("cmd") or raw_input.get("path") or "")[:60]
                recent_activity = f"{label}: {snippet}" if snippet else label
            break

        results.append({
            "session_id": sid,
            "label": sid,  # caller may enrich with agent task name
            "activity_count": activity_count,
            "files_touched": files_touched[:10],
            "recent_activity": recent_activity,
        })

    results.sort(key=lambda s: s["activity_count"], reverse=True)
    return results


async def _get_closed_tasks_today() -> list[dict]:
    """Return tasks closed today (UTC). Async — calls ostk."""
    from services.ostk import ostk

    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        tasks = await ostk.list_tasks(status="closed")
    except Exception:
        return []

    result = []
    for t in tasks:
        closed_at_str = t.get("closed_at") or ""
        if not closed_at_str:
            continue
        try:
            closed_at = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if closed_at >= today_utc:
            result.append({
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "closed_at": closed_at_str,
            })
    return result


async def get_session_digest() -> dict:
    """Return the full cross-session digest with TTL caching (→2455).

    Offloads filesystem work to a thread (→2018 pattern).
    """
    global _digest_cache, _digest_cache_ts
    now = time.monotonic()
    if _digest_cache is not None and (now - _digest_cache_ts) < _DIGEST_TTL_SECONDS:
        return _digest_cache

    async with _digest_refresh_lock:
        now = time.monotonic()
        if _digest_cache is not None and (now - _digest_cache_ts) < _DIGEST_TTL_SECONDS:
            return _digest_cache

        # Enrich session list with agent labels from in-memory metadata
        from routers.agents import agent_metadata

        raw_sessions = await asyncio.to_thread(_build_digest_sync, _effective_sessions_dir())

        # Enrich label with agent task name where available
        meta_snap = dict(agent_metadata)
        for s in raw_sessions:
            sid = s["session_id"]
            meta = meta_snap.get(sid, {})
            task = meta.get("task") or ""
            if task:
                s["label"] = task

        closed_tasks = await _get_closed_tasks_today()

        result = {
            "sessions": raw_sessions,
            "closed_tasks_today": closed_tasks,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _digest_cache = result
        _digest_cache_ts = time.monotonic()
        return result


def reset_session_caches() -> None:
    """Clear short-lived coordination and digest caches when active_repo_path changes."""
    global _coord_cache, _coord_cache_ts, _digest_cache, _digest_cache_ts
    _coord_cache = None
    _coord_cache_ts = 0.0
    _digest_cache = None
    _digest_cache_ts = 0.0
    reset_runtime_sessions_cache()


@router.get("/sessions/digest")
async def get_digest():
    """Cross-session digest for today: what each seat worked on, files touched,
    and tasks closed. Answers 'what did my other sessions do today?'
    """
    return await get_session_digest()


@router.get("/sessions/active")
async def get_active_sessions(include_external: Optional[bool] = Query(default=None)):
    """Return all sessions that have written events in the last 30 minutes,
    merged with every live agent record (Claude Code tabs, chat turns,
    spawned fleet members). Without the merge the counter was always zero
    for tabs that never ran an ostk command.

    The actual scan does synchronous file I/O over every session dir and
    Claude Code transcript, so it is offloaded to a worker thread
    (->2018): on the event loop it blocked every other request and WS
    publish under the live frontend's multi-socket polling.
    """
    payload = await asyncio.to_thread(_build_active_sessions)
    if _should_discover_external(include_external):
        discover_fn = _resolve_discover_callable()
        external_sessions = await discover_fn()
        merged = _merge_external_sessions(payload["sessions"], external_sessions)
        return {
            "sessions": merged,
            "count": len(merged),
            "active_count": sum(1 for s in merged if s.get("status") == "active"),
            "idle_count": sum(1 for s in merged if s.get("status") == "idle"),
        }
    for row in payload["sessions"]:
        row.pop("_transcript_only", None)
    return payload

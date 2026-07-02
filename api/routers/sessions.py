"""Live sessions endpoint.

Reads .ostk/sessions/*/events.jsonl to report which ostk sessions are
currently active, idle, or stale. The endpoint only reads the last few
lines of each file so it stays fast even with long-running sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import OSTK_DIR
from services.session_events import bus as _session_events_bus

router = APIRouter(tags=["sessions"])

SESSIONS_DIR = OSTK_DIR / "sessions"

# Thresholds
ACTIVE_CUTOFF_MINUTES = 5
IDLE_CUTOFF_MINUTES = 30

# Stuck detection: a session that went quiet between these two windows
# is "stuck" — quiet longer than finishing naturally, not yet idle long.
STUCK_MIN_SECONDS = 90
STUCK_MAX_SECONDS = 600  # 10 min

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


def _get_sessions() -> list[dict]:
    if not SESSIONS_DIR.is_dir():
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=IDLE_CUTOFF_MINUTES)
    results = []

    for entry in SESSIONS_DIR.iterdir():
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
        })

    # Sort: active first, then by most recent
    results.sort(key=lambda s: s["last_active"], reverse=True)
    return results


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
    from config import PROJECT_ROOT
    cwd_tag = str(PROJECT_ROOT).replace("/", "-")
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
    if not SESSIONS_DIR.is_dir():
        return 0

    backend_dirs = [
        entry for entry in SESSIONS_DIR.iterdir()
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


def _session_type(session_id: str) -> str:
    if session_id.startswith("claude-code-"):
        return "claude-code"
    if session_id.startswith("agent-"):
        return "agent"
    if session_id.startswith("chat-"):
        return "chat"
    return "ostk"


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
) -> tuple[list[dict], list[dict]]:
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
        if "stuck" in s:
            stuck = s["stuck"]
        else:
            try:
                la = datetime.fromisoformat(s["last_active"].replace("Z", "+00:00"))
                stuck = _is_stuck(la, now_utc)
            except (ValueError, TypeError, KeyError):
                stuck = False
        sessions.append({
            "id": sid,
            "name": task or sid,
            "label": label,
            "type": _session_type(sid),
            "started_at": meta.get("spawned_at"),
            "last_active_at": s["last_active"],
            "status": s["status"],
            "activity": activity,
            "recent_files": recent_files,
            "stuck": stuck,
        })

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
    return sessions, events


async def _build_coordination_data() -> dict:
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

    sessions, events = await asyncio.to_thread(
        _gather_sessions_and_events_sync, meta_snapshot, nudge_snapshot
    )

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


@router.get("/sessions/coordination")
async def get_coordination():
    """Visible coordination panel: sessions, locks, and recent inter-session events."""
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


@router.get("/sessions/active")
async def get_active_sessions():
    """Return all sessions that have written events in the last 30 minutes,
    merged with every live agent record (Claude Code tabs, chat turns,
    spawned fleet members). Without the merge the counter was always zero
    for tabs that never ran an ostk command.

    The actual scan does synchronous file I/O over every session dir and
    Claude Code transcript, so it is offloaded to a worker thread
    (->2018): on the event loop it blocked every other request and WS
    publish under the live frontend's multi-socket polling.
    """
    return await asyncio.to_thread(_build_active_sessions)

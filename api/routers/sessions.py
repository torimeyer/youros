"""Live sessions endpoint.

Reads .ostk/sessions/*/events.jsonl to report which ostk sessions are
currently active, idle, or stale. The endpoint only reads the last few
lines of each file so it stays fast even with long-running sessions.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from config import OSTK_DIR

router = APIRouter(tags=["sessions"])

SESSIONS_DIR = OSTK_DIR / "sessions"

# Thresholds
ACTIVE_CUTOFF_MINUTES = 5
IDLE_CUTOFF_MINUTES = 30

# How many bytes to read from the end of each events.jsonl to find recent events.
TAIL_BYTES = 8192


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

        results.append({
            "session_id": entry.name,
            "last_active": last_ts_str,
            "status": status,
            "recent_events": recent_events,
        })

    # Sort: active first, then by most recent
    results.sort(key=lambda s: s["last_active"], reverse=True)
    return results


@router.get("/sessions/active")
async def get_active_sessions():
    """Return all sessions that have written events in the last 30 minutes."""
    sessions = _get_sessions()
    return {
        "sessions": sessions,
        "count": len(sessions),
        "active_count": sum(1 for s in sessions if s["status"] == "active"),
        "idle_count": sum(1 for s in sessions if s["status"] == "idle"),
    }

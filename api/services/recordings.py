"""Terminal recording service using asciicast v2 format.

Recordings are stored in ~/.myos/recordings/ as <id>.cast files.
The asciicast v2 format is:
  Line 1: JSON header {"version": 2, "width": ..., "height": ..., "timestamp": ..., "title": ...}
  Lines 2+: JSON arrays [elapsed_seconds, event_type, data]
    event_type "o" = output, "i" = input
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional

RECORDINGS_DIR = Path.home() / ".myos" / "recordings"

# In-memory: active recording sessions (cleared on server restart)
_sessions: dict[str, dict] = {}


def _ensure_dir() -> Path:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def start_recording(label: str = "", width: int = 80, height: int = 24) -> dict:
    """Create a new recording session, write the asciicast v2 header, return info."""
    _ensure_dir()
    rec_id = str(uuid.uuid4())[:8]
    path = RECORDINGS_DIR / f"{rec_id}.cast"

    started = time.time()
    header = {
        "version": 2,
        "width": width,
        "height": height,
        "timestamp": int(started),
        "title": label or f"Recording {rec_id}",
    }
    path.write_text(json.dumps(header) + "\n")

    _sessions[rec_id] = {
        "id": rec_id,
        "path": str(path),
        "label": header["title"],
        "started_at": started,
        "status": "recording",
    }

    return {"id": rec_id, "status": "recording", "label": header["title"]}


async def run_command(rec_id: str, cmd: str) -> dict:
    """Run a shell command and append input + output events to the cast file."""
    session = _sessions.get(rec_id)
    if not session or session["status"] != "recording":
        raise ValueError(f"No active recording: {rec_id}")

    path = Path(session["path"])
    base_time = session["started_at"]

    elapsed_i = time.time() - base_time

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        returncode = proc.returncode or 0
    except asyncio.TimeoutError:
        output = "[command timed out]\r\n"
        returncode = -1
    except Exception as e:
        output = f"[error: {e}]\r\n"
        returncode = -1

    elapsed_o = time.time() - base_time

    # Write prompt + input, then output
    prompt_event = json.dumps([round(elapsed_i, 6), "o", f"$ {cmd}\r\n"])
    output_text = output.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    output_event = json.dumps([round(elapsed_o, 6), "o", output_text])

    with open(path, "a") as f:
        f.write(prompt_event + "\n")
        f.write(output_event + "\n")

    return {"output": output, "returncode": returncode}


def stop_recording(rec_id: str) -> dict:
    """Finalize the recording."""
    session = _sessions.get(rec_id)
    if not session:
        raise ValueError(f"No recording found: {rec_id}")

    session["status"] = "complete"
    duration = time.time() - session["started_at"]

    return {
        "id": rec_id,
        "status": "complete",
        "path": session["path"],
        "duration": round(duration, 2),
    }


def list_recordings() -> list[dict]:
    """List all recordings in ~/.myos/recordings/ newest first."""
    _ensure_dir()
    results = []
    cast_files = sorted(
        RECORDINGS_DIR.glob("*.cast"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for cast_file in cast_files:
        try:
            with open(cast_file) as f:
                header_line = f.readline()
            header = json.loads(header_line)
            rec_id = cast_file.stem
            session = _sessions.get(rec_id, {})
            results.append({
                "id": rec_id,
                "title": header.get("title", rec_id),
                "started_at": header.get("timestamp", 0),
                "width": header.get("width", 80),
                "height": header.get("height", 24),
                "size_bytes": cast_file.stat().st_size,
                "status": session.get("status", "complete"),
            })
        except Exception:
            continue
    return results


def get_cast_path(rec_id: str) -> Optional[Path]:
    """Return the Path to a cast file, or None if not found.

    Sanitizes the ID to prevent path traversal.
    """
    _ensure_dir()
    safe_id = "".join(c for c in rec_id if c.isalnum() or c == "-")
    path = RECORDINGS_DIR / f"{safe_id}.cast"
    return path if path.exists() else None

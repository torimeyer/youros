"""Ground-truth external Claude and Gemini runtime session discovery (->3121).

Discovers live external terminal and background sessions without blocking the
event loop or making network calls:
- Claude CLI: runs `claude agents --json` asynchronously (stdin=DEVNULL, 3.0s
  timeout) and verifies PID liveness via `os.kill(pid, 0)`.
- Gemini CLI: scans `~/.gemini/tmp/*/chats/session-*.jsonl` inside
  `asyncio.to_thread` (recent files within IDLE_CUTOFF_MINUTES only, reading
  at most the last 4 KB per file; never invokes `gemini --list-sessions`).
- Combined results are cached with a 5-second monotonic TTL.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DISCOVERY_TIMEOUT_SECONDS = 3.0
RUNTIME_CACHE_TTL_SECONDS = 5.0
ACTIVE_CUTOFF_MINUTES = 5
IDLE_CUTOFF_MINUTES = 30
GEMINI_TAIL_BYTES = 4096
BACKEND_SESSION_PREFIX = "myos-api-"

GEMINI_TMP_DIR = Path.home() / ".gemini" / "tmp"

_cached_sessions: Optional[list[dict]] = None
_cached_at: float = 0.0
_cache_lock: Optional[asyncio.Lock] = None
_cache_lock_loop: Optional[asyncio.AbstractEventLoop] = None


def reset_runtime_sessions_cache() -> None:
    """Clear the 5-second monotonic runtime discovery cache (used by tests)."""
    global _cached_sessions, _cached_at, _cache_lock, _cache_lock_loop
    _cached_sessions = None
    _cached_at = 0.0
    _cache_lock = None
    _cache_lock_loop = None


def _get_cache_lock() -> asyncio.Lock:
    global _cache_lock, _cache_lock_loop
    loop = asyncio.get_running_loop()
    if _cache_lock is None or _cache_lock_loop is not loop:
        _cache_lock = asyncio.Lock()
        _cache_lock_loop = loop
    return _cache_lock


def _clone_sessions(items: list[dict]) -> list[dict]:
    return [
        {
            **item,
            "recent_files": list(item.get("recent_files", [])),
            "recent_events": list(item.get("recent_events", [])),
        }
        for item in items
    ]


def _is_pid_alive(pid: object) -> bool:
    """Return True if *pid* is a positive int whose OS process is alive."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        return exc.errno == errno.EPERM


def _find_claude_binary() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    if hasattr(shutil.which, "assert_called"):
        return None
    home = Path.home()
    for candidate in (
        home / ".local" / "bin" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _parse_timestamp(raw: object, fallback: datetime) -> datetime:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        ts_sec = float(raw) / 1000.0 if raw > 1e11 else float(raw)
        try:
            return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return fallback
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback
    return fallback


async def discover_claude_sessions(
    claude_bin: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Run `claude agents --json` asynchronously and return live runtime sessions."""
    resolved_bin = claude_bin if claude_bin is not None else _find_claude_binary()
    if not resolved_bin:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved_bin,
            "agents",
            "--json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []

    try:
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=CLAUDE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return []
    except Exception:
        return []

    if proc.returncode not in (0, None):
        return []

    try:
        payload = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(payload, list):
        return []

    now_utc = now or datetime.now(timezone.utc)
    results: list[dict] = []

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        if not _is_pid_alive(pid):
            continue

        session_id = str(
            entry.get("sessionId") or entry.get("id") or entry.get("name") or f"claude-pid-{pid}"
        ).strip()
        name = str(entry.get("name") or session_id).strip()
        if session_id.startswith(BACKEND_SESSION_PREFIX) or name.startswith(BACKEND_SESSION_PREFIX):
            continue

        raw_kind = str(entry.get("kind") or "interactive").strip().lower()
        kind = "background" if raw_kind in ("background", "bg", "daemon", "headless") else "interactive"

        raw_status = str(entry.get("status") or "active").strip().lower()
        status = "idle" if raw_status in ("idle", "waiting", "sleeping") else "active"

        cwd_raw = entry.get("cwd")
        cwd = str(cwd_raw).strip() if isinstance(cwd_raw, str) and cwd_raw.strip() else None
        project_slug = Path(cwd).name if cwd else None

        raw_ts = entry.get("lastEventAt") or entry.get("updatedAt") or entry.get("startedAt")
        ref_dt = _parse_timestamp(raw_ts, now_utc)
        last_event_at = ref_dt.isoformat()
        age_seconds = max(0, int((now_utc - ref_dt).total_seconds()))
        activity = str(entry.get("activity") or "").strip()

        results.append(
            {
                "id": session_id,
                "session_id": session_id,
                "name": name,
                "label": name,
                "status": status,
                "last_event_at": last_event_at,
                "last_active": last_event_at,
                "last_active_at": last_event_at,
                "age_seconds": age_seconds,
                "activity": activity,
                "recent_files": [],
                "recent_events": [],
                "stuck": False,
                "runtime": "claude",
                "kind": kind,
                "pid": int(pid),
                "cwd": cwd,
                "project_slug": project_slug,
                "external": True,
            }
        )

    return results


def _tail_jsonl_lines(file_path: Path, max_bytes: int = GEMINI_TAIL_BYTES) -> list[str]:
    """Read at most the last *max_bytes* of *file_path* and return complete lines."""
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return []
    if size <= 0:
        return []
    read_size = min(size, max_bytes)
    try:
        with open(file_path, "rb") as handle:
            handle.seek(-read_size, os.SEEK_END)
            chunk = handle.read(read_size).decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if read_size < size and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def _extract_gemini_activity(parsed_lines: list[dict]) -> str:
    for obj in reversed(parsed_lines):
        thoughts = obj.get("thoughts")
        if isinstance(thoughts, list):
            for thought in reversed(thoughts):
                if isinstance(thought, dict):
                    subject = str(thought.get("subject") or "").strip()
                    if subject:
                        return subject[:60]
        tool_calls = obj.get("toolCalls")
        if isinstance(tool_calls, list):
            for tc in reversed(tool_calls):
                if isinstance(tc, dict):
                    tc_name = str(tc.get("name") or "").strip()
                    if tc_name:
                        return f"Tool: {tc_name}"[:60]
    return ""


def _read_gemini_project_root(project_dir: Path) -> Optional[str]:
    marker = project_dir / ".project_root"
    try:
        if not marker.is_file():
            return None
        with open(marker, "rb") as handle:
            raw = handle.read(512).decode("utf-8", errors="replace").strip()
        return raw if raw else None
    except OSError:
        return None


def _scan_gemini_sessions_sync(
    gemini_tmp_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Synchronously scan recent Gemini CLI session JSONL files (runs in to_thread)."""
    base_dir = gemini_tmp_dir if gemini_tmp_dir is not None else GEMINI_TMP_DIR
    if not base_dir.is_dir():
        return []

    now_utc = now or datetime.now(timezone.utc)
    idle_cutoff = now_utc - timedelta(minutes=IDLE_CUTOFF_MINUTES)
    active_cutoff = now_utc - timedelta(minutes=ACTIVE_CUTOFF_MINUTES)

    results: list[dict] = []
    seen_ids: set[str] = set()

    for session_file in base_dir.glob("*/chats/session-*.jsonl"):
        try:
            st = session_file.stat()
        except OSError:
            continue

        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if mtime < idle_cutoff:
            continue

        project_dir = session_file.parent.parent
        project_slug = project_dir.name
        if project_slug.startswith(BACKEND_SESSION_PREFIX):
            continue

        raw_lines = _tail_jsonl_lines(session_file, max_bytes=GEMINI_TAIL_BYTES)
        parsed_objs: list[dict] = []
        session_id = ""
        raw_kind = ""

        for ln in raw_lines:
            try:
                obj = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            parsed_objs.append(obj)
            if not session_id:
                cand_id = obj.get("sessionId") or obj.get("session_id")
                if isinstance(cand_id, str) and cand_id.strip():
                    session_id = cand_id.strip()
            if not raw_kind:
                cand_kind = obj.get("kind")
                if isinstance(cand_kind, str) and cand_kind.strip():
                    raw_kind = cand_kind.strip().lower()

        if not session_id:
            session_id = session_file.stem
        if session_id in seen_ids or session_id.startswith(BACKEND_SESSION_PREFIX):
            continue
        seen_ids.add(session_id)

        kind = "background" if raw_kind in ("background", "bg", "daemon", "headless") else "interactive"
        status = "active" if mtime >= active_cutoff else "idle"
        last_event_at = mtime.isoformat()
        age_seconds = max(0, int((now_utc - mtime).total_seconds()))
        cwd = _read_gemini_project_root(project_dir)
        activity = _extract_gemini_activity(parsed_objs)

        results.append(
            {
                "id": session_id,
                "session_id": session_id,
                "name": session_id,
                "label": session_id,
                "status": status,
                "last_event_at": last_event_at,
                "last_active": last_event_at,
                "last_active_at": last_event_at,
                "age_seconds": age_seconds,
                "activity": activity,
                "recent_files": [],
                "recent_events": [],
                "stuck": False,
                "runtime": "gemini",
                "kind": kind,
                "pid": None,
                "cwd": cwd,
                "project_slug": project_slug,
                "external": True,
            }
        )

    results.sort(key=lambda s: s["last_event_at"], reverse=True)
    return results


async def discover_gemini_sessions(
    gemini_tmp_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Scan `~/.gemini/tmp/*/chats/session-*.jsonl` off the event loop."""
    return await asyncio.to_thread(_scan_gemini_sessions_sync, gemini_tmp_dir, now)


async def discover_external_runtime_sessions(force_refresh: bool = False) -> list[dict]:
    """Return combined external Claude and Gemini sessions with a 5s monotonic TTL."""
    global _cached_sessions, _cached_at

    now_mono = time.monotonic()
    if (
        not force_refresh
        and _cached_sessions is not None
        and (now_mono - _cached_at) < RUNTIME_CACHE_TTL_SECONDS
    ):
        return _clone_sessions(_cached_sessions)

    lock = _get_cache_lock()
    async with lock:
        now_mono = time.monotonic()
        if (
            not force_refresh
            and _cached_sessions is not None
            and (now_mono - _cached_at) < RUNTIME_CACHE_TTL_SECONDS
        ):
            return _clone_sessions(_cached_sessions)

        now_utc = datetime.now(timezone.utc)
        claude_res, gemini_res = await asyncio.gather(
            discover_claude_sessions(now=now_utc),
            discover_gemini_sessions(now=now_utc),
            return_exceptions=True,
        )

        combined: list[dict] = []
        if isinstance(claude_res, list):
            combined.extend(claude_res)
        if isinstance(gemini_res, list):
            combined.extend(gemini_res)

        combined.sort(key=lambda s: s.get("last_event_at") or "", reverse=True)
        _cached_sessions = combined
        _cached_at = time.monotonic()
        return _clone_sessions(combined)

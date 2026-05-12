"""Kernel JSONL fleet reader — Tier 1.1 wave A.

Reads two ostk-written JSONL files and returns normalised agent rows in the
/api/agents shape so GET /api/agents can prefer kernel-authoritative data.

Sources:
  ~/.ostk/journal.jsonl  — global ostk lifecycle journal (agent.spawned,
                            agent.completed, agent.failed, agent.killed).
                            Agents spawned via `ostk run` (not via myOS FastAPI)
                            appear here first — they are the "peer-session" agents
                            myOS would otherwise never see.

  <OSTK_DIR>/agents.jsonl — kernel socket-connection registry (alias, pid,
                             status, registered_at, last_seen, trust_tier).
                             An active row confirms the process is still speaking
                             to the daemon; last_seen is a live heartbeat timestamp.

Both files are append-only; reads are cached by (file-size, mtime_ns) so
repeated GET /api/agents polls do not re-parse on each call.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default location of the global ostk journal
JOURNAL_PATH = Path("~/.ostk/journal.jsonl").expanduser()

# Per-file cache: str(path) → {"size": int, "mtime_ns": int, "rows": list[dict]}
_journal_cache: dict[str, dict] = {}
_socket_cache: dict[str, dict] = {}

# journal event → normalised status string
_LIFECYCLE_STATUS: dict[str, str] = {
    "agent.completed": "completed",
    "agent.failed":    "failed",
    "agent.killed":    "killed",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_jsonl_cached(path: Path, cache: dict[str, dict]) -> list[dict]:
    """Return parsed rows from *path*, using the cache when content is unchanged.

    Cache key is (file size, mtime_ns).  Returns [] on any I/O or parse error,
    and also when the file does not exist.
    """
    if not path.exists():
        return []
    try:
        stat = path.stat()
    except OSError:
        return []

    key = str(path)
    cached = cache.get(key)
    if (
        cached is not None
        and cached["size"] == stat.st_size
        and cached["mtime_ns"] == stat.st_mtime_ns
    ):
        return cached["rows"]

    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "rows": rows}
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_journal_agents(journal_path: Optional[Path] = None) -> list[dict]:
    """Parse ~/.ostk/journal.jsonl and return normalised agent rows.

    Each row carries at minimum:
      name, source="kernel", status, spawned_at, last_heartbeat_at, model, budget.

    Status is derived from the last lifecycle event seen for each agent name:
      agent.spawned   → "running"
      agent.completed → "completed"
      agent.failed    → "failed"
      agent.killed    → "killed"

    Returns [] when the file is missing, empty, or contains no agent events.
    """
    path = journal_path or JOURNAL_PATH
    entries = _read_jsonl_cached(path, _journal_cache)

    agents: dict[str, dict] = {}
    for entry in entries:
        event = entry.get("event", "")
        name = entry.get("name")
        ts = entry.get("timestamp", "")

        if event == "agent.spawned" and name:
            agents[name] = {
                "name": name,
                "source": "kernel",
                "status": "running",
                "spawned_at": ts,
                "last_heartbeat_at": ts,
                "model": entry.get("model") or "",
                "budget": str(entry.get("budget") or ""),
                "transcript_bytes": 0,
                "transcript_lines": 0,
            }
        elif event in _LIFECYCLE_STATUS and name and name in agents:
            agents[name]["status"] = _LIFECYCLE_STATUS[event]
            agents[name]["completed_at"] = ts

    return list(agents.values())


def read_socket_connections(socket_agents_path: Optional[Path] = None) -> list[dict]:
    """Parse <OSTK_DIR>/agents.jsonl and return raw socket-connection rows.

    Each row has at minimum: alias, pid, status, registered_at, last_seen.
    Returns [] when *socket_agents_path* is None or the file is missing.
    """
    if socket_agents_path is None:
        return []
    return _read_jsonl_cached(socket_agents_path, _socket_cache)


def read_kernel_fleet(
    *,
    journal_path: Optional[Path] = None,
    socket_agents_path: Optional[Path] = None,
) -> list[dict]:
    """Return normalised kernel fleet rows from ~/.ostk/journal.jsonl.

    Journal-sourced rows are the primary value: agents spawned via `ostk run`
    outside myOS appear here and are surfaced to GET /api/agents as peer-session
    agents.

    Socket connections (socket_agents_path) are also read but not yet merged
    into journal rows because agent.spawned events carry no PID field — there
    is no shared key to join on.  Future waves can add that join once ostk
    emits PIDs in agent.spawned.

    Returns [] when both files are missing or contain no agent events.
    """
    return read_journal_agents(journal_path)

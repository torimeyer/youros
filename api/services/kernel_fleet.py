"""Read the ostk kernel's own fleet registry (.ostk/agents.jsonl).

The kernel writes one JSON line per registered process:
  {"alias": "...", "pid": N, "status": "active"|"inactive",
   "registered_at": "<iso>", "last_seen": "<iso>", "trust_tier": "t2"}

We normalise these rows to match the shape expected by the /api/agents
GET handler so kernel-registered agents surface in the fleet view as soon
as the kernel knows about them, even before an HTTP /register call arrives.

Priority contract: this module is purely additive. Callers merge kernel
rows first, then let in-memory agent_metadata overwrite. The kernel is a
better baseline than the audit log but is not ground truth for status
transitions recorded by the API layer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# Cache entry: (file mtime, list[dict])
_cache: dict[str, tuple[float, list[dict]]] = {}

_STATUS_MAP = {
    "active": "running",
    "inactive": "stopped",
}


def _normalise_row(raw: dict) -> Optional[dict]:
    """Convert a raw agents.jsonl row to the /api/agents shape.

    Returns None for rows that are missing the required 'alias' field.
    """
    alias = raw.get("alias")
    if not alias:
        return None
    raw_status = raw.get("status", "")
    status = _STATUS_MAP.get(raw_status, raw_status)
    row: dict = {
        "name": alias,
        "source": "kernel",
        "status": status,
    }
    registered_at = raw.get("registered_at")
    if registered_at:
        row["spawned_at"] = registered_at
    last_seen = raw.get("last_seen")
    if last_seen:
        row["last_heartbeat_at"] = last_seen
    pid = raw.get("pid")
    if pid is not None:
        row["pid"] = pid
    trust_tier = raw.get("trust_tier")
    if trust_tier:
        row["trust_tier"] = trust_tier
    return row


def read_kernel_fleet(ostk_dir: Optional[Path] = None) -> list[dict]:
    """Parse OSTK_DIR/agents.jsonl and return normalised rows.

    Results are cached by file mtime so repeated calls within the same
    request cycle are free. Returns [] if the file is missing or empty.

    Args:
        ostk_dir: Override the directory containing agents.jsonl.  If
            None, the value is read from config.OSTK_DIR at call time so
            tests can inject a temp directory.
    """
    if ostk_dir is None:
        from config import OSTK_DIR as _OSTK_DIR  # lazy import; avoids circular at module load
        ostk_dir = _OSTK_DIR

    agents_path = Path(ostk_dir) / "agents.jsonl"
    cache_key = str(agents_path)

    try:
        mtime = agents_path.stat().st_mtime
    except OSError:
        return []

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return list(cached[1])

    rows: list[dict] = []
    try:
        with agents_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                normalised = _normalise_row(raw)
                if normalised is not None:
                    rows.append(normalised)
    except OSError:
        return []

    _cache[cache_key] = (mtime, rows)
    return list(rows)


def merge_kernel_fleet(agents_map: dict[str, dict], ostk_dir: Optional[Path] = None) -> None:
    """Merge kernel fleet rows into agents_map in-place.

    Kernel rows are written first (lowest priority in the merge sequence)
    so that subsequent in-memory / persisted-metadata / daemon passes can
    overwrite them. Only inserts rows for names not already present so
    that existing audit-log entries are not silently clobbered.

    In practice: a kernel row supplies early visibility for an agent that
    registered with the ostk kernel but has not yet called the HTTP
    /register endpoint. Once the HTTP layer sees it, the in-memory row
    takes over.
    """
    for row in read_kernel_fleet(ostk_dir):
        name = row["name"]
        if name not in agents_map:
            agents_map[name] = row

"""Read .ostk/agents.jsonl directly and return in kernel_ps() format.

Replaces the fragile text-parsing path (ostk kernel ps → fixed-width table)
when MYOS_AGENTS_USE_REGISTRY=1.

Merge rule during dual-run period:
  - Registry provides: name (alias), pid, status, registered_at, last_seen.
  - agent_metadata provides: task, model, budget, transcript_path, source,
    and all other myOS-specific fields.
  - When both exist, registry wins for the fields it provides; agent_metadata
    overlays everything else. The overlay happens in _compute_agents_snapshot_async
    in routers/agents.py — this module only builds the ps_result dict that
    replaces the kernel_ps() return value.
"""
from __future__ import annotations

import json
from pathlib import Path

_OSTK_TO_MYOS_STATUS: dict[str, str] = {
    "active": "running",
    "inactive": "stopped",
    "stopped": "stopped",
    "running": "running",
}


def read_registry_jsonl(path: Path) -> list[dict]:
    """Parse .ostk/agents.jsonl and return raw rows. Missing file → []."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def registry_to_kernel_ps_format(rows: list[dict]) -> dict:
    """Convert .ostk/agents.jsonl rows to the dict kernel_ps() returns.

    Return shape: {"raw": "", "daemon_running": bool, "agents": [...]}.
    Each agent entry: {"name", "status", "source": "registry", ...extra fields}.
    """
    daemon_running = any(r.get("status") == "active" for r in rows)
    agents: list[dict] = []
    for row in rows:
        alias = row.get("alias") or row.get("name")
        if not alias:
            continue
        raw_status = row.get("status", "inactive")
        entry: dict = {
            "name": alias,
            "status": _OSTK_TO_MYOS_STATUS.get(raw_status, "stopped"),
            "source": "registry",
        }
        if (pid := row.get("pid")) is not None:
            entry["pid"] = pid
        if registered_at := row.get("registered_at"):
            entry["spawned_at"] = registered_at
        if last_seen := row.get("last_seen"):
            entry["last_heartbeat_at"] = last_seen
        if trust_tier := row.get("trust_tier"):
            entry["trust_tier"] = trust_tier
        agents.append(entry)
    return {"raw": "", "daemon_running": daemon_running, "agents": agents}


def read_registry_for_snapshot(ostk_dir: Path | None = None) -> dict:
    """Read .ostk/agents.jsonl and return in kernel_ps() format.

    ostk_dir defaults to OSTK_DIR from config when None.
    """
    if ostk_dir is None:
        from config import OSTK_DIR
        ostk_dir = OSTK_DIR
    rows = read_registry_jsonl(ostk_dir / "agents.jsonl")
    return registry_to_kernel_ps_format(rows)

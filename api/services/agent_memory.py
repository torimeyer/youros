"""Per-agent persistent memory.
Each agent gets a JSON file at ~/.youros/agent_memory/{agent_name}.json that
stores key/value facts and a rolling list of session summaries.
This file is stored outside the repo so git pull never clobbers it.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from services.atomic_io import atomic_write_json

# Storage root -- outside the repo, safe from git pull
AGENT_MEMORY_DIR = Path.home() / ".youros" / "agent_memory"

# Maximum number of session summaries to keep per agent
MAX_SUMMARIES = 10

# Default initial weight for new facts and summaries
DEFAULT_WEIGHT = 1.0


def _memory_path(agent_name: str) -> Path:
    return AGENT_MEMORY_DIR / f"{agent_name}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_fact(value) -> dict:
    """Upgrade a plain string fact (old format) to the enriched dict format."""
    if isinstance(value, str):
        return {
            "value": value,
            "weight": DEFAULT_WEIGHT,
            "saved_at": _now_iso(),
            "last_read_at": None,
            "read_count": 0,
        }
    return value


def _load(agent_name: str) -> dict:
    path = _memory_path(agent_name)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # Migrate old string-valued facts to enriched format
            migrated = False
            for k, v in list(data.get("facts", {}).items()):
                if isinstance(v, str):
                    data["facts"][k] = _migrate_fact(v)
                    migrated = True
            if migrated:
                _save(agent_name, data)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"facts": {}, "summaries": [], "config": {"reread_cadence_hours": 0.0}}


def _save(agent_name: str, data: dict) -> None:
    AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _memory_path(agent_name)
    atomic_write_json(path, data)


def save_memory(agent_name: str, key: str, value: str) -> None:
    """Store a key/value fact for an agent, preserving existing weight."""
    data = _load(agent_name)
    existing = data["facts"].get(key)
    weight = existing["weight"] if isinstance(existing, dict) else DEFAULT_WEIGHT
    last_read = existing.get("last_read_at") if isinstance(existing, dict) else None
    read_count = existing.get("read_count", 0) if isinstance(existing, dict) else 0
    data["facts"][key] = {
        "value": value,
        "weight": weight,
        "saved_at": _now_iso(),
        "last_read_at": last_read,
        "read_count": read_count,
    }
    _save(agent_name, data)


def get_memory(agent_name: str) -> dict:
    """Retrieve all stored facts and summaries for an agent."""
    return _load(agent_name)


def reinforce_memory(agent_name: str, key: str, delta: float = 1.0) -> Optional[float]:
    """Increase the weight of a fact. Returns new weight, or None if key not found."""
    data = _load(agent_name)
    fact = data["facts"].get(key)
    if fact is None:
        return None
    fact = _migrate_fact(fact)
    fact["weight"] = round(fact["weight"] + delta, 4)
    data["facts"][key] = fact
    _save(agent_name, data)
    return fact["weight"]


def penalize_memory(agent_name: str, key: str, delta: float = 1.0) -> Optional[float]:
    """Decrease the weight of a fact (floor 0.0). Returns new weight, or None if key not found."""
    data = _load(agent_name)
    fact = data["facts"].get(key)
    if fact is None:
        return None
    fact = _migrate_fact(fact)
    fact["weight"] = round(max(0.0, fact["weight"] - delta), 4)
    data["facts"][key] = fact
    _save(agent_name, data)
    return fact["weight"]


def set_reread_cadence(agent_name: str, hours: float) -> None:
    """Set how many hours must pass before a fact is included in context again.
    0 means no restriction (always include).
    """
    if hours < 0:
        raise ValueError("Cadence hours must be >= 0")
    data = _load(agent_name)
    if "config" not in data:
        data["config"] = {}
    data["config"]["reread_cadence_hours"] = float(hours)
    _save(agent_name, data)


def get_reread_cadence(agent_name: str) -> float:
    """Return the configured re-read cadence in hours (0 = no restriction)."""
    data = _load(agent_name)
    return float(data.get("config", {}).get("reread_cadence_hours", 0.0))


def append_summary(agent_name: str, summary: str) -> None:
    """Append a session summary, keeping only the last MAX_SUMMARIES."""
    data = _load(agent_name)
    data["summaries"].append({
        "text": summary,
        "weight": DEFAULT_WEIGHT,
        "saved_at": _now_iso(),
        "last_read_at": None,
        "read_count": 0,
    })
    data["summaries"] = data["summaries"][-MAX_SUMMARIES:]
    _save(agent_name, data)


def get_context(agent_name: str) -> str:
    """Return a formatted string of past facts and summaries.
    Facts are sorted by weight (highest first). Items read within the
    configured cadence window are skipped. Read timestamps are updated.
    """
    data = _load(agent_name)
    cadence_hours = float(data.get("config", {}).get("reread_cadence_hours", 0.0))
    now = datetime.now(timezone.utc)
    cutoff: Optional[datetime] = (
        now - timedelta(hours=cadence_hours) if cadence_hours > 0 else None
    )

    def _within_cadence(item: dict) -> bool:
        if cutoff is None:
            return False
        last = item.get("last_read_at")
        if not last:
            return False
        try:
            read_dt = datetime.fromisoformat(last)
            return read_dt > cutoff
        except ValueError:
            return False

    # Collect and sort facts by weight descending, skipping cadence-blocked ones
    fact_items = []
    for k, v in data.get("facts", {}).items():
        v = _migrate_fact(v)
        data["facts"][k] = v
        if not _within_cadence(v):
            fact_items.append((k, v))
    fact_items.sort(key=lambda x: x[1]["weight"], reverse=True)

    # Collect summaries respecting cadence
    summary_items = []
    for i, entry in enumerate(data.get("summaries", [])):
        if isinstance(entry, str):
            entry = {
                "text": entry,
                "weight": DEFAULT_WEIGHT,
                "saved_at": "",
                "last_read_at": None,
                "read_count": 0,
            }
            data["summaries"][i] = entry
        if not _within_cadence(entry):
            summary_items.append(entry)

    if not fact_items and not summary_items:
        return ""

    now_iso = now.isoformat()
    lines = [f"=== Memory from past sessions for agent '{agent_name}' ==="]

    if fact_items:
        lines.append("\nRemembered facts:")
        for k, v in fact_items:
            lines.append(f"  {k}: {v['value']}")
            v["last_read_at"] = now_iso
            v["read_count"] = v.get("read_count", 0) + 1

    if summary_items:
        lines.append("\nPast session summaries:")
        for entry in summary_items:
            saved_at = entry.get("saved_at", "")
            text = entry.get("text", "")
            date_str = saved_at[:10] if saved_at else ""
            prefix = f"  [{date_str}] " if date_str else "  "
            lines.append(f"{prefix}{text}")
            entry["last_read_at"] = now_iso
            entry["read_count"] = entry.get("read_count", 0) + 1

    lines.append("\n=== End of memory ===\n")

    # Persist updated read timestamps
    _save(agent_name, data)
    return "\n".join(lines)


def clear_memory(agent_name: str) -> None:
    """Wipe all memory for an agent."""
    path = _memory_path(agent_name)
    if path.exists():
        path.unlink()

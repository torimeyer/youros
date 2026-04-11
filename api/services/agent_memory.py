"""Per-agent persistent memory.

Each agent gets a JSON file at ~/.myos/agent_memory/{agent_name}.json that
stores key/value facts and a rolling list of session summaries.

This file is stored outside the repo so git pull never clobbers it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from services.atomic_io import atomic_write_json

# Storage root — outside the repo, safe from git pull
AGENT_MEMORY_DIR = Path.home() / ".myos" / "agent_memory"

# Maximum number of session summaries to keep per agent
MAX_SUMMARIES = 10


def _memory_path(agent_name: str) -> Path:
    return AGENT_MEMORY_DIR / f"{agent_name}.json"


def _load(agent_name: str) -> dict:
    path = _memory_path(agent_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"facts": {}, "summaries": []}


def _save(agent_name: str, data: dict) -> None:
    path = _memory_path(agent_name)
    atomic_write_json(path, data)


def save_memory(agent_name: str, key: str, value: str) -> None:
    """Store a key/value fact for an agent."""
    data = _load(agent_name)
    data["facts"][key] = value
    _save(agent_name, data)


def get_memory(agent_name: str) -> dict:
    """Retrieve all stored facts and summaries for an agent."""
    return _load(agent_name)


def append_summary(agent_name: str, summary: str) -> None:
    """Append a session summary string, keeping only the last MAX_SUMMARIES."""
    data = _load(agent_name)
    data["summaries"].append({
        "text": summary,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })
    data["summaries"] = data["summaries"][-MAX_SUMMARIES:]
    _save(agent_name, data)


def get_context(agent_name: str) -> str:
    """Return a formatted string of past facts and summaries.

    This is ready to prepend to a new agent prompt so the agent picks up
    where it left off.
    """
    data = _load(agent_name)
    facts = data.get("facts", {})
    summaries = data.get("summaries", [])

    if not facts and not summaries:
        return ""

    lines: list[str] = [
        f"=== Memory from past sessions for agent '{agent_name}' ===",
    ]

    if facts:
        lines.append("\nRemembered facts:")
        for k, v in facts.items():
            lines.append(f"  {k}: {v}")

    if summaries:
        lines.append("\nPast session summaries:")
        for entry in summaries:
            saved_at = entry.get("saved_at", "")
            text = entry.get("text", "")
            date_str = saved_at[:10] if saved_at else ""
            prefix = f"  [{date_str}] " if date_str else "  "
            lines.append(f"{prefix}{text}")

    lines.append("\n=== End of memory ===\n")
    return "\n".join(lines)


def clear_memory(agent_name: str) -> None:
    """Wipe all memory for an agent."""
    path = _memory_path(agent_name)
    if path.exists():
        path.unlink()

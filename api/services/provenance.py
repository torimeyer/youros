"""Provenance sidecar for agent-saved files.

Every file an agent writes gets a matching .provenance.json sidecar
so the user can always see who created it and why.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def write_sidecar(
    file_path: str | Path,
    agent_name: str,
    task_id: str | None = None,
    prompt_summary: str = "",
) -> None:
    """Write a .provenance.json sidecar next to file_path.

    Never raises — a failed write logs a warning and returns so the
    caller's file save is never blocked by this.
    """
    p = Path(file_path)
    sidecar = p.parent / (p.name + ".provenance.json")
    payload = {
        "agent_name": agent_name,
        "task_id": task_id or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt_summary": (prompt_summary or "")[:200],
    }
    try:
        sidecar.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        logger.warning("provenance: could not write sidecar for %s: %s", p, exc)


def read_sidecar(file_path: str | Path) -> dict | None:
    """Return the provenance dict for file_path, or None if no sidecar exists."""
    p = Path(file_path)
    sidecar = p.parent / (p.name + ".provenance.json")
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("provenance: could not read sidecar for %s: %s", p, exc)
        return None

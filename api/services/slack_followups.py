"""Slack follow-up storage service.

Flags are persisted to ~/.youros/slack_followups.json using the same
atomic-write pattern as other myOS data stores. All reads are
non-throwing: a missing or corrupted file returns an empty list.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
FOLLOWUPS_PATH = MYOS_DIR / "slack_followups.json"


def _load() -> list[dict]:
    """Load followups from disk. Returns empty list on any read error."""
    try:
        if not FOLLOWUPS_PATH.exists():
            return []
        return json.loads(FOLLOWUPS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    """Persist followups to disk atomically."""
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(FOLLOWUPS_PATH, rows)


def list_followups() -> list[dict]:
    """Return all follow-ups, newest first."""
    return _load()


def add_followup(
    channel_id: str,
    channel_name: str,
    ts: str,
    user: str,
    text: str,
    permalink: str,
) -> dict:
    """Flag a Slack message as a follow-up and return the new record."""
    row = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "ts": ts,
        "user": user,
        "text": text,
        "permalink": permalink,
    }
    rows = _load()
    rows.insert(0, row)
    _save(rows)
    return row


def remove_followup(followup_id: str) -> bool:
    """Remove a follow-up by id. Returns True if found and removed, False if not found."""
    rows = _load()
    new_rows = [r for r in rows if r.get("id") != followup_id]
    if len(new_rows) == len(rows):
        return False
    _save(new_rows)
    return True


def count() -> int:
    """Return the number of flagged follow-ups."""
    return len(_load())

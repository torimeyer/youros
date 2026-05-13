"""Read .ostk/ contents directly for the Files page browser.

Surfaces decisions (markdown docs), needles history (issues.jsonl),
and recent audit events (journal.jsonl) without needing FUSE.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import OSTK_DIR


def list_decisions(limit: int = 200) -> list[dict]:
    """Return .ostk/*.md files as decision entries, newest first."""
    if not OSTK_DIR.is_dir():
        return []

    entries = []
    for path in OSTK_DIR.glob("*.md"):
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            content = path.read_text(errors="replace")
        except OSError:
            content = ""
        # Use the first non-empty line as a title.
        title = ""
        for line in content.splitlines():
            stripped = line.lstrip("#").strip()
            if stripped:
                title = stripped
                break
        title = title or path.stem

        entries.append(
            {
                "name": path.name,
                "title": title,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "size": stat.st_size,
                "content": content,
            }
        )

    entries.sort(key=lambda e: e["modified_at"], reverse=True)
    return entries[:limit]


def list_needles_history(limit: int = 500) -> list[dict]:
    """Return all needle entries from .ostk/needles/issues.jsonl, newest first."""
    issues_path = OSTK_DIR / "needles" / "issues.jsonl"
    if not issues_path.is_file():
        return []

    entries = []
    try:
        for line in issues_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(obj)
    except OSError:
        return []

    # Sort by created_at descending; fall back to id for stability.
    def _sort_key(e: dict):
        return e.get("created_at") or ""

    entries.sort(key=_sort_key, reverse=True)
    return entries[:limit]


def list_audit_recent(limit: int = 100) -> list[dict]:
    """Return recent entries from .ostk/journal.jsonl, newest first."""
    journal_path = OSTK_DIR / "journal.jsonl"
    if not journal_path.is_file():
        return []

    entries = []
    try:
        for line in journal_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(obj)
    except OSError:
        return []

    # Reverse file order so newest events come first; then truncate.
    entries.reverse()
    return entries[:limit]

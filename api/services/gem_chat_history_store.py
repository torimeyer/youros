"""Per-gem chat history store.

Each Gem's conversation turns are persisted to
``~/.youros/gem_chat/<gem_id>.json`` — one file per Gem, so deleting a
Gem's history is a single unlink and there's no cross-gem data to sweep.

Turn schema (list of objects, ordered oldest-first):
    [
      {"role": "user",  "text": "...", "ts": "2026-05-16T12:00:00+00:00"},
      {"role": "model", "text": "...", "ts": "2026-05-16T12:00:01+00:00"},
    ]
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.atomic_io import atomic_write_json

GEM_CHAT_HISTORY_DIR = Path.home() / ".youros" / "gem_chat"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GemChatHistoryStore:
    def __init__(self, base_dir: Path = GEM_CHAT_HISTORY_DIR):
        self._base_dir = base_dir

    def _path(self, gem_id: str) -> Path:
        return self._base_dir / f"{gem_id}.json"

    def _ensure_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def load(self, gem_id: str) -> list[dict[str, Any]]:
        """Return all stored turns for a Gem, oldest-first. Empty list if none."""
        path = self._path(gem_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        return data

    def append_turns(self, gem_id: str, user_text: str, model_text: str) -> None:
        """Append a completed user+model exchange to the Gem's history."""
        self._ensure_dir()
        turns = self.load(gem_id)
        ts = _now_iso()
        turns.append({"role": "user", "text": user_text, "ts": ts})
        turns.append({"role": "model", "text": model_text, "ts": ts})
        atomic_write_json(self._path(gem_id), turns)

    def seed_turn(self, gem_id: str, role: str, text: str, ts: str | None = None) -> None:
        """Insert a single turn (e.g. a recovered message) as the first entry.

        If a history file already exists the turn is prepended; otherwise a
        new file is created with this single entry.
        """
        self._ensure_dir()
        existing = self.load(gem_id)
        entry: dict[str, Any] = {"role": role, "text": text, "ts": ts or _now_iso()}
        atomic_write_json(self._path(gem_id), [entry] + existing)

    def delete(self, gem_id: str) -> None:
        """Remove the history file for a Gem (called when the Gem is deleted)."""
        try:
            self._path(gem_id).unlink(missing_ok=True)
        except OSError:
            pass


gem_chat_history_store = GemChatHistoryStore()

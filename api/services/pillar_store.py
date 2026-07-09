"""Pillar tags for tasks and projects (spec S009 Track 0.2).

Tasks live in the ostk kernel and projects are derived from the
filesystem, so the pillar tag is stored in a sidecar file, mirroring
task_source_store. Stored in ~/.youros/pillars.json:

  {"tasks": {"→123": "Growth"}, "projects": {"card-compass": "Trust"}}

Items without entries read as None, which keeps every existing surface
behaving exactly as before when no pillars are configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

PILLARS_PATH = youros_home() / "pillars.json"

_KINDS = ("tasks", "projects")


class PillarStore:
    def __init__(self, path: Path = PILLARS_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_json(self._path, {})

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, data: dict) -> None:
        atomic_write_json(self._path, data)

    def get(self, kind: str, item_id: str) -> Optional[str]:
        """Return the pillar for one item, or None when untagged."""
        bucket = self._load().get(kind, {})
        if not isinstance(bucket, dict):
            return None
        return bucket.get(item_id)

    def get_all(self, kind: str) -> dict[str, str]:
        """Return every tagged item of one kind, keyed by item id."""
        bucket = self._load().get(kind, {})
        return bucket if isinstance(bucket, dict) else {}

    def set(self, kind: str, item_id: str, pillar: Optional[str]) -> None:
        """Set or clear an item's pillar. None or blank clears the entry."""
        if kind not in _KINDS or not item_id:
            return
        data = self._load()
        bucket = data.setdefault(kind, {})
        value = (pillar or "").strip()
        if value:
            bucket[item_id] = value
        else:
            bucket.pop(item_id, None)
        self._save(data)

    def remove(self, kind: str, item_id: str) -> None:
        """Drop an item's entry entirely (used on delete cleanup)."""
        data = self._load()
        bucket = data.get(kind)
        if isinstance(bucket, dict) and item_id in bucket:
            bucket.pop(item_id, None)
            self._save(data)


pillar_store = PillarStore()

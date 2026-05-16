"""Persistent side-store for task source and source_ref fields.

Stored in ~/.myos/task_source.json. Format:
  { "<task_id>": {"source": "slack", "source_ref": "slack:C123/..."} }

Existing tasks without entries read as None for both fields.
Only tasks with source set have entries here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json

TASK_SOURCE_PATH = Path.home() / ".myos" / "task_source.json"

_MAX_SOURCE_REF_LEN = 500

_VALID_SOURCES = {"slack", "meeting", "email", "gmail", "manual"}


class TaskSourceStore:
    def __init__(self, path: Path = TASK_SOURCE_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_json(self._path, {})

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, data: dict[str, dict]) -> None:
        atomic_write_json(self._path, data)

    def set_source(
        self,
        task_id: str,
        source: Optional[str],
        source_ref: Optional[str] = None,
    ) -> None:
        if not task_id:
            return
        data = self._load()
        if source is None and source_ref is None:
            data.pop(task_id, None)
        else:
            entry: dict = {}
            if source is not None:
                entry["source"] = source
            if source_ref is not None:
                entry["source_ref"] = source_ref[:_MAX_SOURCE_REF_LEN]
            data[task_id] = entry
        self._save(data)

    def get_source(self, task_id: str) -> dict:
        data = self._load()
        entry = data.get(task_id, {})
        return {
            "source": entry.get("source"),
            "source_ref": entry.get("source_ref"),
        }

    def get_all(self) -> dict[str, dict]:
        """Return the full store keyed by task_id."""
        return self._load()

    def remove_task(self, task_id: str) -> None:
        data = self._load()
        data.pop(task_id, None)
        self._save(data)


task_source_store = TaskSourceStore()

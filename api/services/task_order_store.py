"""Persistent storage for custom task sort order within each priority group.

Stored in ~/.youros/task_order.json. The format is a dict mapping task_id ->
sort_index (integer). Sort indices are relative within each priority group.
Tasks without an entry fall back to ordering by created_at.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.atomic_io import atomic_write_json

TASK_ORDER_PATH = Path.home() / ".youros" / "task_order.json"


class TaskOrderStore:
    def __init__(self, path: Path = TASK_ORDER_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_json(self._path, {})

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, int)}

    def _save(self, order: dict[str, int]) -> None:
        atomic_write_json(self._path, order)

    def get_order(self) -> dict[str, int]:
        """Return a mapping of task_id -> sort_index."""
        return self._load()

    def set_task_position(self, task_id: str, sort_index: int) -> None:
        """Assign a sort index to a task."""
        order = self._load()
        order[task_id] = sort_index
        self._save(order)

    def remove_task(self, task_id: str) -> None:
        """Remove a task from the order store (e.g. when deleted)."""
        order = self._load()
        order.pop(task_id, None)
        self._save(order)

    def apply_order(self, tasks: list[dict]) -> list[dict]:
        """Sort tasks using custom order within each priority group.

        Tasks with a custom sort index come first (sorted ascending).
        Tasks without a custom index follow, sorted by created_at ascending.
        """
        order = self._load()
        import sys

        def sort_key(task: dict):
            task_id = task.get("id", "")
            priority = task.get("priority", "P2")
            if task_id in order:
                return (priority, 0, order[task_id], "")
            created = task.get("created_at", "")
            return (priority, 1, 0, created)

        return sorted(tasks, key=sort_key)


task_order_store = TaskOrderStore()

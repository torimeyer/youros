"""Persistent storage for task-to-label assignments.

Stored in ~/.myos/task_labels.json as a dict mapping task_id -> list of label_ids.
This keeps label assignments separate from the ostk issues.jsonl format.
"""

from __future__ import annotations

import json
from pathlib import Path

TASK_LABELS_PATH = Path.home() / ".myos" / "task_labels.json"


class TaskLabelsStore:
    def __init__(self, path: Path = TASK_LABELS_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("{}")

    def _load(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, list[str]]):
        self._path.write_text(json.dumps(data, indent=2))

    def get_labels_for_task(self, task_id: str) -> list[str]:
        """Return list of label IDs assigned to a task."""
        return self._load().get(task_id, [])

    def get_all_assignments(self) -> dict[str, list[str]]:
        """Return the full task_id -> label_ids mapping."""
        return self._load()

    def assign_label(self, task_id: str, label_id: str) -> list[str]:
        """Add a label to a task. Returns the updated list of label IDs for that task."""
        data = self._load()
        if task_id not in data:
            data[task_id] = []
        if label_id not in data[task_id]:
            data[task_id].append(label_id)
        self._save(data)
        return data[task_id]

    def remove_label(self, task_id: str, label_id: str) -> list[str]:
        """Remove a label from a task. Returns the updated list of label IDs for that task."""
        data = self._load()
        if task_id in data:
            data[task_id] = [lid for lid in data[task_id] if lid != label_id]
            if not data[task_id]:
                del data[task_id]
        self._save(data)
        return data.get(task_id, [])

    def remove_label_from_all_tasks(self, label_id: str):
        """Remove a label from every task (used when deleting a label)."""
        data = self._load()
        changed = False
        to_delete = []
        for task_id, label_ids in data.items():
            if label_id in label_ids:
                data[task_id] = [lid for lid in label_ids if lid != label_id]
                changed = True
                if not data[task_id]:
                    to_delete.append(task_id)
        for task_id in to_delete:
            del data[task_id]
        if changed:
            self._save(data)

    def get_tasks_for_label(self, label_id: str) -> list[str]:
        """Return list of task IDs that have this label assigned."""
        data = self._load()
        return [task_id for task_id, label_ids in data.items() if label_id in label_ids]


task_labels_store = TaskLabelsStore()

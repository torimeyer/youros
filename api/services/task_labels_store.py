"""Persistent storage for task-to-label assignments.

Stored in ~/.youros/task_labels.json. Two top-level keys:

- ``assignments``: dict mapping task_id -> list of label IDs.
- ``auto_applied``: dict mapping task_id -> list of label IDs that were
  auto-suggested (so we can re-run suggestions without clobbering manually
  added labels).
- ``rejected``: dict mapping task_id -> list of label IDs the user explicitly
  removed from an auto-suggestion (so we never re-apply them).

Older versions of this file stored just the assignments dict at the top
level. Loading falls back to that legacy format gracefully.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

TASK_LABELS_PATH = youros_home() / "task_labels.json"


class TaskLabelsStore:
    def __init__(self, path: Path = TASK_LABELS_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_json(self._path, {"assignments": {}, "auto_applied": {}, "rejected": {}})

    def _load_full(self) -> dict:
        """Return the full structured store. Migrates legacy format on the fly."""
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"assignments": {}, "auto_applied": {}, "rejected": {}}
        if not isinstance(data, dict):
            return {"assignments": {}, "auto_applied": {}, "rejected": {}}
        # Legacy format: top-level dict of task_id -> list[str]
        if "assignments" not in data and all(isinstance(v, list) for v in data.values()):
            return {"assignments": data, "auto_applied": {}, "rejected": {}}
        return {
            "assignments": data.get("assignments") or {},
            "auto_applied": data.get("auto_applied") or {},
            "rejected": data.get("rejected") or {},
        }

    def _load(self) -> dict[str, list[str]]:
        """Backwards-compatible: return only the assignments dict."""
        return self._load_full().get("assignments", {})

    def _save_full(self, data: dict):
        atomic_write_json(self._path, data)

    def _save(self, assignments: dict[str, list[str]]):
        full = self._load_full()
        full["assignments"] = assignments
        self._save_full(full)

    def get_labels_for_task(self, task_id: str) -> list[str]:
        """Return list of label IDs assigned to a task."""
        return self._load().get(task_id, [])

    def get_all_assignments(self) -> dict[str, list[str]]:
        """Return the full task_id -> label_ids mapping."""
        return self._load()

    def get_auto_applied(self, task_id: str) -> list[str]:
        """Return label IDs that were auto-applied (not user-added) for this task."""
        return self._load_full().get("auto_applied", {}).get(task_id, [])

    def get_rejected(self, task_id: str) -> list[str]:
        """Return label IDs the user explicitly removed from an auto-suggestion."""
        return self._load_full().get("rejected", {}).get(task_id, [])

    def is_auto_applied(self, task_id: str, label_id: str) -> bool:
        return label_id in self.get_auto_applied(task_id)

    def assign_label(self, task_id: str, label_id: str, auto: bool = False) -> list[str]:
        """Add a label to a task. Returns the updated list of label IDs for that task.

        When ``auto`` is True the label is also tracked in the auto_applied
        map so it can be replaced on the next auto-suggestion run.
        """
        data = self._load_full()
        assignments = data["assignments"]
        if task_id not in assignments:
            assignments[task_id] = []
        if label_id not in assignments[task_id]:
            assignments[task_id].append(label_id)
        if auto:
            auto_map = data["auto_applied"]
            auto_map.setdefault(task_id, [])
            if label_id not in auto_map[task_id]:
                auto_map[task_id].append(label_id)
        self._save_full(data)
        return assignments[task_id]

    def remove_label(
        self, task_id: str, label_id: str, mark_rejected: bool = False
    ) -> list[str]:
        """Remove a label from a task. Returns the updated list of label IDs.

        When ``mark_rejected`` is True we record that the user explicitly
        rejected this label for this task so the auto-suggester does not
        re-apply it.
        """
        data = self._load_full()
        assignments = data["assignments"]
        if task_id in assignments:
            assignments[task_id] = [lid for lid in assignments[task_id] if lid != label_id]
            if not assignments[task_id]:
                del assignments[task_id]
        # Drop from auto_applied tracking if present.
        auto_map = data["auto_applied"]
        if task_id in auto_map:
            auto_map[task_id] = [lid for lid in auto_map[task_id] if lid != label_id]
            if not auto_map[task_id]:
                del auto_map[task_id]
        if mark_rejected:
            rejected = data["rejected"]
            rejected.setdefault(task_id, [])
            if label_id not in rejected[task_id]:
                rejected[task_id].append(label_id)
        self._save_full(data)
        return assignments.get(task_id, [])

    def replace_auto_applied(
        self, task_id: str, new_label_ids: list[str]
    ) -> list[str]:
        """Replace the set of auto-applied labels for a task.

        Manually added labels (anything not currently in auto_applied) are
        preserved. Returns the full updated list of label IDs for the task.
        """
        data = self._load_full()
        assignments = data["assignments"]
        auto_map = data["auto_applied"]

        previously_auto = set(auto_map.get(task_id, []))
        current = assignments.get(task_id, [])
        manual = [lid for lid in current if lid not in previously_auto]

        # Drop rejected labels from the new set.
        rejected = set(data["rejected"].get(task_id, []))
        filtered_new = [lid for lid in new_label_ids if lid not in rejected]

        merged: list[str] = list(manual)
        for lid in filtered_new:
            if lid not in merged:
                merged.append(lid)

        if merged:
            assignments[task_id] = merged
        elif task_id in assignments:
            del assignments[task_id]

        if filtered_new:
            auto_map[task_id] = list(filtered_new)
        elif task_id in auto_map:
            del auto_map[task_id]

        self._save_full(data)
        return assignments.get(task_id, [])

    def remove_label_from_all_tasks(self, label_id: str):
        """Remove a label from every task (used when deleting a label)."""
        data = self._load_full()
        assignments = data["assignments"]
        changed = False
        to_delete = []
        for task_id, label_ids in assignments.items():
            if label_id in label_ids:
                assignments[task_id] = [lid for lid in label_ids if lid != label_id]
                changed = True
                if not assignments[task_id]:
                    to_delete.append(task_id)
        for task_id in to_delete:
            del assignments[task_id]
        # Also strip from auto_applied tracking.
        auto_map = data["auto_applied"]
        for task_id in list(auto_map.keys()):
            if label_id in auto_map[task_id]:
                auto_map[task_id] = [lid for lid in auto_map[task_id] if lid != label_id]
                changed = True
                if not auto_map[task_id]:
                    del auto_map[task_id]
        if changed:
            self._save_full(data)

    def remove_task(self, task_id: str):
        """Remove all label assignments for a task (used when deleting a task)."""
        data = self._load_full()
        touched = False
        for key in ("assignments", "auto_applied", "rejected"):
            if task_id in data[key]:
                del data[key][task_id]
                touched = True
        if touched:
            self._save_full(data)

    def get_tasks_for_label(self, label_id: str) -> list[str]:
        """Return list of task IDs that have this label assigned."""
        data = self._load()
        return [task_id for task_id, label_ids in data.items() if label_id in label_ids]


task_labels_store = TaskLabelsStore()

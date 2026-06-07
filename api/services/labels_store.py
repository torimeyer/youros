"""Persistent storage for user-created labels.

Labels are stored in ~/.youros/labels.json as a simple list of dicts.
Each label has an id, name, and color.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text

LABELS_PATH = Path.home() / ".youros" / "labels.json"

# A palette of 10 pleasant colors for labels.
LABEL_COLORS = [
    "#ef4444",  # red
    "#f97316",  # orange
    "#eab308",  # yellow
    "#22c55e",  # green
    "#06b6d4",  # cyan
    "#3b82f6",  # blue
    "#8b5cf6",  # purple
    "#ec4899",  # pink
    "#6366f1",  # indigo
    "#14b8a6",  # teal
]


class LabelsStore:
    def __init__(self, path: Path = LABELS_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_text(self._path, "[]")

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data, list):
                return []
            return data
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, labels: list[dict]):
        atomic_write_json(self._path, labels)

    def list_labels(self) -> list[dict]:
        return self._load()

    def get_label(self, label_id: str) -> Optional[dict]:
        for label in self._load():
            if label.get("id") == label_id:
                return label
        return None

    def create_label(self, name: str, color: str) -> dict:
        labels = self._load()
        # Check for duplicate name
        for existing in labels:
            if existing.get("name", "").lower() == name.lower():
                raise ValueError(f"A label named '{name}' already exists")
        label = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "color": color,
        }
        labels.append(label)
        self._save(labels)
        return label

    def delete_label(self, label_id: str) -> bool:
        labels = self._load()
        original_len = len(labels)
        labels = [l for l in labels if l.get("id") != label_id]
        if len(labels) == original_len:
            return False
        self._save(labels)
        return True

    def update_label(self, label_id: str, name: Optional[str] = None, color: Optional[str] = None) -> Optional[dict]:
        labels = self._load()
        for label in labels:
            if label.get("id") == label_id:
                if name is not None:
                    # Check duplicate name (exclude self)
                    for other in labels:
                        if other["id"] != label_id and other.get("name", "").lower() == name.lower():
                            raise ValueError(f"A label named '{name}' already exists")
                    label["name"] = name
                if color is not None:
                    label["color"] = color
                self._save(labels)
                return label
        return None


labels_store = LabelsStore()

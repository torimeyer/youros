"""Persistent storage for task threads (mini-projects).

Threads let users group related tasks together under a name.
Stored in ~/.myos/threads.json as a list of thread objects.
Each thread has an id, name, and a list of task IDs (needle_ids).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text

THREADS_PATH = Path.home() / ".myos" / "threads.json"


class ThreadsStore:
    def __init__(self, path: Path = THREADS_PATH):
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

    def _save(self, threads: list[dict]):
        atomic_write_json(self._path, threads)

    def list_threads(self) -> list[dict]:
        return self._load()

    def get_thread(self, thread_id: str) -> Optional[dict]:
        for thread in self._load():
            if thread.get("id") == thread_id:
                return thread
        return None

    def create_thread(self, name: str, needle_ids: Optional[list[str]] = None) -> dict:
        threads = self._load()
        # Check for duplicate name
        for existing in threads:
            if existing.get("name", "").lower() == name.lower():
                raise ValueError(f"A group named '{name}' already exists")
        thread = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "needle_ids": needle_ids or [],
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        threads.append(thread)
        self._save(threads)
        return thread

    def update_thread(self, thread_id: str, name: Optional[str] = None) -> Optional[dict]:
        threads = self._load()
        for thread in threads:
            if thread.get("id") == thread_id:
                if name is not None:
                    # Check duplicate name (exclude self)
                    for other in threads:
                        if other["id"] != thread_id and other.get("name", "").lower() == name.lower():
                            raise ValueError(f"A group named '{name}' already exists")
                    thread["name"] = name
                self._save(threads)
                return thread
        return None

    def delete_thread(self, thread_id: str) -> bool:
        threads = self._load()
        original_len = len(threads)
        threads = [t for t in threads if t.get("id") != thread_id]
        if len(threads) == original_len:
            return False
        self._save(threads)
        return True

    def add_task_to_thread(self, thread_id: str, task_id: str) -> Optional[dict]:
        """Add a task to a thread. Returns the updated thread or None if not found."""
        threads = self._load()
        for thread in threads:
            if thread.get("id") == thread_id:
                needle_ids = thread.get("needle_ids", [])
                if task_id not in needle_ids:
                    needle_ids.append(task_id)
                    thread["needle_ids"] = needle_ids
                self._save(threads)
                return thread
        return None

    def remove_task_from_thread(self, thread_id: str, task_id: str) -> Optional[dict]:
        """Remove a task from a thread. Returns the updated thread or None if not found."""
        threads = self._load()
        for thread in threads:
            if thread.get("id") == thread_id:
                needle_ids = thread.get("needle_ids", [])
                thread["needle_ids"] = [nid for nid in needle_ids if nid != task_id]
                self._save(threads)
                return thread
        return None

    def remove_task_from_all_threads(self, task_id: str):
        """Remove a task from every thread (used when deleting a task)."""
        threads = self._load()
        changed = False
        for thread in threads:
            needle_ids = thread.get("needle_ids", [])
            if task_id in needle_ids:
                thread["needle_ids"] = [nid for nid in needle_ids if nid != task_id]
                changed = True
        if changed:
            self._save(threads)

    def get_thread_for_task(self, task_id: str) -> Optional[dict]:
        """Return the thread a task belongs to, or None."""
        for thread in self._load():
            if task_id in thread.get("needle_ids", []):
                return thread
        return None

    def get_all_task_thread_map(self) -> dict[str, str]:
        """Return a mapping of task_id -> thread_id for all assigned tasks."""
        result: dict[str, str] = {}
        for thread in self._load():
            for nid in thread.get("needle_ids", []):
                result[nid] = thread["id"]
        return result


threads_store = ThreadsStore()

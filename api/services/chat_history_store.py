"""Persistent storage for chat tabs and messages.

Stores the user's chat tabs (each with its name and message list) on disk
so they survive browser localStorage clears, hard refreshes, and switching
to another device. The frontend writes the full set of tabs on every change
(debounced) and reads them once on app boot.

File format (JSON at ~/.myos/chat_history.json):

    {
      "tabs": [
        {
          "id": "abc-123",
          "name": "First chat",
          "messages": [
            {"id": "...", "role": "user", "content": "hi", ...},
            ...
          ]
        }
      ],
      "active_tab_id": "abc-123"
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.atomic_io import atomic_write_json

CHAT_HISTORY_PATH = Path.home() / ".myos" / "chat_history.json"


def _default_state() -> dict[str, Any]:
    return {"tabs": [], "active_tab_id": ""}


class ChatHistoryStore:
    def __init__(self, path: Path = CHAT_HISTORY_PATH):
        self._path = path
        self._ensure_exists()

    def _ensure_exists(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_write_json(self._path, _default_state())

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return _default_state()
        if not isinstance(data, dict):
            return _default_state()
        # Be defensive about shape so a corrupt file does not crash the UI.
        tabs = data.get("tabs")
        if not isinstance(tabs, list):
            tabs = []
        active = data.get("active_tab_id")
        if not isinstance(active, str):
            active = ""
        return {"tabs": tabs, "active_tab_id": active}

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        tabs = data.get("tabs")
        if not isinstance(tabs, list):
            tabs = []
        active = data.get("active_tab_id")
        if not isinstance(active, str):
            active = ""
        payload = {"tabs": tabs, "active_tab_id": active}
        atomic_write_json(self._path, payload)
        return payload

    def get_prior_messages(self, current_tab_id: str = "", limit: int = 10) -> list[dict[str, str]]:
        """Each chat tab is fully isolated — no cross-tab context injection.

        Tabs behave like Warp terminal tabs: separate session, no shared
        state, no leakage. Always returns [] so build_memory_context is a
        no-op and a new tab never sees messages from a different tab.
        """
        return []

    def clear(self) -> None:
        atomic_write_json(self._path, _default_state())


chat_history_store = ChatHistoryStore()

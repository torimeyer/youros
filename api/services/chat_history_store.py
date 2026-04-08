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
            self._path.write_text(json.dumps(_default_state(), indent=2))

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
        self._path.write_text(json.dumps(payload, indent=2))
        return payload

    def clear(self) -> None:
        self._path.write_text(json.dumps(_default_state(), indent=2))


chat_history_store = ChatHistoryStore()

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
        """Return the last *limit* messages from the most recent prior tab.

        Skips the tab whose id matches *current_tab_id* so the caller
        never injects messages from the conversation the user is currently
        in. Returns a list of ``{"role": ..., "content": ...}`` dicts
        suitable for prepending to the messages list.
        """
        data = self.load()
        tabs = data.get("tabs", [])
        if not tabs:
            return []

        # Find the most recent prior tab (tabs are stored newest-last by
        # convention in the frontend, but we simply pick the last tab that
        # is not the current one and has messages).
        candidate: dict[str, Any] | None = None
        for tab in reversed(tabs):
            tab_id = tab.get("id", "")
            if tab_id == current_tab_id:
                continue
            msgs = tab.get("messages")
            if isinstance(msgs, list) and msgs:
                candidate = tab
                break

        if candidate is None:
            return []

        raw_msgs = candidate["messages"]
        # Take the last N messages, keeping only role and content.
        recent = raw_msgs[-limit:] if len(raw_msgs) > limit else raw_msgs
        result: list[dict[str, str]] = []
        for m in recent:
            role = m.get("role", "")
            content = m.get("content", "")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                result.append({"role": role, "content": content})
        return result

    def clear(self) -> None:
        atomic_write_json(self._path, _default_state())


chat_history_store = ChatHistoryStore()

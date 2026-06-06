"""Chat interaction helper for the Text Bridge.

Provides a unified way to append both user and assistant messages to the
user's persistent chat history, ensuring that interactions starting from
a phone appear in the web UI.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.chat_history_store import chat_history_store

logger = logging.getLogger(__name__)


def _ensure_active_tab(data: dict[str, Any]) -> dict[str, Any]:
    """Return the active tab, creating a default one if needed."""
    tabs = data.get("tabs")
    if not isinstance(tabs, list):
        tabs = []
        data["tabs"] = tabs
    
    if not tabs:
        new_id = str(uuid.uuid4())
        tabs.append({"id": new_id, "name": "Chat", "messages": []})
        data["active_tab_id"] = new_id
        return tabs[-1]

    active_id = data.get("active_tab_id") or ""
    for tab in tabs:
        if tab.get("id") == active_id:
            return tab
    return tabs[-1]


def append_chat_interaction(role: str, content: str, model: str = "myos") -> None:
    """Add a message to the active chat history tab."""
    try:
        data = chat_history_store.load()
        tab = _ensure_active_tab(data)
        
        now_iso = datetime.now(timezone.utc).isoformat()
        message = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "model": model,
            "created_at": now_iso,
        }
        
        if "messages" not in tab:
            tab["messages"] = []
        tab["messages"].append(message)
        tab["updatedAt"] = now_iso
        
        chat_history_store.save(data)
    except Exception as exc:
        logger.warning("failed to append chat interaction: %s", exc)

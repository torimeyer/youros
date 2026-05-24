"""Torichat system-message helper.

Lets backend code post a myOS-authored message into the user's current
chat tab so they see it inline the next time they open the chat panel.
The chat panel hydrates from ``~/.myos/chat_history.json`` on mount,
so appending a message there makes it land in the UI as an assistant
bubble labelled "myOS".

This is the simplest "write-to-thread" pattern the repo supports. There
is no live socket broadcast, so the new bubble shows up whenever the
user opens (or refreshes) the chat. Pairing this helper with a
:mod:`services.notifications` entry keeps the bell / push notification
working in parallel so the user gets the cue without needing the chat
panel open.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.chat_history_store import chat_history_store

logger = logging.getLogger(__name__)

# Safety valve for tests. When set, :func:`post_system_message` is a
# no-op so test runs exercising ``/complete`` paths that now go through
# the chat-notification hook never pollute the real
# ``~/.myos/chat_history.json``. Mirrors the same opt-out used by
# :mod:`services.automation_outputs` (``MYOS_SKIP_AUTOMATION_FILES_SAVE``).
_SKIP_ENV = "MYOS_SKIP_CHAT_NOTIFICATIONS"

# The model string shown on the assistant bubble. Keeps these messages
# visually distinct from Claude / Gemini replies.
SYSTEM_MESSAGE_MODEL = "myos"


def _ensure_tab(data: dict[str, Any]) -> dict[str, Any]:
    """Return the active tab dict, creating one if the store is empty.

    The frontend always creates a tab on its first render, but a fresh
    install / cleared history can leave ``tabs`` empty on disk. Posting
    into that empty store needs a tab to write into, so we create a
    default one here.
    """
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
    # Active id missing or stale: fall back to the last tab (newest).
    return tabs[-1]


def post_system_message(
    text: str,
    *,
    model: str = SYSTEM_MESSAGE_MODEL,
    tab_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append an assistant-role message to the user's chat history.

    ``text`` is rendered as markdown in the chat panel, so callers may
    include a ``[label](url)`` link. When ``tab_id`` is provided and
    matches an existing tab the message lands in that tab; otherwise it
    lands in the active tab (or a fresh tab if the store is empty).

    Returns the inserted message dict. Any storage error is logged and
    a stub dict is returned so the caller never has to wrap this in a
    try/except of its own.
    """
    clean = (text or "").strip()
    if not clean:
        return {"id": "", "role": "assistant", "content": ""}

    # Test safety valve. Conftest sets this env var so /complete paths
    # exercised under pytest never write to the real chat history file.
    # Production always sees this unset, so the helper runs normally.
    if os.environ.get(_SKIP_ENV):
        return {"id": "", "role": "assistant", "content": clean, "skipped": True}

    now_iso = datetime.now(timezone.utc).isoformat()
    message: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": clean,
        "model": model,
        "created_at": now_iso,
    }
    if metadata:
        message["metadata"] = dict(metadata)

    try:
        data = chat_history_store.load()
        target_tab: dict[str, Any]
        if tab_id:
            candidate = None
            for tab in data.get("tabs", []) or []:
                if tab.get("id") == tab_id:
                    candidate = tab
                    break
            target_tab = candidate if candidate is not None else _ensure_tab(data)
        else:
            target_tab = _ensure_tab(data)

        msgs = target_tab.get("messages")
        if not isinstance(msgs, list):
            msgs = []
            target_tab["messages"] = msgs
        msgs.append(message)
        target_tab["updatedAt"] = now_iso
        chat_history_store.save(data)
    except Exception as exc:  # noqa: BLE001 - helper must never raise
        logger.warning("chat_notifications: failed to append message: %s", exc)
    return message

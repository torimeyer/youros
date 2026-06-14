"""Persistent store for PM prototype collaboration data.

Data lives at ~/.youros/prototypes.json (outside the repo, safe for git pull).
An in-memory dict is the primary source of truth; disk is loaded on startup
and written after every mutation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from services.youros_paths import youros_home

PROTOTYPES_PATH = Path(str(youros_home() / "prototypes.json"))

# In-memory store: {prototype_id: prototype_dict}
_store: Dict[str, Any] = {}

# Invite token index: {token: {prototype_id, reviewer_email, expires_at}}
_tokens: Dict[str, Any] = {}


def _load() -> None:
    """Load persisted state from disk into memory."""
    global _store, _tokens
    if not PROTOTYPES_PATH.exists():
        return
    try:
        data = json.loads(PROTOTYPES_PATH.read_text(encoding="utf-8"))
        # Support legacy list format from earlier versions.
        if isinstance(data, list):
            _store = {p["id"]: p for p in data if isinstance(p, dict) and "id" in p}
            _tokens = {}
        elif isinstance(data, dict):
            _store = data.get("prototypes", {})
            _tokens = data.get("tokens", {})
    except (json.JSONDecodeError, OSError):
        pass


def _save() -> None:
    """Persist current in-memory state to disk atomically."""
    PROTOTYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROTOTYPES_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"prototypes": _store, "tokens": _tokens}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(PROTOTYPES_PATH)


def get_all() -> list:
    return list(_store.values())


def get_one(prototype_id: str) -> dict | None:
    return _store.get(prototype_id)


def create(prototype: dict) -> dict:
    _store[prototype["id"]] = prototype
    _save()
    return prototype


def update(prototype: dict) -> dict:
    _store[prototype["id"]] = prototype
    _save()
    return prototype


def delete(prototype_id: str) -> bool:
    if prototype_id not in _store:
        return False
    del _store[prototype_id]
    _save()
    return True


def add_token(token: str, payload: dict) -> None:
    _tokens[token] = payload
    _save()


def get_token(token: str) -> dict | None:
    return _tokens.get(token)


# Load on import so the store is warm when the router starts.
_load()

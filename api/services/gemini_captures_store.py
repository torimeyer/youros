"""Store for Gemini conversation captures from the Chrome extension.

Disk store: ~/.youros/gemini_captures.json (outside repo, never clobbered by git pull).

Public surface:
- GeminiCapturesStore -- add turns, list conversations, get turns
- gemini_captures_store -- module-level singleton
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

GEMINI_CAPTURES_PATH = youros_home() / "gemini_captures.json"


class GeminiCapturesStore:
    def __init__(self, path: Path = GEMINI_CAPTURES_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []

    def _save(self, turns: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(turns, indent=2))

    def add_turn(
        self,
        conversation_id: str,
        role: str,
        text: str,
        timestamp: str,
        turn_index: int,
        gem_name: Optional[str] = None,
    ) -> dict:
        with self._lock:
            turns = self._load()
            # Dedupe on (conversation_id, role, turn_index)
            for existing in turns:
                if (
                    existing["conversation_id"] == conversation_id
                    and existing["role"] == role
                    and existing["turn_index"] == turn_index
                ):
                    return existing
            turn = {
                "conversation_id": conversation_id,
                "role": role,
                "text": text,
                "timestamp": timestamp,
                "turn_index": turn_index,
                "gem_name": gem_name,
            }
            turns.append(turn)
            self._save(turns)
            return turn

    def list_conversations(self) -> list[dict]:
        with self._lock:
            turns = self._load()
        by_id: dict[str, dict] = {}
        for turn in turns:
            cid = turn["conversation_id"]
            if cid not in by_id:
                by_id[cid] = {
                    "conversation_id": cid,
                    "gem_name": turn.get("gem_name"),
                    "turn_count": 0,
                    "last_timestamp": turn["timestamp"],
                }
            entry = by_id[cid]
            entry["turn_count"] += 1
            if turn["timestamp"] > entry["last_timestamp"]:
                entry["last_timestamp"] = turn["timestamp"]
        return sorted(by_id.values(), key=lambda x: x["last_timestamp"], reverse=True)

    def get_conversation(self, conversation_id: str) -> list[dict]:
        with self._lock:
            turns = self._load()
        matching = [t for t in turns if t["conversation_id"] == conversation_id]
        return sorted(matching, key=lambda x: x["turn_index"])


gemini_captures_store = GeminiCapturesStore()

"""Shareable snapshot service.

Shares are stored in ~/.myos/shares.json. Each share has a unique token,
an expiry date, and a snapshot of content at the time of creation.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text

SHARES_PATH = Path.home() / ".myos" / "shares.json"

SHARE_TYPES = {"task_list", "agent_output", "label_view", "file"}


class SharesStore:
    def __init__(self, path: Path = SHARES_PATH):
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

    def _save(self, shares: list[dict]):
        atomic_write_json(self._path, shares)

    def create_share(
        self,
        share_type: str,
        content_ids: list[str],
        title: str,
        content_snapshot: Optional[list[dict]] = None,
        expires_in_days: int = 7,
    ) -> dict:
        if share_type not in SHARE_TYPES:
            raise ValueError(f"Unknown share type '{share_type}'. Must be one of: {', '.join(sorted(SHARE_TYPES))}")

        token = secrets.token_urlsafe(16)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        share = {
            "token": token,
            "share_type": share_type,
            "content_ids": content_ids,
            "title": title,
            "content_snapshot": content_snapshot or [],
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "expires_in_days": expires_in_days,
        }

        shares = self._load()
        shares.append(share)
        self._save(shares)
        return share

    def get_share(self, token: str) -> Optional[dict]:
        """Return the share if it exists and has not expired, else None."""
        for share in self._load():
            if share.get("token") == token:
                expires_at = share.get("expires_at")
                if expires_at:
                    try:
                        expiry = datetime.fromisoformat(expires_at)
                        if datetime.now(timezone.utc) > expiry:
                            return None  # expired
                    except ValueError:
                        pass
                return share
        return None

    def is_expired(self, token: str) -> bool:
        """Return True if the share exists but is expired."""
        for share in self._load():
            if share.get("token") == token:
                expires_at = share.get("expires_at")
                if expires_at:
                    try:
                        expiry = datetime.fromisoformat(expires_at)
                        return datetime.now(timezone.utc) > expiry
                    except ValueError:
                        pass
                return False
        return False

    def list_shares(self) -> list[dict]:
        """Return all shares, including expired ones, sorted newest first."""
        shares = self._load()
        now = datetime.now(timezone.utc)
        for share in shares:
            expires_at = share.get("expires_at")
            if expires_at:
                try:
                    expiry = datetime.fromisoformat(expires_at)
                    share["expired"] = now > expiry
                except ValueError:
                    share["expired"] = False
            else:
                share["expired"] = False
        return sorted(shares, key=lambda s: s.get("created_at", ""), reverse=True)

    def delete_share(self, token: str) -> bool:
        shares = self._load()
        original_len = len(shares)
        shares = [s for s in shares if s.get("token") != token]
        if len(shares) == original_len:
            return False
        self._save(shares)
        return True


shares_store = SharesStore()

"""JWT session management for enterprise user identity.

Creates and verifies HttpOnly session tokens so audit events can be
attributed to specific people. In solo mode (no enterprise active),
sessions are not required and everything works without cookies.

Signing secret is stored in ~/.myos/session_secret, outside the repo.
"""
from __future__ import annotations

import jwt
import time
import os
from pathlib import Path

SESSION_COOKIE_NAME = "myos_session"
SESSION_EXPIRY_HOURS = 24


def _get_secret() -> str:
    """Get or create a signing secret. Stored in ~/.myos/session_secret."""
    secret_path = Path.home() / ".myos" / "session_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    import secrets
    secret = secrets.token_hex(32)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret)
    return secret


def create_session(email: str, role: str, member_id: str, org_id: str = "") -> str:
    """Create a JWT token for a logged-in user."""
    payload = {
        "sub": email,
        "role": role,
        "member_id": member_id,
        "org_id": org_id,
        "exp": int(time.time()) + (SESSION_EXPIRY_HOURS * 3600),
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def verify_session(token: str) -> dict | None:
    """Decode and verify a session token. Returns claims dict or None."""
    try:
        return jwt.decode(token, _get_secret(), algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

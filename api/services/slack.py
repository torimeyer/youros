"""Slack integration service.

OAuth2 flow for bot + user tokens, message fetching, posting, and search.
Tokens are stored in ~/.myos/slack_token.json (outside the repo).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from services.atomic_io import atomic_write_json

MYOS_DIR = Path.home() / ".myos"
TOKEN_PATH = MYOS_DIR / "slack_token.json"

SLACK_API_BASE = "https://slack.com/api"

# Circuit breaker: after 2 consecutive Slack API failures, stop trying
# for 5 minutes so page loads are not blocked by a flaky remote API.
_BREAKER_THRESHOLD = 2
_BREAKER_COOLDOWN = 300  # seconds
_breaker_failures: int = 0
_breaker_tripped_at: float = 0.0


def _breaker_is_open() -> bool:
    if _breaker_failures < _BREAKER_THRESHOLD:
        return False
    if _breaker_tripped_at and (time.time() - _breaker_tripped_at) > _BREAKER_COOLDOWN:
        return False
    return True


def _breaker_record_failure() -> None:
    global _breaker_failures, _breaker_tripped_at
    _breaker_failures += 1
    if _breaker_failures >= _BREAKER_THRESHOLD:
        _breaker_tripped_at = time.time()


def _breaker_record_success() -> None:
    global _breaker_failures, _breaker_tripped_at
    _breaker_failures = 0
    _breaker_tripped_at = 0.0


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)


def is_connected() -> bool:
    """Return True if we have saved Slack tokens."""
    return TOKEN_PATH.exists()


def get_tokens() -> dict:
    """Return saved tokens. Raises RuntimeError if not connected."""
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not connected to Slack.")
    return json.loads(TOKEN_PATH.read_text())


def _invalidate_slack_status_cache() -> None:
    """Drop the cached Slack status payload.

    Lazy import so this module has no hard dependency on the cache module
    ordering at import time.
    """
    try:
        from services import connections_cache

        connections_cache.invalidate("slack_status")
    except Exception:
        pass


def save_tokens(tokens: dict) -> None:
    """Persist tokens to disk."""
    _ensure_dirs()
    atomic_write_json(TOKEN_PATH, tokens)
    # Connection state changed: drop cached status so next poll is fresh.
    _invalidate_slack_status_cache()


def disconnect() -> None:
    """Remove saved tokens."""
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink(missing_ok=True)
    _invalidate_slack_status_cache()


def get_team_info() -> Optional[dict]:
    """Return workspace name and team id if connected."""
    if not TOKEN_PATH.exists():
        return None
    try:
        tokens = json.loads(TOKEN_PATH.read_text())
        return {
            "team_name": tokens.get("team_name", ""),
            "team_id": tokens.get("team_id", ""),
        }
    except Exception:
        return None


def _bot_token() -> str:
    tokens = get_tokens()
    token = tokens.get("access_token") or tokens.get("bot_access_token", "")
    if not token:
        raise RuntimeError("No Slack bot token found. Reconnect your workspace.")
    return token


async def _slack_get(method: str, params: Optional[dict] = None) -> dict:
    """Make an authenticated GET request to the Slack API."""
    if _breaker_is_open():
        raise RuntimeError("Slack API is temporarily unavailable. Try again in a few minutes.")

    token = _bot_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{SLACK_API_BASE}/{method}",
                headers=headers,
                params=params or {},
            )
            data = resp.json()
            if not data.get("ok"):
                _breaker_record_failure()
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            _breaker_record_success()
            return data
    except httpx.HTTPError as exc:
        _breaker_record_failure()
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc


async def _slack_post(method: str, body: Optional[dict] = None) -> dict:
    """Make an authenticated POST request to the Slack API."""
    if _breaker_is_open():
        raise RuntimeError("Slack API is temporarily unavailable. Try again in a few minutes.")

    token = _bot_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{SLACK_API_BASE}/{method}",
                headers=headers,
                json=body or {},
            )
            data = resp.json()
            if not data.get("ok"):
                _breaker_record_failure()
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            _breaker_record_success()
            return data
    except httpx.HTTPError as exc:
        _breaker_record_failure()
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc


async def list_channels(limit: int = 100) -> list[dict]:
    """Return a list of channels the bot can see."""
    data = await _slack_get("conversations.list", {
        "types": "public_channel,private_channel",
        "exclude_archived": "true",
        "limit": str(limit),
    })
    channels = data.get("channels", [])
    return [
        {
            "id": ch["id"],
            "name": ch.get("name", ""),
            "is_private": ch.get("is_private", False),
            "num_members": ch.get("num_members", 0),
            "topic": ch.get("topic", {}).get("value", ""),
        }
        for ch in channels
    ]


async def fetch_messages(channel_id: str, limit: int = 50) -> list[dict]:
    """Fetch recent messages from a channel."""
    data = await _slack_get("conversations.history", {
        "channel": channel_id,
        "limit": str(limit),
    })
    messages = data.get("messages", [])
    return [
        {
            "ts": msg.get("ts", ""),
            "user": msg.get("user", ""),
            "text": msg.get("text", ""),
            "type": msg.get("type", "message"),
        }
        for msg in messages
    ]


async def post_message(channel_id: str, text: str) -> dict:
    """Post a message to a channel."""
    data = await _slack_post("chat.postMessage", {
        "channel": channel_id,
        "text": text,
    })
    return {
        "ok": True,
        "ts": data.get("ts", ""),
        "channel": data.get("channel", channel_id),
    }


async def search_messages(query: str, count: int = 20) -> list[dict]:
    """Search messages across the workspace.

    Note: search requires a user token, not a bot token. Falls back gracefully.
    """
    tokens = get_tokens()
    user_token = tokens.get("authed_user", {}).get("access_token", "")
    if not user_token:
        user_token = tokens.get("access_token", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{SLACK_API_BASE}/search.messages",
                headers={"Authorization": f"Bearer {user_token}"},
                params={"query": query, "count": str(count)},
            )
            data = resp.json()
            if not data.get("ok"):
                return []
            matches = data.get("messages", {}).get("matches", [])
            return [
                {
                    "ts": m.get("ts", ""),
                    "text": m.get("text", ""),
                    "channel": m.get("channel", {}).get("name", ""),
                    "user": m.get("username", ""),
                    "permalink": m.get("permalink", ""),
                }
                for m in matches
            ]
    except Exception:
        return []


async def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an OAuth authorization code for tokens."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack OAuth failed: {data.get('error', 'unknown')}")
        # Persist all token data
        save_tokens(data)
        return data

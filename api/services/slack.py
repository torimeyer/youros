"""Slack integration service.

OAuth2 flow for bot + user tokens, message fetching, posting, and search.
Workspaces are stored in ~/.youros/slack_workspaces/{team_id}.json (outside the
repo). The legacy single-workspace token at ~/.youros/slack_token.json is
migrated to the workspaces directory on first use.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from services.atomic_io import atomic_write_json

MYOS_DIR = Path.home() / ".youros"
TOKEN_PATH = MYOS_DIR / "slack_token.json"  # legacy single-workspace path
WORKSPACES_DIR = MYOS_DIR / "slack_workspaces"  # multi-workspace directory

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
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_token() -> None:
    """Move the old single-workspace token into the workspaces directory."""
    if not TOKEN_PATH.exists():
        return
    if WORKSPACES_DIR.exists() and any(WORKSPACES_DIR.glob("*.json")):
        return
    try:
        tokens = json.loads(TOKEN_PATH.read_text())
        team_obj = tokens.get("team") or {}
        team_id = tokens.get("workspace_id") or team_obj.get("id", "")
        if team_id:
            _ensure_dirs()
            workspace_path = WORKSPACES_DIR / f"{team_id}.json"
            if not workspace_path.exists():
                atomic_write_json(workspace_path, tokens)
            TOKEN_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def list_workspaces() -> list[dict]:
    """Return all connected workspaces as [{team_id, team_name}]."""
    _migrate_legacy_token()
    workspaces = []
    if WORKSPACES_DIR.exists():
        for path in sorted(WORKSPACES_DIR.glob("*.json")):
            try:
                tokens = json.loads(path.read_text())
                team_obj = tokens.get("team") or {}
                team_name = tokens.get("workspace_name") or team_obj.get("name", "")
                team_id = tokens.get("workspace_id") or team_obj.get("id", "") or path.stem
                workspaces.append({"team_id": team_id, "team_name": team_name})
            except Exception:
                continue
    return workspaces


def is_connected() -> bool:
    """Return True if at least one workspace is connected."""
    if WORKSPACES_DIR.exists() and any(WORKSPACES_DIR.glob("*.json")):
        return True
    return TOKEN_PATH.exists()


def get_workspace_tokens(team_id: str) -> dict:
    """Return tokens for a specific workspace. Raises RuntimeError if not found."""
    workspace_path = WORKSPACES_DIR / f"{team_id}.json"
    if not workspace_path.exists():
        raise RuntimeError(f"Workspace {team_id} not connected.")
    return json.loads(workspace_path.read_text())


def get_tokens(team_id: Optional[str] = None) -> dict:
    """Return tokens for the given workspace, or the first available workspace."""
    if team_id:
        return get_workspace_tokens(team_id)
    workspaces = list_workspaces()
    if workspaces:
        return get_workspace_tokens(workspaces[0]["team_id"])
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text())
    raise RuntimeError("Not connected to Slack.")


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


def add_workspace(tokens: dict) -> None:
    """Add or update a workspace by team_id. Invalidates the status cache."""
    _ensure_dirs()
    team_obj = tokens.get("team") or {}
    team_id = tokens.get("workspace_id") or team_obj.get("id", "")
    if team_id:
        atomic_write_json(WORKSPACES_DIR / f"{team_id}.json", tokens)
    else:
        atomic_write_json(TOKEN_PATH, tokens)
    _invalidate_slack_status_cache()


def save_tokens(tokens: dict) -> None:
    """Backward-compat shim: persist tokens, routing to workspaces dir when possible."""
    add_workspace(tokens)


def disconnect(team_id: Optional[str] = None) -> None:
    """Remove Slack tokens.

    If team_id is given, remove only that workspace. Otherwise remove all
    workspace files and the legacy TOKEN_PATH.
    """
    if team_id:
        workspace_path = WORKSPACES_DIR / f"{team_id}.json"
        workspace_path.unlink(missing_ok=True)
    else:
        if WORKSPACES_DIR.exists():
            for path in list(WORKSPACES_DIR.glob("*.json")):
                path.unlink(missing_ok=True)
        TOKEN_PATH.unlink(missing_ok=True)
    _invalidate_slack_status_cache()


def get_team_info() -> Optional[dict]:
    """Return the first connected workspace's name and team id."""
    workspaces = list_workspaces()
    if workspaces:
        return {"team_name": workspaces[0]["team_name"], "team_id": workspaces[0]["team_id"]}
    if TOKEN_PATH.exists():
        try:
            tokens = json.loads(TOKEN_PATH.read_text())
            team_obj = tokens.get("team") or {}
            return {
                "team_name": tokens.get("workspace_name") or team_obj.get("name", ""),
                "team_id": tokens.get("workspace_id") or team_obj.get("id", ""),
            }
        except Exception:
            return None
    return None


def _bot_token(team_id: Optional[str] = None) -> str:
    tokens = get_tokens(team_id)
    token = tokens.get("access_token") or tokens.get("bot_access_token", "")
    if not token:
        raise RuntimeError("No Slack bot token found. Reconnect your workspace.")
    return token


async def _slack_get(method: str, params: Optional[dict] = None, team_id: Optional[str] = None) -> dict:
    """Make an authenticated GET request to the Slack API."""
    if _breaker_is_open():
        raise RuntimeError("Slack API is temporarily unavailable. Try again in a few minutes.")

    token = _bot_token(team_id)
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


async def _slack_post(method: str, body: Optional[dict] = None, team_id: Optional[str] = None) -> dict:
    """Make an authenticated POST request to the Slack API."""
    if _breaker_is_open():
        raise RuntimeError("Slack API is temporarily unavailable. Try again in a few minutes.")

    token = _bot_token(team_id)
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


async def list_channels(limit: int = 100, team_id: Optional[str] = None) -> list[dict]:
    """Return a list of channels the bot can see."""
    data = await _slack_get("conversations.list", {
        "types": "public_channel,private_channel",
        "exclude_archived": "true",
        "limit": str(limit),
    }, team_id=team_id)
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


async def fetch_messages(channel_id: str, limit: int = 50, team_id: Optional[str] = None) -> list[dict]:
    """Fetch recent messages from a channel."""
    data = await _slack_get("conversations.history", {
        "channel": channel_id,
        "limit": str(limit),
    }, team_id=team_id)
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


async def post_message(channel_id: str, text: str, team_id: Optional[str] = None) -> dict:
    """Post a message to a channel."""
    data = await _slack_post("chat.postMessage", {
        "channel": channel_id,
        "text": text,
    }, team_id=team_id)
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


async def get_message_permalink(channel_id: str, ts: str, team_id: Optional[str] = None) -> str:
    """Return the permalink URL for a Slack message.

    Calls chat.getPermalink with the bot token. Returns an empty string
    if the call fails so callers can store the followup anyway.
    """
    try:
        data = await _slack_get("chat.getPermalink", {"channel": channel_id, "message_ts": ts}, team_id=team_id)
        return data.get("permalink", "")
    except Exception:
        return ""


async def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an OAuth authorization code for tokens and add the workspace."""
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
        team_obj = data.get("team") or {}
        enriched = {
            **data,
            "workspace_id": team_obj.get("id", ""),
            "workspace_name": team_obj.get("name", ""),
        }
        add_workspace(enriched)
        return enriched


async def connect_with_token(access_token: str, app_id: str = "") -> dict:
    """Connect a workspace by pasting a Slack bot Access Token + App ID.

    This is the "paste your token" path: instead of running the OAuth
    redirect, the user copies their Bot User OAuth Token (starts with
    ``xoxb-``) and App ID straight from their Slack app config. We call
    Slack's ``auth.test`` to (a) prove the token is live and (b) read the
    workspace identity. A token that fails ``auth.test`` is rejected, so we
    never store a dead token that makes the UI claim it is connected.
    """
    token = (access_token or "").strip()
    if not token:
        raise RuntimeError("Paste your Slack bot access token (it starts with xoxb-).")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{SLACK_API_BASE}/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Slack: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Slack rejected that token: {data.get('error', 'invalid_auth')}")
    team_id = data.get("team_id", "")
    team_name = data.get("team", "")
    enriched = {
        "access_token": token,
        "app_id": (app_id or "").strip(),
        "token_type": "bot",
        "connected_via": "access_token",
        "team": {"id": team_id, "name": team_name},
        "workspace_id": team_id,
        "workspace_name": team_name,
        "authed_user": {},
    }
    add_workspace(enriched)
    return {"team_id": team_id, "team_name": team_name}

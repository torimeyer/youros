"""Gmail service.

Fetches unread messages from the user's Gmail inbox and caches them locally.
Cache lives in ~/.myos/gmail_cache/ -- never inside the repo.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from services.google_auth import get_credentials, is_authenticated

MYOS_DIR = Path.home() / ".myos"
GMAIL_CACHE_DIR = MYOS_DIR / "gmail_cache"
INBOX_CACHE_PATH = GMAIL_CACHE_DIR / "inbox.json"

# 5 minutes TTL for the inbox cache.
_CACHE_TTL_SECONDS = 300


def _ensure_dirs() -> None:
    GMAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _build_gmail_service():
    """Build an authenticated Gmail API service object."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Google API client is not available on this server."
        ) from exc

    tokens = get_credentials()
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=None,
        client_secret=None,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _load_cache() -> list[dict] | None:
    """Return cached messages if they exist and are less than 5 minutes old."""
    if not INBOX_CACHE_PATH.exists():
        return None
    age = time.time() - INBOX_CACHE_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(INBOX_CACHE_PATH.read_text())
    except Exception:
        return None


def _save_cache(messages: list[dict]) -> None:
    """Persist messages to the cache file."""
    _ensure_dirs()
    INBOX_CACHE_PATH.write_text(json.dumps(messages))


def _clear_cache() -> None:
    """Remove the cached inbox file."""
    if INBOX_CACHE_PATH.exists():
        INBOX_CACHE_PATH.unlink(missing_ok=True)


def _parse_message(msg: dict) -> dict:
    """Convert a raw Gmail message dict to our summary format."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    from_raw = headers.get("from", "")
    from_name, from_email = parseaddr(from_raw)
    if not from_name:
        from_name = from_email

    date_str = headers.get("date", "")
    date_iso = ""
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            date_iso = dt.isoformat()
        except Exception:
            date_iso = date_str

    snippet = msg.get("snippet", "")
    label_ids = msg.get("labelIds", [])
    is_unread = "UNREAD" in label_ids

    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": subject,
        "from_name": from_name,
        "from_email": from_email,
        "snippet": snippet,
        "date": date_iso,
        "is_unread": is_unread,
    }


def _fetch_unread_sync(max_results: int = 20) -> list[dict]:
    """Synchronous call to the Gmail API to fetch unread inbox messages."""
    service = _build_gmail_service()

    list_result = (
        service.users().messages()
        .list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results,
            fields="messages(id,threadId)",
        )
        .execute()
    )

    message_refs = list_result.get("messages", [])
    if not message_refs:
        return []

    messages = []
    for ref in message_refs:
        try:
            msg = (
                service.users().messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["subject", "from", "date"],
                    fields="id,threadId,snippet,labelIds,payload/headers",
                )
                .execute()
            )
            messages.append(_parse_message(msg))
        except Exception:
            continue

    return messages


async def get_unread_summary(max_results: int = 20) -> list[dict]:
    """Return unread inbox messages.

    Checks the on-disk cache first (5 min TTL). On cache miss, calls the
    Gmail API in a thread so the async event loop is not blocked.
    """
    cached = _load_cache()
    if cached is not None:
        return cached

    messages = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_unread_sync(max_results)
    )
    _save_cache(messages)
    return messages


def _fetch_thread_sync(thread_id: str) -> dict:
    """Synchronous call to fetch a single thread."""
    service = _build_gmail_service()
    return (
        service.users().threads()
        .get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["subject", "from", "date"],
        )
        .execute()
    )


async def get_thread(thread_id: str) -> dict:
    """Fetch a single Gmail thread for detail view."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_thread_sync(thread_id)
    )


def _mark_read_sync(message_id: str) -> None:
    """Synchronous call to remove UNREAD label from a message."""
    service = _build_gmail_service()
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


async def mark_read(message_id: str) -> None:
    """Mark a Gmail message as read."""
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: _mark_read_sync(message_id)
    )
    # Invalidate the cache so the next fetch reflects the change.
    _clear_cache()


async def needs_reauth() -> bool:
    """Return True if the Gmail scope is missing.

    Makes a minimal API call and checks for the specific error. Any other
    error is treated as False to avoid spurious reauth prompts.
    """
    if not is_authenticated():
        return False
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _fetch_unread_sync(max_results=1)
        )
        return False
    except Exception as exc:
        msg = str(exc).lower()
        return "insufficient" in msg or "403" in msg

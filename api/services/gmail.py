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

from services.atomic_io import atomic_write_text
from services.google_auth import get_credentials, is_authenticated

MYOS_DIR = Path.home() / ".myos"
GMAIL_CACHE_DIR = MYOS_DIR / "gmail_cache"
INBOX_CACHE_PATH = GMAIL_CACHE_DIR / "inbox.json"
FULL_INBOX_CACHE_PATH = GMAIL_CACHE_DIR / "inbox_full.json"

# Circuit breaker. After 2 consecutive Gmail API failures, stop trying
# for 5 minutes. Without this, every page load (briefing, sidebar unread
# badge, Gmail page) triggers a 25-second blocking call that poisons the
# backend event loop. The breaker resets on any successful call.
_BREAKER_THRESHOLD = 2
_BREAKER_COOLDOWN = 300  # seconds
_breaker_failures: int = 0
_breaker_tripped_at: float = 0.0


def _breaker_is_open() -> bool:
    """Return True when the circuit breaker is open (Gmail calls blocked)."""
    if _breaker_failures < _BREAKER_THRESHOLD:
        return False
    if _breaker_tripped_at and (time.time() - _breaker_tripped_at) > _BREAKER_COOLDOWN:
        return False  # cooldown expired, allow a retry
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

# 5 minutes TTL for the unread-only cache (used by the briefing and the
# unread count shown on /api/gmail/auth/status).
_CACHE_TTL_SECONDS = 300

# 5 minutes TTL for the full inbox cache.
#
# We used to ship a 60 second TTL, but on a cold cache the Gmail fan-out
# fetch takes 3-10 seconds (50 messages over a thread pool of 10) and the
# user pays that full cost every minute just from leaving the tab open.
# Any action that actually changes the inbox (send a reply, mark read,
# trash a message, hit the Sync button) already calls
# ``invalidate_full_inbox_cache`` explicitly, so fresh content still shows
# up right away when it matters. The 5 minute TTL applies only when
# nothing has changed, which means the page feels instant for the common
# case of tab-switching and stays correct for the case of user actions.
_FULL_INBOX_CACHE_TTL_SECONDS = 300

# Hard cap on how many messages the full inbox fetch will pull in a single
# request. 200 keeps the response small enough to render quickly, keeps
# parallel fan-out reasonable, and still shows several pages of history on
# the average account.
# 50 messages is plenty for the inbox view. 200 turned the cold page
# load into a 5-10 second wait because the service fans out per-message
# metadata fetches across a thread pool of 10, and 200 / 10 = 20 rounds
# of parallel API calls. Regression guard for needle 285.
FULL_INBOX_CAP = 50

# How many message ids Gmail returns per list page. 100 is the default max
# the API allows; using it means FULL_INBOX_CAP=200 resolves in 2 list calls.
_LIST_PAGE_SIZE = 100


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
    client_config = {}
    try:
        from services.google_auth import _load_client_config
        client_config = _load_client_config()
    except Exception:
        pass
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config.get("client_id"),
        client_secret=client_config.get("client_secret"),
    )
    return build("gmail", "v1", credentials=creds)


def _load_cache() -> list[dict] | None:
    """Return cached unread messages if fresh AND non-empty.

    The non-empty guard is deliberate. A previous run could have written []
    during a transient auth or API error. Without the guard that stale
    empty result is served for the full TTL window and the user sees a
    blank inbox even though mail exists. An empty cache file is treated as
    a cache miss so the next call retries Gmail.
    """
    if not INBOX_CACHE_PATH.exists():
        return None
    age = time.time() - INBOX_CACHE_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    try:
        data = json.loads(INBOX_CACHE_PATH.read_text())
    except Exception:
        return None
    if not data:
        # Empty list is treated as a miss so we retry Gmail instead of
        # pinning the UI to a stale empty inbox.
        return None
    return data


def _save_cache(messages: list[dict]) -> None:
    """Persist messages to the cache file."""
    atomic_write_text(INBOX_CACHE_PATH, json.dumps(messages))


def _clear_cache() -> None:
    """Remove the cached inbox file."""
    if INBOX_CACHE_PATH.exists():
        INBOX_CACHE_PATH.unlink(missing_ok=True)


def _load_full_inbox_cache(allow_stale: bool = False) -> list[dict] | None:
    """Return cached full inbox if fresh AND non-empty.

    Same non-empty guard as the unread cache so a transient fetch error
    never pins the UI to an empty state. When ``allow_stale`` is True
    the TTL check is skipped, which the fetch-failure fallback uses to
    keep the UI populated on a Gmail hiccup. Regression guard for
    needle 285.
    """
    if not FULL_INBOX_CACHE_PATH.exists():
        return None
    if not allow_stale:
        age = time.time() - FULL_INBOX_CACHE_PATH.stat().st_mtime
        if age > _FULL_INBOX_CACHE_TTL_SECONDS:
            return None
    try:
        data = json.loads(FULL_INBOX_CACHE_PATH.read_text())
    except Exception:
        return None
    if not data:
        return None
    return data


def _save_full_inbox_cache(messages: list[dict]) -> None:
    """Persist full inbox messages to the cache file."""
    atomic_write_text(FULL_INBOX_CACHE_PATH, json.dumps(messages))


def invalidate_full_inbox_cache() -> None:
    """Remove the cached full inbox file.

    Called by the reply service right after a successful send so the
    outgoing reply (and any new incoming mail pulled in the same refresh)
    shows up on the next fetch instead of waiting for the 60 second TTL.
    """
    if FULL_INBOX_CACHE_PATH.exists():
        FULL_INBOX_CACHE_PATH.unlink(missing_ok=True)


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
    """Synchronous call to the Gmail API to fetch unread inbox messages.

    The per-message metadata gets are fanned out across a thread pool so
    Gmail sees them in parallel instead of one-at-a-time. For 20 unread
    messages that turns roughly 20 serial RTTs into roughly 1.
    """
    from concurrent.futures import ThreadPoolExecutor

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

    def _get_one(ref: dict) -> dict | None:
        try:
            # Each thread needs its own service object because the underlying
            # httplib2 client used by googleapiclient is not thread-safe.
            local_service = _build_gmail_service()
            return (
                local_service.users().messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["subject", "from", "date"],
                    fields="id,threadId,snippet,labelIds,payload/headers",
                )
                .execute()
            )
        except Exception:
            return None

    # Cap the pool so we do not hammer Gmail, but still get real parallelism
    # for the common case of 10 to 20 unread messages.
    pool_size = min(len(message_refs), 10)
    messages: list[dict] = []
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for raw in pool.map(_get_one, message_refs):
            if raw is not None:
                messages.append(_parse_message(raw))

    return messages


async def get_unread_summary(max_results: int = 20) -> list[dict]:
    """Return unread inbox messages.

    Checks the on-disk cache first (5 min TTL, empty results treated as a
    miss so a transient error does not pin the UI to an empty inbox). On
    cache miss, calls the Gmail API in a thread behind a 15 second timeout
    so the async event loop is not blocked. Circuit breaker stops attempts
    after 2 consecutive failures for 5 minutes.
    """
    cached = _load_cache()
    if cached is not None:
        return cached

    if _breaker_is_open():
        return []

    try:
        messages = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: _fetch_unread_sync(max_results)
            ),
            timeout=15.0,
        )
        _breaker_record_success()
    except (asyncio.TimeoutError, Exception):
        _breaker_record_failure()
        return []
    _save_cache(messages)
    return messages


def _fetch_inbox_sync(cap: int = FULL_INBOX_CAP) -> list[dict]:
    """Synchronous call to the Gmail API to fetch recent inbox messages.

    Unlike _fetch_unread_sync this pulls everything in the INBOX label,
    not just UNREAD. Pages through message ids until we have ``cap`` or
    Gmail runs out of pages, then fans out the per-message metadata gets
    across a thread pool so the N round trips become roughly one. Each
    worker builds its own Gmail service object because the underlying
    httplib2 client is not thread-safe.

    Returns messages sorted newest first (Gmail already lists that way,
    and we preserve the order as we accumulate pages).
    """
    from concurrent.futures import ThreadPoolExecutor

    service = _build_gmail_service()

    # ---- Step 1: page through message ids until we hit the cap. ----
    message_refs: list[dict] = []
    page_token: str | None = None
    while len(message_refs) < cap:
        remaining = cap - len(message_refs)
        page_size = min(_LIST_PAGE_SIZE, remaining)

        list_kwargs = {
            "userId": "me",
            "labelIds": ["INBOX"],
            "maxResults": page_size,
            "fields": "messages(id,threadId),nextPageToken",
        }
        if page_token:
            list_kwargs["pageToken"] = page_token

        list_result = (
            service.users().messages().list(**list_kwargs).execute()
        )

        page_refs = list_result.get("messages", []) or []
        if not page_refs:
            break
        message_refs.extend(page_refs)

        page_token = list_result.get("nextPageToken")
        if not page_token:
            break

    if not message_refs:
        return []

    # Enforce the cap exactly (the last page could overshoot if Gmail
    # returned more than we asked for).
    message_refs = message_refs[:cap]

    # ---- Step 2: fan the per-message gets across a thread pool. ----
    def _get_one(ref: dict) -> dict | None:
        try:
            local_service = _build_gmail_service()
            return (
                local_service.users().messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["subject", "from", "date"],
                    fields="id,threadId,snippet,labelIds,payload/headers,internalDate",
                )
                .execute()
            )
        except Exception:
            return None

    # Cap the pool so we do not hammer Gmail. 10 workers is the same
    # setting the unread path uses and is plenty for 200 messages.
    pool_size = min(len(message_refs), 10)
    raw_messages: list[dict | None] = [None] * len(message_refs)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for idx, raw in enumerate(pool.map(_get_one, message_refs)):
            raw_messages[idx] = raw

    parsed: list[dict] = [_parse_message(r) for r in raw_messages if r is not None]
    return parsed


async def get_inbox_messages(cap: int = FULL_INBOX_CAP) -> list[dict]:
    """Return recent inbox messages (read AND unread).

    Checks the on-disk cache first (60 s TTL, empty results treated as a
    miss). On cache miss, calls the Gmail API in a thread behind a
    25 second timeout. The Gmail API is run from a thread pool of 10
    workers fanning out per-message metadata fetches, which realistically
    takes 5-10 seconds on a cold fetch of 50 messages. 25s is the
    ceiling: below the frontend's 30 second fetch timeout so the UI
    still shows a real error instead of the browser's generic
    network failure, but generous enough that we never time out a
    healthy Gmail round trip on the cold path. On timeout or failure,
    falls back to the stale cache (if any) so the UI keeps showing the
    previous inbox snapshot instead of spinning forever or rendering
    empty. Regression guard for needles 285 and 288.
    """
    cached = _load_full_inbox_cache()
    if cached is not None:
        return cached

    # Circuit breaker: if Gmail has failed twice in a row, don't try
    # again for 5 minutes. Return stale cache or empty list instead of
    # hanging for 25 seconds on every page load.
    if _breaker_is_open():
        stale = _load_full_inbox_cache(allow_stale=True)
        return stale if stale is not None else []

    try:
        messages = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: _fetch_inbox_sync(cap)
            ),
            timeout=25.0,
        )
        _breaker_record_success()
    except asyncio.TimeoutError:
        _breaker_record_failure()
        stale = _load_full_inbox_cache(allow_stale=True)
        if stale is not None:
            return stale
        raise
    except Exception:
        _breaker_record_failure()
        stale = _load_full_inbox_cache(allow_stale=True)
        if stale is not None:
            return stale
        raise
    _save_full_inbox_cache(messages)
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
    # Invalidate both caches so the next fetch reflects the change.
    _clear_cache()
    invalidate_full_inbox_cache()


def _trash_message_sync(message_id: str) -> None:
    """Synchronous call to move a message to Trash.

    Matches Gmail's own "Delete" button behavior. The message is removed
    from the inbox view but can still be recovered from Trash for 30 days
    before Gmail auto purges it.
    """
    service = _build_gmail_service()
    service.users().messages().trash(
        userId="me",
        id=message_id,
    ).execute()


async def trash_message(message_id: str) -> None:
    """Move a Gmail message to Trash (recoverable for 30 days)."""
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: _trash_message_sync(message_id)
    )
    # Invalidate both caches so the trashed message stops showing in the
    # inbox list on the next fetch.
    _clear_cache()
    invalidate_full_inbox_cache()


def _permanent_delete_sync(message_id: str) -> None:
    """Synchronous call to permanently delete a message. No undo."""
    service = _build_gmail_service()
    service.users().messages().delete(
        userId="me",
        id=message_id,
    ).execute()


async def permanent_delete_message(message_id: str) -> None:
    """Permanently delete a Gmail message. Not recoverable."""
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: _permanent_delete_sync(message_id)
    )
    _clear_cache()
    invalidate_full_inbox_cache()


def _search_messages_sync(query: str, max_results: int = 25) -> list[dict]:
    """Synchronous search across the user's mailbox.

    Uses Gmail's search syntax so the query can contain operators like
    ``from:amazon subject:marketing`` as well as plain keywords. Results
    are returned newest first with the same summary shape as the inbox
    endpoint.
    """
    from concurrent.futures import ThreadPoolExecutor

    service = _build_gmail_service()
    list_result = (
        service.users().messages()
        .list(
            userId="me",
            q=query,
            maxResults=max_results,
            fields="messages(id,threadId)",
        )
        .execute()
    )
    refs = list_result.get("messages", []) or []
    if not refs:
        return []

    def _get_one(ref: dict) -> dict | None:
        try:
            local_service = _build_gmail_service()
            return (
                local_service.users().messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["subject", "from", "date"],
                    fields="id,threadId,snippet,labelIds,payload/headers",
                )
                .execute()
            )
        except Exception:
            return None

    pool_size = min(len(refs), 10)
    raw_messages: list[dict | None] = [None] * len(refs)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for idx, raw in enumerate(pool.map(_get_one, refs)):
            raw_messages[idx] = raw
    return [_parse_message(r) for r in raw_messages if r is not None]


async def search_messages(query: str, max_results: int = 25) -> list[dict]:
    """Search Gmail using Gmail query syntax. Returns message summaries."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: _search_messages_sync(query, max_results)
    )


def _fetch_message_full_sync(message_id: str) -> dict:
    """Synchronous call to fetch a single message with full body text."""
    service = _build_gmail_service()
    msg = (
        service.users().messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
        )
        .execute()
    )
    return msg


def _extract_body_text(msg: dict) -> str:
    """Extract the plain-text body from a full Gmail message.

    Walks the MIME parts looking for text/plain first, then falls back
    to text/html (stripped to plain text). Returns the snippet as a last
    resort.
    """
    payload = msg.get("payload", {})

    def _decode_part(part: dict) -> str:
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    def _walk_parts(parts: list[dict]) -> tuple[str, str]:
        """Return (plain_text, html_text) from nested MIME parts."""
        plain = ""
        html = ""
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and not plain:
                plain = _decode_part(part)
            elif mime == "text/html" and not html:
                html = _decode_part(part)
            # Recurse into nested multipart containers.
            sub_parts = part.get("parts", [])
            if sub_parts:
                sub_plain, sub_html = _walk_parts(sub_parts)
                if not plain:
                    plain = sub_plain
                if not html:
                    html = sub_html
        return plain, html

    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        text = _decode_part(payload)
        if text:
            return text
    elif mime_type == "text/html":
        text = _decode_part(payload)
        if text:
            # Strip HTML tags as a rough plain-text extraction.
            import re as _re
            return _re.sub(r"<[^>]+>", " ", text).strip()

    parts = payload.get("parts", [])
    if parts:
        plain, html = _walk_parts(parts)
        if plain:
            return plain
        if html:
            import re as _re
            return _re.sub(r"<[^>]+>", " ", html).strip()

    return msg.get("snippet", "")


async def get_message_content(message_id: str) -> dict:
    """Fetch a single Gmail message and return parsed subject, sender, and body.

    Returns a dict with keys: subject, from_name, from_email, body, snippet.
    """
    raw = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_message_full_sync(message_id)
    )
    parsed = _parse_message(raw)
    body = _extract_body_text(raw)
    return {
        **parsed,
        "body": body,
    }


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
        # API-not-enabled is a GCP setup issue, not a scope problem.
        if "accessnotconfigured" in msg or "has not been used" in msg:
            return False
        return "insufficientpermissions" in msg or "insufficient authentication scopes" in msg

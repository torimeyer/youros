"""iMessage service.

Reads messages from the macOS iMessage database (~/Library/Messages/chat.db)
and sends messages via AppleScript. The database is read-only. Reading
requires Full Disk Access permission in System Settings > Privacy & Security.

Cache lives in ~/.myos/imessage_cache/ so user data stays outside the repo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from services.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

MYOS_DIR = Path.home() / ".myos"
IMESSAGE_CACHE_DIR = MYOS_DIR / "imessage_cache"
CONVERSATIONS_CACHE_PATH = IMESSAGE_CACHE_DIR / "conversations.json"
CONTACTS_CACHE_PATH = IMESSAGE_CACHE_DIR / "contacts.json"
CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Cache TTL: 60 seconds for conversations list.
_CONVERSATIONS_CACHE_TTL = 60

# Contact name cache: loaded once from disk, updated in background.
_contacts_cache: dict[str, str] | None = None

# Circuit breaker: after 2 consecutive failures, stop for 5 minutes.
_BREAKER_THRESHOLD = 2
_BREAKER_COOLDOWN = 300
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
    IMESSAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_contacts_cache() -> dict[str, str]:
    """Load the persistent contact name cache from disk."""
    global _contacts_cache
    if _contacts_cache is not None:
        return _contacts_cache
    _ensure_dirs()
    if CONTACTS_CACHE_PATH.exists():
        try:
            _contacts_cache = json.loads(CONTACTS_CACHE_PATH.read_text())
            return _contacts_cache
        except Exception:
            pass
    _contacts_cache = {}
    return _contacts_cache


def _save_contacts_cache() -> None:
    """Persist the contact name cache to disk."""
    if _contacts_cache is None:
        return
    _ensure_dirs()
    atomic_write_text(CONTACTS_CACHE_PATH, json.dumps(_contacts_cache))


def is_available() -> dict:
    """Check whether iMessage integration can work on this machine.

    Returns a dict with:
    - available: True if chat.db exists and is readable
    - reason: human-readable explanation if not available
    """
    if not CHAT_DB_PATH.exists():
        return {
            "available": False,
            "reason": "iMessage database not found. This feature only works on macOS.",
        }
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        conn.execute("SELECT COUNT(*) FROM chat")
        conn.close()
        return {"available": True, "reason": None}
    except sqlite3.OperationalError:
        return {
            "available": False,
            "reason": (
                "Cannot read the iMessage database. "
                "Go to System Settings > Privacy & Security > Full Disk Access "
                "and enable access for the app running myOS (Terminal, iTerm2, etc.)."
            ),
        }
    except Exception as exc:
        msg = str(exc)
        if "authorization" in msg.lower() or "not authorized" in msg.lower():
            return {
                "available": False,
                "reason": (
                    "macOS blocked access to the iMessage database. "
                    "Go to System Settings > Privacy & Security > Full Disk Access "
                    "and enable access for the app running myOS (Terminal, iTerm2, VS Code, etc.), "
                    "then restart that app."
                ),
            }
        return {
            "available": False,
            "reason": f"Unexpected error accessing iMessage database: {exc}",
        }


def _open_db() -> sqlite3.Connection:
    """Open chat.db in read-only mode. Raises RuntimeError on failure."""
    if not CHAT_DB_PATH.exists():
        raise RuntimeError(
            "iMessage database not found. This feature only works on macOS."
        )
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "Cannot read the iMessage database. "
            "Go to System Settings > Privacy & Security > Full Disk Access "
            "and enable access for the app running myOS."
        ) from exc


def _apple_epoch_to_unix(apple_ts: int | None) -> float:
    """Convert Apple's Core Data timestamp to Unix epoch.

    Apple stores dates as nanoseconds since 2001-01-01 00:00:00 UTC.
    """
    if not apple_ts:
        return 0.0
    # Apple epoch offset: seconds between 2001-01-01 and 1970-01-01
    APPLE_EPOCH_OFFSET = 978307200
    # chat.db stores timestamps in nanoseconds since 2001-01-01
    return (apple_ts / 1_000_000_000) + APPLE_EPOCH_OFFSET


def _unix_to_iso(ts: float) -> str:
    """Convert a unix timestamp to ISO 8601 string."""
    if ts <= 0:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _format_phone(identifier: str) -> str:
    """Clean up a phone number or email for display."""
    if not identifier:
        return ""
    # Already an email
    if "@" in identifier:
        return identifier
    # Strip leading + and non-digit chars for display
    digits = re.sub(r"\D", "", identifier)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return identifier


def _get_contact_display_name_cached(identifier: str) -> str:
    """Return a display name from the persistent cache, or the formatted number.

    Never blocks on AppleScript. If the name isn't cached yet, returns the
    formatted phone number. Background refresh will fill in names over time.
    """
    cache = _load_contacts_cache()
    if identifier in cache:
        return cache[identifier]
    return _format_phone(identifier)


def _lookup_contact_name(identifier: str) -> str | None:
    """Look up a single contact name via AppleScript. Returns None on miss."""
    if not identifier or "@" in identifier:
        return None
    try:
        script = (
            f'tell application "Contacts" to get name of '
            f'(people whose value of phones contains "{identifier}")'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
        name = result.stdout.strip()
        if name and name != "missing value" and name != "{}":
            name = name.strip("{}")
            if name:
                return name
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


def _refresh_contacts_for_identifiers(identifiers: list[str]) -> None:
    """Look up contact names for uncached identifiers and persist results.

    This runs in a background thread. It does one AppleScript call per
    uncached identifier but the results persist on disk, so subsequent
    requests are instant.
    """
    cache = _load_contacts_cache()
    uncached = [i for i in identifiers if i and "@" not in i and i not in cache]
    if not uncached:
        return

    for identifier in uncached:
        name = _lookup_contact_name(identifier)
        if name:
            cache[identifier] = name
        else:
            # Store the formatted version so we don't retry on every request.
            cache[identifier] = _format_phone(identifier)

    _save_contacts_cache()


def get_conversations_sync(limit: int = 50) -> list[dict]:
    """Fetch recent conversations from chat.db.

    Returns a list of conversation dicts sorted by most recent message,
    newest first. Uses cached contact names only (no blocking AppleScript).
    """
    conn = _open_db()
    try:
        # CTE finds the latest message ROWID per chat, then we join
        # back to get its text. This avoids the invalid
        # "MAX() inside subquery WHERE" that SQLite rejects.
        # Runs in ~10ms vs the old correlated subquery (6+ seconds).
        rows = conn.execute("""
            WITH latest_msg AS (
                SELECT cmj.chat_id, MAX(m.ROWID) as max_msg_id
                FROM chat_message_join cmj
                JOIN message m ON cmj.message_id = m.ROWID
                GROUP BY cmj.chat_id
            )
            SELECT
                c.ROWID as chat_id,
                c.chat_identifier,
                c.display_name,
                c.service_name,
                MAX(m.date) as last_message_date,
                lm_text.text as last_message_text,
                COUNT(m.ROWID) as message_count,
                SUM(CASE WHEN m.is_read = 0 AND m.is_from_me = 0
                    THEN 1 ELSE 0 END) as unread_count
            FROM chat c
            LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            LEFT JOIN message m ON cmj.message_id = m.ROWID
            LEFT JOIN latest_msg lm ON lm.chat_id = c.ROWID
            LEFT JOIN message lm_text ON lm_text.ROWID = lm.max_msg_id
            GROUP BY c.ROWID
            ORDER BY last_message_date DESC
            LIMIT ?
        """, (limit,)).fetchall()

        conversations = []
        identifiers_to_lookup: list[str] = []
        for row in rows:
            chat_id = row["chat_id"]
            identifier = row["chat_identifier"] or ""
            display_name = row["display_name"] or ""

            # Use cached contact name (never blocks on AppleScript)
            if not display_name:
                display_name = _get_contact_display_name_cached(identifier)
                identifiers_to_lookup.append(identifier)

            last_date = _apple_epoch_to_unix(row["last_message_date"])
            last_text = row["last_message_text"] or ""
            # Truncate long preview text
            if len(last_text) > 150:
                last_text = last_text[:150] + "..."

            conversations.append({
                "id": chat_id,
                "identifier": identifier,
                "display_name": display_name,
                "service": row["service_name"] or "iMessage",
                "last_message_date": _unix_to_iso(last_date),
                "last_message_preview": last_text,
                "message_count": row["message_count"] or 0,
                "unread_count": row["unread_count"] or 0,
            })

        return conversations
    finally:
        conn.close()


def _load_conversations_cache() -> list[dict] | None:
    """Return cached conversations if fresh and non-empty."""
    if not CONVERSATIONS_CACHE_PATH.exists():
        return None
    age = time.time() - CONVERSATIONS_CACHE_PATH.stat().st_mtime
    if age > _CONVERSATIONS_CACHE_TTL:
        return None
    try:
        data = json.loads(CONVERSATIONS_CACHE_PATH.read_text())
    except Exception:
        return None
    if not data:
        return None
    return data


def _save_conversations_cache(conversations: list[dict]) -> None:
    _ensure_dirs()
    atomic_write_text(CONVERSATIONS_CACHE_PATH, json.dumps(conversations))


def invalidate_conversations_cache() -> None:
    """Remove the cached conversations file."""
    if CONVERSATIONS_CACHE_PATH.exists():
        CONVERSATIONS_CACHE_PATH.unlink(missing_ok=True)


async def get_conversations(limit: int = 50) -> list[dict]:
    """Return recent conversations.

    Checks the on-disk cache first (60 second TTL). On cache miss, reads
    from chat.db in a thread so the async event loop is not blocked.
    Circuit breaker stops attempts after 2 consecutive failures for 5 minutes.

    Contact name lookups are non-blocking: uses cached names immediately,
    schedules a background refresh for uncached identifiers.
    """
    cached = _load_conversations_cache()
    if cached is not None:
        return cached

    if _breaker_is_open():
        logger.warning("iMessage circuit breaker is open, returning empty list")
        return []

    try:
        conversations = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: get_conversations_sync(limit)
            ),
            timeout=10.0,
        )
        _breaker_record_success()
    except Exception as exc:
        logger.error("Failed to load iMessage conversations: %s", exc)
        _breaker_record_failure()
        return []

    _save_conversations_cache(conversations)

    # Schedule background contact name refresh for uncached identifiers.
    # This doesn't block the response. Next request will have real names.
    uncached_ids = [
        c["identifier"] for c in conversations
        if c["identifier"] and "@" not in c["identifier"]
        and c["identifier"] not in (_contacts_cache or {})
    ]
    if uncached_ids:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, lambda: _refresh_contacts_for_identifiers(uncached_ids)
        )

    return conversations


def get_messages_sync(chat_id: int, limit: int = 100) -> list[dict]:
    """Fetch messages for a specific conversation from chat.db.

    Returns messages sorted oldest first (natural reading order).
    """
    conn = _open_db()
    try:
        rows = conn.execute("""
            SELECT
                m.ROWID as message_id,
                m.text,
                m.date as message_date,
                m.is_from_me,
                m.is_read,
                m.service,
                h.id as sender_identifier
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE cmj.chat_id = ?
            ORDER BY m.date DESC
            LIMIT ?
        """, (chat_id, limit)).fetchall()

        messages = []
        for row in rows:
            msg_date = _apple_epoch_to_unix(row["message_date"])
            text = row["text"] or ""
            sender = ""
            if row["is_from_me"]:
                sender = "me"
            else:
                sender = row["sender_identifier"] or ""

            messages.append({
                "id": row["message_id"],
                "text": text,
                "date": _unix_to_iso(msg_date),
                "is_from_me": bool(row["is_from_me"]),
                "is_read": bool(row["is_read"]),
                "sender": sender,
            })

        # Reverse so messages are oldest-first (natural reading order)
        messages.reverse()
        return messages
    finally:
        conn.close()


async def get_messages(chat_id: int, limit: int = 100) -> list[dict]:
    """Return messages for a conversation.

    Reads from chat.db in a thread so the async event loop is not blocked.
    """
    return await asyncio.wait_for(
        asyncio.get_event_loop().run_in_executor(
            None, lambda: get_messages_sync(chat_id, limit)
        ),
        timeout=10.0,
    )


def search_messages_sync(query: str, limit: int = 50) -> list[dict]:
    """Search messages in chat.db by text content.

    Returns matching messages sorted by date, newest first.
    """
    if not query or len(query.strip()) < 2:
        return []

    conn = _open_db()
    try:
        search_term = f"%{query}%"
        rows = conn.execute("""
            SELECT
                m.ROWID as message_id,
                m.text,
                m.date as message_date,
                m.is_from_me,
                c.ROWID as chat_id,
                c.chat_identifier,
                c.display_name,
                h.id as sender_identifier
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.text LIKE ?
            ORDER BY m.date DESC
            LIMIT ?
        """, (search_term, limit)).fetchall()

        results = []
        for row in rows:
            msg_date = _apple_epoch_to_unix(row["message_date"])
            text = row["text"] or ""
            display_name = row["display_name"] or ""
            identifier = row["chat_identifier"] or ""
            if not display_name:
                display_name = _format_phone(identifier)

            results.append({
                "message_id": row["message_id"],
                "text": text,
                "date": _unix_to_iso(msg_date),
                "is_from_me": bool(row["is_from_me"]),
                "chat_id": row["chat_id"],
                "chat_identifier": identifier,
                "chat_display_name": display_name,
                "sender": row["sender_identifier"] or ("me" if row["is_from_me"] else ""),
            })

        return results
    finally:
        conn.close()


async def search_messages(query: str, limit: int = 50) -> list[dict]:
    """Search iMessages by text content.

    Runs in a thread so the async event loop is not blocked.
    """
    return await asyncio.wait_for(
        asyncio.get_event_loop().run_in_executor(
            None, lambda: search_messages_sync(query, limit)
        ),
        timeout=10.0,
    )


def send_message_sync(recipient: str, text: str) -> dict:
    """Send an iMessage via AppleScript.

    Args:
        recipient: phone number or email to send to
        text: the message body

    Returns a dict with ok=True on success, or raises RuntimeError on failure.
    """
    if not recipient or not text:
        raise ValueError("Both recipient and message text are required.")

    # Sanitize inputs for AppleScript
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_recipient = recipient.replace("\\", "\\\\").replace('"', '\\"')

    # Use the newer Messages app AppleScript approach
    script = f'''
        tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant "{safe_recipient}" of targetService
            send "{safe_text}" to targetBuddy
        end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error sending message"
            raise RuntimeError(f"Failed to send iMessage: {error_msg}")

        return {"ok": True}
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Timed out trying to send the message. "
            "Make sure the Messages app is running."
        )


async def send_message(recipient: str, text: str) -> dict:
    """Send an iMessage asynchronously.

    Runs the AppleScript send in a thread so the async event loop is not blocked.
    """
    return await asyncio.wait_for(
        asyncio.get_event_loop().run_in_executor(
            None, lambda: send_message_sync(recipient, text)
        ),
        timeout=20.0,
    )

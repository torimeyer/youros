"""iMessage service.

Reads messages from the macOS iMessage database (~/Library/Messages/chat.db)
and sends messages via AppleScript. The database is read-only. Reading
requires Full Disk Access permission in System Settings > Privacy & Security.

Cache lives in ~/.youros/imessage_cache/ so user data stays outside the repo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import plistlib
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from services.atomic_io import atomic_write_text
from services.imessage_contacts import list_contacts

logger = logging.getLogger(__name__)

MYOS_DIR = Path.home() / ".youros"
IMESSAGE_CACHE_DIR = MYOS_DIR / "imessage_cache"
CONVERSATIONS_CACHE_PATH = IMESSAGE_CACHE_DIR / "conversations.json"
CONTACTS_CACHE_PATH = IMESSAGE_CACHE_DIR / "contacts.json"
CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Cache TTL: 15 seconds for conversations list.
_CONVERSATIONS_CACHE_TTL = 15

# Contact name cache: loaded once from disk, updated in background.
_contacts_cache: dict[str, str] | None = None

# Set to True after the bulk Swift loader has run (or failed) once per boot.
_bulk_contacts_loaded: bool = False

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


# Emit the mute-parse warning at most once per backend lifetime.
_muted_parse_logged: bool = False


def _is_chat_muted(properties_blob: bytes | None) -> bool:
    """Return True when the chat has Hide Alerts active.

    Checks CKDMutedUntilDateKey (a future datetime) in the binary plist stored
    in chat.properties. Falls back to a boolean isMuted key. Returns False on
    any parse error and logs a warning once per backend lifetime.
    """
    if not properties_blob:
        return False
    global _muted_parse_logged
    try:
        pl = plistlib.loads(bytes(properties_blob))
        if not isinstance(pl, dict):
            return False
        muted_until = pl.get("CKDMutedUntilDateKey")
        if muted_until is not None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            if isinstance(muted_until, datetime):
                if muted_until.tzinfo is None:
                    muted_until = muted_until.replace(tzinfo=timezone.utc)
                return muted_until > now
        return bool(pl.get("isMuted", False))
    except Exception as exc:
        if not _muted_parse_logged:
            logger.warning("Could not parse chat.properties plist: %s", exc)
            _muted_parse_logged = True
        return False


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


def _normalize_phone_key(phone: str) -> str:
    """Return the last 10 digits of a phone number for cache indexing."""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def _populate_contacts_from_bulk_loader() -> None:
    """Populate _contacts_cache from the Swift bulk loader.

    Runs the Swift subprocess (~1s) once per backend boot. Indexes contacts by:
    - Last 10 digits of each phone number (handles +1 prefix variation)
    - Lowercased email address

    Merges into the existing on-disk cache so previously-resolved names are
    retained. Persists the result so next boot serves from disk instantly.
    """
    global _contacts_cache
    try:
        contacts = list_contacts()
        if not contacts:
            logger.info("Bulk contact loader: no contacts returned")
            return
        cache = _load_contacts_cache()
        for contact in contacts:
            name = contact.get("name", "")
            if not name:
                continue
            for phone in contact.get("phone_numbers", []):
                key = _normalize_phone_key(phone)
                if key:
                    cache[key] = name
            for email in contact.get("emails", []):
                if email:
                    cache[email.lower()] = name
        _contacts_cache = cache
        _save_contacts_cache()
        logger.info("Bulk contact loader: %d contacts indexed", len(contacts))
    except Exception as exc:
        logger.warning("Bulk contact loader failed, AppleScript fallback available: %s", exc)


def _get_contact_display_name_cached(identifier: str) -> str:
    """Return a display name from the persistent cache, or the formatted number.

    Never blocks. Checks the cache by exact identifier, then by normalized
    10-digit phone key (handles +1 vs local-number differences).
    """
    cache = _load_contacts_cache()
    if identifier in cache:
        return cache[identifier]
    if "@" not in identifier:
        key = _normalize_phone_key(identifier)
        if key and key in cache:
            return cache[key]
    return _format_phone(identifier)


def _is_contact_known(identifier: str) -> bool:
    """Return True if this identifier has a resolved name in the contacts cache."""
    cache = _load_contacts_cache()
    if identifier in cache:
        return True
    if "@" not in identifier:
        key = _normalize_phone_key(identifier)
        if key and key in cache:
            return True
    return False


def save_contact(identifier: str, name: str) -> None:
    """Save a name for an identifier to the local contacts cache only.

    Does NOT modify macOS Contacts. Persists to
    ~/.youros/imessage_cache/contacts.json so the name survives backend restarts.
    Also invalidates the conversations cache so the new name appears immediately.
    """
    cache = _load_contacts_cache()
    cache[identifier] = name
    if "@" not in identifier:
        key = _normalize_phone_key(identifier)
        if key:
            cache[key] = name
    _save_contacts_cache()
    invalidate_conversations_cache()


def _get_first_name_for_identifier(identifier: str) -> str:
    """Return the first name of a contact, or the raw identifier if unknown.

    Looks up the contact in the cache by exact key and normalized 10-digit
    phone key. If found, returns the first whitespace-delimited token of the
    full name. If not found, returns the raw identifier (phone or email) so
    group chat names still degrade gracefully.
    """
    cache = _load_contacts_cache()
    name: str | None = None
    if identifier in cache:
        name = cache[identifier]
    elif "@" not in identifier:
        key = _normalize_phone_key(identifier)
        if key and key in cache:
            name = cache[key]
    if name:
        parts = name.split()
        return parts[0] if parts else identifier
    return identifier


def _synthesize_group_display_name(conn: sqlite3.Connection, chat_id: int) -> str:
    """Build a display name for a group chat from participant first names.

    Queries chat_handle_join + handle for all participants, maps each
    handle.id to a contact first name (or raw identifier as fallback),
    then formats the result:
      - 1-3 names  → "Alice, Bob, Carol"
      - 4+ names   → "Alice, Bob & N others"

    Falls back to "Group Chat" if the table is missing or empty.
    """
    try:
        rows = conn.execute(
            """SELECT h.id
               FROM chat_handle_join chj
               JOIN handle h ON h.ROWID = chj.handle_id
               WHERE chj.chat_id = ?""",
            (chat_id,),
        ).fetchall()
    except Exception:
        return "Group Chat"

    if not rows:
        return "Group Chat"

    first_names = [_get_first_name_for_identifier(row["id"]) for row in rows]
    count = len(first_names)
    if count <= 3:
        return ", ".join(first_names)
    others = count - 2
    return f"{first_names[0]}, {first_names[1]} & {others} others"


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
                c.properties,
                MAX(m.date) as last_message_date,
                lm_text.text as last_message_text,
                COUNT(m.ROWID) as message_count,
                SUM(CASE
                    WHEN m.is_from_me = 1 THEN 0
                    WHEN c.last_read_message_timestamp > 0 AND m.date > c.last_read_message_timestamp THEN 1
                    WHEN c.last_read_message_timestamp = 0 AND m.is_read = 0 THEN 1
                    ELSE 0
                END) as unread_count
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
        for row in rows:
            chat_id = row["chat_id"]
            identifier = row["chat_identifier"] or ""
            display_name = row["display_name"] or ""

            # Use cached contact name (never blocks on AppleScript).
            # For group chats (UUID identifiers), show "Group Chat" if no
            # display_name is set. For direct chats, show the contact name
            # or formatted phone number.
            has_contact_name: bool
            if not display_name:
                if _is_direct_identifier(identifier):
                    has_contact_name = _is_contact_known(identifier)
                    display_name = _get_contact_display_name_cached(identifier)
                else:
                    has_contact_name = True
                    display_name = _synthesize_group_display_name(conn, chat_id)
            else:
                has_contact_name = True

            last_date = _apple_epoch_to_unix(row["last_message_date"])
            last_text = row["last_message_text"] or ""
            # Truncate long preview text
            if len(last_text) > 150:
                last_text = last_text[:150] + "..."

            conversations.append({
                "id": chat_id,
                "identifier": identifier,
                "display_name": display_name,
                "has_contact_name": has_contact_name,
                "service": row["service_name"] or "iMessage",
                "last_message_date": _unix_to_iso(last_date),
                "last_message_preview": last_text,
                "message_count": row["message_count"] or 0,
                "unread_count": 0 if _is_chat_muted(row["properties"]) else (row["unread_count"] or 0),
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

    On the first call after backend boot, runs the Swift bulk contact loader
    (~1s) so names appear immediately instead of trickling in one-by-one.
    Subsequent calls use the on-disk contact cache and conversations cache
    (15s TTL). Circuit breaker stops attempts after 2 consecutive failures
    for 5 minutes.
    """
    global _bulk_contacts_loaded
    if not _bulk_contacts_loaded:
        _bulk_contacts_loaded = True
        await asyncio.get_event_loop().run_in_executor(
            None, _populate_contacts_from_bulk_loader
        )

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
    return conversations


def _decode_attributed_body(blob: bytes | None) -> str | None:
    """Decode an NSAttributedString binary blob from macOS chat.db.

    macOS stores recent message bodies in the attributedBody BLOB column using
    StreamTypedCoder encoding. The text column is NULL for these messages.
    Format: find the NSString marker, skip 5 metadata bytes, read the length,
    then decode the UTF-8 content.
    """
    if not blob:
        return None
    try:
        data = bytes(blob)
        idx = data.find(b"NSString")
        if idx == -1:
            return None
        after = data[idx + 8:]
        if len(after) < 7:
            return None
        # Skip 5 metadata bytes (class type tag + version + value header).
        # The 6th byte encodes the string length.
        length_byte = after[5]
        if length_byte <= 0x7F:
            n = length_byte
            text_bytes = after[6:6 + n]
        elif length_byte == 0x81:
            if len(after) < 8:
                return None
            n = after[6]
            text_bytes = after[7:7 + n]
        elif length_byte == 0x82:
            if len(after) < 9:
                return None
            n = (after[6] << 8) | after[7]
            text_bytes = after[8:8 + n]
        else:
            return None
        text = text_bytes.decode("utf-8", errors="replace")
        # Strip leading StreamTypedCoder marker bytes (control chars < 0x20).
        i = 0
        while i < len(text) and ord(text[i]) < 32:
            i += 1
        text = text[i:]
        return text if text else None
    except Exception:
        return None


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
                m.attributedBody,
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

        # Build a set of message IDs to batch-fetch attachments
        msg_ids = [r["message_id"] for r in rows]
        attachments_by_msg: dict[int, list[dict]] = {}
        if msg_ids:
            placeholders = ",".join("?" * len(msg_ids))
            att_rows = conn.execute(f"""
                SELECT
                    maj.message_id,
                    a.filename,
                    a.mime_type,
                    a.transfer_name
                FROM message_attachment_join maj
                JOIN attachment a ON maj.attachment_id = a.ROWID
                WHERE maj.message_id IN ({placeholders})
            """, msg_ids).fetchall()
            for ar in att_rows:
                mid = ar["message_id"]
                if mid not in attachments_by_msg:
                    attachments_by_msg[mid] = []
                fname = ar["filename"] or ""
                # macOS stores paths with ~ prefix, expand it
                if fname.startswith("~"):
                    fname = str(Path.home() / fname[2:])
                attachments_by_msg[mid].append({
                    "filename": fname,
                    "mime_type": ar["mime_type"] or "",
                    "transfer_name": ar["transfer_name"] or "",
                })

        messages = []
        for row in rows:
            msg_date = _apple_epoch_to_unix(row["message_date"])
            text = row["text"] or _decode_attributed_body(row["attributedBody"]) or ""
            sender = ""
            if row["is_from_me"]:
                sender = "me"
            else:
                sender = row["sender_identifier"] or ""

            msg: dict = {
                "id": row["message_id"],
                "text": text,
                "date": _unix_to_iso(msg_date),
                "is_from_me": bool(row["is_from_me"]),
                "is_read": bool(row["is_read"]),
                "sender": sender,
            }
            atts = attachments_by_msg.get(row["message_id"])
            if atts:
                msg["attachments"] = atts
            messages.append(msg)

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


def _escape_applescript_text(text: str) -> str:
    """Escape text for embedding inside an AppleScript double-quoted string.

    Order matters: backslash and quote escaping must happen before the newline
    substitution so the injected '& return &' delimiters are never double-escaped.
    """
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    # Newlines cannot appear inside an AppleScript string literal; replace with
    # AppleScript's built-in return constant, concatenated around the string.
    safe = safe.replace("\n", '" & return & "')
    return safe


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
    safe_text = _escape_applescript_text(text)
    safe_recipient = _escape_applescript_text(recipient)

    # send_message is for NEW messages to a phone number or email only.
    # Replies to existing conversations go through reply_to_chat_sync.
    if safe_recipient.startswith("chat"):
        raise ValueError(
            "Use the reply endpoint for existing conversations. "
            "send_message is for new messages to a phone number or email."
        )

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


def _is_direct_identifier(identifier: str) -> bool:
    """Return True if identifier looks like a phone number or email (direct chat).

    Group chats use UUIDs like 'chat00000000-0000-0000-0000-000000000000'.
    Direct chats use phone numbers ('+15551234567') or emails ('user@example.com').
    """
    if not identifier:
        return False
    if "@" in identifier:
        return True
    return bool(re.match(r"^\+?[\d\s\-().]+$", identifier.strip()))


def reply_to_chat_sync(chat_id: int, text: str) -> dict:
    """Reply to a specific conversation by its database row ID.

    Works for both direct messages (phone/email identifiers) and group chats
    (UUID identifiers). Looks up the chat type from chat.db and uses the
    appropriate AppleScript approach for each.

    Args:
        chat_id: The chat.ROWID from chat.db.
        text: The message body.

    Returns a dict with ok=True on success, or raises RuntimeError on failure.
    """
    if not text:
        raise ValueError("Message text is required.")

    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT chat_identifier, service_name FROM chat WHERE ROWID = ?",
            (chat_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f"Conversation {chat_id} not found.")

    identifier = row["chat_identifier"] or ""
    service_name = (row["service_name"] or "iMessage").lower()

    if not identifier:
        raise ValueError("Conversation has no identifier.")

    safe_text = _escape_applescript_text(text)
    safe_identifier = _escape_applescript_text(identifier)

    if _is_direct_identifier(identifier):
        service_type = "SMS" if "sms" in service_name else "iMessage"
        script = f'''
            tell application "Messages"
                set targetService to 1st account whose service type = {service_type}
                set targetBuddy to participant "{safe_identifier}" of targetService
                send "{safe_text}" to targetBuddy
            end tell
        '''
    else:
        # Group chat: find by chat identifier. AppleScript chat IDs
        # use the format "iMessage;+;chatNNNN" or "iMessage;-;chatNNNN"
        # so we match with "contains" instead of exact equality.
        script = f'''
            tell application "Messages"
                repeat with c in (every chat)
                    if (id of c) contains "{safe_identifier}" then
                        send "{safe_text}" to c
                        return
                    end if
                end repeat
                error "Chat not found: {safe_identifier}"
            end tell
        '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error sending reply"
            raise RuntimeError(f"Failed to send reply: {error_msg}")
        return {"ok": True}
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Timed out trying to send the reply. "
            "Make sure the Messages app is running."
        )


async def reply_to_chat(chat_id: int, text: str) -> dict:
    """Reply to a chat asynchronously.

    Runs the AppleScript send in a thread so the async event loop is not blocked.
    """
    return await asyncio.wait_for(
        asyncio.get_event_loop().run_in_executor(
            None, lambda: reply_to_chat_sync(chat_id, text)
        ),
        timeout=20.0,
    )


def _names_match(query: str, name: str) -> bool:
    """Fuzzy-match a query string against a contact display name.

    Accepts: exact match, prefix match, substring match, or all query words
    appearing somewhere in the name (handles nicknames like "Lil Oatmeal").
    """
    q = query.lower().strip()
    n = name.lower().strip()
    if not q or not n:
        return False
    if q == n:
        return True
    if n.startswith(q) or q in n:
        return True
    q_words = q.split()
    n_words = n.split()
    return all(any(qw in nw or nw.startswith(qw) for nw in n_words) for qw in q_words)


def resolve_contact_phrase_sync(phrase: str) -> dict:
    """Resolve a contact name and message from a natural-language phrase.

    Tries splitting the phrase at word positions 1 through 4 and fuzzy-matches
    each candidate name prefix against known contacts (cache then conversations).

    Args:
        phrase: text after the intent verb, e.g. "lil oatmeal goodnight"

    Returns:
        { identifier, display_name, message_text }

    Raises:
        ValueError if no matching contact is found.
    """
    words = phrase.strip().split()
    if len(words) < 2:
        raise ValueError("Need at least a contact name and a message word.")

    cache = _load_contacts_cache()
    try:
        conversations = get_conversations_sync(limit=200)
    except Exception:
        conversations = []

    # Iterate longest possible name first so "lil oatmeal goodnight" resolves
    # to name="lil oatmeal" + message="goodnight" rather than name="lil" +
    # message="oatmeal goodnight" (which a prefix match on "lil" would give).
    for name_len in range(min(4, len(words) - 1), 0, -1):
        candidate_name = " ".join(words[:name_len])
        candidate_msg = " ".join(words[name_len:])
        if not candidate_msg.strip():
            continue

        for identifier, cached_name in cache.items():
            if _names_match(candidate_name, cached_name):
                return {
                    "identifier": identifier,
                    "display_name": cached_name,
                    "message_text": candidate_msg,
                }

        for conv in conversations:
            if _names_match(candidate_name, conv["display_name"]):
                return {
                    "identifier": conv["identifier"],
                    "display_name": conv["display_name"],
                    "message_text": candidate_msg,
                }

    raise ValueError(f"No contact found matching '{phrase}'.")


async def resolve_contact_phrase(phrase: str) -> dict:
    """Async wrapper for resolve_contact_phrase_sync."""
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: resolve_contact_phrase_sync(phrase)
    )


def get_all_recent_messages_sync(limit: int = 50) -> list[dict]:
    """Fetch the most recent messages across ALL chats for inbound polling.

    Unlike get_messages_sync (which requires a chat_id), this returns a
    flat list of the N most recent messages regardless of conversation,
    ordered newest-first. Used by InboundPoller to detect new inbound texts.

    Returns dicts with:
      id, text, date (float Unix timestamp), is_from_me, sender
    """
    conn = _open_db()
    try:
        rows = conn.execute("""
            SELECT
                m.ROWID as message_id,
                m.text,
                m.attributedBody,
                m.date as message_date,
                m.is_from_me,
                m.service,
                cmj.chat_id,
                h.id as sender_identifier
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            ORDER BY m.date DESC
            LIMIT ?
        """, (limit,)).fetchall()

        messages = []
        for row in rows:
            msg_date_ts = _apple_epoch_to_unix(row["message_date"])
            text = row["text"] or _decode_attributed_body(row["attributedBody"]) or ""
            sender = row["sender_identifier"] or ("me" if row["is_from_me"] else "")
            messages.append({
                "id": row["message_id"],
                "text": text,
                "date": msg_date_ts,
                "is_from_me": bool(row["is_from_me"]),
                "sender": sender,
                "service": row["service"],
                "chat_id": row["chat_id"],
            })
        return messages
    finally:
        conn.close()

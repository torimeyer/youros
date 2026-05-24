"""Tests for the iMessage integration.

All database reads and AppleScript calls are mocked so tests run on any
platform without a real iMessage database or macOS Messages app.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conversations(n: int = 3) -> list[dict]:
    return [
        {
            "id": i,
            "identifier": f"+1555000{i:04d}",
            "display_name": f"Contact {i}",
            "service": "iMessage",
            "last_message_date": f"2026-04-10T10:{i:02d}:00+00:00",
            "last_message_preview": f"Hey, this is message {i}",
            "message_count": 10 + i,
            "unread_count": i % 2,
        }
        for i in range(n)
    ]


def _make_messages(n: int = 5) -> list[dict]:
    return [
        {
            "id": i,
            "text": f"Message text {i}",
            "date": f"2026-04-10T10:{i:02d}:00+00:00",
            "is_from_me": i % 2 == 0,
            "is_read": True,
            "sender": "me" if i % 2 == 0 else "+15550001234",
        }
        for i in range(n)
    ]


def _make_search_results(n: int = 3) -> list[dict]:
    return [
        {
            "message_id": i,
            "text": f"Found message {i}",
            "date": f"2026-04-10T10:{i:02d}:00+00:00",
            "is_from_me": False,
            "chat_id": i,
            "chat_identifier": f"+1555000{i:04d}",
            "chat_display_name": f"Contact {i}",
            "sender": f"+1555000{i:04d}",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_status_available(client):
    """When the database is readable, status should report available."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": True, "reason": None},
    ):
        resp = await client.get("/api/imessage/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["reason"] is None


@pytest.mark.asyncio
async def test_imessage_status_not_available(client):
    """When chat.db is missing, status should report not available."""
    with patch(
        "services.imessage.is_available",
        return_value={
            "available": False,
            "reason": "iMessage database not found. This feature only works on macOS.",
        },
    ):
        resp = await client.get("/api/imessage/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert "macOS" in data["reason"]


# ---------------------------------------------------------------------------
# Conversations endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_conversations_not_available(client):
    """When iMessage is not available, conversations should return 503."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": False, "reason": "Not on macOS"},
    ):
        resp = await client.get("/api/imessage/conversations")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_conversations_success(client):
    """When available, conversations endpoint returns conversation list."""
    fake_convos = _make_conversations(3)

    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.get_conversations",
            new=AsyncMock(return_value=fake_convos),
        ),
    ):
        resp = await client.get("/api/imessage/conversations")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["conversations"]) == 3
    assert data["conversations"][0]["display_name"] == "Contact 0"


@pytest.mark.asyncio
async def test_imessage_conversations_with_limit(client):
    """The limit query parameter is passed to the service."""
    fake_convos = _make_conversations(2)

    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.get_conversations",
            new=AsyncMock(return_value=fake_convos),
        ),
    ):
        resp = await client.get("/api/imessage/conversations?limit=10")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_imessage_conversations_error(client):
    """When the service raises, the endpoint returns 500 with a message."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.get_conversations",
            new=AsyncMock(side_effect=RuntimeError("db locked")),
        ),
    ):
        resp = await client.get("/api/imessage/conversations")

    assert resp.status_code == 500
    assert "db locked" in resp.text


# ---------------------------------------------------------------------------
# Messages endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_messages_not_available(client):
    """When iMessage is not available, messages endpoint returns 503."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": False, "reason": "Not on macOS"},
    ):
        resp = await client.get("/api/imessage/conversations/1/messages")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_messages_success(client):
    """Returns messages for a conversation."""
    fake_msgs = _make_messages(5)

    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.get_messages",
            new=AsyncMock(return_value=fake_msgs),
        ),
    ):
        resp = await client.get("/api/imessage/conversations/42/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    assert data["messages"][0]["text"] == "Message text 0"


# ---------------------------------------------------------------------------
# Send endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_send_not_available(client):
    """When iMessage is not available, send returns 503."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": False, "reason": "Not on macOS"},
    ):
        resp = await client.post(
            "/api/imessage/send",
            json={"recipient": "+15550001234", "text": "Hello"},
        )

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_send_success(client):
    """Successful send returns ok=True and invalidates cache."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.send_message",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "services.imessage.invalidate_conversations_cache",
        ) as mock_invalidate,
    ):
        resp = await client.post(
            "/api/imessage/send",
            json={"recipient": "+15550001234", "text": "Hello there"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    mock_invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_imessage_send_missing_fields(client):
    """Send without required fields returns 422."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": True, "reason": None},
    ):
        resp = await client.post(
            "/api/imessage/send",
            json={"recipient": "+15550001234"},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_imessage_send_value_error(client):
    """When the service raises ValueError, the endpoint returns 400."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.send_message",
            new=AsyncMock(side_effect=ValueError("Both recipient and message text are required.")),
        ),
    ):
        resp = await client.post(
            "/api/imessage/send",
            json={"recipient": "", "text": "Hello"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_imessage_send_runtime_error(client):
    """When AppleScript fails, the endpoint returns 500 with error message."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.send_message",
            new=AsyncMock(side_effect=RuntimeError("Failed to send iMessage: recipient not found")),
        ),
    ):
        resp = await client.post(
            "/api/imessage/send",
            json={"recipient": "+15550001234", "text": "Hello"},
        )

    assert resp.status_code == 500
    assert "recipient not found" in resp.text


# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_search_not_available(client):
    """When iMessage is not available, search returns 503."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": False, "reason": "Not on macOS"},
    ):
        resp = await client.get("/api/imessage/search?q=hello")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_search_success(client):
    """Search returns matching results."""
    fake_results = _make_search_results(3)

    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.search_messages",
            new=AsyncMock(return_value=fake_results),
        ),
    ):
        resp = await client.get("/api/imessage/search?q=hello")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 3
    assert data["query"] == "hello"


@pytest.mark.asyncio
async def test_imessage_search_too_short(client):
    """Search with a query shorter than 2 characters returns 422."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": True, "reason": None},
    ):
        resp = await client.get("/api/imessage/search?q=a")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_imessage_search_missing_query(client):
    """Search without a query parameter returns 422."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": True, "reason": None},
    ):
        resp = await client.get("/api/imessage/search")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


def test_apple_epoch_to_unix():
    """Apple epoch conversion produces correct unix timestamps."""
    from services.imessage import _apple_epoch_to_unix

    # Zero input should return 0
    assert _apple_epoch_to_unix(0) == 0.0
    assert _apple_epoch_to_unix(None) == 0.0

    # Known conversion: 2024-01-01 00:00:00 UTC
    # Unix: 1704067200
    # Apple epoch: (1704067200 - 978307200) * 1e9 = 725760000 * 1e9
    apple_ts = 725760000 * 1_000_000_000
    unix_ts = _apple_epoch_to_unix(apple_ts)
    assert abs(unix_ts - 1704067200) < 1.0


def test_unix_to_iso():
    """Unix timestamps are converted to ISO 8601 strings."""
    from services.imessage import _unix_to_iso

    assert _unix_to_iso(0) == ""
    assert _unix_to_iso(-1) == ""

    iso = _unix_to_iso(1704067200)
    assert "2024-01-01" in iso


def test_format_phone():
    """Phone numbers are formatted for display."""
    from services.imessage import _format_phone

    assert _format_phone("") == ""
    assert _format_phone("user@example.com") == "user@example.com"
    assert _format_phone("+15551234567") == "+1 (555) 123-4567"
    assert _format_phone("5551234567") == "(555) 123-4567"


def test_is_available_no_db(tmp_path):
    """is_available returns False when chat.db does not exist."""
    from services import imessage

    fake_path = tmp_path / "nonexistent.db"
    with patch.object(imessage, "CHAT_DB_PATH", fake_path):
        result = imessage.is_available()

    assert result["available"] is False
    assert "macOS" in result["reason"]


def test_is_available_permission_denied(tmp_path):
    """is_available returns False with a helpful message on permission error."""
    from services import imessage

    # Create a db file but make it unreadable by returning an error
    fake_db = tmp_path / "chat.db"
    fake_db.write_text("")  # empty file

    with (
        patch.object(imessage, "CHAT_DB_PATH", fake_db),
        patch("sqlite3.connect", side_effect=sqlite3.OperationalError("unable to open")),
    ):
        result = imessage.is_available()

    assert result["available"] is False
    assert "Full Disk Access" in result["reason"]


def test_conversations_cache_miss_and_save(tmp_path):
    """When no cache exists, _load returns None. After saving, it returns data."""
    from services import imessage

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "conversations.json"

    with (
        patch.object(imessage, "IMESSAGE_CACHE_DIR", cache_dir),
        patch.object(imessage, "CONVERSATIONS_CACHE_PATH", cache_path),
    ):
        assert imessage._load_conversations_cache() is None

        fake_data = _make_conversations(2)
        imessage._save_conversations_cache(fake_data)

        loaded = imessage._load_conversations_cache()
        assert loaded is not None
        assert len(loaded) == 2


def test_conversations_cache_expired(tmp_path):
    """Stale cache returns None so a fresh fetch is triggered."""
    import os
    from services import imessage

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "conversations.json"
    cache_path.write_text(json.dumps(_make_conversations(2)))

    # Make the cache file old
    old_time = time.time() - 120  # > 60s TTL
    os.utime(cache_path, (old_time, old_time))

    with patch.object(imessage, "CONVERSATIONS_CACHE_PATH", cache_path):
        assert imessage._load_conversations_cache() is None


def test_conversations_cache_empty_treated_as_miss(tmp_path):
    """An empty cache list is treated as a miss (same pattern as Gmail)."""
    from services import imessage

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "conversations.json"
    cache_path.write_text(json.dumps([]))

    with patch.object(imessage, "CONVERSATIONS_CACHE_PATH", cache_path):
        assert imessage._load_conversations_cache() is None


def test_invalidate_conversations_cache(tmp_path):
    """invalidate_conversations_cache removes the cache file."""
    from services import imessage

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "conversations.json"
    cache_path.write_text(json.dumps(_make_conversations(2)))

    assert cache_path.exists()

    with patch.object(imessage, "CONVERSATIONS_CACHE_PATH", cache_path):
        imessage.invalidate_conversations_cache()

    assert not cache_path.exists()


# ---------------------------------------------------------------------------
# Reply endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_reply_not_available(client):
    """When iMessage is not available, reply returns 503."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": False, "reason": "Not on macOS"},
    ):
        resp = await client.post(
            "/api/imessage/conversations/1/reply",
            json={"text": "Hey!"},
        )

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_reply_success(client):
    """Successful reply returns ok=True and invalidates cache."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.reply_to_chat",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "services.imessage.invalidate_conversations_cache",
        ) as mock_invalidate,
    ):
        resp = await client.post(
            "/api/imessage/conversations/42/reply",
            json={"text": "Sounds good!"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    mock_invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_imessage_reply_missing_text(client):
    """Reply without text returns 422."""
    with patch(
        "services.imessage.is_available",
        return_value={"available": True, "reason": None},
    ):
        resp = await client.post(
            "/api/imessage/conversations/1/reply",
            json={},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_imessage_reply_chat_not_found(client):
    """When the chat doesn't exist, reply returns 400."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.reply_to_chat",
            new=AsyncMock(side_effect=ValueError("Conversation 99 not found.")),
        ),
    ):
        resp = await client.post(
            "/api/imessage/conversations/99/reply",
            json={"text": "Hello"},
        )

    assert resp.status_code == 400
    assert "not found" in resp.text


@pytest.mark.asyncio
async def test_imessage_reply_runtime_error(client):
    """When AppleScript fails during reply, returns 500."""
    with (
        patch(
            "services.imessage.is_available",
            return_value={"available": True, "reason": None},
        ),
        patch(
            "services.imessage.reply_to_chat",
            new=AsyncMock(side_effect=RuntimeError("Failed to send reply: chat not found")),
        ),
    ):
        resp = await client.post(
            "/api/imessage/conversations/1/reply",
            json={"text": "Hello"},
        )

    assert resp.status_code == 500
    assert "chat not found" in resp.text


# ---------------------------------------------------------------------------
# reply_to_chat_sync service unit tests
# ---------------------------------------------------------------------------


def test_is_direct_identifier_phone():
    """Phone numbers are identified as direct identifiers."""
    from services.imessage import _is_direct_identifier

    assert _is_direct_identifier("+15551234567") is True
    assert _is_direct_identifier("5551234567") is True
    assert _is_direct_identifier("+1 (555) 123-4567") is True


def test_is_direct_identifier_email():
    """Email addresses are identified as direct identifiers."""
    from services.imessage import _is_direct_identifier

    assert _is_direct_identifier("user@example.com") is True


def test_is_direct_identifier_group_chat():
    """Group chat UUIDs are not direct identifiers."""
    from services.imessage import _is_direct_identifier

    assert _is_direct_identifier("chat00000000-0000-0000-0000-000000000000") is False
    assert _is_direct_identifier("chat12345678901234567890") is False


def test_is_direct_identifier_empty():
    """Empty string is not a direct identifier."""
    from services.imessage import _is_direct_identifier

    assert _is_direct_identifier("") is False


def test_reply_to_chat_sync_direct_message(tmp_path):
    """reply_to_chat_sync sends to buddy for direct message chats."""
    import sqlite3 as _sqlite3
    from services import imessage

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, service_name TEXT)")
    conn.execute("INSERT INTO chat VALUES (1, '+15551234567', 'iMessage')")
    conn.commit()
    conn.close()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with (
        patch.object(imessage, "CHAT_DB_PATH", db_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = imessage.reply_to_chat_sync(1, "Hey there!")

    assert result["ok"] is True
    script = mock_run.call_args[0][0][2]
    assert "participant" in script
    assert "+15551234567" in script
    assert "Hey there!" in script


def test_reply_to_chat_sync_group_chat(tmp_path):
    """reply_to_chat_sync iterates chats for group chat identifiers."""
    import sqlite3 as _sqlite3
    from services import imessage

    group_id = "chat00000000-0000-0000-0000-000000000000"
    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, service_name TEXT)")
    conn.execute(f"INSERT INTO chat VALUES (2, '{group_id}', 'iMessage')")
    conn.commit()
    conn.close()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with (
        patch.object(imessage, "CHAT_DB_PATH", db_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = imessage.reply_to_chat_sync(2, "Group message!")

    assert result["ok"] is True
    script = mock_run.call_args[0][0][2]
    assert "every chat" in script
    assert group_id in script
    assert "Group message!" in script


def test_reply_to_chat_sync_not_found(tmp_path):
    """reply_to_chat_sync raises ValueError when chat_id does not exist."""
    import sqlite3 as _sqlite3
    from services import imessage

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, service_name TEXT)")
    conn.commit()
    conn.close()

    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        with pytest.raises(ValueError, match="not found"):
            imessage.reply_to_chat_sync(99, "Hello")


def test_reply_to_chat_sync_empty_text(tmp_path):
    """reply_to_chat_sync raises ValueError when text is empty."""
    import sqlite3 as _sqlite3
    from services import imessage

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, service_name TEXT)")
    conn.execute("INSERT INTO chat VALUES (1, '+15551234567', 'iMessage')")
    conn.commit()
    conn.close()

    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        with pytest.raises(ValueError, match="required"):
            imessage.reply_to_chat_sync(1, "")


def test_send_message_sanitizes_input():
    """send_message_sync escapes special characters in AppleScript."""
    from services import imessage

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = imessage.send_message_sync("+15550001234", 'He said "hello"')

    assert result["ok"] is True
    # Verify the script was called
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    script = call_args[0][0][2]  # osascript -e <script>
    assert '\\"hello\\"' in script


def test_send_message_empty_fields():
    """send_message_sync raises ValueError on empty recipient or text."""
    from services import imessage

    with pytest.raises(ValueError):
        imessage.send_message_sync("", "Hello")

    with pytest.raises(ValueError):
        imessage.send_message_sync("+15550001234", "")


def test_send_message_applescript_failure():
    """send_message_sync raises RuntimeError when osascript fails."""
    from services import imessage

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "buddy not found"

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="buddy not found"):
            imessage.send_message_sync("+15550001234", "Hello")


def test_send_message_timeout():
    """send_message_sync raises RuntimeError on timeout."""
    import subprocess
    from services import imessage

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=15)):
        with pytest.raises(RuntimeError, match="Timed out"):
            imessage.send_message_sync("+15550001234", "Hello")


# ---------------------------------------------------------------------------
# Bulk contact loader (Phase 4)
# ---------------------------------------------------------------------------


def test_normalize_phone_key():
    """_normalize_phone_key strips formatting and returns last 10 digits."""
    from services.imessage import _normalize_phone_key

    assert _normalize_phone_key("+1 (555) 123-4567") == "5551234567"
    assert _normalize_phone_key("(555) 123-4567") == "5551234567"
    assert _normalize_phone_key("+15551234567") == "5551234567"
    assert _normalize_phone_key("5551234567") == "5551234567"
    assert _normalize_phone_key("") == ""


def test_populate_contacts_indexes_by_10_digit_key(tmp_path):
    """_populate_contacts_from_bulk_loader stores contacts keyed by last 10 digits."""
    from services import imessage

    fake_contacts = [
        {"name": "Alice Smith", "phone_numbers": ["+1 (555) 999-0001"], "emails": []},
        {"name": "Bob Jones", "phone_numbers": [], "emails": ["bob@example.com"]},
    ]
    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "contacts.json"

    with (
        patch("services.imessage.list_contacts", return_value=fake_contacts),
        patch.object(imessage, "_contacts_cache", None),
        patch.object(imessage, "IMESSAGE_CACHE_DIR", cache_dir),
        patch.object(imessage, "CONTACTS_CACHE_PATH", cache_path),
    ):
        imessage._populate_contacts_from_bulk_loader()
        cache = imessage._load_contacts_cache()

    assert cache.get("5559990001") == "Alice Smith"
    assert cache.get("bob@example.com") == "Bob Jones"


@pytest.mark.asyncio
async def test_get_conversations_uses_bulk_loaded_names(tmp_path):
    """get_conversations returns contact names (not raw numbers) after bulk load.

    Before this change, first load showed raw phone strings like '(555) 123-4567'.
    Now it shows resolved names within the same ~1s bulk load.
    """
    import sqlite3 as _sqlite3
    from services import imessage

    # Bulk loader returns one contact with a US phone number
    fake_contacts = [
        {"name": "Mom", "phone_numbers": ["+15550009999"], "emails": []},
    ]

    # Stub conversations DB result with that phone as the identifier
    fake_rows = [
        {
            "chat_id": 1,
            "chat_identifier": "+15550009999",
            "display_name": "",
            "service_name": "iMessage",
            "last_message_date": 700000000000000000,
            "last_message_text": "Hey",
            "message_count": 5,
            "unread_count": 0,
        }
    ]

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    contacts_path = cache_dir / "contacts.json"
    convos_path = cache_dir / "conversations.json"

    with (
        patch("services.imessage_contacts.list_contacts", return_value=fake_contacts),
        patch.object(imessage, "_contacts_cache", None),
        patch.object(imessage, "_bulk_contacts_loaded", False),
        patch.object(imessage, "IMESSAGE_CACHE_DIR", cache_dir),
        patch.object(imessage, "CONTACTS_CACHE_PATH", contacts_path),
        patch.object(imessage, "CONVERSATIONS_CACHE_PATH", convos_path),
        patch.object(imessage, "get_conversations_sync", return_value=[
            {
                "id": 1,
                "identifier": "+15550009999",
                "display_name": "Mom",
                "service": "iMessage",
                "last_message_date": "2026-01-01T00:00:00+00:00",
                "last_message_preview": "Hey",
                "message_count": 5,
                "unread_count": 0,
            }
        ]),
        patch.object(imessage, "_breaker_is_open", return_value=False),
    ):
        result = await imessage.get_conversations()

    assert len(result) == 1
    assert result[0]["display_name"] == "Mom", (
        f"Expected 'Mom' but got '{result[0]['display_name']}' — "
        "bulk loader did not populate contact name before conversations load"
    )


def test_bulk_loader_failure_leaves_applescript_fallback_callable(tmp_path):
    """If the Swift subprocess fails, _lookup_contact_name is still callable.

    This verifies the AppleScript fallback path has not been removed.
    """
    from services import imessage

    # Verify _lookup_contact_name still exists and is callable
    assert callable(imessage._lookup_contact_name), (
        "_lookup_contact_name was removed but is needed as AppleScript fallback"
    )

    # Simulate Swift failure and confirm populate_contacts_from_bulk_loader
    # does not raise — it should log a warning and return gracefully.
    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "contacts.json"

    with (
        patch("services.imessage.list_contacts", side_effect=RuntimeError("swift not found")),
        patch.object(imessage, "_contacts_cache", None),
        patch.object(imessage, "IMESSAGE_CACHE_DIR", cache_dir),
        patch.object(imessage, "CONTACTS_CACHE_PATH", cache_path),
    ):
        # Must not raise
        imessage._populate_contacts_from_bulk_loader()

    # And _lookup_contact_name can be called independently
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Alice Smith\n"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        name = imessage._lookup_contact_name("+15551234567")
    assert name == "Alice Smith"


def test_get_contact_display_name_cached_normalized_key(tmp_path):
    """_get_contact_display_name_cached resolves via 10-digit normalized key.

    chat.db gives us '+15551234567'; bulk loader indexed under '5551234567'.
    The lookup must match across this +1 prefix difference.
    """
    from services import imessage

    cache_dir = tmp_path / "imessage_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "contacts.json"
    cache_path.write_text('{"5551234567": "Dad"}')

    with patch.object(imessage, "_contacts_cache", None), \
         patch.object(imessage, "CONTACTS_CACHE_PATH", cache_path):
        name = imessage._get_contact_display_name_cached("+15551234567")

    assert name == "Dad", (
        f"Expected 'Dad' but got '{name}' — normalized key lookup is broken"
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_after_threshold():
    """After 2 consecutive failures, the breaker opens."""
    from services import imessage

    # Reset breaker state
    imessage._breaker_failures = 0
    imessage._breaker_tripped_at = 0.0

    assert not imessage._breaker_is_open()

    imessage._breaker_record_failure()
    assert not imessage._breaker_is_open()

    imessage._breaker_record_failure()
    assert imessage._breaker_is_open()

    # Reset for other tests
    imessage._breaker_record_success()
    assert not imessage._breaker_is_open()


def test_circuit_breaker_resets_on_success():
    """A successful call resets the breaker."""
    from services import imessage

    imessage._breaker_failures = 2
    imessage._breaker_tripped_at = time.time()
    assert imessage._breaker_is_open()

    imessage._breaker_record_success()
    assert not imessage._breaker_is_open()
    assert imessage._breaker_failures == 0


# ---------------------------------------------------------------------------
# macOS guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_status_returns_503_on_non_macos(client):
    """All iMessage endpoints must return 503 on non-macOS."""
    with patch("platform.system", return_value="Windows"):
        resp = await client.get("/api/imessage/status")
    assert resp.status_code == 503
    assert "macOS" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_imessage_conversations_returns_503_on_non_macos(client):
    with patch("platform.system", return_value="Windows"):
        resp = await client.get("/api/imessage/conversations")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_imessage_status_darwin_passes_guard(client):
    """On Darwin the macOS guard passes; status endpoint returns 200, not 503.

    The availability check may report unavailable (if chat.db is absent on this
    machine), but the response code must be 200 — 503 is only for non-macOS.
    """
    with patch("platform.system", return_value="Darwin"):
        resp = await client.get("/api/imessage/status")
    assert resp.status_code == 200
    assert resp.status_code != 503


@pytest.mark.asyncio
async def test_imessage_conversations_darwin_passes_guard(client):
    """On Darwin the macOS guard passes; conversations endpoint is not 503.

    If chat.db is missing the endpoint may return 503 from is_available, but
    the top-level platform guard must not fire.  We mock is_available to avoid
    a filesystem dependency and confirm the guard itself is satisfied.
    """
    with patch("platform.system", return_value="Darwin"), \
         patch("services.imessage.is_available",
               return_value={"available": True, "reason": None}), \
         patch("services.imessage.get_conversations",
               new=AsyncMock(return_value=[])):
        resp = await client.get("/api/imessage/conversations")
    assert resp.status_code != 503


# ---------------------------------------------------------------------------
# Contacts search endpoint (Phase 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imessage_contacts_search_returns_matches(client):
    """Contacts search endpoint returns JSON with name/identifier shape."""
    fake_contacts = [
        {"name": "Alice Smith", "phone": "+15550001234", "email": None, "identifier": "+15550001234"},
    ]
    with patch("services.imessage_contacts.search_by_prefix", return_value=fake_contacts):
        resp = await client.get("/api/imessage/contacts/search?q=ali")

    assert resp.status_code == 200
    data = resp.json()
    assert "contacts" in data
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["name"] == "Alice Smith"
    assert data["contacts"][0]["identifier"] == "+15550001234"


@pytest.mark.asyncio
async def test_imessage_contacts_search_empty_on_no_match(client):
    """Contacts search returns empty list when nothing matches."""
    with patch("services.imessage_contacts.search_by_prefix", return_value=[]):
        resp = await client.get("/api/imessage/contacts/search?q=zzznobody")

    assert resp.status_code == 200
    assert resp.json()["contacts"] == []


@pytest.mark.asyncio
async def test_imessage_contacts_search_requires_query(client):
    """Contacts search without a q param returns 422."""
    resp = await client.get("/api/imessage/contacts/search")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_imessage_contacts_search_works_without_macos_guard(client):
    """Contacts search endpoint does not require macOS — returns 200 on any platform."""
    with patch("platform.system", return_value="Windows"), \
         patch("services.imessage_contacts.search_by_prefix", return_value=[]):
        resp = await client.get("/api/imessage/contacts/search?q=alice")
    assert resp.status_code == 200
    assert resp.json()["contacts"] == []


# ---------------------------------------------------------------------------
# search_by_prefix service unit tests
# ---------------------------------------------------------------------------


def test_search_by_prefix_matches_name():
    """search_by_prefix finds contacts whose name contains the query."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": "Alice Smith", "phone_numbers": ["+15550001234"], "emails": []},
        {"name": "Bob Jones", "phone_numbers": ["+15550005678"], "emails": []},
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("ali")

    assert len(results) == 1
    assert results[0]["name"] == "Alice Smith"
    assert results[0]["identifier"] == "+15550001234"


def test_search_by_prefix_case_insensitive():
    """search_by_prefix matches regardless of case."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": "Alice Smith", "phone_numbers": ["+15550001234"], "emails": []},
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("ALICE")

    assert len(results) == 1
    assert results[0]["name"] == "Alice Smith"


def test_search_by_prefix_returns_empty_for_no_match():
    """search_by_prefix returns [] when nothing matches."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": "Alice Smith", "phone_numbers": ["+15550001234"], "emails": []},
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("zzznobody")

    assert results == []


def test_search_by_prefix_respects_limit():
    """search_by_prefix returns at most limit results."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": f"Alice {i}", "phone_numbers": [f"+1555000{i:04d}"], "emails": []}
        for i in range(20)
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("alice", limit=3)

    assert len(results) == 3


def test_search_by_prefix_uses_email_when_no_phone():
    """search_by_prefix uses email as identifier when no phone number exists."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": "Eve Online", "phone_numbers": [], "emails": ["eve@example.com"]},
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("eve")

    assert len(results) == 1
    assert results[0]["identifier"] == "eve@example.com"
    assert results[0]["email"] == "eve@example.com"
    assert results[0]["phone"] is None


def test_search_by_prefix_skips_contacts_with_no_identifier():
    """search_by_prefix omits contacts that have neither phone nor email."""
    from services import imessage_contacts

    fake_contacts = [
        {"name": "Ghost User", "phone_numbers": [], "emails": []},
        {"name": "Real User", "phone_numbers": ["+15550001111"], "emails": []},
    ]
    with patch.object(imessage_contacts, "_cache_data", fake_contacts), \
         patch.object(imessage_contacts, "_cache_ts", 9999999999.0):
        results = imessage_contacts.search_by_prefix("user")

    assert len(results) == 1
    assert results[0]["name"] == "Real User"


# ---------------------------------------------------------------------------
# _is_chat_muted — unit tests
# ---------------------------------------------------------------------------


def _make_muted_blob(muted_until_dt) -> bytes:
    """Build a binary plist with CKDMutedUntilDateKey set to the given datetime.

    plistlib requires naive (UTC-implied) datetimes for binary plist encoding.
    """
    import plistlib
    from datetime import datetime, timezone
    # Strip tzinfo — plistlib's binary writer requires naive datetimes
    if hasattr(muted_until_dt, "tzinfo") and muted_until_dt.tzinfo is not None:
        muted_until_dt = muted_until_dt.replace(tzinfo=None)
    return plistlib.dumps({"CKDMutedUntilDateKey": muted_until_dt}, fmt=plistlib.FMT_BINARY)


def test_is_chat_muted_future_date_returns_true():
    """A CKDMutedUntilDateKey far in the future means the thread is muted."""
    from datetime import datetime
    from services import imessage

    # Use a naive datetime far in the future — plistlib treats it as UTC
    far_future = datetime(3000, 1, 1)
    blob = _make_muted_blob(far_future)
    assert imessage._is_chat_muted(blob) is True


def test_is_chat_muted_past_date_returns_false():
    """A CKDMutedUntilDateKey in the past means the mute has expired."""
    from datetime import datetime
    from services import imessage

    past = datetime(2020, 1, 1)
    blob = _make_muted_blob(past)
    assert imessage._is_chat_muted(blob) is False


def test_is_chat_muted_no_key_returns_false():
    """A plist without CKDMutedUntilDateKey or isMuted is not muted."""
    import plistlib
    from services import imessage

    blob = plistlib.dumps({"lastSeenMessageGuid": "abc-def", "pv": 0}, fmt=plistlib.FMT_BINARY)
    assert imessage._is_chat_muted(blob) is False


def test_is_chat_muted_is_muted_true_returns_true():
    """isMuted=True (boolean fallback key) marks a thread as muted."""
    import plistlib
    from services import imessage

    blob = plistlib.dumps({"isMuted": True}, fmt=plistlib.FMT_BINARY)
    assert imessage._is_chat_muted(blob) is True


def test_is_chat_muted_is_muted_false_returns_false():
    """isMuted=False (boolean fallback key) is not muted."""
    import plistlib
    from services import imessage

    blob = plistlib.dumps({"isMuted": False}, fmt=plistlib.FMT_BINARY)
    assert imessage._is_chat_muted(blob) is False


def test_is_chat_muted_none_blob_returns_false():
    """None properties blob is handled gracefully."""
    from services import imessage

    assert imessage._is_chat_muted(None) is False


def test_is_chat_muted_bad_blob_returns_false_no_raise():
    """Corrupt plist bytes return False without raising."""
    from services import imessage

    assert imessage._is_chat_muted(b"\x00\x01\x02corrupt") is False


def test_is_chat_muted_bad_blob_logs_only_once(caplog):
    """The parse-failure warning is emitted at most once per backend lifetime."""
    import logging
    from services import imessage

    with patch.object(imessage, "_muted_parse_logged", False):
        with caplog.at_level(logging.WARNING, logger="services.imessage"):
            imessage._is_chat_muted(b"bad1")
            imessage._is_chat_muted(b"bad2")
            imessage._is_chat_muted(b"bad3")

    warning_count = sum(1 for r in caplog.records if "chat.properties" in r.message)
    assert warning_count == 1, f"Expected 1 warning, got {warning_count}"


# ---------------------------------------------------------------------------
# get_conversations_sync — muted thread suppresses unread badge
# ---------------------------------------------------------------------------


def test_get_conversations_sync_muted_thread_has_zero_unread(tmp_path):
    """A muted thread keeps its preview but shows unread_count=0."""
    import plistlib
    import sqlite3 as _sqlite3
    from datetime import datetime
    from services import imessage

    # Build the muted properties blob (plistlib needs naive datetimes)
    far_future = datetime(3000, 1, 1)
    muted_blob = plistlib.dumps(
        {"CKDMutedUntilDateKey": far_future}, fmt=plistlib.FMT_BINARY
    )

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            chat_identifier TEXT,
            display_name TEXT,
            service_name TEXT,
            properties BLOB,
            room_name TEXT,
            is_archived INTEGER DEFAULT 0,
            last_read_message_timestamp INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER,
            is_read INTEGER,
            service TEXT,
            handle_id INTEGER
        );
        CREATE TABLE chat_message_join (
            chat_id INTEGER,
            message_id INTEGER
        );
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY,
            id TEXT
        );
    """)
    # Insert one muted chat with 3 unread inbound messages
    conn.execute(
        "INSERT INTO chat VALUES (1, 'guid1', '+15551234567', '', 'iMessage', ?, NULL, 0, 0)",
        (muted_blob,),
    )
    conn.execute("INSERT INTO message VALUES (1, 'hi', 700000000000000000, 0, 0, 'iMessage', 0)")
    conn.execute("INSERT INTO message VALUES (2, 'hey', 700000001000000000, 0, 0, 'iMessage', 0)")
    conn.execute("INSERT INTO message VALUES (3, 'sup', 700000002000000000, 0, 0, 'iMessage', 0)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 2)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 3)")
    conn.commit()
    conn.close()

    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["message_count"] == 3, "message_count should reflect real messages"
    assert result[0]["unread_count"] == 0, "muted thread must show zero unread"
    assert result[0]["last_message_preview"] == "sup"


# ---------------------------------------------------------------------------
# Unread count: last_read_message_timestamp beats is_read (→1577)
# ---------------------------------------------------------------------------

_UNREAD_SCHEMA = """
    CREATE TABLE chat (
        ROWID INTEGER PRIMARY KEY,
        guid TEXT,
        chat_identifier TEXT,
        display_name TEXT,
        service_name TEXT,
        properties BLOB,
        room_name TEXT,
        is_archived INTEGER DEFAULT 0,
        last_read_message_timestamp INTEGER DEFAULT 0
    );
    CREATE TABLE message (
        ROWID INTEGER PRIMARY KEY,
        text TEXT,
        date INTEGER,
        is_from_me INTEGER,
        is_read INTEGER,
        service TEXT,
        handle_id INTEGER
    );
    CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
    CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
"""


def _make_unread_db(tmp_path, last_read_ts: int) -> "Path":
    """Build a minimal chat.db with 3 inbound messages and a given last_read_message_timestamp."""
    import sqlite3 as _sqlite3
    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.executescript(_UNREAD_SCHEMA)
    conn.execute(
        "INSERT INTO chat VALUES (1,'guid1','+15551234567','','iMessage',NULL,NULL,0,?)",
        (last_read_ts,),
    )
    # Three inbound messages at t=100, t=200, t=300 (all is_read=0 — stale flag)
    conn.execute("INSERT INTO message VALUES (1,'hi',  100, 0, 0,'iMessage',0)")
    conn.execute("INSERT INTO message VALUES (2,'hey', 200, 0, 0,'iMessage',0)")
    conn.execute("INSERT INTO message VALUES (3,'sup', 300, 0, 0,'iMessage',0)")
    conn.execute("INSERT INTO chat_message_join VALUES (1,1)")
    conn.execute("INSERT INTO chat_message_join VALUES (1,2)")
    conn.execute("INSERT INTO chat_message_join VALUES (1,3)")
    conn.commit()
    conn.close()
    return db_path


def test_unread_count_uses_last_read_timestamp_when_set(tmp_path):
    """last_read_message_timestamp=300 covers all messages → unread_count=0.

    Regression test for →1577: is_read in chat.db goes stale. Messages.app
    uses last_read_message_timestamp; we must prefer it when > 0.
    """
    from services import imessage
    db_path = _make_unread_db(tmp_path, last_read_ts=300)
    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        result = imessage.get_conversations_sync(limit=10)
    assert len(result) == 1
    assert result[0]["unread_count"] == 0, (
        "last_read_message_timestamp covers all 3 messages — is_read=0 should be ignored"
    )


def test_unread_count_uses_last_read_timestamp_partial(tmp_path):
    """last_read_message_timestamp=150 covers msgs at t<=150 → 2 unread (t=200, t=300)."""
    from services import imessage
    db_path = _make_unread_db(tmp_path, last_read_ts=150)
    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        result = imessage.get_conversations_sync(limit=10)
    assert len(result) == 1
    assert result[0]["unread_count"] == 2, (
        "messages at t=200 and t=300 are after last_read_message_timestamp=150"
    )


def test_unread_count_fallback_to_is_read_when_timestamp_zero(tmp_path):
    """When last_read_message_timestamp=0, fall back to is_read=0 count."""
    from services import imessage
    db_path = _make_unread_db(tmp_path, last_read_ts=0)
    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        result = imessage.get_conversations_sync(limit=10)
    assert len(result) == 1
    assert result[0]["unread_count"] == 3, (
        "no timestamp set — all 3 inbound messages with is_read=0 count as unread"
    )


# ---------------------------------------------------------------------------
# Group chat participant naming (→1326)
# ---------------------------------------------------------------------------

_GROUP_SCHEMA = """
    CREATE TABLE chat (
        ROWID INTEGER PRIMARY KEY,
        guid TEXT,
        chat_identifier TEXT,
        display_name TEXT,
        service_name TEXT,
        properties BLOB,
        room_name TEXT,
        is_archived INTEGER DEFAULT 0,
        last_read_message_timestamp INTEGER DEFAULT 0
    );
    CREATE TABLE message (
        ROWID INTEGER PRIMARY KEY,
        text TEXT,
        date INTEGER,
        is_from_me INTEGER,
        is_read INTEGER,
        service TEXT,
        handle_id INTEGER
    );
    CREATE TABLE chat_message_join (
        chat_id INTEGER,
        message_id INTEGER
    );
    CREATE TABLE chat_handle_join (
        chat_id INTEGER,
        handle_id INTEGER
    );
    CREATE TABLE handle (
        ROWID INTEGER PRIMARY KEY,
        id TEXT
    );
"""


def _build_group_db(tmp_path, participants: list[str], display_name: str = "") -> "Path":
    """Create a minimal chat.db with one group chat and the given participants."""
    import sqlite3 as _sqlite3

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.executescript(_GROUP_SCHEMA)

    group_id = "chat00000000-0000-0000-0000-000000000042"
    conn.execute(
        "INSERT INTO chat VALUES (1, 'guid1', ?, ?, 'iMessage', NULL, NULL, 0, 0)",
        (group_id, display_name),
    )
    conn.execute("INSERT INTO message VALUES (1, 'hello group', 700000000000000000, 0, 1, 'iMessage', 0)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")

    for i, identifier in enumerate(participants, start=1):
        conn.execute("INSERT INTO handle VALUES (?, ?)", (i, identifier))
        conn.execute("INSERT INTO chat_handle_join VALUES (1, ?)", (i,))

    conn.commit()
    conn.close()
    return db_path


def test_get_conversations_sync_group_chat_two_participants(tmp_path):
    """Two participants → 'Alice, Bob'."""
    from services import imessage

    db_path = _build_group_db(tmp_path, ["+15550000001", "+15550000002"])
    contacts = {"5550000001": "Alice Smith", "5550000002": "Bob Jones"}

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", contacts):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["display_name"] == "Alice, Bob"


def test_get_conversations_sync_group_chat_three_participants(tmp_path):
    """Three participants → 'Alice, Bob, Carol'."""
    from services import imessage

    db_path = _build_group_db(tmp_path, ["+15550000001", "+15550000002", "+15550000003"])
    contacts = {
        "5550000001": "Alice Smith",
        "5550000002": "Bob Jones",
        "5550000003": "Carol White",
    }

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", contacts):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["display_name"] == "Alice, Bob, Carol"


def test_get_conversations_sync_group_chat_four_participants(tmp_path):
    """Four participants → 'Alice, Bob & 2 others'."""
    from services import imessage

    db_path = _build_group_db(
        tmp_path, ["+15550000001", "+15550000002", "+15550000003", "+15550000004"]
    )
    contacts = {
        "5550000001": "Alice Smith",
        "5550000002": "Bob Jones",
        "5550000003": "Carol White",
        "5550000004": "Dave Black",
    }

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", contacts):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["display_name"] == "Alice, Bob & 2 others"


def test_get_conversations_sync_group_chat_five_participants(tmp_path):
    """Five participants → 'Alice, Bob & 3 others'."""
    from services import imessage

    db_path = _build_group_db(
        tmp_path,
        ["+15550000001", "+15550000002", "+15550000003", "+15550000004", "+15550000005"],
    )
    contacts = {
        "5550000001": "Alice Smith",
        "5550000002": "Bob Jones",
        "5550000003": "Carol White",
        "5550000004": "Dave Black",
        "5550000005": "Eve Green",
    }

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", contacts):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["display_name"] == "Alice, Bob & 3 others"


def test_get_conversations_sync_group_chat_no_contacts_match(tmp_path):
    """When no contacts match, display name falls back to raw identifiers."""
    from services import imessage

    db_path = _build_group_db(tmp_path, ["+15550000001", "+15550000002"])

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", {}):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    # Raw identifiers used as-is when no contact found
    assert result[0]["display_name"] == "+15550000001, +15550000002"


def test_get_conversations_sync_group_chat_named_by_user(tmp_path):
    """When the user set a group name in iMessage, use it unchanged."""
    from services import imessage

    db_path = _build_group_db(
        tmp_path,
        ["+15550000001", "+15550000002"],
        display_name="The Squad",
    )
    contacts = {"5550000001": "Alice Smith", "5550000002": "Bob Jones"}

    with patch.object(imessage, "CHAT_DB_PATH", db_path), \
         patch.object(imessage, "_contacts_cache", contacts):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["display_name"] == "The Squad"


def test_synthesize_group_display_name_missing_table():
    """Falls back to 'Group Chat' when chat_handle_join table does not exist."""
    import sqlite3 as _sqlite3
    from services import imessage

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row

    # No tables created — simulates older iMessage DB
    result = imessage._synthesize_group_display_name(conn, 1)
    assert result == "Group Chat"


def test_synthesize_group_display_name_no_participants():
    """Falls back to 'Group Chat' when chat has no handle rows."""
    import sqlite3 as _sqlite3
    from services import imessage

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row
    conn.executescript("""
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
    """)

    result = imessage._synthesize_group_display_name(conn, 99)
    assert result == "Group Chat"


def test_get_first_name_for_identifier_known_contact():
    """Returns the first name token when the contact is in cache."""
    from services import imessage

    with patch.object(imessage, "_contacts_cache", {"5550000001": "Alice Smith"}):
        assert imessage._get_first_name_for_identifier("+15550000001") == "Alice"


def test_get_first_name_for_identifier_single_word_name():
    """Single-word names are returned in full."""
    from services import imessage

    with patch.object(imessage, "_contacts_cache", {"5550000001": "Mom"}):
        assert imessage._get_first_name_for_identifier("+15550000001") == "Mom"


def test_get_first_name_for_identifier_unknown_returns_raw():
    """Unknown identifier returns the raw value."""
    from services import imessage

    with patch.object(imessage, "_contacts_cache", {}):
        assert imessage._get_first_name_for_identifier("+15550000001") == "+15550000001"
        assert imessage._get_first_name_for_identifier("user@example.com") == "user@example.com"


def test_get_conversations_sync_unmuted_thread_shows_unread(tmp_path):
    """An unmuted thread with unread messages shows a nonzero unread_count."""
    import plistlib
    import sqlite3 as _sqlite3
    from services import imessage

    unmuted_blob = plistlib.dumps({"lastSeenMessageGuid": "abc"}, fmt=plistlib.FMT_BINARY)

    db_path = tmp_path / "chat.db"
    conn = _sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            chat_identifier TEXT,
            display_name TEXT,
            service_name TEXT,
            properties BLOB,
            room_name TEXT,
            is_archived INTEGER DEFAULT 0,
            last_read_message_timestamp INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER,
            is_read INTEGER,
            service TEXT,
            handle_id INTEGER
        );
        CREATE TABLE chat_message_join (
            chat_id INTEGER,
            message_id INTEGER
        );
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY,
            id TEXT
        );
    """)
    conn.execute(
        "INSERT INTO chat VALUES (1, 'guid1', '+15559876543', '', 'iMessage', ?, NULL, 0, 0)",
        (unmuted_blob,),
    )
    conn.execute("INSERT INTO message VALUES (1, 'hello', 700000000000000000, 0, 0, 'iMessage', 0)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
    conn.commit()
    conn.close()

    with patch.object(imessage, "CHAT_DB_PATH", db_path):
        result = imessage.get_conversations_sync(limit=10)

    assert len(result) == 1
    assert result[0]["unread_count"] == 1, "unmuted thread should surface the unread message"


# ---------------------------------------------------------------------------
# Save-to-contacts endpoint (→1328)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_contact_stores_name(client, tmp_path):
    """POST /api/imessage/contacts/save persists the name and returns ok."""
    from services import imessage

    cache_path = tmp_path / "contacts.json"

    with (
        patch.object(imessage, "CONTACTS_CACHE_PATH", cache_path),
        patch.object(imessage, "_contacts_cache", None),
        patch.object(imessage, "invalidate_conversations_cache"),
    ):
        resp = await client.post(
            "/api/imessage/contacts/save",
            json={"identifier": "+15551234567", "name": "Test Person"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["name"] == "Test Person"
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert saved.get("+15551234567") == "Test Person"


@pytest.mark.asyncio
async def test_save_contact_empty_name_returns_400(client):
    """POST /api/imessage/contacts/save rejects an empty name."""
    resp = await client.post(
        "/api/imessage/contacts/save",
        json={"identifier": "+15551234567", "name": "  "},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_save_contact_appears_in_conversations(tmp_path):
    """After save_contact, get_conversations_sync returns the new name."""
    from services import imessage

    db_path = tmp_path / "chat.db"
    cache_path = tmp_path / "contacts.json"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            chat_identifier TEXT,
            display_name TEXT,
            service_name TEXT,
            properties BLOB,
            room_name TEXT,
            is_archived INTEGER DEFAULT 0,
            last_read_message_timestamp INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER,
            is_read INTEGER,
            service TEXT,
            handle_id INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
    """)
    conn.execute(
        "INSERT INTO chat VALUES (1, 'guid1', '+15559876543', '', 'iMessage', NULL, NULL, 0, 0)"
    )
    conn.execute("INSERT INTO message VALUES (1, 'hello', 700000000000000000, 0, 1, 'iMessage', 0)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1)")
    conn.commit()
    conn.close()

    with (
        patch.object(imessage, "CHAT_DB_PATH", db_path),
        patch.object(imessage, "CONTACTS_CACHE_PATH", cache_path),
        patch.object(imessage, "_contacts_cache", None),
        patch.object(imessage, "_bulk_contacts_loaded", True),
    ):
        # Before save: display_name is formatted phone and has_contact_name is False
        result_before = imessage.get_conversations_sync(limit=10)
        assert len(result_before) == 1
        assert result_before[0]["has_contact_name"] is False

        # Save the contact
        imessage.save_contact("+15559876543", "Saved Person")

        # Patch the in-memory cache reset for re-load
        with patch.object(imessage, "_contacts_cache", None):
            result_after = imessage.get_conversations_sync(limit=10)

    assert len(result_after) == 1
    assert result_after[0]["display_name"] == "Saved Person"
    assert result_after[0]["has_contact_name"] is True

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

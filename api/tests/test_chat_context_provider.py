"""Tests for build_memory_context including the cross-session digest."""

import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_build_memory_context_includes_digest_section():
    """When digest has sessions, build_memory_context includes a digest block."""
    from routers.chat import build_memory_context

    digest_data = {
        "sessions": [
            {
                "session_id": "agent-alpha",
                "label": "Build feature X",
                "activity_count": 10,
                "files_touched": ["api/routers/chat.py"],
                "recent_activity": "Writing files: chat.py",
            }
        ],
        "closed_tasks_today": [],
        "generated_at": "2026-07-06T12:00:00Z",
    }

    with patch("routers.sessions.get_session_digest", new_callable=AsyncMock, return_value=digest_data):
        result = await build_memory_context(current_tab_id="tab-1")

    # Should return a non-empty list with digest content
    assert isinstance(result, list)
    # At least one message should mention today's session activity
    combined = " ".join(m.get("content", "") for m in result)
    assert "agent-alpha" in combined or "Build feature X" in combined


@pytest.mark.asyncio
async def test_build_memory_context_no_sessions_no_digest():
    """When digest is empty, no digest block is injected (no noise)."""
    from routers.chat import build_memory_context

    digest_data = {
        "sessions": [],
        "closed_tasks_today": [],
        "generated_at": "2026-07-06T12:00:00Z",
    }

    with patch("routers.sessions.get_session_digest", new_callable=AsyncMock, return_value=digest_data), \
         patch("routers.chat.chat_history_store") as mock_store, \
         patch("routers.chat.settings_store") as mock_settings:
        mock_settings.get.return_value = True
        mock_store.get_prior_messages.return_value = []
        result = await build_memory_context(current_tab_id="tab-1")

    # No sessions to report, no digest noise
    combined = " ".join(m.get("content", "") for m in result)
    assert "Today across sessions" not in combined


@pytest.mark.asyncio
async def test_build_memory_context_includes_closed_tasks():
    """When closed tasks exist today, they appear in digest section."""
    from routers.chat import build_memory_context

    digest_data = {
        "sessions": [],
        "closed_tasks_today": [
            {"id": "→1234", "title": "Fix the login bug", "closed_at": "2026-07-06T10:00:00Z"}
        ],
        "generated_at": "2026-07-06T12:00:00Z",
    }

    with patch("routers.sessions.get_session_digest", new_callable=AsyncMock, return_value=digest_data):
        result = await build_memory_context(current_tab_id="tab-2")

    combined = " ".join(m.get("content", "") for m in result)
    assert "Fix the login bug" in combined or "→1234" in combined


@pytest.mark.asyncio
async def test_build_memory_context_digest_error_is_silent():
    """If the digest call raises, build_memory_context still returns without crashing."""
    from routers.chat import build_memory_context

    with patch("routers.sessions.get_session_digest", new_callable=AsyncMock, side_effect=Exception("boom")), \
         patch("routers.chat.chat_history_store") as mock_store, \
         patch("routers.chat.settings_store") as mock_settings:
        mock_settings.get.return_value = False  # memory disabled
        mock_store.get_prior_messages.return_value = []
        # Should not raise
        result = await build_memory_context(current_tab_id="tab-3")

    assert isinstance(result, list)

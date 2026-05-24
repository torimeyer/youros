"""Tests for services.slack_reply."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_draft_reply_returns_gemini_text():
    """draft_reply fetches the thread and returns Gemini's response."""
    thread_data = {
        "ok": True,
        "messages": [
            {"user": "U111", "text": "Hey, can you take a look at this?", "ts": "1700000000.000100"},
            {"user": "U222", "text": "Sure, what do you need?", "ts": "1700000001.000100"},
        ],
    }

    with (
        patch("services.slack_reply._slack._slack_get", new=AsyncMock(return_value=thread_data)),
        patch("services.slack_reply.gemini_drafter.draft", new=AsyncMock(return_value="Sounds good, will do.")),
    ):
        from services import slack_reply
        result = await slack_reply.draft_reply("C123", "1700000000.000100")

    assert result == "Sounds good, will do."


@pytest.mark.asyncio
async def test_draft_reply_falls_back_to_fetch_messages_on_error():
    """If conversations.replies fails, fall back to fetch_messages."""
    with (
        patch("services.slack_reply._slack._slack_get", new=AsyncMock(side_effect=RuntimeError("API error"))),
        patch(
            "services.slack_reply._slack.fetch_messages",
            new=AsyncMock(return_value=[
                {"ts": "1700000000.000100", "user": "U111", "text": "Hello"},
            ]),
        ),
        patch("services.slack_reply.gemini_drafter.draft", new=AsyncMock(return_value="Hi there.")),
    ):
        from services import slack_reply
        result = await slack_reply.draft_reply("C123", "1700000000.000100")

    assert result == "Hi there."


@pytest.mark.asyncio
async def test_draft_reply_raises_when_gemini_not_connected():
    """RuntimeError from Gemini drafter propagates cleanly."""
    thread_data = {"ok": True, "messages": [{"user": "U1", "text": "hi", "ts": "1700000000.1"}]}

    with (
        patch("services.slack_reply._slack._slack_get", new=AsyncMock(return_value=thread_data)),
        patch(
            "services.slack_reply.gemini_drafter.draft",
            new=AsyncMock(side_effect=RuntimeError("Gemini Enterprise not connected")),
        ),
    ):
        from services import slack_reply

        with pytest.raises(RuntimeError, match="Gemini Enterprise not connected"):
            await slack_reply.draft_reply("C123", "1700000000.1")


@pytest.mark.asyncio
async def test_send_reply_posts_as_thread():
    """send_reply calls chat.postMessage with thread_ts."""
    post_response = {"ok": True, "ts": "1700000002.000100", "channel": "C123"}

    with patch("services.slack_reply._slack._slack_post", new=AsyncMock(return_value=post_response)) as mock_post:
        from services import slack_reply
        result = await slack_reply.send_reply("C123", "1700000000.000100", "On it!")

    mock_post.assert_awaited_once_with(
        "chat.postMessage",
        {"channel": "C123", "text": "On it!", "thread_ts": "1700000000.000100"},
    )
    assert result["ok"] is True
    assert result["ts"] == "1700000002.000100"
    assert "permalink" in result

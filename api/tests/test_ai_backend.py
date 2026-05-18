"""Unit tests for api/services/ai_backend.py — the shared AI backend helper.

These tests verify that resolve_ai_backend() and get_ai_client() honour the
chat_backend_preference setting so every non-chat router uses the right
backend.

Run: api/.venv/bin/python3.11 -m pytest api/tests/test_ai_backend.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# resolve_ai_backend()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_always_subscription_returns_claude_code():
    """setting=claude_code → kind=="claude_code", no api_key needed."""
    with patch("services.settings_store.settings_store.get", return_value="claude_code"):
        # Import fresh inside patch scope
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        result = await mod.resolve_ai_backend()

    assert result["kind"] == "claude_code"
    assert result["api_key"] == ""


@pytest.mark.asyncio
async def test_always_api_key_returns_anthropic_api():
    """setting=anthropic_api → kind=="anthropic_api" with resolved key."""
    with (
        patch("services.settings_store.settings_store.get", return_value="anthropic_api"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="sk-test"),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        result = await mod.resolve_ai_backend()

    assert result["kind"] == "anthropic_api"
    assert result["api_key"] == "sk-test"


@pytest.mark.asyncio
async def test_auto_uses_api_key_when_present():
    """auto mode → anthropic_api when an API key is available."""
    with (
        patch("services.settings_store.settings_store.get", return_value="auto"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="sk-test"),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        result = await mod.resolve_ai_backend()

    assert result["kind"] == "anthropic_api"
    assert result["api_key"] == "sk-test"


@pytest.mark.asyncio
async def test_auto_falls_back_to_claude_code_when_no_key():
    """auto mode → claude_code when no API key but CLI is available."""
    with (
        patch("services.settings_store.settings_store.get", return_value="auto"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        result = await mod.resolve_ai_backend()

    assert result["kind"] == "claude_code"


@pytest.mark.asyncio
async def test_auto_returns_anthropic_api_when_both_missing():
    """auto with no key and no CLI → anthropic_api with empty key (callers handle it)."""
    with (
        patch("services.settings_store.settings_store.get", return_value="auto"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=False),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        result = await mod.resolve_ai_backend()

    assert result["kind"] == "anthropic_api"
    assert result["api_key"] == ""


# ---------------------------------------------------------------------------
# get_ai_client()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ai_client_returns_anthropic_for_api_key():
    """get_ai_client() returns an AsyncAnthropic instance for api_key backend."""
    import anthropic

    with (
        patch("services.settings_store.settings_store.get", return_value="anthropic_api"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="sk-test"),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        client = await mod.get_ai_client()

    assert isinstance(client, anthropic.AsyncAnthropic)


@pytest.mark.asyncio
async def test_get_ai_client_returns_cli_client_for_subscription():
    """get_ai_client() returns ClaudeCliClient for subscription backend."""
    with patch("services.settings_store.settings_store.get", return_value="claude_code"):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        client = await mod.get_ai_client()

    assert isinstance(client, mod.ClaudeCliClient)


@pytest.mark.asyncio
async def test_get_ai_client_returns_none_when_both_missing():
    """get_ai_client() returns None when API key is empty and CLI unavailable."""
    with (
        patch("services.settings_store.settings_store.get", return_value="auto"),
        patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=False),
        ),
    ):
        import importlib
        import services.ai_backend as mod
        importlib.reload(mod)
        client = await mod.get_ai_client()

    assert client is None


# ---------------------------------------------------------------------------
# ClaudeCliClient duck-typing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_client_messages_create_returns_fake_response():
    """ClaudeCliClient.messages.create() wraps CLI output in a response object."""
    import importlib
    import services.ai_backend as mod
    importlib.reload(mod)

    with patch(
        "services.claude_code_provider.complete",
        new=AsyncMock(return_value="Generated text"),
    ):
        cli_client = mod.ClaudeCliClient()
        response = await cli_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert response.content[0].text == "Generated text"
    assert response.content[0].type == "text"


@pytest.mark.asyncio
async def test_cli_client_handles_string_system_prompt():
    """ClaudeCliClient passes string system prompts through to complete()."""
    import importlib
    import services.ai_backend as mod
    importlib.reload(mod)

    mock_complete = AsyncMock(return_value="reply")
    with patch("services.claude_code_provider.complete", new=mock_complete):
        cli_client = mod.ClaudeCliClient()
        await cli_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=50,
            messages=[{"role": "user", "content": "hi"}],
            system="You are helpful.",
        )

    mock_complete.assert_awaited_once()
    call_kwargs = mock_complete.call_args.kwargs
    assert call_kwargs["system"] == "You are helpful."


@pytest.mark.asyncio
async def test_cli_client_handles_list_system_prompt():
    """ClaudeCliClient collapses list-style system prompts (cache-control format)."""
    import importlib
    import services.ai_backend as mod
    importlib.reload(mod)

    mock_complete = AsyncMock(return_value="reply")
    with patch("services.claude_code_provider.complete", new=mock_complete):
        cli_client = mod.ClaudeCliClient()
        await cli_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=50,
            messages=[{"role": "user", "content": "hi"}],
            system=[{"type": "text", "text": "System text", "cache_control": {"type": "ephemeral"}}],
        )

    call_kwargs = mock_complete.call_args.kwargs
    assert call_kwargs["system"] == "System text"

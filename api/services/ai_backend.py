"""Shared AI backend selection for all non-chat AI calls in myOS.

The ``chat_backend_preference`` setting governs EVERY AI call, not just chat.
This module centralises the routing decision so the seven non-chat routers
(specs, adventures, tasks, transcripts, onboarding, narrative, beautify) do
not each re-implement the logic.

Resolution order (matches _resolve_chat_backend in chat_providers.py):
  "claude_code"    → subscription path (Claude CLI), ignoring API key
  "anthropic_api"  → API-key path, regardless of CLI availability
  "auto" (default) → API key when present, CLI when not, None when neither

Usage::

    from services.ai_backend import get_ai_client

    client = await get_ai_client()
    if client is None:
        return fallback_value  # neither API key nor CLI available

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
        system="Optional system prompt",
    )
    text = response.content[0].text
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def resolve_ai_backend() -> dict:
    """Return ``{"kind": str, "api_key": str}`` for the configured backend.

    ``kind`` is one of ``"claude_code"`` or ``"anthropic_api"``.
    ``api_key`` is the resolved Anthropic key (empty string when
    ``kind == "claude_code"``).
    """
    from services.settings_store import settings_store
    from services.chat_providers import _resolve_api_key
    from services import claude_code_provider

    pref = str(settings_store.get("chat_backend_preference", "auto") or "auto").lower()

    if pref == "claude_code":
        return {"kind": "claude_code", "api_key": ""}

    if pref == "anthropic_api":
        api_key = await _resolve_api_key("anthropic_api_key")
        return {"kind": "anthropic_api", "api_key": api_key}

    # auto: API key first, then CLI, then signal "nothing available"
    api_key = await _resolve_api_key("anthropic_api_key")
    if api_key:
        return {"kind": "anthropic_api", "api_key": api_key}
    if await claude_code_provider.is_claude_code_available():
        return {"kind": "claude_code", "api_key": ""}
    # Neither available — return anthropic_api with empty key so callers
    # detect "no backend" via get_ai_client() returning None.
    return {"kind": "anthropic_api", "api_key": ""}


# ---------------------------------------------------------------------------
# Duck-typed Claude CLI client
# ---------------------------------------------------------------------------


class _FakeBlock:
    """Minimal text-block object compatible with Anthropic API blocks."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    """Minimal anthropic.Message-compatible object for CLI-based responses."""

    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    """Async ``.messages.create()`` shim that routes through the Claude CLI."""

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict],
        system: Any = None,
        **kwargs: Any,
    ) -> _FakeResponse:
        from services import claude_code_provider

        system_str: Optional[str] = None
        if isinstance(system, str):
            system_str = system
        elif isinstance(system, list):
            # Handle cache-control system prompts (list of dicts with type/text)
            parts = [
                b.get("text", "")
                for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            system_str = "\n".join(parts) if parts else None

        text = await claude_code_provider.complete(
            messages=messages,
            system=system_str,
            model=model,
            max_tokens=max_tokens,
        )
        return _FakeResponse(text or "")


class ClaudeCliClient:
    """Anthropic-client-shaped object routing through the local Claude CLI.

    Drop-in replacement for ``anthropic.AsyncAnthropic`` for non-streaming
    message calls. Only ``.messages.create()`` is implemented — that covers
    every non-chat router that was previously calling the Anthropic API
    directly with an API key.
    """

    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


async def get_ai_client() -> Optional[Any]:
    """Return a client for AI calls, or None when no backend is reachable.

    Returns either an ``anthropic.AsyncAnthropic`` instance (for API-key
    mode) or a ``ClaudeCliClient`` (for subscription mode).  Returns
    ``None`` when neither backend is configured/available — callers should
    return their fallback value in that case.

    The returned object always exposes ``.messages.create(**kwargs)`` with
    the same signature and a compatible response shape::

        response.content        # list of blocks
        response.content[0].text  # first block text
        block.type              # "text"
        block.text              # str
    """
    backend = await resolve_ai_backend()

    if backend["kind"] == "claude_code":
        return ClaudeCliClient()

    if backend["api_key"]:
        import anthropic
        return anthropic.AsyncAnthropic(api_key=backend["api_key"])

    return None

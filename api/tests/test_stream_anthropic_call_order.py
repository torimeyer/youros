"""Tests for →2640: stream_anthropic sends backend_active before the slow key lookup.

Verifies that _send_backend_active fires immediately after _resolve_chat_backend,
before _resolve_api_key returns, so the frontend "thinking" signal is not delayed
behind the keychain call on cold reload.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.chat_providers import ChatService


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def messages_of_type(self, t: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == t]


@pytest.mark.asyncio
class TestStreamAnthropicCallOrder:
    """_send_backend_active must fire before _resolve_api_key returns."""

    async def _run_stream(
        self,
        *,
        initial_backend: str = "claude_code",
        api_key: str = "test-key",
        force_api: bool = False,
        has_images: bool = False,
        messages=None,
    ) -> list[str]:
        """Run stream_anthropic with mocked internals; return the call-order log."""
        if messages is None:
            messages = [{"role": "user", "content": "hello"}]

        log: list[str] = []

        async def _fake_resolve_backend():
            log.append("resolve_backend")
            return initial_backend

        async def _fake_resolve_api_key(_name):
            log.append("resolve_api_key")
            return api_key

        async def _fake_send_backend_active(ws, backend, tab_id=""):
            log.append(f"backend_active:{backend}")

        ws = FakeWebSocket()
        service = ChatService()

        with (
            patch("services.chat_providers._resolve_chat_backend", new=_fake_resolve_backend),
            patch("services.chat_providers._resolve_api_key", new=_fake_resolve_api_key),
            patch("services.chat_providers._send_backend_active", new=_fake_send_backend_active),
            patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
            patch("services.chat_providers._messages_contain_images", return_value=has_images),
            patch("services.chat_providers._standing_instructions_block", return_value=None),
            patch("services.chat_providers.claude_code_provider") as mock_cc,
            patch("services.chat_providers._get_anthropic_client", return_value=MagicMock()),
            patch("services.chat_providers.settings_store") as mock_ss,
        ):
            mock_cc.stream_chat = AsyncMock(return_value="")
            mock_ss.get.side_effect = lambda k, d=None: [] if k == "mcp_servers" else d
            try:
                await service.stream_anthropic(messages, ws, force_api=force_api)
            except Exception:
                pass  # ordering is what matters, not successful completion

        return log

    async def test_backend_active_before_api_key_claude_code_backend(self):
        """backend_active fires before resolve_api_key when backend is claude_code."""
        log = await self._run_stream(initial_backend="claude_code")

        assert "resolve_api_key" in log
        ba_idx = next(i for i, e in enumerate(log) if e.startswith("backend_active:"))
        ak_idx = log.index("resolve_api_key")
        assert ba_idx < ak_idx, (
            f"backend_active must precede resolve_api_key; got: {log}"
        )

    async def test_backend_active_before_api_key_api_backend(self):
        """backend_active fires before resolve_api_key when backend is anthropic_api."""
        log = await self._run_stream(initial_backend="anthropic_api")

        assert "resolve_api_key" in log
        ba_idx = next(i for i, e in enumerate(log) if e.startswith("backend_active:"))
        ak_idx = log.index("resolve_api_key")
        assert ba_idx < ak_idx, (
            f"backend_active must precede resolve_api_key; got: {log}"
        )

    async def test_force_api_flip_sends_second_backend_active(self):
        """force_api flipping claude_code → anthropic_api must produce a second backend_active."""
        log = await self._run_stream(initial_backend="claude_code", force_api=True)

        ba_events = [e for e in log if e.startswith("backend_active:")]
        assert ba_events == ["backend_active:claude_code", "backend_active:anthropic_api"], (
            f"Expected two backend_active signals for force_api flip; got: {ba_events}"
        )

    async def test_no_flip_sends_single_backend_active(self):
        """When backend does not change, exactly one backend_active is sent."""
        log = await self._run_stream(initial_backend="anthropic_api", force_api=False, has_images=False)

        ba_events = [e for e in log if e.startswith("backend_active:")]
        assert len(ba_events) == 1, (
            f"Expected exactly one backend_active signal; got: {ba_events}"
        )

    async def test_image_flip_sends_second_backend_active(self):
        """Inline images flipping claude_code → anthropic_api must produce a second backend_active."""
        log = await self._run_stream(initial_backend="claude_code", has_images=True)

        ba_events = [e for e in log if e.startswith("backend_active:")]
        assert ba_events == ["backend_active:claude_code", "backend_active:anthropic_api"], (
            f"Expected two backend_active signals for image flip; got: {ba_events}"
        )

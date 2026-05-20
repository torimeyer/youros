"""E2E tests for user-memory v1 — verify the full trigger → store → system-prompt chain.

FR-001: proves the end-to-end path works:
  1. A chat turn "remember I prefer plain language" triggers memory_trigger.handle().
  2. handle() calls user_memory_store.append_bullet() which creates MEMORY.md.
  3. _user_memory_block() in chat_providers reads the file and returns the block.
  4. The block appears in the assembled system prompt (both compose and cached paths).

Tests in this file are intentionally written to FAIL first (RED) so that any
dormant wiring bug surfaces as a test failure before we fix it.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import services.user_memory_store as store
import services.memory_trigger as trigger


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Redirect the memory store to a per-test tmp path and reset the cache."""
    mem_file = tmp_path / "MEMORY.md"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    store._cached_mtime = -1.0
    store._cached_content = ""
    yield mem_file
    store._cached_mtime = -1.0
    store._cached_content = ""


# ---------------------------------------------------------------------------
# Test 1: trigger fires and creates MEMORY.md
# ---------------------------------------------------------------------------


class TestTriggerWritesMemoryFile:
    """The memory trigger must write a bullet to MEMORY.md on a matching phrase."""

    @pytest.mark.asyncio
    async def test_remember_phrase_creates_file(self, isolated_memory):
        """'remember I prefer plain language' must create MEMORY.md with the bullet."""
        assert not isolated_memory.exists(), "MEMORY.md should not exist yet"

        ws = AsyncMock()
        fired = await trigger.handle("remember I prefer plain language", ws)

        assert fired is True, (
            "trigger.handle() returned False — regex did not match "
            "'remember I prefer plain language'. Check _TRIGGER_PATTERNS."
        )
        assert isolated_memory.exists(), (
            "MEMORY.md was not created after trigger fired. "
            "Check user_memory_store.append_bullet() path creation."
        )
        content = isolated_memory.read_text(encoding="utf-8")
        assert "plain language" in content, (
            f"Expected 'plain language' in MEMORY.md but got:\n{content}"
        )

    @pytest.mark.asyncio
    async def test_memory_added_websocket_event_emitted(self, isolated_memory):
        """A memory_added websocket event must be emitted on successful write."""
        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_added" in sent_types, (
            f"Expected 'memory_added' event but got: {sent_types}"
        )

    @pytest.mark.asyncio
    async def test_extracted_text_is_filed_under_preferences(self, isolated_memory):
        """The plain-language preference should land under '# Preferences'."""
        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)
        content = isolated_memory.read_text(encoding="utf-8")
        assert "# Preferences" in content, (
            f"Expected '# Preferences' section but got:\n{content}"
        )

    @pytest.mark.asyncio
    async def test_i_prefer_phrase_also_triggers(self, isolated_memory):
        """'I prefer plain language' (without 'remember') must also trigger."""
        ws = AsyncMock()
        fired = await trigger.handle("I prefer plain language", ws)
        assert fired is True, (
            "trigger.handle() returned False for 'I prefer plain language'. "
            "Check the i_prefer pattern in _TRIGGER_PATTERNS."
        )
        assert isolated_memory.exists()

    @pytest.mark.asyncio
    async def test_from_now_on_phrase_triggers(self, isolated_memory):
        """'from now on use plain language' must trigger."""
        ws = AsyncMock()
        fired = await trigger.handle("from now on use plain language", ws)
        assert fired is True, (
            "trigger.handle() returned False for 'from now on use plain language'."
        )

    @pytest.mark.asyncio
    async def test_always_phrase_triggers(self, isolated_memory):
        """'always use plain language' must trigger."""
        ws = AsyncMock()
        fired = await trigger.handle("always use plain language", ws)
        assert fired is True, (
            "trigger.handle() returned False for 'always use plain language'."
        )


# ---------------------------------------------------------------------------
# Test 2: preference appears in system-prompt block
# ---------------------------------------------------------------------------


class TestSystemPromptInjection:
    """After the trigger writes, the preference must appear in the system prompt."""

    def test_user_memory_block_empty_when_no_file(self, isolated_memory):
        """_user_memory_block() returns empty string when MEMORY.md doesn't exist."""
        from services.chat_providers import _user_memory_block
        assert not isolated_memory.exists()
        assert _user_memory_block() == "", (
            "_user_memory_block() should return '' when MEMORY.md is absent"
        )

    @pytest.mark.asyncio
    async def test_preference_appears_in_memory_block(self, isolated_memory):
        """After trigger fires, _user_memory_block() must include the preference."""
        from services.chat_providers import _user_memory_block

        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)

        block = _user_memory_block()
        assert block != "", (
            "_user_memory_block() returned empty string after trigger fired. "
            "Check that _user_memory_store.read() sees the written file."
        )
        assert "plain language" in block, (
            f"Expected 'plain language' in memory block but got:\n{block}"
        )

    @pytest.mark.asyncio
    async def test_memory_block_has_header(self, isolated_memory):
        """The memory block must include the 'User preferences and facts' header."""
        from services.chat_providers import _user_memory_block

        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)

        block = _user_memory_block()
        assert "User preferences" in block or "preferences" in block.lower(), (
            f"Expected a user-preferences header in block but got:\n{block}"
        )

    @pytest.mark.asyncio
    async def test_compose_system_prompt_includes_memory(self, isolated_memory):
        """_compose_system_prompt() must include memory content when file exists."""
        from services.chat_providers import _compose_system_prompt

        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)

        with patch("services.chat_providers._system_prompt", return_value="BASE SYSTEM PROMPT"), \
             patch("services.chat_providers._build_component_index", return_value=""), \
             patch("services.chat_providers._project_claude_md_context", return_value=""), \
             patch("services.chat_providers._standing_instructions_block", return_value=""), \
             patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""):
            prompt = _compose_system_prompt(None)

        assert "plain language" in prompt, (
            f"Expected 'plain language' in _compose_system_prompt() output but got:\n"
            f"{prompt[:500]}..."
        )

    @pytest.mark.asyncio
    async def test_cached_system_blocks_include_memory(self, isolated_memory):
        """_build_cached_system_blocks() must include memory in the volatile block."""
        from services.chat_providers import _build_cached_system_blocks

        ws = AsyncMock()
        await trigger.handle("remember I prefer plain language", ws)

        with patch("services.chat_providers._system_prompt", return_value="BASE"), \
             patch("services.chat_providers._build_component_index", return_value=""), \
             patch("services.chat_providers._project_claude_md_context", return_value=""), \
             patch("services.chat_providers._standing_instructions_block", return_value=""), \
             patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""):
            blocks = _build_cached_system_blocks(None)

        all_text = " ".join(b.get("text", "") for b in blocks)
        assert "plain language" in all_text, (
            f"Expected 'plain language' in cached system blocks but got:\n"
            f"{all_text[:500]}..."
        )

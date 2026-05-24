"""Tests for F2 forget trigger (→1538).

Covers:
- 6+ natural phrasings that fire the forget trigger
- 2 false-positive guards (forget when / forget to <verb>)
- match_forget_trigger returns None for non-forget messages
- remove_bullet: single match removes + returns text
- remove_bullet: multiple matches returns candidate list
- remove_bullet: no match returns None
- handle() emits memory_removed on successful removal
- handle() emits memory_remove_ambiguous on multiple matches
- handle() emits memory_remove_failed on no match
- Undo: restore_undo() restores pre-removal content
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

import services.user_memory_store as store
from services.memory_trigger import match_forget_trigger, handle as trigger_handle


@pytest.fixture(autouse=True)
def tmp_memory_path(tmp_path, monkeypatch):
    mem_file = tmp_path / "MEMORY.md"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    store._cached_mtime = -1.0
    store._cached_content = ""
    yield mem_file
    store._cached_mtime = -1.0
    store._cached_content = ""


class TestMatchForgetTrigger:
    """match_forget_trigger returns extracted text or None."""

    def test_bare_forget(self):
        assert match_forget_trigger("forget I prefer plain language") == "I prefer plain language"

    def test_forget_with_comma(self):
        assert match_forget_trigger("forget, I hate jargon") == "I hate jargon"

    def test_forget_with_colon(self):
        assert match_forget_trigger("forget: plain language preference") == "plain language preference"

    def test_stop_remembering(self):
        assert match_forget_trigger("stop remembering that I like bullet points") is not None

    def test_never_mind(self):
        assert match_forget_trigger("never mind about plain language") is not None

    def test_please_forget_prefix(self):
        assert match_forget_trigger("please forget I prefer plain language") is not None

    def test_can_you_forget(self):
        assert match_forget_trigger("can you forget that I hate jargon") is not None

    def test_returns_none_for_unrelated(self):
        assert match_forget_trigger("what do you remember about me") is None

    # False-positive guards
    def test_forget_when_is_excluded(self):
        assert match_forget_trigger("forget when we talked about this") is None

    def test_forget_to_verb_is_excluded(self):
        assert match_forget_trigger("forget to send that email") is None


class TestRemoveBullet:
    """user_memory_store.remove_bullet(text) — single / ambiguous / no-match."""

    def _seed(self, tmp_memory_path, content: str):
        tmp_memory_path.write_text(content, encoding="utf-8")
        store._cached_mtime = -1.0
        store._cached_content = ""

    def test_single_match_removes_and_returns_text(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language. <!-- added 2026-01-01T00:00Z -->\n- No jargon.\n")
        result = store.remove_bullet("plain language")
        assert isinstance(result, str)
        assert "plain language" in result.lower()
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" not in content.lower()
        assert "No jargon" in content

    def test_multiple_matches_returns_candidate_list(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n- Always use plain speech.\n")
        result = store.remove_bullet("plain")
        assert isinstance(result, list)
        assert len(result) == 2

    def test_no_match_returns_none(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n")
        result = store.remove_bullet("completely unrelated topic xyz")
        assert result is None

    def test_case_insensitive_match(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use Plain Language.\n")
        result = store.remove_bullet("plain language")
        assert isinstance(result, str)

    def test_removes_correct_bullet_leaves_others(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- No jargon.\n- Use plain language.\n- Be concise.\n")
        store.remove_bullet("plain language")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "No jargon" in content
        assert "Be concise" in content


class TestRestoreUndo:
    """restore_undo() restores the snapshot taken before remove_bullet."""

    def _seed(self, tmp_memory_path, content: str):
        tmp_memory_path.write_text(content, encoding="utf-8")
        store._cached_mtime = -1.0
        store._cached_content = ""

    def test_undo_restores_removed_bullet(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n- No jargon.\n")
        store.remove_bullet("plain language")
        content_after_remove = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" not in content_after_remove.lower()

        store.restore_undo()
        content_after_undo = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" in content_after_undo.lower()

    def test_undo_with_no_snapshot_is_noop(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n")
        store._undo_snapshot = None
        result = store.restore_undo()
        assert result is False


class TestHandleForget:
    """handle() dispatches forget → store → websocket events."""

    def _seed(self, tmp_memory_path, content: str):
        tmp_memory_path.write_text(content, encoding="utf-8")
        store._cached_mtime = -1.0
        store._cached_content = ""

    @pytest.mark.asyncio
    async def test_single_match_emits_memory_removed(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n")
        ws = AsyncMock()
        result = await trigger_handle("forget plain language", ws)
        assert result is True
        types = [c.args[0]["type"] for c in ws.send_json.call_args_list]
        assert "memory_removed" in types

    @pytest.mark.asyncio
    async def test_ambiguous_match_emits_memory_remove_ambiguous(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n- Always use plain speech.\n")
        ws = AsyncMock()
        await trigger_handle("forget plain", ws)
        types = [c.args[0]["type"] for c in ws.send_json.call_args_list]
        assert "memory_remove_ambiguous" in types

    @pytest.mark.asyncio
    async def test_no_match_emits_memory_remove_failed(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n")
        ws = AsyncMock()
        await trigger_handle("forget something completely unrelated xyz123", ws)
        types = [c.args[0]["type"] for c in ws.send_json.call_args_list]
        assert "memory_remove_failed" in types

    @pytest.mark.asyncio
    async def test_non_forget_message_not_handled_as_forget(self, tmp_memory_path):
        self._seed(tmp_memory_path, "# Preferences\n- Use plain language.\n")
        ws = AsyncMock()
        result = await trigger_handle("remember I like bullet points", ws)
        assert result is True
        types = [c.args[0]["type"] for c in ws.send_json.call_args_list]
        assert "memory_removed" not in types
        assert "memory_added" in types

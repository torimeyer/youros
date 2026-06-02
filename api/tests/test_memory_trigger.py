"""Tests for services.memory_trigger (FR-002, FR-003, FR-005).

Covers:
- match_forget_trigger detects 'forget X', 'stop remembering X', 'never mind X'
- match_forget_trigger respects exclusions (forget when, forget to verb)
- match_trigger still fires on remember/i prefer/always/from now on
- handle() dispatches to the forget path and emits correct websocket events
- handle() dispatches to the remember path and emits memory_added
- handle() returns False for neutral messages
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

import services.user_memory_store as store
import services.memory_trigger as trigger


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    mem_file = tmp_path / "MEMORY.md"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    store._cached_mtime = -1.0
    store._cached_content = ""
    store._undo_snapshot = None
    yield mem_file
    store._cached_mtime = -1.0
    store._cached_content = ""
    store._undo_snapshot = None


# ---------------------------------------------------------------------------
# match_forget_trigger — positive cases
# ---------------------------------------------------------------------------


class TestMatchForgetTrigger:
    def test_bare_forget(self):
        assert trigger.match_forget_trigger("forget plain language") == "plain language"

    def test_forget_with_comma(self):
        assert trigger.match_forget_trigger("forget, plain language") == "plain language"

    def test_forget_with_colon(self):
        assert trigger.match_forget_trigger("forget: plain language") == "plain language"

    def test_stop_remembering(self):
        assert trigger.match_forget_trigger("stop remembering use plain language") == "use plain language"

    def test_never_mind(self):
        result = trigger.match_forget_trigger("never mind that I prefer plain language")
        assert result is not None
        assert "plain language" in result

    def test_never_mind_about(self):
        result = trigger.match_forget_trigger("never mind about the plain language preference")
        assert result is not None

    def test_conversational_prefix_please_forget(self):
        result = trigger.match_forget_trigger("please forget plain language")
        assert result == "plain language"

    def test_conversational_prefix_can_you_forget(self):
        result = trigger.match_forget_trigger("can you forget plain language")
        assert result == "plain language"


# ---------------------------------------------------------------------------
# match_forget_trigger — exclusions (must NOT fire)
# ---------------------------------------------------------------------------


class TestMatchForgetTriggerExclusions:
    def test_forget_when_not_triggered(self):
        assert trigger.match_forget_trigger("forget when we launched") is None

    def test_forget_to_verb_not_triggered(self):
        assert trigger.match_forget_trigger("forget to pick up milk") is None

    def test_neutral_message_not_triggered(self):
        assert trigger.match_forget_trigger("what do you remember about me?") is None

    def test_remember_phrase_not_triggered(self):
        assert trigger.match_forget_trigger("remember I prefer plain language") is None


# ---------------------------------------------------------------------------
# match_trigger — remember patterns still fire
# ---------------------------------------------------------------------------


class TestMatchTrigger:
    def test_remember(self):
        assert trigger.match_trigger("remember I prefer plain language") is not None

    def test_i_prefer(self):
        assert trigger.match_trigger("I prefer plain language") is not None

    def test_always(self):
        assert trigger.match_trigger("always use plain language") is not None

    def test_from_now_on(self):
        assert trigger.match_trigger("from now on use plain language") is not None

    def test_forget_phrase_not_triggered_by_remember(self):
        assert trigger.match_trigger("forget plain language") is None

    def test_stop_remembering_not_triggered_by_remember(self):
        assert trigger.match_trigger("stop remembering plain language") is None


# ---------------------------------------------------------------------------
# classify_section
# ---------------------------------------------------------------------------


class TestClassifySection:
    def test_plain_preference_classified_as_preference(self):
        assert trigger.classify_section("use plain language") == "Preferences"

    def test_i_am_classified_as_fact(self):
        assert trigger.classify_section("I am a product manager") == "Facts"

    def test_my_name_is_classified_as_fact(self):
        assert trigger.classify_section("my name is Tori") == "Facts"


# ---------------------------------------------------------------------------
# handle() — forget path websocket events
# ---------------------------------------------------------------------------


class TestHandleForgetPath:
    @pytest.mark.asyncio
    async def test_forget_single_match_emits_memory_removed(self, isolated_memory):
        """After writing a bullet, forgetting it emits memory_removed."""
        store.append_bullet("Preferences", "use plain language")
        ws = AsyncMock()
        result = await trigger.handle("forget plain language", ws)
        assert result is True
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_removed" in sent_types

    @pytest.mark.asyncio
    async def test_forget_no_match_emits_memory_remove_failed(self, isolated_memory):
        """Forgetting a phrase that doesn't match any bullet emits memory_remove_failed."""
        ws = AsyncMock()
        result = await trigger.handle("forget my birthday", ws)
        assert result is True
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_remove_failed" in sent_types

    @pytest.mark.asyncio
    async def test_forget_ambiguous_emits_memory_remove_ambiguous(self, isolated_memory):
        """When multiple bullets match, ambiguous event is emitted with candidates."""
        store.append_bullet("Preferences", "use plain language always")
        store.append_bullet("Preferences", "use plain speech always")
        ws = AsyncMock()
        result = await trigger.handle("forget plain", ws)
        assert result is True
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_remove_ambiguous" in sent_types
        ambig_call = next(c for c in ws.send_json.call_args_list if c.args[0]["type"] == "memory_remove_ambiguous")
        assert len(ambig_call.args[0]["data"]) >= 2

    @pytest.mark.asyncio
    async def test_stop_remembering_removes_bullet(self, isolated_memory):
        """'stop remembering X' also triggers the forget path."""
        store.append_bullet("Preferences", "use plain language")
        ws = AsyncMock()
        result = await trigger.handle("stop remembering use plain language", ws)
        assert result is True
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_removed" in sent_types


# ---------------------------------------------------------------------------
# handle() — remember path websocket events
# ---------------------------------------------------------------------------


class TestHandleRememberPath:
    @pytest.mark.asyncio
    async def test_remember_emits_memory_added(self, isolated_memory):
        ws = AsyncMock()
        result = await trigger.handle("remember I prefer plain language", ws)
        assert result is True
        sent_types = [call.args[0]["type"] for call in ws.send_json.call_args_list]
        assert "memory_added" in sent_types

    @pytest.mark.asyncio
    async def test_neutral_message_returns_false(self, isolated_memory):
        ws = AsyncMock()
        result = await trigger.handle("what is the capital of France?", ws)
        assert result is False
        ws.send_json.assert_not_called()

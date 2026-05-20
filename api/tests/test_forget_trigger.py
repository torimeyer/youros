"""api/tests/test_forget_trigger.py

TDD coverage for F2 forget trigger (→1538).

Coverage shape mirrors test_memory_trigger.py (→1536):
  - 8 natural forget phrasings via match_forget_trigger
  - Exclusion guards ("forget about it", "forget when X")
  - match_trigger must NOT fire on forget phrases
  - remove_bullet: single-match, ambiguous, not_found
  - handle_forget ws events: memory_removed / memory_remove_ambiguous / memory_remove_failed
  - Non-forget phrase returns False from handle_forget
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

import services.memory_trigger as trigger
import services.user_memory_store as store

SAMPLE_MEMORY = """\
# Preferences
- I prefer plain language <!-- added 2026-05-20T10:00Z -->
- I work in IAM security <!-- added 2026-05-20T10:01Z -->
- Always use plain style <!-- added 2026-05-20T10:02Z -->
"""


# ── match_forget_trigger: 8 natural phrasings ────────────────────────────────


class TestMatchForgetTrigger:
    def test_bare_forget(self):
        assert trigger.match_forget_trigger("forget I prefer plain language") == "I prefer plain language"

    def test_please_forget(self):
        assert trigger.match_forget_trigger("please forget I prefer plain language") == "I prefer plain language"

    def test_stop_remembering(self):
        assert trigger.match_forget_trigger("stop remembering I prefer plain language") == "I prefer plain language"

    def test_never_mind_that(self):
        assert trigger.match_forget_trigger("never mind that I prefer plain language") == "I prefer plain language"

    def test_btw_forget(self):
        assert trigger.match_forget_trigger("btw forget I work in IAM") == "I work in IAM"

    def test_ok_so_forget(self):
        assert trigger.match_forget_trigger("ok so forget I work in IAM") == "I work in IAM"

    def test_can_you_forget(self):
        assert trigger.match_forget_trigger("can you forget I work in IAM") == "I work in IAM"

    def test_fyi_forget(self):
        assert trigger.match_forget_trigger("fyi forget I work in IAM") == "I work in IAM"


# ── exclusion guards ──────────────────────────────────────────────────────────


class TestMatchForgetTriggerExclusions:
    def test_forget_about_it_excluded(self):
        assert trigger.match_forget_trigger("forget about it") is None

    def test_forget_when_excluded(self):
        assert trigger.match_forget_trigger("forget when the bug happened") is None

    def test_write_trigger_does_not_fire_on_forget_phrase(self):
        # "forget X" must NOT also trigger a memory write.
        assert trigger.match_trigger("forget I prefer plain language") is None


# ── remove_bullet ─────────────────────────────────────────────────────────────


class TestRemoveBullet:
    def _setup(self, tmp_path: Path, monkeypatch) -> Path:
        mem = tmp_path / "MEMORY.md"
        mem.write_text(SAMPLE_MEMORY, encoding="utf-8")
        monkeypatch.setattr(store, "_MEMORY_PATH", mem)
        store._reset_cache()
        return mem

    def test_single_match_removes_bullet(self, tmp_path, monkeypatch):
        mem = self._setup(tmp_path, monkeypatch)
        result = store.remove_bullet("plain language")
        assert result["status"] == "removed"
        assert "plain language" in result["bullet"]
        remaining = mem.read_text()
        assert "plain language" not in remaining
        assert "IAM security" in remaining

    def test_single_match_correct_section_returned(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = store.remove_bullet("IAM security")
        assert result["status"] == "removed"
        assert result["section"] == "Preferences"

    def test_ambiguous_match_returns_candidates(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        # "plain" appears in "I prefer plain language" and "Always use plain style"
        result = store.remove_bullet("plain")
        assert result["status"] == "ambiguous"
        assert len(result["candidates"]) >= 2
        assert any("plain language" in c for c in result["candidates"])
        assert any("plain style" in c for c in result["candidates"])

    def test_no_match_returns_not_found(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = store.remove_bullet("something completely unrelated xyz")
        assert result["status"] == "not_found"

    def test_file_not_found_returns_not_found(self, tmp_path, monkeypatch):
        mem = tmp_path / "MEMORY.md"  # does not exist
        monkeypatch.setattr(store, "_MEMORY_PATH", mem)
        store._reset_cache()
        result = store.remove_bullet("anything")
        assert result["status"] == "not_found"


# ── handle_forget websocket events ───────────────────────────────────────────


class TestHandleForget:
    def _setup(self, tmp_path: Path, monkeypatch) -> Path:
        mem = tmp_path / "MEMORY.md"
        mem.write_text(SAMPLE_MEMORY, encoding="utf-8")
        monkeypatch.setattr(store, "_MEMORY_PATH", mem)
        store._reset_cache()
        return mem

    @pytest.mark.asyncio
    async def test_memory_removed_event_on_success(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        ws = AsyncMock()
        result = await trigger.handle_forget("forget I prefer plain language", ws)
        assert result is True
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "memory_removed"
        assert "plain language" in payload["data"]

    @pytest.mark.asyncio
    async def test_memory_removed_includes_section(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        ws = AsyncMock()
        await trigger.handle_forget("forget IAM security", ws)
        payload = ws.send_json.call_args[0][0]
        assert "section" in payload
        assert payload["section"] == "Preferences"

    @pytest.mark.asyncio
    async def test_memory_remove_ambiguous_event(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        ws = AsyncMock()
        # "plain" matches two bullets → ambiguous
        result = await trigger.handle_forget("forget plain", ws)
        assert result is True
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "memory_remove_ambiguous"
        assert len(payload["candidates"]) >= 2

    @pytest.mark.asyncio
    async def test_memory_remove_failed_event_on_no_match(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        ws = AsyncMock()
        result = await trigger.handle_forget("forget something xyz completely unrelated", ws)
        assert result is True
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "memory_remove_failed"

    @pytest.mark.asyncio
    async def test_non_forget_phrase_returns_false(self):
        ws = AsyncMock()
        result = await trigger.handle_forget("remember I prefer plain language", ws)
        assert result is False
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_prefix_normalized_before_forget_match(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        ws = AsyncMock()
        # "please forget X" should normalize to "forget X" and fire
        result = await trigger.handle_forget("please forget IAM security", ws)
        assert result is True
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "memory_removed"

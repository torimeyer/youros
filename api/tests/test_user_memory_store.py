"""Tests for services.user_memory_store (FR-007).

Covers:
- read() returns empty string when file is absent (no error)
- append_bullet() creates file on first write (create-on-first-write)
- append_bullet() writes under the correct section heading
- Two contradictory bullets are both kept (contradiction-tolerance)
- mtime-based cache invalidation: re-read after write returns new content
- replace_all() overwrites content and invalidates cache
- 50 KB warning threshold is logged (not raised as exception)
- write failure emits memory_write_failed websocket event
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.user_memory_store as store


@pytest.fixture(autouse=True)
def tmp_memory_path(tmp_path, monkeypatch):
    """Redirect the store to a temp directory for every test."""
    mem_file = tmp_path / "MEMORY.md"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    # Reset module-level cache so tests are independent.
    store._cached_mtime = -1.0
    store._cached_content = ""
    yield mem_file
    # Clean up cache after test.
    store._cached_mtime = -1.0
    store._cached_content = ""


class TestRead:
    def test_missing_file_returns_empty_string(self, tmp_memory_path):
        assert tmp_memory_path.exists() is False
        assert store.read() == ""

    def test_existing_file_returns_content(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- Use plain language.\n", encoding="utf-8")
        content = store.read()
        assert "# Preferences" in content
        assert "Use plain language" in content

    def test_returns_cached_content_on_second_call(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- line one\n", encoding="utf-8")
        first = store.read()
        # Mutate the file externally without changing mtime (we mock stat).
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_mtime=store._cached_mtime)
            second = store.read()
        assert first == second


class TestAppendBullet:
    def test_creates_file_on_first_write(self, tmp_memory_path):
        assert not tmp_memory_path.exists()
        store.append_bullet("Preferences", "Use plain language")
        assert tmp_memory_path.exists()

    def test_bullet_appears_under_correct_section(self, tmp_memory_path):
        store.append_bullet("Preferences", "No jargon")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "# Preferences" in content
        assert "- No jargon" in content

    def test_fact_bullet_goes_under_facts(self, tmp_memory_path):
        store.append_bullet("Facts", "I am a product manager")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "# Facts" in content
        assert "I am a product manager" in content

    def test_bullet_includes_timestamp_comment(self, tmp_memory_path):
        store.append_bullet("Preferences", "Use plain language")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "<!-- added " in content

    def test_two_contradictory_bullets_are_both_kept(self, tmp_memory_path):
        """Contradiction-tolerance: do not deduplicate. Both bullets must survive."""
        store.append_bullet("Preferences", "Use formal language")
        store.append_bullet("Preferences", "Use informal language")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "Use formal language" in content
        assert "Use informal language" in content

    def test_mtime_cache_invalidated_after_write(self, tmp_memory_path):
        """Cache is busted after append_bullet so read() sees fresh content."""
        tmp_memory_path.write_text("# Preferences\n- Old content\n", encoding="utf-8")
        first = store.read()
        assert "Old content" in first
        store.append_bullet("Preferences", "New preference")
        second = store.read()
        assert "New preference" in second

    def test_50kb_warning_is_logged(self, tmp_memory_path, caplog):
        """Writing past the 50 KB threshold emits a warning (does not raise)."""
        # Pre-fill the file with just over 50 KB of content.
        # 970 * 53 bytes per line + 14 byte heading = 51,424 bytes (> 51,200).
        big_content = "# Preferences\n" + ("- " + "x" * 50 + "\n") * 970
        tmp_memory_path.write_text(big_content, encoding="utf-8")
        # Reset cache so the store reads the pre-filled content.
        store._cached_mtime = -1.0
        store._cached_content = ""
        with caplog.at_level(logging.WARNING, logger="services.user_memory_store"):
            store.append_bullet("Preferences", "This tips it over 50 KB")
        # Should not raise; warning should be present.
        assert any("50 KB" in m for m in caplog.messages)


class TestReplaceAll:
    def test_replaces_entire_file(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- Old\n", encoding="utf-8")
        store.replace_all("# Facts\n- New content\n")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert content == "# Facts\n- New content\n"
        assert "Old" not in content

    def test_cache_invalidated_after_replace_all(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- Before\n", encoding="utf-8")
        store.read()  # warm cache
        store.replace_all("# Facts\n- After\n")
        assert "After" in store.read()


class TestRemoveBullet:
    def test_single_match_returns_removed_text(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        result = store.remove_bullet("plain language")
        assert isinstance(result, str)
        assert "plain language" in result

    def test_single_match_removes_bullet_from_file(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.remove_bullet("plain language")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" not in content

    def test_no_match_returns_none(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        result = store.remove_bullet("something completely unrelated xyz")
        assert result is None

    def test_no_file_returns_none(self, tmp_memory_path):
        assert not tmp_memory_path.exists()
        result = store.remove_bullet("anything")
        assert result is None

    def test_ambiguous_match_returns_list(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language always")
        store.append_bullet("Preferences", "use plain speech always")
        result = store.remove_bullet("plain")
        assert isinstance(result, list)
        assert len(result) >= 2

    def test_single_match_saves_undo_snapshot(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.remove_bullet("plain language")
        assert store._undo_snapshot is not None

    def test_cache_invalidated_after_remove(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.read()  # warm cache
        store.remove_bullet("plain language")
        content = store.read()
        assert "plain language" not in content


class TestRestoreUndo:
    def test_restore_returns_true_after_remove(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.remove_bullet("plain language")
        assert store.restore_undo() is True

    def test_restored_bullet_is_back_in_file(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.remove_bullet("plain language")
        store.restore_undo()
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" in content

    def test_restore_returns_false_when_no_snapshot(self, tmp_memory_path):
        store._undo_snapshot = None
        assert store.restore_undo() is False

    def test_restore_clears_snapshot(self, tmp_memory_path):
        store.append_bullet("Preferences", "use plain language")
        store.remove_bullet("plain language")
        store.restore_undo()
        assert store._undo_snapshot is None


class TestComputeOverflowStatus:
    def test_no_file_returns_not_overflowed(self, tmp_memory_path):
        status = store.compute_overflow_status()
        assert status["overflowed"] is False
        assert status["kb"] == 0.0
        assert status["lines"] == 0

    def test_small_file_not_overflowed(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- use plain language\n", encoding="utf-8")
        store._cached_mtime = -1.0
        status = store.compute_overflow_status()
        assert status["overflowed"] is False

    def test_line_count_overflow(self, tmp_memory_path):
        content = "# Preferences\n" + ("- bullet line here\n" * 155)
        tmp_memory_path.write_text(content, encoding="utf-8")
        store._cached_mtime = -1.0
        status = store.compute_overflow_status()
        assert status["overflowed"] is True
        assert status["reason"] == "lines"
        assert status["lines"] > 150

    def test_kb_overflow(self, tmp_memory_path):
        content = "# Preferences\n" + ("- " + "x" * 100 + "\n") * 320
        tmp_memory_path.write_text(content, encoding="utf-8")
        store._cached_mtime = -1.0
        status = store.compute_overflow_status()
        assert status["overflowed"] is True
        assert status["reason"] == "kb"
        assert status["kb"] > 30.0

    def test_hard_cap_detected(self, tmp_memory_path, monkeypatch):
        monkeypatch.setattr(store, "_HARD_CAP_BYTES", 10)
        tmp_memory_path.write_text("# Preferences\n- use plain language\n", encoding="utf-8")
        store._cached_mtime = -1.0
        status = store.compute_overflow_status()
        assert status["hard_cap"] is True


class TestSplitIntoTopic:
    def test_split_moves_bullet_to_topic_file(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        result = store.split_into_topic("plain language", "style")
        assert result is True
        topic_file = topic_dir / "style.md"
        assert topic_file.exists()
        assert "plain language" in topic_file.read_text(encoding="utf-8")

    def test_split_removes_bullet_from_index(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        store.split_into_topic("plain language", "style")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "plain language" not in content

    def test_split_adds_topic_link_to_index(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        store.split_into_topic("plain language", "style")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "[style](memory/style.md)" in content

    def test_split_no_match_returns_false(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        result = store.split_into_topic("something not here xyz", "style")
        assert result is False

    def test_split_saves_undo_snapshot(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        store.split_into_topic("plain language", "style")
        assert store._undo_snapshot is not None


class TestRenameTopic:
    def test_rename_renames_file(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        store.split_into_topic("plain language", "style")
        result = store.rename_topic("style", "writing-style")
        assert result is True
        assert not (topic_dir / "style.md").exists()
        assert (topic_dir / "writing-style.md").exists()

    def test_rename_updates_index_link(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        store.append_bullet("Preferences", "use plain language")
        store.split_into_topic("plain language", "style")
        store.rename_topic("style", "writing-style")
        content = tmp_memory_path.read_text(encoding="utf-8")
        assert "[writing-style](memory/writing-style.md)" in content
        assert "[style](memory/style.md)" not in content

    def test_rename_nonexistent_topic_returns_false(self, tmp_memory_path, monkeypatch):
        topic_dir = tmp_memory_path.parent / "memory"
        monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
        result = store.rename_topic("nonexistent", "new-name")
        assert result is False


class TestReadForContext:
    def test_no_overflow_returns_full_index(self, tmp_memory_path):
        tmp_memory_path.write_text("# Preferences\n- use plain language\n", encoding="utf-8")
        store._cached_mtime = -1.0
        content = store.read_for_context(["plain"])
        assert "plain language" in content

    def test_empty_file_returns_empty_string(self, tmp_memory_path):
        assert store.read_for_context([]) == ""


class TestWriteFailureWebsocketEvent:
    """On write failure the memory_write_failed event is emitted (chat continues)."""

    @pytest.mark.asyncio
    async def test_write_failure_emits_event(self, tmp_memory_path, monkeypatch):
        from services.memory_trigger import handle as trigger_handle

        def broken_append(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store, "append_bullet", broken_append)

        fake_ws = AsyncMock()
        result = await trigger_handle("remember use plain language", fake_ws)

        assert result is False
        sent_types = [call.args[0]["type"] for call in fake_ws.send_json.call_args_list]
        assert "memory_write_failed" in sent_types

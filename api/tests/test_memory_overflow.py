"""Tests for user-memory F5: index-overflow → topic files (→1537).

RED tests — all expected to fail until implementation lands.

Covers:
  FR-009  split_into_topic() moves bullet, writes topic file, updates index
  FR-010  compute_overflow_status() 30KB / 150-line threshold detection
  FR-012  read_for_context() index + matching topic files in overflow mode
  FR-013  200KB hard cap detection
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import services.user_memory_store as store


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    """Redirect store to a fresh tmp directory for each test."""
    mem_file = tmp_path / "MEMORY.md"
    topic_dir = tmp_path / "memory"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    monkeypatch.setattr(store, "_TOPIC_DIR", topic_dir)
    # Reset module-level cache between tests.
    monkeypatch.setattr(store, "_cached_mtime", -1.0)
    monkeypatch.setattr(store, "_cached_content", "")
    yield tmp_path


def _write_memory(tmp_path: Path, content: str) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text(content, encoding="utf-8")


# ── FR-010: compute_overflow_status ──────────────────────────────────────────


def test_overflow_status_small_file(tmp_path):
    """File under both thresholds → overflowed=False."""
    _write_memory(tmp_path, "# Preferences\n- I prefer plain language.\n")
    status = store.compute_overflow_status()
    assert status["overflowed"] is False
    assert status["hard_cap"] is False


def test_overflow_status_line_threshold(tmp_path):
    """151 bullet lines → overflowed=True, reason='lines'."""
    lines = ["# Preferences"] + [f"- Bullet {i}" for i in range(151)]
    _write_memory(tmp_path, "\n".join(lines) + "\n")
    status = store.compute_overflow_status()
    assert status["overflowed"] is True
    assert status["reason"] == "lines"
    assert status["lines"] >= 151


def test_overflow_status_size_threshold(tmp_path):
    """File > 30KB → overflowed=True, reason='kb'."""
    # 31KB of content
    content = "# Preferences\n" + ("- " + "x" * 80 + "\n") * 400
    _write_memory(tmp_path, content)
    status = store.compute_overflow_status()
    assert status["overflowed"] is True
    assert status["reason"] == "kb"
    assert status["kb"] > 30


def test_overflow_status_absent_file(tmp_path):
    """No file → overflowed=False, no error."""
    status = store.compute_overflow_status()
    assert status["overflowed"] is False
    assert status["hard_cap"] is False


def test_hard_cap_detection(tmp_path):
    """Total memory (index + topics) > 200KB → hard_cap=True."""
    # Put 100KB in main file
    big = "# Preferences\n" + ("- " + "a" * 80 + "\n") * 1300
    _write_memory(tmp_path, big)
    # Put another 110KB in a topic file
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "vocabulary.md").write_text("# vocabulary\n" + ("- " + "b" * 80 + "\n") * 1400, encoding="utf-8")
    status = store.compute_overflow_status()
    assert status["hard_cap"] is True
    assert status["total_kb"] > 200


# ── FR-009: split_into_topic ──────────────────────────────────────────────────


def test_split_creates_topic_file(tmp_path):
    """Bullet moved out of index; topic file created with the bullet."""
    _write_memory(tmp_path, "# Preferences\n- Don't say grep. <!-- added 2026-05-01T10:00Z -->\n")
    result = store.split_into_topic("Don't say grep", "vocabulary")
    assert result is True
    topic_file = tmp_path / "memory" / "vocabulary.md"
    assert topic_file.exists()
    assert "Don't say grep" in topic_file.read_text(encoding="utf-8")


def test_split_updates_index_link(tmp_path):
    """After split, index contains [vocabulary](memory/vocabulary.md) link."""
    _write_memory(tmp_path, "# Preferences\n- Don't say grep.\n")
    store.split_into_topic("Don't say grep", "vocabulary")
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "[vocabulary](memory/vocabulary.md)" in index


def test_split_removes_bullet_from_index(tmp_path):
    """Bullet must not remain in index after split."""
    _write_memory(tmp_path, "# Preferences\n- Don't say grep.\n- Use plain language.\n")
    store.split_into_topic("Don't say grep", "vocabulary")
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "Don't say grep" not in index
    assert "Use plain language" in index


def test_split_appends_to_existing_topic_file(tmp_path):
    """Second bullet split into same topic appends rather than overwrites."""
    _write_memory(tmp_path, "# Preferences\n- Don't say grep.\n- Don't say 'rg'.\n")
    store.split_into_topic("Don't say grep", "vocabulary")
    store.split_into_topic("Don't say 'rg'", "vocabulary")
    topic_file = tmp_path / "memory" / "vocabulary.md"
    content = topic_file.read_text(encoding="utf-8")
    assert "Don't say grep" in content
    assert "Don't say 'rg'" in content


def test_split_missing_bullet_returns_false(tmp_path):
    """split_into_topic returns False when bullet not found."""
    _write_memory(tmp_path, "# Preferences\n- Use plain language.\n")
    result = store.split_into_topic("nonexistent bullet", "vocabulary")
    assert result is False


def test_split_is_undoable(tmp_path):
    """restore_undo() after split_into_topic restores the index."""
    original = "# Preferences\n- Don't say grep.\n"
    _write_memory(tmp_path, original)
    store.split_into_topic("Don't say grep", "vocabulary")
    store.restore_undo()
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "Don't say grep" in index


# ── rename_topic ──────────────────────────────────────────────────────────────


def test_rename_topic_renames_file(tmp_path):
    """rename_topic moves the topic file."""
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir()
    (topic_dir / "vocab.md").write_text("# vocab\n- Don't say grep.\n", encoding="utf-8")
    _write_memory(tmp_path, "# Preferences\n\n[vocab](memory/vocab.md)\n")
    result = store.rename_topic("vocab", "vocabulary")
    assert result is True
    assert (topic_dir / "vocabulary.md").exists()
    assert not (topic_dir / "vocab.md").exists()


def test_rename_topic_updates_index_link(tmp_path):
    """Index link is updated to new name after rename."""
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir()
    (topic_dir / "vocab.md").write_text("# vocab\n- Don't say grep.\n", encoding="utf-8")
    _write_memory(tmp_path, "# Preferences\n\n[vocab](memory/vocab.md)\n")
    store.rename_topic("vocab", "vocabulary")
    index = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "[vocabulary](memory/vocabulary.md)" in index
    assert "vocab.md" not in index


# ── FR-012: read_for_context ──────────────────────────────────────────────────


def test_read_for_context_small_file_returns_full(tmp_path):
    """No overflow → read_for_context returns full index content."""
    _write_memory(tmp_path, "# Preferences\n- I prefer plain language.\n")
    result = store.read_for_context([])
    assert "I prefer plain language" in result


def test_read_for_context_always_loads_index(tmp_path):
    """In overflow mode, index is always included."""
    lines = ["# Preferences"] + [f"- Bullet {i}" for i in range(151)]
    content = "\n".join(lines) + "\n\n[vocabulary](memory/vocabulary.md)\n"
    _write_memory(tmp_path, content)
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir()
    (topic_dir / "vocabulary.md").write_text("# vocabulary\n- Don't say grep.\n", encoding="utf-8")
    result = store.read_for_context(["something unrelated"])
    assert "Bullet 0" in result


def test_read_for_context_loads_matching_topic_files(tmp_path):
    """Keyword match in topic name loads that topic file."""
    lines = ["# Preferences"] + [f"- Bullet {i}" for i in range(151)]
    content = "\n".join(lines) + "\n\n[vocabulary](memory/vocabulary.md)\n"
    _write_memory(tmp_path, content)
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir()
    (topic_dir / "vocabulary.md").write_text("# vocabulary\n- Don't say grep.\n", encoding="utf-8")
    result = store.read_for_context(["vocabulary"])
    assert "Don't say grep" in result


def test_read_for_context_skips_non_matching_topics(tmp_path):
    """In overflow mode, topic files with no keyword match are excluded."""
    lines = ["# Preferences"] + [f"- Bullet {i}" for i in range(151)]
    content = "\n".join(lines) + "\n\n[vocabulary](memory/vocabulary.md)\n[git-style](memory/git-style.md)\n"
    _write_memory(tmp_path, content)
    topic_dir = tmp_path / "memory"
    topic_dir.mkdir()
    (topic_dir / "vocabulary.md").write_text("# vocabulary\n- Don't say grep.\n", encoding="utf-8")
    (topic_dir / "git-style.md").write_text("# git-style\n- Use plain commit messages.\n", encoding="utf-8")
    result = store.read_for_context(["vocabulary"])
    assert "Don't say grep" in result
    assert "plain commit messages" not in result

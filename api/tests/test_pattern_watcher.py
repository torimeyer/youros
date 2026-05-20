"""Tests for pattern_watcher v1 — observation write layer (→1539).

All three ACs:
  AC1: task:defer bullet written within one turn when user defers a task.
  AC2: vocab:new bullet written when a new token appears in chat.
  AC3: recall_stats shows observations as a configured ostk-recall source.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_bullets(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# AC1 — task:defer detection and write
# ---------------------------------------------------------------------------

def test_defer_detection_writes_task_defer_bullet(tmp_path, monkeypatch):
    """User saying 'defer this' writes a task:defer bullet to tasks.md."""
    import services.pattern_watcher as pw
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(pw, "TASKS_FILE", tmp_path / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", tmp_path / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", tmp_path / ".seen_vocab.txt")

    user_text = "defer →1234 for now"
    reason = pw.detect_task_defer(user_text)
    assert reason is not None, "detect_task_defer should return a reason for 'defer'"
    result = pw.write_task_defer(user_text, reason)
    assert result is True

    bullets = _read_bullets(tmp_path / "tasks.md")
    assert len(bullets) == 1
    assert "task:defer" in bullets[0]
    assert "→1234" in bullets[0] or "defer" in bullets[0].lower()


def test_defer_not_detected_for_normal_message(tmp_path, monkeypatch):
    """Normal chat message does not trigger defer detection."""
    import services.pattern_watcher as pw
    assert pw.detect_task_defer("what's the weather like today?") is None
    assert pw.detect_task_defer("ship this to production") is None
    assert pw.detect_task_defer("") is None
    assert pw.detect_task_defer(None) is None  # type: ignore[arg-type]


def test_various_defer_phrasings_detected():
    """Several common ways to defer a task are all detected."""
    import services.pattern_watcher as pw
    phrases = [
        "defer this task",
        "we'll do this later",
        "postpone →999",
        "shelve that one",
        "not now please",
        "hold off on this",
    ]
    for phrase in phrases:
        assert pw.detect_task_defer(phrase) is not None, f"Should detect defer in: {phrase!r}"


# ---------------------------------------------------------------------------
# AC2 — vocab:new write
# ---------------------------------------------------------------------------

def test_new_token_writes_vocab_bullet(tmp_path, monkeypatch):
    """A token not seen before writes a vocab:new bullet to vocab.md."""
    import services.pattern_watcher as pw
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(pw, "TASKS_FILE", tmp_path / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", tmp_path / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", tmp_path / ".seen_vocab.txt")

    count = pw.write_vocab_new("say elit when you want plain language")
    assert count > 0, "Should write at least one new vocab bullet"

    bullets = _read_bullets(tmp_path / "vocab.md")
    assert len(bullets) == count
    assert all("vocab:new" in b for b in bullets)
    assert any("elit" in b for b in bullets)


def test_second_call_same_tokens_writes_nothing(tmp_path, monkeypatch):
    """Tokens already seen on a previous turn are not re-written."""
    import services.pattern_watcher as pw
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(pw, "TASKS_FILE", tmp_path / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", tmp_path / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", tmp_path / ".seen_vocab.txt")

    pw.write_vocab_new("saa this needle now")
    first_bullets = _read_bullets(tmp_path / "vocab.md")
    assert len(first_bullets) > 0

    # Same text again — should write nothing new
    count2 = pw.write_vocab_new("saa this needle now")
    assert count2 == 0
    second_bullets = _read_bullets(tmp_path / "vocab.md")
    assert len(second_bullets) == len(first_bullets)


def test_stop_words_not_recorded(tmp_path, monkeypatch):
    """Common stop words (is, the, a, in) never appear as vocab:new bullets."""
    import services.pattern_watcher as pw
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(pw, "TASKS_FILE", tmp_path / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", tmp_path / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", tmp_path / ".seen_vocab.txt")

    pw.write_vocab_new("the is a in to it")
    bullets = _read_bullets(tmp_path / "vocab.md")
    assert len(bullets) == 0, "Stop words should never generate vocab bullets"


# ---------------------------------------------------------------------------
# Append-only (both files)
# ---------------------------------------------------------------------------

def test_bullets_append_not_overwrite(tmp_path, monkeypatch):
    """Multiple observations accumulate; earlier lines are never lost."""
    import services.pattern_watcher as pw
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", tmp_path)
    monkeypatch.setattr(pw, "TASKS_FILE", tmp_path / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", tmp_path / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", tmp_path / ".seen_vocab.txt")

    pw.write_task_defer("defer →1", "tori said later")
    pw.write_task_defer("defer →2", "tori said later")
    bullets = _read_bullets(tmp_path / "tasks.md")
    assert len(bullets) == 2


# ---------------------------------------------------------------------------
# Safe when observations dir missing
# ---------------------------------------------------------------------------

def test_safe_when_dir_missing(tmp_path, monkeypatch):
    """Service creates ~/myos/observations/ if it doesn't exist."""
    import services.pattern_watcher as pw
    new_dir = tmp_path / "deep" / "nested"
    monkeypatch.setattr(pw, "OBSERVATIONS_DIR", new_dir)
    monkeypatch.setattr(pw, "TASKS_FILE", new_dir / "tasks.md")
    monkeypatch.setattr(pw, "VOCAB_FILE", new_dir / "vocab.md")
    monkeypatch.setattr(pw, "SEEN_VOCAB_FILE", new_dir / ".seen_vocab.txt")

    assert not new_dir.exists()
    result = pw.write_task_defer("defer →1", "later")
    assert result is True
    assert new_dir.exists()
    assert (new_dir / "tasks.md").exists()


# ---------------------------------------------------------------------------
# AC3 — recall_stats shows observations source
# ---------------------------------------------------------------------------

def test_recall_stats_shows_observations_when_configured(tmp_path):
    """recall_stats returns observations_configured=True when source is in config."""
    import services.pattern_watcher as pw
    import tomllib

    config_content = textwrap.dedent("""\
        [corpus]
        root = "~/.local/share/ostk-recall"

        [[sources]]
        kind = "markdown"
        project = "torios-memory"
        paths = ["/some/path"]

        [[sources]]
        kind = "markdown"
        project = "observations"
        paths = ["~/myos/observations"]
    """)
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_content)

    import unittest.mock as mock
    with mock.patch("services.pattern_watcher.Path.home", return_value=tmp_path.parent):
        # Override config path resolution for this test
        stats = pw.recall_stats(config_path=config_path)

    assert stats["observations_configured"] is True
    assert "observations" in stats["sources"]


def test_recall_stats_returns_false_when_not_configured(tmp_path):
    """recall_stats returns observations_configured=False when source is absent."""
    import services.pattern_watcher as pw

    config_content = textwrap.dedent("""\
        [[sources]]
        kind = "markdown"
        project = "torios-memory"
        paths = ["/some/path"]
    """)
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_content)

    stats = pw.recall_stats(config_path=config_path)
    assert stats["observations_configured"] is False
    assert "observations" not in stats["sources"]

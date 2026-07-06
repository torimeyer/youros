"""Tests for pattern_watcher v1+v2 observation write layer (→1539 →2484).

AC coverage:
  AC1 — task:defer bullet written to ~/myos/observations/tasks.md
  AC2 — vocab:new bullet written to ~/myos/observations/vocab.md
  AC3 — recall_stats source registration (config file has observations source)
  AC5 — confirmed cluster (tier >= 2) generates inline chat hint via read_context_for_turn

Daemon-stopped edge case: observations still write even when recall is unavailable.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bullet_pattern(kind: str) -> re.Pattern[str]:
    """Return a compiled regex matching a bullet of the given kind."""
    return re.compile(rf"^- \d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z {re.escape(kind)}", re.MULTILINE)


# ---------------------------------------------------------------------------
# AC1 — task:defer bullet
# ---------------------------------------------------------------------------

def test_defer_event_writes_task_defer_bullet(tmp_path: Path) -> None:
    """observe_turn writes a task:defer bullet to observations/tasks.md when
    the user message signals a task deferral (e.g. 'do this later')."""
    from services.pattern_watcher import observe_turn

    tasks_file = tmp_path / "tasks.md"
    vocab_file = tmp_path / "vocab.md"

    observe_turn(
        user_message="I'll fix the badge thing later",
        assistant_reply="Sure, I'll park that for now.",
        tasks_path=tasks_file,
        vocab_path=vocab_file,
    )

    assert tasks_file.exists(), "tasks.md was not created"
    content = tasks_file.read_text()
    assert _bullet_pattern("task:defer").search(content), (
        f"No task:defer bullet found in tasks.md:\n{content}"
    )


def test_defer_bullet_includes_reason(tmp_path: Path) -> None:
    """The task:defer bullet should contain a reason= field."""
    from services.pattern_watcher import observe_turn

    tasks_file = tmp_path / "tasks.md"
    vocab_file = tmp_path / "vocab.md"

    observe_turn(
        user_message="defer the formatting task, I'll do it another time",
        assistant_reply="Got it, deferred.",
        tasks_path=tasks_file,
        vocab_path=vocab_file,
    )

    content = tasks_file.read_text()
    assert "reason=" in content, f"bullet missing reason= field:\n{content}"


# ---------------------------------------------------------------------------
# AC2 — vocab:new bullet
# ---------------------------------------------------------------------------

def test_new_vocab_token_writes_vocab_new_bullet(tmp_path: Path) -> None:
    """observe_turn writes a vocab:new bullet to observations/vocab.md when
    the user message contains a new specialized token not in the vocab file."""
    from services.pattern_watcher import observe_turn

    tasks_file = tmp_path / "tasks.md"
    vocab_file = tmp_path / "vocab.md"

    observe_turn(
        user_message="elit on the boot output please",
        assistant_reply="Here is the plain-language version.",
        tasks_path=tasks_file,
        vocab_path=vocab_file,
    )

    assert vocab_file.exists(), "vocab.md was not created"
    content = vocab_file.read_text()
    assert _bullet_pattern("vocab:new").search(content), (
        f"No vocab:new bullet found in vocab.md:\n{content}"
    )
    assert 'token="elit"' in content, f"token field missing:\n{content}"


def test_already_seen_vocab_token_not_duplicated(tmp_path: Path) -> None:
    """A token already in vocab.md does not get a second bullet on next turn."""
    from services.pattern_watcher import observe_turn

    tasks_file = tmp_path / "tasks.md"
    vocab_file = tmp_path / "vocab.md"
    # Pre-seed vocab.md with elit already recorded
    vocab_file.write_text(
        "- 2026-05-01T10:00:00Z vocab:new token=\"elit\" surrounding=\"say elit\"\n"
    )

    observe_turn(
        user_message="elit on the boot output please",
        assistant_reply="Plain language version.",
        tasks_path=tasks_file,
        vocab_path=vocab_file,
    )

    content = vocab_file.read_text()
    # elit specifically must not be duplicated
    elit_bullets = [ln for ln in content.splitlines() if 'token="elit"' in ln]
    assert len(elit_bullets) == 1, (
        f"elit bullet duplicated — expected 1, got {len(elit_bullets)}:\n{content}"
    )


# ---------------------------------------------------------------------------
# AC3 — daemon-stopped edge case (observations still write)
# ---------------------------------------------------------------------------

def test_observe_turn_succeeds_when_recall_unavailable(tmp_path: Path) -> None:
    """observe_turn must not raise even when the recall daemon is unreachable.

    This exercises the edge-case AC: observations queue in markdown and the
    turn completes without errors visible to Tori.
    """
    from services.pattern_watcher import observe_turn

    tasks_file = tmp_path / "tasks.md"
    vocab_file = tmp_path / "vocab.md"

    # Should not raise regardless of recall daemon state
    observe_turn(
        user_message="do this later, also nvrfgt the rule",
        assistant_reply="Noted.",
        tasks_path=tasks_file,
        vocab_path=vocab_file,
        _recall_available=False,  # simulate daemon down
    )

    # Observations still wrote to markdown
    assert tasks_file.exists() or vocab_file.exists(), (
        "Neither observation file was created when recall was unavailable"
    )


# ---------------------------------------------------------------------------
# AC3 — recall source config
# ---------------------------------------------------------------------------

def test_observations_source_in_recall_config() -> None:
    """The ostk-recall config file must contain an observations markdown source."""
    config_path = Path.home() / ".config" / "ostk-recall" / "config.toml"
    assert config_path.exists(), f"ostk-recall config not found at {config_path}"

    content = config_path.read_text()
    assert "observations" in content, (
        "ostk-recall config.toml does not mention 'observations' source"
    )
    assert 'project = "observations"' in content, (
        "No [[sources]] block with project = \"observations\" found"
    )


# ---------------------------------------------------------------------------
# AC5 — tier store + confirmed-cluster inline hint (→2484)
# ---------------------------------------------------------------------------

def test_store_tier_persists_confirm_with_label(tmp_path: Path) -> None:
    """store_tier writes tier + label to the tiers file on confirm."""
    from services.pattern_watcher import store_tier, load_tiers

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You frequently defer tasks", kind="task:defer", tiers_path=tiers_file)
    tiers = load_tiers(tiers_path=tiers_file)

    assert "abc" in tiers
    assert tiers["abc"]["tier"] == 2
    assert tiers["abc"]["label"] == "You frequently defer tasks"
    assert tiers["abc"]["kind"] == "task:defer"


def test_store_tier_dismiss_removes_entry(tmp_path: Path) -> None:
    """store_tier with tier=0 (dismiss) removes the cluster entry from the file."""
    from services.pattern_watcher import store_tier, load_tiers

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You frequently defer tasks", tiers_path=tiers_file)
    store_tier("abc", 0, tiers_path=tiers_file)
    tiers = load_tiers(tiers_path=tiers_file)

    assert "abc" not in tiers


def test_load_tiers_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """load_tiers returns {} when the tiers file does not exist."""
    from services.pattern_watcher import load_tiers

    result = load_tiers(tiers_path=tmp_path / "nonexistent.json")
    assert result == {}


def test_get_confirmed_hint_returns_hint_for_tier2_cluster(tmp_path: Path) -> None:
    """get_confirmed_hint returns a hint string containing the cluster label."""
    from services.pattern_watcher import store_tier, get_confirmed_hint

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You frequently defer tasks", kind="task:defer", tiers_path=tiers_file)
    hint = get_confirmed_hint(tiers_path=tiers_file)

    assert hint is not None
    assert "You frequently defer tasks" in hint
    assert "WHAT MYOS HAS LEARNED" in hint


def test_get_confirmed_hint_returns_none_when_no_confirmed_clusters(tmp_path: Path) -> None:
    """get_confirmed_hint returns None when no clusters are tier >= 2."""
    from services.pattern_watcher import get_confirmed_hint

    hint = get_confirmed_hint(tiers_path=tmp_path / "empty.json")
    assert hint is None


def test_get_confirmed_hint_ignores_dismissed_clusters(tmp_path: Path) -> None:
    """get_confirmed_hint skips clusters that were dismissed (tier=0 removes them)."""
    from services.pattern_watcher import store_tier, get_confirmed_hint

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You defer tasks", tiers_path=tiers_file)
    store_tier("abc", 0, tiers_path=tiers_file)
    hint = get_confirmed_hint(tiers_path=tiers_file)

    assert hint is None


def test_read_context_falls_back_to_confirmed_hint_when_recall_empty(tmp_path: Path) -> None:
    """read_context_for_turn returns confirmed-cluster hint when recall returns nothing."""
    from services.pattern_watcher import store_tier, read_context_for_turn

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You frequently defer tasks", tiers_path=tiers_file)

    with patch("services.pattern_watcher._call_recall_fault", return_value=None):
        result = read_context_for_turn("hey", tiers_path=tiers_file)

    assert result is not None
    assert "You frequently defer tasks" in result


def test_read_context_prefers_recall_result_over_hint(tmp_path: Path) -> None:
    """read_context_for_turn returns recall content when recall succeeds, not the hint."""
    from services.pattern_watcher import store_tier, read_context_for_turn

    tiers_file = tmp_path / ".tiers.json"
    store_tier("abc", 2, label="You defer tasks", tiers_path=tiers_file)

    with patch("services.pattern_watcher._call_recall_fault", return_value="- recall content here"):
        result = read_context_for_turn("hey", tiers_path=tiers_file)

    assert result is not None
    assert "recall content here" in result
    assert "You defer tasks" not in result

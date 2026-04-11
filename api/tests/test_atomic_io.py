"""Regression tests for services.atomic_io.

These tests lock in the behavior that every store file save either
lands the full new content or leaves the original file untouched.
Before the atomic_io helper, a SIGKILL or disk-full error between
``path.write_text`` opening the file in 'w' mode (which truncates)
and finishing the buffered write would leave an empty or partial
file on disk and wipe user state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from services.atomic_io import atomic_write_json, atomic_write_text


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "file.json"
    atomic_write_text(target, "hello")
    assert target.exists()
    assert target.read_text() == "hello"


def test_atomic_write_text_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "file.json"
    target.write_text("old content")
    atomic_write_text(target, "new content")
    assert target.read_text() == "new content"


def test_atomic_write_text_leaves_no_tmp_file_on_success(tmp_path: Path) -> None:
    target = tmp_path / "file.json"
    atomic_write_text(target, "payload")
    # No .tmp sibling should remain after a successful write.
    assert not (tmp_path / "file.json.tmp").exists()
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_text_preserves_original_when_write_fails(tmp_path: Path) -> None:
    """If the write-then-rename fails partway, the original file must
    still be intact. This is the entire reason atomic_write exists.
    """
    target = tmp_path / "file.json"
    target.write_text("original")

    # Simulate a failure during os.replace by patching it to raise.
    with patch("services.atomic_io.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            atomic_write_text(target, "new content")

    # Original file must still be there, with its original content.
    assert target.exists()
    assert target.read_text() == "original"
    # The helper should have cleaned up the .tmp file on failure.
    assert not (tmp_path / "file.json.tmp").exists()


def test_atomic_write_json_serializes_with_indent(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    payload = {"name": "alice", "count": 3, "items": ["a", "b"]}
    atomic_write_json(target, payload)
    # Round-trip.
    loaded = json.loads(target.read_text())
    assert loaded == payload
    # Indent applied (pretty-printed, not single line).
    text = target.read_text()
    assert "\n" in text
    assert "  " in text


def test_atomic_write_json_handles_non_ascii(tmp_path: Path) -> None:
    """We pass ensure_ascii=False so unicode stays readable on disk."""
    target = tmp_path / "data.json"
    atomic_write_json(target, {"title": "café", "emoji": "✓"})
    text = target.read_text()
    assert "café" in text
    assert "✓" in text


def test_atomic_write_text_truncates_longer_previous_content(tmp_path: Path) -> None:
    """Writing shorter content must not leave stale tail bytes from the
    old file. This would be a real risk with ``open(path, 'r+')`` but
    os.replace guarantees the new inode is a fresh file.
    """
    target = tmp_path / "file.txt"
    target.write_text("this is a long original line" * 10)
    atomic_write_text(target, "short")
    assert target.read_text() == "short"

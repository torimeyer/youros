"""Tests for the token_metrics chat-turn recorder.

These lock in the contract that wires myOS chat token usage into ostk
self-measurement: every recorded turn must land in
``<project>/.ostk/metrics.jsonl`` as a ``chat_turn`` event with the
fields the Rust ``ostk metrics`` aggregator reads. If this contract
breaks, ``ostk metrics`` will silently report zero again, which is
exactly the failure mode that prompted this work.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from services import token_metrics


@pytest.fixture
def metrics_file(tmp_path: Path) -> Path:
    """Redirect token_metrics writes into a tmp metrics.jsonl."""
    sf = tmp_path / ".ostk" / "metrics.jsonl"
    with patch.object(token_metrics, "_METRICS_PATH", sf):
        yield sf


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_record_chat_turn_writes_event(metrics_file: Path):
    token_metrics.record_chat_turn(
        model="claude-sonnet-4-20250514",
        input_tokens=1000,
        output_tokens=200,
        has_ostk_boot=True,
        boot_context_bytes=800,
        backend="anthropic_api",
    )
    events = _read_events(metrics_file)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "chat_turn"
    assert e["model"] == "claude-sonnet-4-20250514"
    assert e["input_tokens"] == 1000
    assert e["output_tokens"] == 200
    assert e["has_ostk_boot"] is True
    assert e["boot_context_bytes"] == 800
    assert e["backend"] == "anthropic_api"
    assert "ts" in e and e["ts"].endswith("Z")


def test_record_chat_turn_appends(metrics_file: Path):
    for i in range(3):
        token_metrics.record_chat_turn(
            model="claude-sonnet-4-20250514",
            input_tokens=100 + i,
            output_tokens=50,
            has_ostk_boot=(i % 2 == 0),
        )
    events = _read_events(metrics_file)
    assert len(events) == 3
    assert [e["input_tokens"] for e in events] == [100, 101, 102]
    assert [e["has_ostk_boot"] for e in events] == [True, False, True]


def test_record_chat_turn_skips_zero_usage(metrics_file: Path):
    """If both token counts are zero (failed/empty turn), do not pollute the file."""
    token_metrics.record_chat_turn(
        model="claude-sonnet-4-20250514",
        input_tokens=0,
        output_tokens=0,
        has_ostk_boot=True,
    )
    assert _read_events(metrics_file) == []


def test_safe_record_swallows_exceptions(metrics_file: Path):
    """A broken recorder must never raise into the chat handler."""
    with patch.object(token_metrics, "record_chat_turn", side_effect=RuntimeError("boom")):
        # No assertion needed beyond "this does not raise"
        token_metrics.safe_record_chat_turn(
            model="x",
            input_tokens=1,
            output_tokens=1,
            has_ostk_boot=False,
        )


def test_metrics_file_lives_under_project_dot_ostk():
    """The recorder must write to the same path the Rust `ostk metrics` reads.

    If anyone moves _METRICS_PATH, ostk metrics will stop seeing chat data.
    The Rust read path is ``<project>/.ostk/metrics.jsonl``.
    """
    p = token_metrics._METRICS_PATH
    assert p.name == "metrics.jsonl"
    assert p.parent.name == ".ostk"

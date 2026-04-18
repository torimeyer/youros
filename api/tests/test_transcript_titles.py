"""Tests for transcript title derivation: mailbox boilerplate skipping."""

import json
import tempfile
from pathlib import Path

import pytest

from routers.transcripts import (
    _skip_mailbox_boilerplate,
    _extract_first_user_message,
    _derive_title,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, messages: list[dict]) -> None:
    """Write a list of JSONL message entries to path."""
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def _user_entry(text: str) -> dict:
    return {
        "type": "user",
        "message": {"content": text},
        "timestamp": "2026-04-14T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# _skip_mailbox_boilerplate
# ---------------------------------------------------------------------------

MAILBOX_BLOCK = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
Before doing ANY work, register yourself:
   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`

### Heartbeat (every 60 seconds, CRITICAL for long tasks)
POST heartbeat before and after every long-running tool call.

## Your actual task
Build the onboarding wizard polish and fix the sidebar nav link.
"""


class TestSkipMailboxBoilerplate:
    def test_returns_text_unchanged_when_no_boilerplate(self):
        text = "Fix the onboarding wizard layout."
        assert _skip_mailbox_boilerplate(text) == text

    def test_skips_registration_block_and_returns_task_line(self):
        result = _skip_mailbox_boilerplate(MAILBOX_BLOCK)
        assert "Build the onboarding wizard" in result
        assert "register" not in result.lower()

    def test_empty_input_returns_empty(self):
        assert _skip_mailbox_boilerplate("") == ""

    def test_entirely_boilerplate_returns_empty(self):
        boilerplate_only = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
`curl -X POST /api/agents/register`

### Heartbeat
POST every 60 seconds.
"""
        result = _skip_mailbox_boilerplate(boilerplate_only)
        assert result == ""

    def test_prose_line_after_block_is_returned(self):
        text = (
            "## Agent registration and mailbox\n"
            "curl curl curl\n"
            "\n"
            "Please add a dog walk reminder feature.\n"
        )
        result = _skip_mailbox_boilerplate(text)
        assert result == "Please add a dog walk reminder feature."


# ---------------------------------------------------------------------------
# _extract_first_user_message
# ---------------------------------------------------------------------------

class TestExtractFirstUserMessage:
    def test_skips_mailbox_block_and_returns_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(MAILBOX_BLOCK)])
            result = _extract_first_user_message(p)
        assert "Build the onboarding wizard" in result
        assert "## Agent registration" not in result

    def test_plain_message_returned_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry("Implement the cost tracking page.")])
            result = _extract_first_user_message(p)
        assert result == "Implement the cost tracking page."

    def test_returns_empty_when_all_boilerplate(self):
        boilerplate = (
            "## Agent registration and mailbox\n"
            "`curl -X POST /api/agents/register`\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(boilerplate)])
            result = _extract_first_user_message(p)
        assert result == ""

    def test_falls_through_to_second_message_if_first_is_boilerplate(self):
        boilerplate = (
            "## Agent registration and mailbox\n"
            "`curl -X POST /api/agents/register`\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [
                _user_entry(boilerplate),
                _user_entry("Wire up the sidebar collapse button."),
            ])
            result = _extract_first_user_message(p)
        assert result == "Wire up the sidebar collapse button."

    def test_caps_at_200_chars(self):
        long_text = "A" * 300
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(long_text)])
            result = _extract_first_user_message(p)
        assert len(result) == 200


# ---------------------------------------------------------------------------
# _derive_title
# ---------------------------------------------------------------------------

class TestDeriveTitle:
    def test_title_from_real_task_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(MAILBOX_BLOCK)])
            title = _derive_title(p)
        assert "Build the onboarding wizard" in title

    def test_title_capped_at_60_chars(self):
        long_msg = "Fix the " + "very " * 20 + "broken thing."
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(long_msg)])
            title = _derive_title(p)
        assert len(title) <= 60

    def test_falls_back_to_spawn_task(self):
        boilerplate = (
            "## Agent registration and mailbox\n"
            "`curl -X POST /api/agents/register`\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(boilerplate)])
            title = _derive_title(p, spawn_task="Fix the cost tracker page")
        assert title == "Fix the cost tracker page"

    def test_no_title_when_boilerplate_and_no_spawn_task(self):
        boilerplate = (
            "## Agent registration and mailbox\n"
            "`curl -X POST /api/agents/register`\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(boilerplate)])
            title = _derive_title(p)
        assert title == ""

    def test_does_not_show_agent_registration_as_title(self):
        """The title must never start with 'Agent registration'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "session.jsonl"
            _write_jsonl(p, [_user_entry(MAILBOX_BLOCK)])
            title = _derive_title(p)
        assert not title.lower().startswith("agent registration")

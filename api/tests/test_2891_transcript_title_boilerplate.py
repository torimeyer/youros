"""→2891: agent transcript titles showed the instruction sheet, not the task.

Nearly every agent session on the Transcripts page was titled
"Never wait for a response from the user after posting. Resum" (or an
older "from Tori" wording). Two holes in ``_skip_mailbox_boilerplate``:

- task prose placed BEFORE the "## Agent registration" heading (how the
  orchestrator writes every brief) was never considered, and
- when no second "## " heading followed the block, the fallback keyword
  scan hunted for the first "unrecognized" line INSIDE the mailbox block,
  and the generated "Never wait for a response…" sentence passed every
  filter and became the derived title and first_message preview.

Second leak from the same keyword-hunting design in two days (→2888),
so the fix is structural. Covered here:

- task prose ahead of the mailbox block always wins
- the generated mailbox block is wrapped in ``<!-- mailbox:begin -->`` /
  ``<!-- mailbox:end -->`` sentinels and sentinel regions are excised
  wholesale before any other title logic runs, so future wording changes
  can never leak, no matter what they say
- old sentinel-free transcripts whose first message is pure mailbox
  block yield "" so extraction advances to the next real user message
  (or the spawn_task fallback)
- the backfill endpoint clears cached "Never wait…" titles and
  empty-string sentinel entries so both regenerate with fixed extraction
"""

import json

import pytest

import routers.transcripts as transcripts
from routers.transcripts import (
    _derive_title,
    _extract_first_user_message,
    _skip_mailbox_boilerplate,
)


TASK_PROSE = "Diagnose the empty Activity tabs and fix them."

NEVER_WAIT_USER = (
    "Never wait for a response from the user after posting. Resume "
    "your task immediately. They want the answer now, not after they reply."
)
NEVER_WAIT_TORI = (
    "Never wait for a response from Tori after posting. Resume "
    "your task immediately."
)


def _old_style_mailbox_block(never_wait_line: str) -> str:
    """A realistic pre-sentinel mailbox block.

    No sentinels, no second "## " heading, and it ends with the exact
    instruction lines that used to leak out of the fallback scan.
    """
    return (
        "## Agent registration and mailbox (mandatory)\n"
        "\n"
        "### Step 0: Register immediately\n"
        "Before doing ANY work, register yourself so the user can see you "
        "in the Agents page:\n"
        "   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`\n"
        "\n"
        "### Mailbox checking (adaptive: 10s to 60s)\n"
        "\n"
        "The user may send you follow up instructions while you work via "
        "the Agents page in yourOS.\n"
        "\n"
        "1. On each cycle, call:\n"
        "   `curl --connect-timeout 3 -m 35 -sSk "
        "\"https://127.0.0.1:8000/api/agents/x/nudges?wait=30\"`\n"
        "2. Compare the timestamps to the last batch you handled.\n"
        "   d. Post the answer via /reply BEFORE resuming work:\n"
        "   Post another /reply when the work the nudge asked about is done.\n"
        f"   {never_wait_line}\n"
    )


def _user_entry(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"content": text},
        "timestamp": "2026-07-13T10:00:00Z",
    })


# ---------------------------------------------------------------------------
# Task prose placed before the mailbox block always wins
# ---------------------------------------------------------------------------


class TestLeadingTaskProseWins:
    def test_prose_before_registration_heading_is_returned(self):
        text = TASK_PROSE + "\n\n" + _old_style_mailbox_block(NEVER_WAIT_USER)
        assert _skip_mailbox_boilerplate(text) == TASK_PROSE

    def test_derived_title_uses_leading_prose_not_the_never_wait_line(self, tmp_path):
        jsonl = tmp_path / "sess-leading.jsonl"
        jsonl.write_text(_user_entry(
            TASK_PROSE + "\n\n" + _old_style_mailbox_block(NEVER_WAIT_USER)
        ) + "\n")
        title = _derive_title(jsonl)
        assert title == TASK_PROSE
        assert "never wait" not in title.lower()


# ---------------------------------------------------------------------------
# Old-style transcripts: pure mailbox block, no sentinels, no second heading
# ---------------------------------------------------------------------------


class TestOldStyleBlockOnly:
    @pytest.mark.parametrize("never_wait", [NEVER_WAIT_USER, NEVER_WAIT_TORI])
    def test_pure_block_yields_empty_string(self, never_wait):
        """No line inside the block qualifies as task prose, so the skipper
        must return "" and let callers advance to the next user message."""
        result = _skip_mailbox_boilerplate(_old_style_mailbox_block(never_wait))
        assert result == ""

    def test_extraction_falls_through_to_the_next_real_message(self, tmp_path):
        jsonl = tmp_path / "sess-fallthrough.jsonl"
        jsonl.write_text(
            _user_entry(_old_style_mailbox_block(NEVER_WAIT_USER)) + "\n"
            + _user_entry("Fix the sidebar collapse button.") + "\n"
        )
        assert _extract_first_user_message(jsonl) == "Fix the sidebar collapse button."
        title = _derive_title(jsonl)
        assert title == "Fix the sidebar collapse button."
        assert "never wait" not in title.lower()

    def test_block_only_transcript_falls_back_to_spawn_task(self, tmp_path):
        jsonl = tmp_path / "sess-spawntask.jsonl"
        jsonl.write_text(_user_entry(_old_style_mailbox_block(NEVER_WAIT_TORI)) + "\n")
        title = _derive_title(jsonl, spawn_task="fix the agent transcript titles")
        assert title == "fix the agent transcript titles"


# ---------------------------------------------------------------------------
# Sentinel-wrapped blocks are excised wholesale, regardless of wording
# ---------------------------------------------------------------------------


SENTINEL_BLOCK = (
    "<!-- mailbox:begin -->\n"
    "## Bootstrap (do this before ANYTHING else)\n"
    "Never wait for a response from the user after posting.\n"
    "Entirely new wording with no known keywords whatsoever leaks nothing.\n"
    "<!-- mailbox:end -->"
)


class TestSentinelExcision:
    def test_prose_before_sentinel_region_wins(self):
        text = TASK_PROSE + "\n\n" + SENTINEL_BLOCK
        assert _skip_mailbox_boilerplate(text) == TASK_PROSE

    def test_prose_after_sentinel_region_wins(self):
        # Backend spawn layout: block on top, separator, then the brief.
        text = SENTINEL_BLOCK + "\n\n---\n\n" + TASK_PROSE
        assert _skip_mailbox_boilerplate(text) == TASK_PROSE

    def test_sentinel_only_message_yields_empty_string(self):
        assert _skip_mailbox_boilerplate(SENTINEL_BLOCK) == ""

    def test_unterminated_sentinel_region_is_dropped(self):
        # A truncated capture (begin marker, no end marker) must not leak.
        text = TASK_PROSE + "\n\n<!-- mailbox:begin -->\nTruncated capture, no end marker."
        assert _skip_mailbox_boilerplate(text) == TASK_PROSE

    def test_real_generated_block_is_wrapped_and_excised(self):
        """The actual builder output must carry the sentinels, and both
        real-world prompt layouts must title from the task prose."""
        from routers.agents import agent_mailbox_instruction

        block = agent_mailbox_instruction("agent-2891")
        lines = block.splitlines()
        assert lines[0].strip() == "<!-- mailbox:begin -->"
        assert lines[-1].strip() == "<!-- mailbox:end -->"
        # Orchestrator layout: task prose first, block appended.
        assert _skip_mailbox_boilerplate(TASK_PROSE + "\n\n" + block) == TASK_PROSE
        # Backend spawn layout: block first, brief after a separator.
        assert _skip_mailbox_boilerplate(block + "\n\n---\n\n" + TASK_PROSE) == TASK_PROSE


# ---------------------------------------------------------------------------
# Backfill endpoint clears cached never-wait titles and empty sentinels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_clears_never_wait_titles_and_empty_sentinels(
    client, tmp_path, monkeypatch
):
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-nw": "Never wait for a response from the user after posting. Resum",
        "sess-nw-tori": "Never wait for a response from Tori after posting. Resume yo",
        "sess-empty": "",
        "sess-good": "Fix sidebar collapse button",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 3
    cache = json.loads(cache_file.read_text())
    assert set(cache) == {"sess-good"}
    assert cache["sess-good"] == "Fix sidebar collapse button"

"""→2894: three residual junk-title families after the 2891/2892 fixes.

After the 2891 and 2892 fixes the four measured junk families dropped to zero
out of 528 transcripts.  A long tail remained, measured 2026-07-13:

Family 5 (76 transcripts): a template rule sentence that appears inside spawn
briefs before the instruction block.  Example: "Exiting after analysis only,
no commit, no task."  The title logic treated it as task prose because it sat
before the mailbox heading and passed every existing filter.

Family 6 (37 transcripts): bare speaker label "Assistant:" left after template
stripping.  When a message reduces to just the label after boilerplate removal
it is never meaningful task prose.

Family 7 (unknown): AI-generated cached titles that paraphrase the old
instruction sheet -- variants of "State Sharing and Progress Signaling
Primitives" produced while the title writer was still fed junk context.

Fixes:
  - Family 5: add "exiting after analysis only" to _MAILBOX_BOILERPLATE_RE
    so _is_meaningful_prose rejects the sentence when scanning for prose
    before the mailbox block.
  - Family 6: _extract_first_user_message skips messages whose stripped
    content is just a bare speaker label.
  - Family 7: _looks_like_instruction_paraphrase detects stale cached
    titles; _generate_titles_background and the backfill endpoint use it
    to clear and regenerate them.
"""

import json

import pytest

import routers.transcripts as transcripts
from routers.transcripts import (
    _derive_title,
    _extract_context,
    _extract_first_user_message,
    _looks_like_instruction_paraphrase,
    _skip_mailbox_boilerplate,
)


def _user_entry(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"content": text},
        "timestamp": "2026-07-13T10:00:00Z",
    })


# A realistic legacy mailbox block (no sentinels) for use in multi-message
# fixtures — same shape used in test_2892_old_transcript_title_families.py.
# Includes the Atlassian terminator paragraph that real legacy blocks always
# carry; the "/api/atlassian" keyword pushes last_boilerplate past all
# in-block prose so the fallback scan returns nothing.
_LEGACY_BLOCK = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
Before doing ANY work, register yourself so the user can see you in the Agents page:
   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`

### Coordination primitives

Use these primitives to share state, signal progress, and leave a trail.

### Finishing your work (mandatory)

When you finish the work you were asked to do, you MUST mark yourself complete so the Agents page stops showing you as active. This is not optional. Do this as the very last step, after any final reply:
   `curl --connect-timeout 3 -m 5 -sSk -X POST https://127.0.0.1:8000/api/agents/x/complete -H 'Content-Type: application/json' -d '{"summary": "<one line summary>"}'`
Without this call the agent row stays in the running state forever even though you exited.

Atlassian (Jira and Confluence) is connected through yourOS. Server endpoints are available at /api/atlassian/jira/issue/{key} (GET for ticket detail) and /api/atlassian/confluence/page/{id} (GET). Skip if /api/atlassian/status returns connected=false.
"""

# ---------------------------------------------------------------------------
# Family 5: brief template rule sentence "Exiting after analysis only…"
# ---------------------------------------------------------------------------


class TestFamily5BriefRuleSentence:
    """The sentence sits before the mailbox block, passes all previous
    filters, and used to win as the first meaningful prose line."""

    # The minimal layout that triggers the bug: rule sentence, then mailbox.
    BRIEF_WITH_RULE_THEN_BLOCK = (
        "Exiting after analysis only, no commit, no task.\n\n"
        "---\n\n"
        + _LEGACY_BLOCK
    )

    def test_rule_sentence_rejected_by_is_meaningful_prose(self):
        """The boilerplate belt must now reject the sentence."""
        from routers.transcripts import _is_meaningful_prose, _MAILBOX_BOILERPLATE_RE
        sentence = "Exiting after analysis only, no commit, no task."
        assert _MAILBOX_BOILERPLATE_RE.search(sentence) is not None
        assert _is_meaningful_prose(sentence) is False

    def test_skip_mailbox_returns_empty_for_rule_only_before_block(self):
        """When the only pre-block prose is the rule sentence, the function
        must return '' so the caller advances to the next message."""
        result = _skip_mailbox_boilerplate(self.BRIEF_WITH_RULE_THEN_BLOCK)
        assert result == ""

    def test_extraction_advances_to_next_real_message(self, tmp_path):
        """_extract_first_user_message must skip the rule-sentence message
        and use the next real message."""
        jsonl = tmp_path / "sess-family5.jsonl"
        jsonl.write_text(
            _user_entry(self.BRIEF_WITH_RULE_THEN_BLOCK) + "\n"
            + _user_entry("Add dark-mode toggle to the settings panel.") + "\n"
        )
        result = _extract_first_user_message(jsonl)
        assert result == "Add dark-mode toggle to the settings panel."
        assert "exiting" not in result.lower()

    def test_derive_title_never_starts_with_rule_sentence(self, tmp_path):
        jsonl = tmp_path / "sess-family5-title.jsonl"
        jsonl.write_text(
            _user_entry(self.BRIEF_WITH_RULE_THEN_BLOCK) + "\n"
            + _user_entry("Add dark-mode toggle to the settings panel.") + "\n"
        )
        title = _derive_title(jsonl)
        assert title == "Add dark-mode toggle to the settings panel."
        assert "exiting" not in title.lower()

    def test_rule_then_real_task_same_message(self):
        """When the rule sentence and the real task are in the same message
        (real task on the next line, no mailbox block), the rule sentence
        must not shadow the task text."""
        text = (
            "Exiting after analysis only, no commit, no task.\n\n"
            "Analyse the failing payment-confirmation tests and report findings."
        )
        result = _skip_mailbox_boilerplate(text)
        assert "Analyse the failing" in result
        assert "exiting" not in result.lower()

    def test_extract_context_skips_rule_sentence(self, tmp_path):
        """The context passed to the AI generator must not include the rule
        sentence — it would produce a paraphrase title."""
        jsonl = tmp_path / "sess-family5-ctx.jsonl"
        jsonl.write_text(
            _user_entry(self.BRIEF_WITH_RULE_THEN_BLOCK) + "\n"
            + _user_entry("Add dark-mode toggle to the settings panel.") + "\n"
        )
        ctx = _extract_context(jsonl)
        assert "exiting" not in ctx.lower()
        assert "Add dark-mode toggle" in ctx

    def test_only_rule_sentence_falls_back_to_spawn_task(self, tmp_path):
        """If the transcript only has the rule-sentence message, derive falls
        back to the spawn_task field rather than the rule sentence."""
        jsonl = tmp_path / "sess-family5-only.jsonl"
        jsonl.write_text(_user_entry(self.BRIEF_WITH_RULE_THEN_BLOCK) + "\n")
        assert _extract_first_user_message(jsonl) == ""
        title = _derive_title(jsonl, spawn_task="analyse payment tests")
        assert title == "analyse payment tests"


# ---------------------------------------------------------------------------
# Family 6: bare speaker label "Assistant:"
# ---------------------------------------------------------------------------


class TestFamily6BareSpeakerLabel:
    """After template stripping the message reduces to just "Assistant:" (or
    a similar bare label).  That is never meaningful task prose."""

    BARE_LABELS = [
        "Assistant:",
        "assistant:",
        "ASSISTANT:",
        "User:",
        "user:",
        "Human:",
        "AI:",
        "Claude:",
        # with trailing whitespace
        "Assistant: ",
        "assistant:  ",
    ]

    @pytest.mark.parametrize("label", BARE_LABELS)
    def test_bare_label_skipped_in_extraction(self, tmp_path, label):
        jsonl = tmp_path / f"sess-label-{label[:3]}.jsonl"
        jsonl.write_text(
            _user_entry(label) + "\n"
            + _user_entry("Fix the flaky checkout flow test.") + "\n"
        )
        result = _extract_first_user_message(jsonl)
        assert result == "Fix the flaky checkout flow test."

    def test_bare_assistant_alone_yields_empty(self, tmp_path):
        """A transcript that is ONLY the bare label should yield empty."""
        jsonl = tmp_path / "sess-bare-only.jsonl"
        jsonl.write_text(_user_entry("Assistant:") + "\n")
        assert _extract_first_user_message(jsonl) == ""

    def test_derive_title_falls_through_on_bare_label(self, tmp_path):
        jsonl = tmp_path / "sess-bare-derive.jsonl"
        jsonl.write_text(
            _user_entry("Assistant:") + "\n"
            + _user_entry("Fix the flaky checkout flow test.") + "\n"
        )
        title = _derive_title(jsonl)
        assert title == "Fix the flaky checkout flow test."

    def test_assistant_with_content_is_kept(self, tmp_path):
        """A message starting "Assistant: <real content>" is NOT a bare label
        and must NOT be skipped."""
        jsonl = tmp_path / "sess-assistant-content.jsonl"
        jsonl.write_text(
            _user_entry("Assistant: Here is the analysis you asked for.") + "\n"
        )
        result = _extract_first_user_message(jsonl)
        assert "analysis" in result

    def test_bare_label_before_mailbox_block(self, tmp_path):
        """Bare label + mailbox block in the same message — the whole
        message must be skipped."""
        text = "Assistant:\n\n" + _LEGACY_BLOCK
        jsonl = tmp_path / "sess-bare-block.jsonl"
        jsonl.write_text(
            _user_entry(text) + "\n"
            + _user_entry("Refactor the auth token refresh logic.") + "\n"
        )
        result = _extract_first_user_message(jsonl)
        assert result == "Refactor the auth token refresh logic."


# ---------------------------------------------------------------------------
# Family 7: AI-generated paraphrase of the old instruction sheet
# ---------------------------------------------------------------------------


class TestFamily7InstructionParaphrase:
    PARAPHRASE_TITLES = [
        # Exact phrasing from the task description
        "State Sharing and Progress Signaling Primitives",
        # Common AI-generated variants
        "Progress Signaling and State Sharing",
        "Coordination Primitives and State Sharing",
        "Signaling Primitives for Agent Coordination",
        "State Sharing Progress Signaling Agent Setup",
        # Only the paraphrase fragment is enough
        "Progress Signaling in Agent Briefings",
        "Using Coordination Primitives Effectively",
    ]

    GOOD_TITLES = [
        "Fix Sidebar Collapse Button",
        "Add Dark Mode Toggle to Settings",
        "Refactor Auth Token Refresh Logic",
        "ostk boot",
        "Diagnose Flaky Payment Tests",
        # Edge: "signal" and "share" appear but not as the distinctive combo
        "Share the test signal across pipelines",
    ]

    @pytest.mark.parametrize("title", PARAPHRASE_TITLES)
    def test_paraphrase_detected(self, title):
        assert _looks_like_instruction_paraphrase(title) is True

    @pytest.mark.parametrize("title", GOOD_TITLES)
    def test_good_title_not_detected_as_paraphrase(self, title):
        assert _looks_like_instruction_paraphrase(title) is False

    def test_empty_string_not_paraphrase(self):
        assert _looks_like_instruction_paraphrase("") is False

    @pytest.mark.asyncio
    async def test_background_generator_treats_paraphrase_as_junk(
        self, tmp_path, monkeypatch
    ):
        """_generate_titles_background must clear a cached paraphrase title
        and attempt to regenerate it from fresh context."""
        from routers.transcripts import _generate_titles_background

        cache_file = tmp_path / "titles.json"
        stale = "State Sharing and Progress Signaling Primitives"
        cache_file.write_text(json.dumps({"sess-paraphrase": stale}))
        monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)

        # Provide a transcript with no meaningful context so regeneration
        # produces nothing (simulates a transcript whose context was all
        # boilerplate that is now properly stripped).
        jsonl = tmp_path / "sess-paraphrase.jsonl"
        jsonl.write_text(_user_entry("Assistant:") + "\n")

        await _generate_titles_background([("sess-paraphrase", jsonl)])

        cache = json.loads(cache_file.read_text())
        # The stale paraphrase must have been replaced — either by a real
        # title (if context was rich enough) or by the empty sentinel.
        assert cache.get("sess-paraphrase") != stale


# ---------------------------------------------------------------------------
# Backfill endpoint clears all three new families
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_clears_all_three_new_families(client, tmp_path, monkeypatch):
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        # Family 5: brief rule sentence
        "sess-rule": "Exiting after analysis only, no commit, no task.",
        # Family 6: bare speaker label
        "sess-label": "Assistant:",
        # Family 7: instruction paraphrase (multiple variants)
        "sess-paraphrase1": "State Sharing and Progress Signaling Primitives",
        "sess-paraphrase2": "Coordination Primitives and State Sharing",
        "sess-paraphrase3": "Progress Signaling in Agent Sessions",
        # Existing families still cleared
        "sess-old-primitives": "Use these primitives to share state, signal progress, and le",
        "sess-old-worktree": "[WORKTREE CWD →2503] Your git worktree is: /Users/torimeyer/",
        # Good titles must survive
        "sess-good1": "Fix Sidebar Collapse Button",
        "sess-good2": "ostk boot",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)

    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["removed"] == 7

    cache = json.loads(cache_file.read_text())
    assert set(cache.keys()) == {"sess-good1", "sess-good2"}
    assert cache["sess-good1"] == "Fix Sidebar Collapse Button"
    assert cache["sess-good2"] == "ostk boot"


@pytest.mark.asyncio
async def test_backfill_idempotent_on_clean_cache(client, tmp_path, monkeypatch):
    """Calling backfill twice in a row must not remove extra entries."""
    cache_file = tmp_path / "titles-clean.json"
    cache_file.write_text(json.dumps({
        "sess-a": "Fix Sidebar Collapse Button",
        "sess-b": "Add dark-mode toggle",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)

    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 0

    resp2 = await client.post("/api/transcripts/backfill-titles")
    assert resp2.status_code == 200
    assert resp2.json()["removed"] == 0


# ---------------------------------------------------------------------------
# Regression: existing 2891/2892 families still work alongside new ones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_seven_families_cleared_together(client, tmp_path, monkeypatch):
    cache_file = tmp_path / "titles-all.json"
    cache_file.write_text(json.dumps({
        # pre-2894 families (2891)
        "s1": "Never wait for a response from the user",
        # pre-2894 families (2892)
        "s2": "Use these primitives to share state",
        "s3": "[WORKTREE CWD] Your git worktree is",
        "s4": "User: You are Claude. You are having",
        "s5": "The `Bash` tool is blocked globally",
        # →2894 new families
        "s6": "Exiting after analysis only, no commit, no task.",
        "s7": "Assistant:",
        "s8": "State Sharing and Progress Signaling Primitives",
        # good survivors
        "good": "Diagnose Flaky Payment Tests",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)

    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 8

    cache = json.loads(cache_file.read_text())
    assert list(cache.keys()) == ["good"]

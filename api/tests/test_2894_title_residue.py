"""→2894 (retry): residue left by the first round-three pass, measured 2026-07-13.

The merged round-three fix (ff0a3740 + 91c546cc) filtered the single
"Exiting after analysis only" sentence, skipped label-only messages, and
cleared instruction-sheet paraphrase titles from the cache. Review against
the measured corpus found three gaps:

1. The task-bridge hook (.claude/hooks/lib/rules/isolation_bridge.sh)
   appends a whole COMPLETION REQUIREMENT paragraph to every bridged spawn
   prompt. Filtering only its "Exiting…" sentence promotes the NEXT
   prose-looking sentence of the same paragraph ("If running tests: commit
   your work BEFORE running any verify step") to the title for the same 76
   transcripts. The paragraph must be excised wholesale, and each of its
   prose-looking sentences independently rejected as boilerplate.
2. Bare speaker labels: only exact label-only messages were skipped. A
   label trailed by fewer than four words ("Assistant: ok done") is just
   as meaningless, and a label with real content after it should title
   from the content, minus the label. Named bridge speakers ("Gemini: …")
   are real conversation content and stay untouched.
3. The cache side had no plausibility rule: a cached title survived even
   when the session's freshly extracted context is empty (nothing it could
   derive from), and the old-format "[2026-06-07] Responded to user
   greeting" cluster matched no family. Backfill now drops cached titles
   whose transcript is on disk but yields no usable context, plus the
   date-prefixed family the current generator can no longer produce.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

import routers.transcripts as transcripts
from routers.transcripts import (
    _derive_title,
    _extract_context,
    _extract_first_user_message,
    _skip_mailbox_boilerplate,
)


def _user_entry(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"content": text},
        "timestamp": "2026-07-14T10:00:00Z",
    })


# The COMPLETION REQUIREMENT paragraph exactly as the task-bridge hook
# (.claude/hooks/lib/rules/isolation_bridge.sh) appends it to every bridged
# spawn prompt. Byte-faithful fixture, em-dashes included.
COMPLETION_CLAUSE = (
    "COMPLETION REQUIREMENT: Before your process exits you MUST do one of:\n"
    '  (a) git commit -m "..." (commit all file changes to your worktree branch), OR\n'
    '  (b) ostk work add "..." --priority P0 (file a task with the evidence).\n'
    "Exiting after analysis only — no commit, no task — is a failed run.\n"
    "If running tests: commit your work BEFORE running any verify step\n"
    "(e.g. git commit -m 'wip(→NNN): implementation' then pytest path/to/test.py -x -q).\n"
    "A failed verify still leaves a recoverable commit. Do not block a commit on full-suite pass."
)

# A realistic legacy mailbox block (no sentinels), same shape as the ones in
# test_2892 and test_2894_round3: ends with the Atlassian terminator
# paragraph that real legacy blocks always carry.
LEGACY_BLOCK = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
Before doing ANY work, register yourself so the user can see you in the Agents page:
   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`

### Coordination primitives

Use these primitives to share state, signal progress, and leave a trail.

### Finishing your work (mandatory)

When you finish the work you were asked to do, you MUST mark yourself complete so the Agents page stops showing you as active. This is not optional. Do this as the very last step, after any final reply:
   `curl --connect-timeout 3 -m 5 -sSk -X POST https://127.0.0.1:8000/api/agents/x/complete`
Without this call the agent row stays in the running state forever even though you exited.

Atlassian (Jira and Confluence) is connected through yourOS. Server endpoints are available at /api/atlassian/jira/issue/{key} (GET for ticket detail) and /api/atlassian/confluence/page/{id} (GET). Skip if /api/atlassian/status returns connected=false.
"""

TASK = "Diagnose the stuck settings save and add regression coverage."


# ---------------------------------------------------------------------------
# Gap 1: the whole COMPLETION REQUIREMENT paragraph, not just one sentence
# ---------------------------------------------------------------------------


class TestCompletionClauseParagraph:
    def test_clause_then_block_yields_empty(self):
        """With no real brief text, no sentence of the clause may become the
        title. The first pass only filtered "Exiting…"; the next sentence
        ("If running tests: commit your work BEFORE…") leaked instead."""
        result = _skip_mailbox_boilerplate(COMPLETION_CLAUSE + "\n\n" + LEGACY_BLOCK)
        assert result == ""

    def test_task_before_clause_still_wins(self):
        text = TASK + "\n\n" + COMPLETION_CLAUSE + "\n\n" + LEGACY_BLOCK
        assert _skip_mailbox_boilerplate(text) == TASK

    def test_block_first_layout_recovers_the_brief(self):
        """Backend spawn layout: block on top, brief after a separator, the
        clause appended by the task-bridge hook at the very end. The clause
        paragraph is template, so the brief must win. Before this fix the
        clause's non-prose lines pushed the block boundary past the brief
        and a clause sentence became the title."""
        text = LEGACY_BLOCK + "\n\n---\n\n" + TASK + "\n\n" + COMPLETION_CLAUSE
        assert _skip_mailbox_boilerplate(text) == TASK

    def test_clause_at_start_of_plain_message(self):
        """No mailbox block at all: a message that opens with the clause
        must title from the task text that follows it."""
        text = COMPLETION_CLAUSE + "\n\n" + TASK
        assert _skip_mailbox_boilerplate(text) == TASK

    @pytest.mark.parametrize("sentence", [
        "Exiting after analysis only — no commit, no task — is a failed run.",
        "If running tests: commit your work BEFORE running any verify step",
        "(e.g. git commit -m 'wip(→NNN): implementation' then pytest path/to/test.py -x -q).",
        "A failed verify still leaves a recoverable commit. Do not block a commit on full-suite pass.",
    ])
    def test_each_clause_sentence_rejected_alone(self, sentence):
        """Belt for truncated or reworded copies of the clause: every
        prose-looking sentence of it is boilerplate on its own."""
        assert _skip_mailbox_boilerplate(sentence + "\n\n" + LEGACY_BLOCK) == ""

    def test_derive_title_advances_past_clause_only_message(self, tmp_path):
        jsonl = tmp_path / "sess-clause.jsonl"
        jsonl.write_text(
            _user_entry(COMPLETION_CLAUSE + "\n\n" + LEGACY_BLOCK) + "\n"
            + _user_entry(TASK) + "\n"
        )
        title = _derive_title(jsonl)
        assert title == TASK[:60]
        assert "commit your work" not in title.lower()

    @pytest.mark.parametrize("prose", [
        "Fix the app exiting early on logout.",
        "Commit your work at the end of the session notes feature.",
        "The failed verify banner needs a retry button.",
    ])
    def test_genuine_prose_near_the_template_words_is_kept(self, prose):
        """Real briefs that merely share words with the template sentences
        must keep their titles."""
        assert _skip_mailbox_boilerplate(prose) == prose
        assert _skip_mailbox_boilerplate(prose + "\n\n" + LEGACY_BLOCK) == prose


# ---------------------------------------------------------------------------
# Gap 2: speaker labels with thin trailers, and label stripping
# ---------------------------------------------------------------------------


class TestSpeakerLabelResidue:
    @pytest.mark.parametrize("thin", [
        "Assistant: ok done",
        "User: sounds good",
        "Human: yes",
        "assistant: done.",
    ])
    def test_label_with_thin_trailer_advances(self, tmp_path, thin):
        """A label followed by fewer than four words is as meaningless as
        the bare label; extraction must advance to the next message."""
        jsonl = tmp_path / "sess-thin-label.jsonl"
        jsonl.write_text(
            _user_entry(thin) + "\n"
            + _user_entry(TASK) + "\n"
        )
        assert _extract_first_user_message(jsonl) == TASK

    def test_label_only_message_still_yields_empty(self, tmp_path):
        jsonl = tmp_path / "sess-thin-only.jsonl"
        jsonl.write_text(_user_entry("Assistant: ok done") + "\n")
        assert _extract_first_user_message(jsonl) == ""

    def test_label_with_content_titles_from_the_content(self, tmp_path):
        """A generic label with real content after it titles from the
        content, with the label stripped off."""
        jsonl = tmp_path / "sess-label-content.jsonl"
        jsonl.write_text(
            _user_entry("User: please fix the login redirect loop now") + "\n"
        )
        assert _extract_first_user_message(jsonl) == (
            "please fix the login redirect loop now"
        )

    def test_named_bridge_speaker_is_untouched(self, tmp_path):
        """Multi-AI bridge speakers ("Gemini: …") are real conversation
        content; their label is part of the title (→2892 behavior)."""
        jsonl = tmp_path / "sess-gemini.jsonl"
        jsonl.write_text(
            _user_entry("Gemini: Spaces survive every editor, tabs do not.") + "\n"
        )
        assert _extract_first_user_message(jsonl) == (
            "Gemini: Spaces survive every editor, tabs do not."
        )

    def test_context_excludes_label_residue(self, tmp_path):
        """The context fed to the AI title generator must not contain label
        residue either; it poisons the naming model (→2888 lesson)."""
        jsonl = tmp_path / "sess-label-ctx.jsonl"
        jsonl.write_text(
            _user_entry("Assistant:") + "\n"
            + _user_entry("Assistant: ok") + "\n"
            + _user_entry(TASK) + "\n"
        )
        ctx = _extract_context(jsonl)
        assert "assistant" not in ctx.lower()
        assert TASK in ctx


# ---------------------------------------------------------------------------
# Gap 3: cache side, backfill plausibility rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_drops_dated_greeting_titles(client, tmp_path, monkeypatch):
    """The "[2026-06-07] Responded to user greeting" cluster: the current
    generator writes short bare titles with no date prefix, so any cached
    "[YYYY-MM-DD] …" title dates from the old junk-context era."""
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-dated": "[2026-06-07] Responded to user greeting",
        "sess-dated2": "[2026-05-30] Discussed session setup",
        "sess-good": "Fix Sidebar Collapse Button",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 2
    cache = json.loads(cache_file.read_text())
    assert set(cache) == {"sess-good"}


@pytest.mark.asyncio
async def test_backfill_drops_title_when_fresh_context_is_empty(
    client, tmp_path, monkeypatch
):
    """A cached title whose transcript is on disk but now extracts to
    nothing cannot derive from anything: it was generated from junk
    context and must be dropped so the row falls back cleanly."""
    projects = tmp_path / "projects"
    proj = projects / "-test-proj"
    proj.mkdir(parents=True)
    (proj / "sess-stale.jsonl").write_text(_user_entry(LEGACY_BLOCK) + "\n")
    (proj / "sess-live.jsonl").write_text(_user_entry(
        "Fix the export retry flow and add regression coverage for the timeout path."
    ) + "\n")
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-stale": "Improve Agent Session Handling",
        "sess-live": "Fix export retry flow",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    monkeypatch.setattr(transcripts, "PROJECTS_DIR", projects)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    cache = json.loads(cache_file.read_text())
    assert set(cache) == {"sess-live"}
    assert cache["sess-live"] == "Fix export retry flow"


@pytest.mark.asyncio
async def test_backfill_keeps_title_when_jsonl_is_missing(
    client, tmp_path, monkeypatch
):
    """No transcript on disk means nothing to re-derive from; the cached
    title is kept rather than judged."""
    projects = tmp_path / "projects-empty"
    projects.mkdir()
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-ghost": "A Perfectly Reasonable Title",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    monkeypatch.setattr(transcripts, "PROJECTS_DIR", projects)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 0
    cache = json.loads(cache_file.read_text())
    assert cache["sess-ghost"] == "A Perfectly Reasonable Title"


@pytest.mark.asyncio
async def test_background_regen_replaces_dated_title(tmp_path, monkeypatch):
    """The background generator must treat a date-prefixed cached title as
    junk and regenerate it from fresh context."""
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-dated": "[2026-06-07] Responded to user greeting",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    jsonl = tmp_path / "sess-dated.jsonl"
    jsonl.write_text(_user_entry(
        "Diagnose the export retry loop and add regression coverage for it."
    ) + "\n")
    gen = AsyncMock(return_value="Diagnose export retry loop")
    with patch("routers.transcripts._generate_title", gen):
        await transcripts._generate_titles_background([("sess-dated", jsonl)])
    cache = json.loads(cache_file.read_text())
    assert cache["sess-dated"] == "Diagnose export retry loop"


@pytest.mark.asyncio
async def test_background_regen_dated_title_with_thin_context_gets_sentinel(
    tmp_path, monkeypatch
):
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-dated": "[2026-06-07] Responded to user greeting",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    jsonl = tmp_path / "sess-dated.jsonl"
    jsonl.write_text(_user_entry("Assistant:") + "\n")
    gen = AsyncMock(return_value="Should never be called")
    with patch("routers.transcripts._generate_title", gen):
        await transcripts._generate_titles_background([("sess-dated", jsonl)])
    cache = json.loads(cache_file.read_text())
    assert cache["sess-dated"] == ""
    gen.assert_not_awaited()

"""→2892: old transcripts still derived junk titles from boilerplate families.

After →2891 landed, 356 of 528 transcripts still showed instruction-sheet
leftovers as titles. Four families, measured against the live corpus:

1. 147x "Use these primitives to share state, signal progress, and le" --
   the sentence under "### Coordination primitives" inside legacy
   sentinel-free mailbox blocks. The old fallback scan hunted for the
   first "unrecognized" line INSIDE the block and this sentence passed
   every keyword filter.
2. 103x "[WORKTREE CWD ...]" -- a harness-injected worktree preamble
   paragraph; the real task text follows it in the same message.
3. 73x "User: You are Claude. You are having a real back and forth w" --
   the multi-AI chat-bridge template opener; the real conversation
   content follows it.
4. 27x "The `Bash` tool is blocked globally..." -- a tooling notice
   paragraph at the top of some spawn briefs; the real brief follows it.

The fix is structural. Known leading preamble paragraphs are stripped
repeatedly before any title logic runs (families 2, 3, 4). For legacy
mailbox blocks, survivor prose only counts AFTER the last line the
boilerplate belt recognizes, so a sentence sandwiched inside the block
can never become a title (family 1); block-only messages yield "" and
extraction advances to the next real message or the spawn_task fallback.
Genuine first messages like "ostk boot" are untouched, and the backfill
endpoint clears cached titles from all four families so they regenerate.
"""

import json

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
        "timestamp": "2026-07-13T10:00:00Z",
    })


# A realistic legacy (pre-sentinel) mailbox block, mirroring the builder in
# routers/agents.py: no sentinels, no second "## " heading, and it contains
# the exact standalone sentences that used to leak out of the fallback scan
# ("Use these primitives...", "Without this call the agent row stays...",
# and the trailing Atlassian paragraph).
LEGACY_BLOCK_WITH_PRIMITIVES = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
Before doing ANY work, register yourself so the user can see you in the Agents page:
   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`

### Mailbox checking (adaptive: 10s to 60s)

The user may send you follow up instructions while you work via the Agents page in yourOS.

1. On each cycle, call:
   `curl --connect-timeout 3 -m 35 -sSk "https://127.0.0.1:8000/api/agents/x/nudges?wait=30"`
2. Compare the timestamps to the last batch you handled.
   Never wait for a response from the user after posting. Resume your task immediately.

### Coordination primitives

Use these primitives to share state, signal progress, and leave a trail.

**Handoff summary** (write before /complete so a recovery agent can pick up):
   `curl --connect-timeout 3 -m 5 -sSk -X POST https://127.0.0.1:8000/api/agents/x/handoff`

**Context pages** (share large data between agents):
- Store: `mcp__ostk__context_store(name='<page>', content='...')`

### Finishing your work (mandatory)

When you finish the work you were asked to do, you MUST mark yourself complete so the Agents page stops showing you as active. This is not optional. Do this as the very last step, after any final reply:
   `curl --connect-timeout 3 -m 5 -sSk -X POST https://127.0.0.1:8000/api/agents/x/complete`
Without this call the agent row stays in the running state forever even though you exited.

Atlassian (Jira and Confluence) is connected through yourOS. Server endpoints are available at /api/atlassian/jira/issue/{key} (GET for ticket detail) and /api/atlassian/confluence/page/{id} (GET). Skip if /api/atlassian/status returns connected=false.
"""

# The harness-injected worktree header from routers/agents.py (→1240/→2503):
# one contiguous paragraph, followed by a --- rule, then the real prompt.
WORKTREE_PREAMBLE = (
    "[WORKTREE CWD →1240] Your git worktree is: "
    "/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-thing\n"
    "REQUIRED — every mcp__ostk__bash call MUST include "
    'cwd="/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-thing". '
    "Without it, bash runs in the MAIN repo and commits land on main "
    "instead of your branch.\n"
    "REQUIRED — every mcp__ostk__fs_ops call MUST use absolute paths "
    "starting with /Users/torimeyer/claude/torios/.claude/worktrees/"
    "agent-fix-thing/ (paths in this prompt are already remapped).\n"
    "[WORKTREE COMMIT →2503] Your work only counts once it is committed on "
    "your worktree branch. NEVER end your session or POST /complete while "
    "the worktree has uncommitted changes."
)

# The multi-AI chat-bridge template from services/chat_providers.py, as it
# lands in the transcript ("User: " prefixed). Round 2+: real conversation
# content sits under "Conversation so far:".
CHAT_BRIDGE_ROUND2 = (
    "User: You are Claude. You are having a real back and forth with "
    'Gemini. The user asked you both: "should tabs be spaces".\n'
    "\n"
    "Conversation so far:\n"
    "\n"
    "Gemini: Spaces survive every editor, tabs do not.\n"
    "\n"
    "Reply directly to the previous speaker. Do not prefix your reply with "
    "your own name. Do not write a fake script with labels for both sides. "
    "Do not narrate the exchange. Keep it short and conversational, two or "
    "three sentences."
)

# Round 1 first speaker: nothing but template, no conversation content yet.
CHAT_BRIDGE_ROUND1 = (
    "User: You are Claude. You are having a real back and forth with "
    'Gemini. The user asked you both: "should tabs be spaces".\n'
    "\n"
    "This is the first message in the conversation. Nothing has been said yet.\n"
    "\n"
    "Reply directly to the previous speaker. Do not prefix your reply with "
    "your own name. Keep it short and conversational, two or three "
    "sentences. You are going first, so open the conversation by sharing "
    "your own view on the topic."
)

# The tooling notice from the top of some spawn briefs (routers/agents.py).
BASH_BLOCKED_PREAMBLE = (
    "The `Bash` tool is blocked globally. `mcp__ostk__bash` is your only "
    "shell and it is deferred; calling it before ToolSearch loads it fails "
    "with InputValidationError. Skipping this step leaves you with no "
    "working shell. Load it first, then proceed."
)


# ---------------------------------------------------------------------------
# Family 1: "Use these primitives..." inside a legacy mailbox block
# ---------------------------------------------------------------------------


class TestFamily1CoordinationPrimitives:
    def test_pure_legacy_block_never_titles_from_inside(self):
        """No sentence from inside the block may survive, even ones that
        pass every keyword filter. Block-only messages must yield ""."""
        result = _skip_mailbox_boilerplate(LEGACY_BLOCK_WITH_PRIMITIVES)
        assert result == ""

    def test_derivation_falls_through_to_the_next_real_message(self, tmp_path):
        jsonl = tmp_path / "sess-family1.jsonl"
        jsonl.write_text(
            _user_entry(LEGACY_BLOCK_WITH_PRIMITIVES) + "\n"
            + _user_entry("Fix the flaky export button and add regression coverage.") + "\n"
        )
        assert _extract_first_user_message(jsonl) == (
            "Fix the flaky export button and add regression coverage."
        )
        title = _derive_title(jsonl)
        assert title.startswith("Fix the flaky export button")
        assert "use these primitives" not in title.lower()

    def test_block_only_transcript_falls_back_to_spawn_task(self, tmp_path):
        jsonl = tmp_path / "sess-family1-only.jsonl"
        jsonl.write_text(_user_entry(LEGACY_BLOCK_WITH_PRIMITIVES) + "\n")
        assert _extract_first_user_message(jsonl) == ""
        title = _derive_title(jsonl, spawn_task="fix old transcript titles")
        assert title == "fix old transcript titles"


# ---------------------------------------------------------------------------
# Family 2: "[WORKTREE CWD ...]" harness preamble
# ---------------------------------------------------------------------------


class TestFamily2WorktreePreamble:
    def test_title_comes_from_the_task_after_the_preamble(self):
        text = WORKTREE_PREAMBLE + "\n\n---\n\n" + "Fix the sidebar collapse animation jitter."
        assert _skip_mailbox_boilerplate(text) == "Fix the sidebar collapse animation jitter."

    def test_preamble_then_brief_then_legacy_block(self):
        """The full historical layout: worktree header, task prose, block.
        The task prose ahead of the block must still win (→2891)."""
        text = (
            WORKTREE_PREAMBLE + "\n\n---\n\n"
            + "Fix the sidebar collapse animation jitter.\n\n"
            + LEGACY_BLOCK_WITH_PRIMITIVES
        )
        assert _skip_mailbox_boilerplate(text) == "Fix the sidebar collapse animation jitter."

    def test_derived_title_never_starts_with_worktree_cwd(self, tmp_path):
        jsonl = tmp_path / "sess-family2.jsonl"
        jsonl.write_text(_user_entry(
            WORKTREE_PREAMBLE + "\n\n---\n\nFix the sidebar collapse animation jitter."
        ) + "\n")
        title = _derive_title(jsonl)
        assert title == "Fix the sidebar collapse animation jitter."
        assert "worktree" not in title.lower()


# ---------------------------------------------------------------------------
# Family 3: chat-bridge "User: You are Claude..." template opener
# ---------------------------------------------------------------------------


class TestFamily3ChatBridgeOpener:
    def test_round2_titles_from_conversation_content(self, tmp_path):
        jsonl = tmp_path / "sess-family3.jsonl"
        jsonl.write_text(_user_entry(CHAT_BRIDGE_ROUND2) + "\n")
        title = _derive_title(jsonl)
        assert title.startswith("Gemini: Spaces survive every editor")
        assert "you are claude" not in title.lower()

    def test_round1_template_only_falls_through_to_next_message(self, tmp_path):
        """The first-speaker prompt is pure template: opener, first-message
        notice, reply instructions. It must yield "" so extraction advances
        to the next message, which has real conversation content."""
        jsonl = tmp_path / "sess-family3-r1.jsonl"
        jsonl.write_text(
            _user_entry(CHAT_BRIDGE_ROUND1) + "\n"
            + _user_entry(CHAT_BRIDGE_ROUND2) + "\n"
        )
        title = _derive_title(jsonl)
        assert title.startswith("Gemini: Spaces survive every editor")
        assert "you are claude" not in title.lower()


# ---------------------------------------------------------------------------
# Family 4: "The `Bash` tool is blocked globally..." tooling notice
# ---------------------------------------------------------------------------


class TestFamily4BashBlockedNotice:
    def test_title_comes_from_the_brief_after_the_notice(self):
        text = BASH_BLOCKED_PREAMBLE + "\n\n" + "Diagnose the stuck settings save and fix it."
        assert _skip_mailbox_boilerplate(text) == "Diagnose the stuck settings save and fix it."

    def test_derived_title_never_starts_with_the_notice(self, tmp_path):
        jsonl = tmp_path / "sess-family4.jsonl"
        jsonl.write_text(_user_entry(
            BASH_BLOCKED_PREAMBLE + "\n\nDiagnose the stuck settings save and fix it."
        ) + "\n")
        title = _derive_title(jsonl)
        assert title == "Diagnose the stuck settings save and fix it."
        assert "bash" not in title.lower()


# ---------------------------------------------------------------------------
# Genuine first messages are untouched
# ---------------------------------------------------------------------------


def test_genuine_ostk_boot_message_keeps_its_title(tmp_path):
    jsonl = tmp_path / "sess-boot.jsonl"
    jsonl.write_text(_user_entry("ostk boot") + "\n")
    assert _derive_title(jsonl) == "ostk boot"


# ---------------------------------------------------------------------------
# _extract_context (feeds the AI title generator) uses the same path
# ---------------------------------------------------------------------------


def test_extract_context_skips_the_block_and_uses_real_messages(tmp_path):
    jsonl = tmp_path / "sess-context.jsonl"
    jsonl.write_text(
        _user_entry(LEGACY_BLOCK_WITH_PRIMITIVES) + "\n"
        + _user_entry("Fix the flaky export button and add regression coverage.") + "\n"
    )
    context = _extract_context(jsonl)
    assert "use these primitives" not in context.lower()
    assert "Fix the flaky export button" in context


# ---------------------------------------------------------------------------
# Backfill endpoint clears cached titles from all four families
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_clears_all_four_family_titles(client, tmp_path, monkeypatch):
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-primitives": "Use these primitives to share state, signal progress, and le",
        "sess-worktree": "[WORKTREE CWD →2503] Your git worktree is: /Users/torimeyer/",
        "sess-bridge": "User: You are Claude. You are having a real back and forth w",
        "sess-bash": "The `Bash` tool is blocked globally. `mcp__ostk__bash` is yo",
        "sess-good": "Fix sidebar collapse button",
        "sess-boot": "ostk boot",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 4
    cache = json.loads(cache_file.read_text())
    assert set(cache) == {"sess-good", "sess-boot"}
    assert cache["sess-good"] == "Fix sidebar collapse button"
    assert cache["sess-boot"] == "ostk boot"

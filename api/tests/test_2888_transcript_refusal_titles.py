"""→2888: the Transcripts tab was full of junk refusal-text summaries.

The transcript title generator fed the naming model "context" that was
actually leaked mailbox-instruction lines ("1. On each cycle, call:"),
the model answered with a refusal ("I don't see the actual messages…"),
and that refusal was cached verbatim as the transcript's display title.

Covered here:
- the boilerplate skipper no longer leaks numbered/lettered instruction
  lines or bold-led rule lines out of the mailbox block
- transcripts with too-thin context never reach the model at all
- refusal-looking model output is never stored as a title
- previously cached junk titles are hidden by the list endpoint,
  replaced by the background regeneration pass, and cleared by the
  backfill endpoint — all without touching the source JSONL files
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

import routers.transcripts as transcripts
from routers.transcripts import _looks_like_refusal, _skip_mailbox_boilerplate


REALISTIC_MAILBOX_BRIEF = """\
## Agent registration and mailbox (mandatory)

### Step 0: Register immediately
Before doing ANY work, register yourself so the user can see you in the Agents page:
   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register`

### Mailbox checking (adaptive: 10s to 60s)

The user may send you follow up instructions while you work via the Agents page in yourOS.

**Adaptive poll schedule**: start your poll interval at 10 seconds and double it on quiet cycles.

**Long-poll (fastest delivery)**: the endpoint supports a wait parameter and returns the instant a new message arrives.

1. On each cycle, call:
   `curl --connect-timeout 3 -m 35 -sSk "https://127.0.0.1:8000/api/agents/x/nudges?wait=30"`
2. Compare the timestamps to the last batch you handled.
3. Treat each new message as an additional instruction added to your task.
   a. Read it fully.
   b. Gather whatever context you need to answer it. Use tools if needed -- do not guess.
"""


def _user_entry(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"content": text},
        "timestamp": "2026-07-13T10:00:00Z",
    })


# ---------------------------------------------------------------------------
# Boilerplate skipper hardening
# ---------------------------------------------------------------------------


class TestMailboxBoilerplateHardening:
    def test_instruction_steps_never_leak(self):
        """The observed junk previews ("1. On each cycle, call:") came from
        instruction-list lines inside the mailbox block leaking through the
        fallback scan. An all-boilerplate brief must yield an empty string."""
        result = _skip_mailbox_boilerplate(REALISTIC_MAILBOX_BRIEF)
        assert result == ""

    def test_real_task_prose_after_block_still_returned(self):
        text = REALISTIC_MAILBOX_BRIEF + "\nDiagnose the empty Activity tabs and fix them.\n"
        assert _skip_mailbox_boilerplate(text) == "Diagnose the empty Activity tabs and fix them."


# ---------------------------------------------------------------------------
# Refusal detection
# ---------------------------------------------------------------------------


class TestRefusalDetection:
    @pytest.mark.parametrize("title", [
        "I don't see the actual messages from the coding session in y",
        "I don't have enough context from the message shown. The mess",
        "I don't see the complete context needed to summarize what wa",
        "I don't have enough context to see the full content of those",
        "I need to see the actual messages from the coding session to",
        "I cannot summarize this session",
        "I can't determine what was worked on here",
        "Unable to determine what was worked on",
        "There is no conversation content to summarize",
        "Not enough context to write a title",
    ])
    def test_refusals_detected(self, title):
        assert _looks_like_refusal(title)

    @pytest.mark.parametrize("title", [
        "Frontend Vite dev server connectivity issues diagnosis",
        "Fix sidebar collapse button",
        "Activity feed noise filtering work",
        "Improve transcript title generation",
        "Investigate messages page rendering bug",
        "",
    ])
    def test_real_titles_not_flagged(self, title):
        assert not _looks_like_refusal(title)


# ---------------------------------------------------------------------------
# Background title generation guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTitleGenerationGuards:
    async def test_refusal_output_stored_as_empty_sentinel(self, tmp_path, monkeypatch):
        """A refusal must never be cached as the display title. The empty
        sentinel is stored instead so the same doomed model call is not
        repeated on every fetch."""
        cache_file = tmp_path / "titles.json"
        monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
        jsonl = tmp_path / "sess-refusal.jsonl"
        jsonl.write_text(_user_entry(
            "Investigate the flaky login form and write regression coverage for it."
        ) + "\n")
        refusal = "I don't see the actual messages from the coding session"
        with patch("routers.transcripts._generate_title", AsyncMock(return_value=refusal)):
            await transcripts._generate_titles_background([("sess-refusal", jsonl)])
        cache = json.loads(cache_file.read_text())
        assert cache["sess-refusal"] == ""

    async def test_thin_context_never_reaches_the_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", tmp_path / "titles.json")
        jsonl = tmp_path / "sess-thin.jsonl"
        jsonl.write_text(_user_entry("1. On each cycle, call:") + "\n")
        gen = AsyncMock(return_value="Should never be called")
        with patch("routers.transcripts._generate_title", gen):
            await transcripts._generate_titles_background([("sess-thin", jsonl)])
        gen.assert_not_awaited()

    async def test_cached_junk_replaced_on_regen(self, tmp_path, monkeypatch):
        """A session whose cache entry is a refusal gets regenerated. When
        the transcript turns out to be pure boilerplate, the junk entry is
        replaced with the empty sentinel — no model call, no junk left."""
        cache_file = tmp_path / "titles.json"
        cache_file.write_text(json.dumps(
            {"sess-junk": "I don't have enough context from the message shown"}
        ))
        monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
        jsonl = tmp_path / "sess-junk.jsonl"
        jsonl.write_text(_user_entry(REALISTIC_MAILBOX_BRIEF) + "\n")
        gen = AsyncMock(return_value="whatever")
        with patch("routers.transcripts._generate_title", gen):
            await transcripts._generate_titles_background([("sess-junk", jsonl)])
        cache = json.loads(cache_file.read_text())
        assert cache["sess-junk"] == ""
        gen.assert_not_awaited()

    async def test_good_titles_still_cached(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "titles.json"
        monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
        jsonl = tmp_path / "sess-good.jsonl"
        jsonl.write_text(_user_entry(
            "Diagnose the empty Activity tabs, fix the feed, and add regression tests."
        ) + "\n")
        gen = AsyncMock(return_value="Diagnose empty Activity tabs")
        with patch("routers.transcripts._generate_title", gen):
            await transcripts._generate_titles_background([("sess-good", jsonl)])
        cache = json.loads(cache_file.read_text())
        assert cache["sess-good"] == "Diagnose empty Activity tabs"


# ---------------------------------------------------------------------------
# Read side: list endpoint hides cached junk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_hides_cached_refusal_title(client, tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-test-proj"
    project_dir.mkdir(parents=True)
    sid = "sess-refusal-title"
    (project_dir / f"{sid}.jsonl").write_text(_user_entry(
        "Diagnose the flaky onboarding test and add regression coverage."
    ) + "\n")
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps(
        {sid: "I don't see the actual messages from the coding session in y"}
    ))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    with patch("routers.transcripts.SESSIONS_DIR", sessions_dir), \
         patch("routers.transcripts.PROJECTS_DIR", tmp_path / "projects"), \
         patch("routers.transcripts._generate_titles_background", AsyncMock()):
        resp = await client.get("/api/transcripts")
    rows = [t for t in resp.json()["transcripts"] if t["session_id"] == sid]
    assert rows, "the transcript row must still be listed"
    assert not _looks_like_refusal(rows[0]["name"])
    assert rows[0]["name"].startswith("Diagnose the flaky onboarding test")


# ---------------------------------------------------------------------------
# Backfill endpoint clears junk from the cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_clears_refusal_titles(client, tmp_path, monkeypatch):
    cache_file = tmp_path / "titles.json"
    cache_file.write_text(json.dumps({
        "sess-a": "I don't see the actual messages from the coding session in y",
        "sess-b": "Fix sidebar collapse button",
        "sess-c": "Agent registration and mailbox",
    }))
    monkeypatch.setattr(transcripts, "TITLE_CACHE_PATH", cache_file)
    resp = await client.post("/api/transcripts/backfill-titles")
    assert resp.status_code == 200
    assert resp.json()["removed"] == 2
    cache = json.loads(cache_file.read_text())
    assert "sess-a" not in cache
    assert "sess-c" not in cache
    assert cache["sess-b"] == "Fix sidebar collapse button"

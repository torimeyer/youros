"""Transcripts router: reads Claude Code session files from disk."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.atomic_io import atomic_write_json
from services import session_task_map
from services.youros_paths import youros_home

router = APIRouter(tags=["transcripts"])

# Patterns that identify mailbox/registration boilerplate lines.
# A first-message line matching any of these is skipped when deriving a title.
_MAILBOX_BOILERPLATE_RE = re.compile(
    r"(?i)"
    r"(##\s*(agent\s+registration|mailbox)|"
    r"register\s+immediately|"
    r"step\s+0\s*:|"
    r"curl.*agents/register|"
    r"heartbeat.*every|"
    r"mailbox\s+check(?:ing)?|"
    r"nudge.*poll|"
    # Belt for historical, sentinel-free transcripts (→2891): the generated
    # "Never wait for a response from the user/Tori after posting…" sentence
    # passed every other filter and became the derived title. New blocks are
    # excised structurally via the mailbox sentinels below; this keyword only
    # protects old files.
    r"never\s+wait\s+for\s+a\s+response|"
    r"post\s*/api/agents|"
    # →2892 belt for the remaining legacy-block sentences that passed every
    # other filter and became titles on old, frozen transcripts: the
    # "### Coordination primitives" intro, the Atlassian tail paragraph
    # (matched via its endpoint paths so real briefs that merely mention
    # Atlassian are unaffected), and the preamble families that can also
    # appear mid-message.
    r"use\s+these\s+primitives|"
    r"/api/atlassian|"
    r"the\s+`bash`\s+tool\s+is\s+blocked|"
    r"\[worktree\s+(cwd|commit)|"
    r"having\s+a\s+real\s+back\s+and\s+forth|"
    # →2894 family 5: brief template rule sentence that appears inside
    # spawn briefs before the instruction block. Looks like prose but is
    # an operational constraint on the agent, not task content. Measured:
    # 76 transcripts showed "Exiting after analysis only, no commit, no
    # task" (or a truncation of it) as their title.
    r"exiting\s+after\s+analysis\s+only|"
    # Belt for the "### Finishing your work (mandatory)" section sentence
    # that can survive the fallback post-block scan when a legacy block
    # lacks the Atlassian terminator paragraph.
    r"without\s+this\s+call\s+the\s+agent\s+row|"
    # →2894 retry: the rest of the COMPLETION REQUIREMENT paragraph from
    # the task-bridge hook. Filtering only the "Exiting…" sentence just
    # promotes the paragraph's next prose-looking sentence to the title,
    # so every sentence of the clause is boilerplate on its own.
    r"completion\s+requirement|"
    r"commit\s+your\s+work\s+before\s+running|"
    r"a\s+failed\s+verify\s+still\s+leaves|"
    r"\(e\.g\.?\s+git\s+commit)"
)

# Structural containment (→2891): the mailbox-instruction builder in
# routers/agents.py wraps its generated block in these sentinel lines.
# Every sentinel-delimited region is excised wholesale BEFORE any title
# logic runs, so future wording changes inside the block can never leak
# into a transcript title, no matter what they say.
_MAILBOX_SENTINEL_REGION_RE = re.compile(
    r"<!--\s*mailbox:begin\s*-->.*?<!--\s*mailbox:end\s*-->\n?",
    re.DOTALL | re.IGNORECASE,
)
# A begin marker with no matching end marker (truncated capture): drop
# everything from the marker onward rather than let half a block leak.
_MAILBOX_SENTINEL_OPEN_RE = re.compile(
    r"<!--\s*mailbox:begin\s*-->.*\Z",
    re.DOTALL | re.IGNORECASE,
)

# Headings that open a legacy (sentinel-free) mailbox/registration block:
# the long block starts with "## Bootstrap (do this before ANYTHING else)"
# followed by "## Agent registration and mailbox (mandatory)"; the compact
# block uses "## Mailbox (mandatory)…". Kept precise so a real task heading
# like "## Mailbox feature work" is not mistaken for boilerplate.
_MAILBOX_BLOCK_START_RE = re.compile(
    r"(?i)^##\s*(agent\s+registration|bootstrap\b|mailbox\s*\(mandatory\))"
)

# →2894 family 6: generic chat-template speaker labels. A message that
# reduces to a bare label ("Assistant:"), or a label followed by fewer than
# _MIN_LABEL_REMAINDER_WORDS words, is never meaningful task prose. A label
# with substantial content after it titles from the content, minus the
# label. Named bridge speakers ("Gemini: …") are intentionally NOT in this
# set: their lines are real conversation content (→2892).
_BARE_SPEAKER_LABEL_RE = re.compile(
    r"(?i)^(assistant|user|human|ai|claude)\s*:?\s*$"
)
_SPEAKER_LABEL_PREFIX_RE = re.compile(
    r"(?i)^(assistant|user|human|ai|claude)\s*:\s*"
)
_MIN_LABEL_REMAINDER_WORDS = 4


def _strip_speaker_label(text: str) -> str:
    """Strip a leading generic speaker label from title/context text (→2894).

    Returns "" when the text is only a label, or a label trailed by fewer
    than four words (too thin to title); returns the text after the label
    when real content follows it; returns the input untouched otherwise.
    """
    stripped = text.strip()
    if _BARE_SPEAKER_LABEL_RE.match(stripped):
        return ""
    m = _SPEAKER_LABEL_PREFIX_RE.match(stripped)
    if not m:
        return text
    remainder = stripped[m.end():].strip()
    if len(remainder.split()) < _MIN_LABEL_REMAINDER_WORDS:
        return ""
    return remainder

# →2894 family 7: titles generated by the AI while it was fed junk context
# (the old instruction sheet). They look like real titles but paraphrase
# "Coordination primitives / Use these primitives to share state, signal
# progress…" — variants of "State Sharing and Progress Signaling Primitives".
_INSTRUCTION_PARAPHRASE_RE = re.compile(
    r"(?i)("
    r"coordination\s+primitive"
    r"|state\s+shar\w*\s+.*progress"
    r"|progress\s+signal"
    r"|signaling\s+primitive"
    r")"
)


def _strip_sentinel_mailbox_regions(text: str) -> str:
    """Excise every ``<!-- mailbox:begin -->``…``<!-- mailbox:end -->`` region.

    Also trims the blank lines and ``---`` separator rules left at the
    seams so the surviving task prose starts (and ends) clean. Text
    without sentinels is returned untouched.
    """
    if "mailbox:begin" not in text.lower():
        return text
    cleaned = _MAILBOX_SENTINEL_REGION_RE.sub("", text)
    cleaned = _MAILBOX_SENTINEL_OPEN_RE.sub("", cleaned)
    lines = cleaned.splitlines()
    def _is_seam(line: str) -> bool:
        stripped = line.strip()
        return not stripped or set(stripped) == {"-"}
    while lines and _is_seam(lines[0]):
        lines.pop(0)
    while lines and _is_seam(lines[-1]):
        lines.pop()
    return "\n".join(lines)


# →2894 retry: the task-bridge hook (.claude/hooks/lib/rules/
# isolation_bridge.sh) appends a COMPLETION REQUIREMENT paragraph to every
# bridged spawn prompt. The paragraph is excised wholesale, from its header
# line through the end of its paragraph, wherever it appears: filtering
# single sentences just promotes the next sentence to the title.
_COMPLETION_CLAUSE_RE = re.compile(
    r"^[ \t]*completion\s+requirement:.*?(?=\n[ \t]*\n|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


def _strip_completion_clauses(text: str) -> str:
    """Excise every COMPLETION REQUIREMENT template paragraph (→2894)."""
    if "completion requirement" not in text.lower():
        return text
    return _COMPLETION_CLAUSE_RE.sub("", text).strip()


# Known preamble paragraphs (→2892): text the harness or a template puts at
# the START of a message, with the real content following it in the same
# message. Each pattern is matched against the first non-seam line; when it
# hits, the whole leading paragraph (up to the first blank line) is dropped,
# repeatedly, until the message no longer starts with a known preamble.
_KNOWN_PREAMBLE_RES = [
    # Worktree cwd/commit header injected by the spawn path in
    # routers/agents.py (→1240/→2503).
    re.compile(r"(?i)^\[worktree\s+(cwd|commit)\b"),
    # Tooling notice at the top of some spawn briefs.
    re.compile(r"(?i)^the\s+`bash`\s+tool\s+is\s+blocked\s+globally"),
    # Multi-AI chat-bridge template (services/chat_providers.py), as stored
    # in transcripts with a "User: " prefix. The opener, the round-1
    # nothing-said-yet notice, the transcript header line, and the closing
    # reply instructions are all template; the conversation content between
    # them is what a title should come from.
    re.compile(r"(?i)^(user:\s*)?you\s+are\s+\w+\.\s+you\s+are\s+having\s+a\s+real\s+back\s+and\s+forth\b"),
    re.compile(r"(?i)^conversation\s+so\s+far:\s*$"),
    re.compile(r"(?i)^this\s+is\s+the\s+first\s+message\s+in\s+the\s+conversation\b"),
    re.compile(r"(?i)^reply\s+directly\s+to\s+the\s+previous\s+speaker\b"),
    # →2894 family 5: brief template rule sentence that appears at the START
    # of spawn briefs (before the mailbox block or with the task text on the
    # next paragraph). Adding it here strips it in the no-mailbox-block path
    # too; the _MAILBOX_BOILERPLATE_RE belt handles the within-block scan.
    re.compile(r"(?i)^exiting\s+after\s+analysis\s+only"),
]


def _is_seam_line(line: str) -> bool:
    """True for lines that only separate content: blank or a --- rule."""
    stripped = line.strip()
    return not stripped or set(stripped) == {"-"}


def _strip_known_preambles(text: str) -> str:
    """Drop known leading preamble paragraphs from a message (→2892).

    Old transcripts carry leading paragraphs injected by the spawn harness
    (worktree cwd headers, Bash-blocked tooling notices) or by the multi-AI
    chat-bridge template; the real task or conversation text follows them
    in the same message. Repeats until the text no longer starts with a
    known preamble, trimming seam lines (blanks, --- rules) between rounds.
    Text that starts with none of the known preambles is returned untouched.
    """
    while True:
        lines = text.splitlines()
        start = 0
        while start < len(lines) and _is_seam_line(lines[start]):
            start += 1
        if start >= len(lines):
            return ""
        first = lines[start].strip()
        if not any(r.match(first) for r in _KNOWN_PREAMBLE_RES):
            return "\n".join(lines[start:]) if start else text
        # Drop the whole leading paragraph: everything up to the next
        # blank line belongs to the preamble.
        end = start
        while end < len(lines) and lines[end].strip():
            end += 1
        text = "\n".join(lines[end:])


def _is_meaningful_prose(candidate: str) -> bool:
    """True when a stripped line reads as task prose rather than mailbox
    boilerplate, markup, or a shell command.

    Shared by the leading-section scan and the post-block fallback scan
    in :func:`_skip_mailbox_boilerplate`. The instruction-step and
    bold-led filters exist because those lines used to leak out of the
    mailbox block, become the transcript preview, and poison the AI
    title generator's context until it refused — and the refusal was
    then cached as the title (→2888).
    """
    if not candidate:
        return False
    if candidate.startswith("#"):
        return False
    if candidate.startswith("`") or candidate.startswith("curl") or candidate.startswith("POST "):
        return False
    # Separator rules (---) and other letterless lines are never prose.
    if not re.search(r"[A-Za-z]", candidate):
        return False
    # Instruction-list steps: "1. On each cycle, call:", "d. Post the…"
    if re.match(r"^\(?(\d{1,3}|[A-Za-z])[.)]\s", candidate):
        return False
    if candidate.startswith("**") or candidate.endswith(":"):
        return False
    if _MAILBOX_BOILERPLATE_RE.search(candidate):
        return False
    # Lines that are clearly still part of registration prose.
    if re.search(r"(?i)(register|heartbeat|mailbox|nudge|agents page|before doing)", candidate):
        return False
    return True

# Titles that are actually the naming model refusing ("I don't see the
# actual messages…") must never be shown or cached (→2888). Matches the
# common refusal openings; "enough context" is checked anywhere in the text.
_REFUSAL_TITLE_RE = re.compile(
    r"(?i)^\s*("
    r"i\s+(don'?t|do\s+not|can'?t|cannot|need|would\s+need)\b"
    r"|i\s+am\s+unable\b|i'?m\s+unable\b"
    r"|unable\s+to\b"
    r"|there\s+(is|are)\s+no\b"
    r"|no\s+(actual\s+)?(messages?|conversation|context)\b"
    r"|not\s+enough\s+context\b"
    r"|sorry[,\s]"
    r")"
)

# Context shorter than this never reaches the naming model: there is
# nothing to summarize, and the old behavior produced refusals that were
# then cached verbatim as titles (→2888).
_MIN_TITLE_CONTEXT_CHARS = 40


def _looks_like_refusal(title: str) -> bool:
    """True when a generated title reads as a model refusal rather than a
    session summary."""
    if not title:
        return False
    return bool(_REFUSAL_TITLE_RE.match(title)) or "enough context" in title.lower()


def _looks_like_instruction_paraphrase(title: str) -> bool:
    """True when a generated title paraphrases the old instruction sheet
    rather than describing the actual session work (→2894, family 7).

    These titles were generated while the title writer was fed junk context
    (registration/coordination boilerplate) before the extraction fixes
    landed. They look real but describe the instruction template, not the
    session. Detecting them here lets the background generator and the
    backfill endpoint clear and regenerate them.
    """
    if not title:
        return False
    return bool(_INSTRUCTION_PARAPHRASE_RE.search(title))


# →2894 retry: the current generator writes short bare titles (5-8 words,
# no prefix), so a cached "[YYYY-MM-DD] …" title can only date from the old
# junk-context era (the "[2026-06-07] Responded to user greeting" cluster).
_STALE_DATED_TITLE_RE = re.compile(r"^\s*\[\d{4}-\d{2}-\d{2}\]")


def _looks_like_stale_generated_title(title: str) -> bool:
    """True for cached titles from known stale families (→2894): AI
    paraphrases of the old instruction sheet, and old-format date-prefixed
    titles the current generator can no longer produce."""
    if not title:
        return False
    return bool(_STALE_DATED_TITLE_RE.match(title)) or _looks_like_instruction_paraphrase(title)


MYOS_DIR = youros_home()
TITLE_CACHE_PATH = MYOS_DIR / "transcript_titles.json"


def _load_title_cache() -> dict[str, str]:
    try:
        return json.loads(TITLE_CACHE_PATH.read_text()) if TITLE_CACHE_PATH.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_title_cache(cache: dict[str, str]) -> None:
    # Atomic to avoid a crash mid-save wiping the title cache.
    atomic_write_json(TITLE_CACHE_PATH, cache)


async def _save_title_cache_async(cache: dict[str, str]) -> None:
    """Thread-offloaded title cache write so the async title generator
    does not block the event loop on the fsync. Used from
    :func:`_generate_titles_background` which runs from the
    /api/transcripts handler.
    """
    await asyncio.to_thread(_save_title_cache, cache)

# Claude Code stores session index files and transcript JSONL files in these locations.
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

def _skip_mailbox_boilerplate(text: str) -> str:
    """Return the meaningful portion of a prompt text, skipping mailbox/registration blocks.

    Strategy (→2888, →2891, →2892):
    0. Excise every sentinel-delimited region (``<!-- mailbox:begin -->`` …
       ``<!-- mailbox:end -->``) wholesale before anything else. Newly
       generated instruction blocks are wrapped in these sentinels, so no
       future wording change inside the block can leak into a title.
    0.2 Excise every COMPLETION REQUIREMENT template paragraph appended
       by the task-bridge hook (→2894): filtering single sentences only
       promotes the paragraph's next sentence to the title.
    0.5 Strip known leading preamble paragraphs (worktree cwd headers,
       Bash-blocked tooling notices, chat-bridge template openers) until
       the message no longer starts with one; the real content follows
       them in the same message (→2892).
    1. Locate the start of a legacy (sentinel-free) mailbox block: the
       '## Agent registration', '## Bootstrap', or '## Mailbox (mandatory)'
       heading.
    2. If meaningful prose exists BEFORE that block, it is the task brief
       (how the orchestrator writes every brief): return its first
       meaningful line. Task text ahead of the block always wins.
    3. Otherwise scan forward for the NEXT top-level '##' heading that is
       NOT itself a registration/mailbox heading and return the first
       prose line under it.
    4. If no such heading exists, only prose AFTER the last line the
       boilerplate belt recognizes can be the task text (→2892): the
       block extends through its last recognizable line, so a sentence
       sandwiched INSIDE it ("Use these primitives…") can never win,
       while a brief appended BELOW the block still can. If nothing
       qualifies, return "" so callers advance to the next user message.
    5. If the input has no mailbox block at all, return it unchanged
       (minus any stripped preambles).
    """
    text = _strip_sentinel_mailbox_regions(text)
    text = _strip_completion_clauses(text)
    text = _strip_known_preambles(text)
    lines = text.splitlines()

    # Find the line index of the mailbox block start
    mailbox_start: Optional[int] = None
    for i, line in enumerate(lines):
        if _MAILBOX_BLOCK_START_RE.match(line.strip()):
            mailbox_start = i
            break

    if mailbox_start is None:
        return text

    # Task prose placed BEFORE the mailbox block is the actual brief
    # (→2891): it always wins over anything the block-relative scans
    # below could dig up.
    for i in range(mailbox_start):
        candidate = lines[i].strip()
        if _is_meaningful_prose(candidate):
            return candidate

    # Scan lines after the mailbox heading for the next top-level ## heading
    # that is NOT itself mailbox boilerplate.
    for i in range(mailbox_start + 1, len(lines)):
        line = lines[i].strip()
        if re.match(r"^##\s+", line) and not _MAILBOX_BOILERPLATE_RE.search(line):
            # Found the real task section. Return its first prose line.
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if not candidate:
                    continue
                if candidate.startswith("#"):
                    continue
                if candidate.startswith("`") or candidate.startswith("curl") or candidate.startswith("POST "):
                    continue
                return candidate
            # Heading found but no prose lines under it
            return ""

    # No secondary heading found. →2892: never return a line from INSIDE
    # the block. The legacy block has no end marker, but it extends at
    # least through the last line the belt recognizes as boilerplate, so
    # only prose after that boundary can be the task text (the layout
    # where a brief was appended below the block). Sentences sandwiched
    # inside the block ("Use these primitives…") can never be returned.
    last_boilerplate = mailbox_start
    for i in range(mailbox_start + 1, len(lines)):
        candidate = lines[i].strip()
        if candidate and not _is_meaningful_prose(candidate):
            last_boilerplate = i
    for i in range(last_boilerplate + 1, len(lines)):
        candidate = lines[i].strip()
        if _is_meaningful_prose(candidate):
            return candidate

    return ""


def _extract_context(jsonl_path: Path, max_messages: int = 5) -> str:
    """Extract the first few user messages for context, skipping mailbox boilerplate."""
    parts: list[str] = []
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user" or "message" not in entry:
                    continue
                msg = entry["message"]
                content = msg.get("content", "")
                text = ""
                if isinstance(content, str):
                    text = content.strip()
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "").strip()
                            break
                        elif isinstance(part, str) and part.strip():
                            text = part.strip()
                            break
                text = _skip_mailbox_boilerplate(text)
                if text:
                    text = _strip_speaker_label(text)
                if text:
                    parts.append(text[:300])
                    if len(parts) >= max_messages:
                        break
    except OSError:
        pass
    return "\n---\n".join(parts)


async def _generate_title(context: str) -> str:
    """Call the AI backend to generate a short summary title from transcript context."""
    from services.ai_backend import get_ai_client
    client = await get_ai_client()
    if client is None:
        return ""
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=30,
                messages=[{
                    "role": "user",
                    "content": (
                        "Below are the first few messages from a coding session. "
                        "Write a short title (5-8 words max) that summarizes what was worked on. "
                        "No quotes, no punctuation at the end, just the title.\n\n"
                        f"{context}"
                    ),
                }],
            ),
            timeout=10.0,
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                return (getattr(block, "text", "") or "").strip().strip('"\'.')
    except Exception:
        pass
    return ""


async def _generate_titles_background(items: list[tuple[str, Path]]) -> None:
    """Generate titles for transcripts that don't have a usable one cached.

    Guards (→2888):
    - a cached refusal-looking title counts as junk and is regenerated
    - context shorter than ``_MIN_TITLE_CONTEXT_CHARS`` never reaches the
      model: there is nothing to summarize, and the old behavior turned
      those calls into refusals that were cached verbatim as titles
    - refusal-looking model output is cached as the empty sentinel so the
      display falls back to the derived name and the doomed model call is
      not repeated on every fetch
    """
    cache = _load_title_cache()
    for session_id, jsonl_path in items:
        cached = cache.get(session_id)
        cached_is_junk = cached is not None and (
            _looks_like_refusal(cached) or _looks_like_stale_generated_title(cached)
        )
        if cached is not None and not cached_is_junk:
            continue
        context = _extract_context(jsonl_path)
        if not context or len(context.strip()) < _MIN_TITLE_CONTEXT_CHARS:
            if cached_is_junk:
                cache[session_id] = ""
            continue
        title = await _generate_title(context)
        if _looks_like_refusal(title):
            cache[session_id] = ""
            continue
        if title:
            cache[session_id] = title
        elif cached_is_junk:
            cache[session_id] = ""
    await _save_title_cache_async(cache)


def _find_all_project_dirs() -> list[Path]:
    """Return all Claude Code project directories that have JSONL session files."""
    dirs = []
    if PROJECTS_DIR.exists():
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and any(d.glob("*.jsonl")):
                dirs.append(d)
    return dirs


def _session_index() -> dict[str, dict]:
    """Build a lookup of sessionId -> session metadata from the sessions directory."""
    index: dict[str, dict] = {}
    if not SESSIONS_DIR.exists():
        return index
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sid = data.get("sessionId")
            if sid:
                index[sid] = data
        except (json.JSONDecodeError, OSError):
            continue
    return index


def _format_timestamp(ts_ms: Optional[int] = None, ts_iso: Optional[str] = None) -> str:
    """Convert a timestamp (millis or ISO string) to a readable string."""
    try:
        if ts_ms:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M")
        if ts_iso:
            # Handle ISO strings with or without trailing Z
            cleaned = ts_iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        pass
    return ""


def _extract_first_user_message(jsonl_path: Path) -> str:
    """Read the JSONL and return the first meaningful user message text (truncated).

    Skips mailbox/registration boilerplate. If the first user message is entirely
    boilerplate, reads subsequent messages until a meaningful line is found.
    Caps output at 200 characters.
    """
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") == "user" and "message" in entry:
                    msg = entry["message"]
                    content = msg.get("content", "")
                    raw_text = ""
                    if isinstance(content, str) and content.strip():
                        raw_text = content.strip()
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                candidate = part.get("text", "").strip()
                                if candidate:
                                    raw_text = candidate
                                    break
                            elif isinstance(part, str) and part.strip():
                                raw_text = part.strip()
                                break

                    if not raw_text:
                        continue

                    # Strip mailbox boilerplate and pick the real content
                    cleaned = _skip_mailbox_boilerplate(raw_text)
                    # If the entire first message was boilerplate, skip to next message
                    if not cleaned:
                        continue
                    # A bare speaker label ("Assistant:", "User: ok") is
                    # never task prose; a label with real content after it
                    # titles from the content, minus the label (→2894
                    # family 6).
                    cleaned = _strip_speaker_label(cleaned)
                    if not cleaned:
                        continue

                    return cleaned[:200] if len(cleaned) > 200 else cleaned
    except OSError:
        pass
    return ""


def _derive_title(jsonl_path: Path, spawn_task: str = "") -> str:
    """Derive a display title for a transcript without calling an LLM.

    Rules (in priority order):
    1. First meaningful prose line after mailbox boilerplate, capped at 60 chars.
    2. spawn_task field from the agent row.
    3. Empty string (caller falls back to session name or session ID prefix).
    """
    first = _extract_first_user_message(jsonl_path)
    if first:
        return first[:60]
    if spawn_task:
        return spawn_task[:60]
    return ""


def _count_messages(jsonl_path: Path) -> dict[str, int]:
    """Count user and assistant messages in a transcript JSONL file.

    Reads only the type field from each line to stay fast on large files.
    """
    user_count = 0
    assistant_count = 0
    tool_count = 0
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = entry.get("type", "")
                if t == "user":
                    # Only count real user messages, not tool results
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        user_count += 1
                    elif isinstance(content, list):
                        # Tool results have tool_result items; skip those
                        has_text = any(
                            isinstance(p, str) or (isinstance(p, dict) and p.get("type") == "text")
                            for p in content
                        )
                        if has_text:
                            user_count += 1
                elif t == "assistant":
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "tool_use":
                                    tool_count += 1
                    assistant_count += 1
    except OSError:
        pass
    return {"user": user_count, "assistant": assistant_count, "tool_calls": tool_count}


def _transcript_contains_text(jsonl_path: Path, query: str) -> bool:
    """Check whether any message in a transcript JSONL file contains the query text.

    Case-insensitive search through user and assistant message content.
    """
    query_lower = query.lower()
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                if entry_type not in ("user", "assistant"):
                    continue

                msg = entry.get("message", {})
                content = msg.get("content", "")

                # Extract text from content (can be string or list of parts)
                if isinstance(content, str):
                    if query_lower in content.lower():
                        return True
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, str):
                            if query_lower in part.lower():
                                return True
                        elif isinstance(part, dict):
                            text = part.get("text", "")
                            if isinstance(text, str) and query_lower in text.lower():
                                return True
    except OSError:
        pass
    return False


def _parse_started_at(started_at_str: str) -> Optional[datetime]:
    """Parse a started_at string (YYYY-MM-DD HH:MM) into a datetime."""
    if not started_at_str:
        return None
    try:
        return datetime.strptime(started_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _matches_date_range(started_at_str: str, date_range: str) -> bool:
    """Check whether a transcript's start time falls within the given date range.

    Supported ranges: today, week, month, all.
    """
    if date_range == "all":
        return True

    dt = _parse_started_at(started_at_str)
    if dt is None:
        # If we cannot parse the date, include it only for 'all'
        return False

    now = datetime.now(tz=timezone.utc)

    if date_range == "today":
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt >= start_of_day
    elif date_range == "week":
        start_of_week = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return dt >= start_of_week
    elif date_range == "month":
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return dt >= start_of_month

    return True


@router.get("/transcripts")
async def list_transcripts(
    search: Optional[str] = Query(None, description="Search through message text"),
    date_range: Optional[str] = Query(None, description="Filter by date range: today, week, month, all"),
    kind: Optional[str] = Query(None, description="Filter by session kind (e.g. interactive, task)"),
):
    """List all available session transcripts, with optional search and filters."""
    session_index = _session_index()
    title_cache = _load_title_cache()
    transcripts = []
    needs_title: list[tuple[str, Path]] = []

    # Scan all project directories for JSONL session files
    project_dirs = _find_all_project_dirs()

    for project_dir in project_dirs:
        # Derive a project label from the directory name
        dir_name = project_dir.name  # e.g. "-Users-alice-claude-myproject"
        # Convert back to a readable path
        project_label = dir_name.lstrip("-").replace("-", "/")

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        try:
            jsonl_files = sorted(project_dir.glob("*.jsonl"), key=_safe_mtime, reverse=True)
        except OSError:
            continue

        for jsonl_file in jsonl_files:
            session_id = jsonl_file.stem
            meta = session_index.get(session_id, {})

            # Kind filter: check early before expensive operations
            session_kind = meta.get("kind", "unknown")
            if kind and session_kind != kind:
                continue

            started_at = _format_timestamp(ts_ms=meta.get("startedAt"))

            # Date range filter: check early before expensive operations
            if date_range and date_range != "all":
                if not _matches_date_range(started_at, date_range):
                    continue

            # Search filter: scan message content (most expensive, do last)
            if search:
                if not _transcript_contains_text(jsonl_file, search):
                    continue

            first_message = _extract_first_user_message(jsonl_file)
            try:
                file_size = jsonl_file.stat().st_size
            except OSError:
                file_size = 0
            counts = _count_messages(jsonl_file)

            # Use cached AI title > session name > derived title (boilerplate-skipped) > session ID
            name = title_cache.get(session_id, "")
            if _looks_like_refusal(name):
                # Junk cached by the pre-→2888 generator: hide it now; the
                # background pass below replaces the cache entry.
                name = ""
            if not name:
                name = meta.get("name", "")
            if not name:
                # Derive from first meaningful message, skipping mailbox boilerplate
                name = _derive_title(jsonl_file)
            if not name and first_message:
                name = first_message[:60]
            if not name:
                name = f"Session {session_id[:8]}"
            # Always cap at 60 chars
            name = name[:60]

            cached_title = title_cache.get(session_id)
            if cached_title is None or _looks_like_refusal(cached_title):
                needs_title.append((session_id, jsonl_file))

            transcripts.append({
                "session_id": session_id,
                "name": name,
                "project": project_label,
                "cwd": meta.get("cwd", ""),
                "started_at": started_at,
                "kind": session_kind,
                "entrypoint": meta.get("entrypoint", ""),
                "first_message": first_message,
                "message_counts": counts,
                "file_size_bytes": file_size,
            })

    # Generate titles in the background for transcripts that don't have one
    if needs_title:
        asyncio.create_task(_generate_titles_background(needs_title[:10]))

    return {"transcripts": transcripts, "total": len(transcripts)}


@router.get("/transcripts/{session_id}")
async def get_transcript(session_id: str, limit: int = 100, offset: int = 0):
    """Get the messages from a specific session transcript.

    Returns user and assistant messages in order, skipping internal/system entries.
    The limit and offset parameters control pagination.
    """
    # Find the JSONL file across all project directories
    jsonl_path = None
    for project_dir in _find_all_project_dirs():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            jsonl_path = candidate
            break

    if not jsonl_path:
        raise HTTPException(status_code=404, detail="Transcript not found")

    session_index = _session_index()
    meta = session_index.get(session_id, {})

    messages = []
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                timestamp = entry.get("timestamp", "")

                if entry_type == "user" and "message" in entry:
                    # Claude Code stamps automated re-injections (system
                    # reminders, image-source placeholders, wakeup pings)
                    # with ``isMeta: true``. These are runtime scaffolding,
                    # not things the user actually typed, so scrolling up
                    # should not show the same "Claude Code opening"
                    # banner 100+ times. Drop them at read time so the
                    # on-disk JSONL stays an untouched audit log.
                    if entry.get("isMeta") is True:
                        continue

                    msg = entry["message"]
                    content = msg.get("content", "")
                    text = ""

                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        # Combine text parts, note tool results
                        parts = []
                        for part in content:
                            if isinstance(part, str):
                                parts.append(part)
                            elif isinstance(part, dict):
                                if part.get("type") == "text":
                                    parts.append(part.get("text", ""))
                                elif part.get("type") == "tool_result":
                                    # Summarize tool result
                                    result_content = part.get("content", "")
                                    if isinstance(result_content, str) and result_content.strip():
                                        preview = result_content[:300]
                                        parts.append(f"[Tool output: {preview}]")
                        text = "\n".join(parts)

                    if text.strip():
                        cleaned = text.strip()
                        # Collapse consecutive identical user bubbles into
                        # one. In long sessions the same "60s check: idle"
                        # style ping can fire dozens of times in a row and
                        # is not useful to render separately on scroll-up.
                        if (
                            messages
                            and messages[-1]["role"] == "user"
                            and messages[-1]["text"] == cleaned
                        ):
                            continue
                        messages.append({
                            "role": "user",
                            "text": cleaned,
                            "timestamp": _format_timestamp(ts_iso=timestamp) if timestamp else "",
                        })

                elif entry_type == "assistant" and "message" in entry:
                    msg = entry["message"]
                    content = msg.get("content", [])
                    text_parts = []
                    tool_uses = []

                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                                elif part.get("type") == "tool_use":
                                    tool_name = part.get("name", "unknown")
                                    tool_input = part.get("input", {})
                                    # Show a brief summary of what tool was called
                                    summary = f"Used {tool_name}"
                                    if isinstance(tool_input, dict):
                                        if "command" in tool_input:
                                            cmd = tool_input["command"]
                                            if len(cmd) > 120:
                                                cmd = cmd[:120] + "..."
                                            summary = f"Ran: {cmd}"
                                        elif "file_path" in tool_input:
                                            summary = f"Read: {tool_input['file_path']}"
                                        elif "pattern" in tool_input:
                                            summary = f"Searched for: {tool_input['pattern']}"
                                    tool_uses.append(summary)
                            elif isinstance(part, str):
                                text_parts.append(part)
                    elif isinstance(content, str):
                        text_parts.append(content)

                    text = "\n".join(text_parts).strip()
                    if text or tool_uses:
                        messages.append({
                            "role": "assistant",
                            "text": text,
                            "tool_uses": tool_uses,
                            "timestamp": _format_timestamp(ts_iso=timestamp) if timestamp else "",
                        })

    except OSError:
        raise HTTPException(status_code=500, detail="Could not read transcript file")

    # Apply pagination
    total = len(messages)
    paginated = messages[offset:offset + limit]

    return {
        "session_id": session_id,
        "name": meta.get("name", ""),
        "cwd": meta.get("cwd", ""),
        "started_at": _format_timestamp(ts_ms=meta.get("startedAt")),
        "kind": meta.get("kind", ""),
        "total_messages": total,
        "messages": paginated,
    }


@router.post("/transcripts/backfill-titles")
async def backfill_transcript_titles():
    """Re-derive titles for transcripts whose cached title looks like junk.

    Checks every entry in the title cache. An entry is deleted so the
    next /transcripts fetch re-derives it from the actual message content
    when the title:
    - starts with 'Agent registration' (case-insensitive),
    - reads as a model refusal ("I don't see the actual messages…", →2888),
    - contains the leaked mailbox sentence "never wait for a response"
      (case-insensitive, →2891),
    - starts with one of the →2892 junk families: "use these primitives",
      "[worktree cwd", "user: you are claude", or "the `bash` tool is
      blocked" (all case-insensitive), or
    - is the empty-string sentinel (cached when the extracted context was
      junk; with extraction fixed those deserve one more regeneration
      attempt, →2891),
    - matches a →2894 family: the brief completion-rule sentence, a bare
      speaker label (with or without a too-thin trailer), an instruction-
      sheet paraphrase, or an old-format "[YYYY-MM-DD] …" title, or
    - no longer plausibly derives from its transcript (→2894): when the
      session's JSONL file is on disk but freshly extracted context is
      empty or too thin to have produced any title, the cached title was
      generated from junk context and is dropped. Sessions whose JSONL
      cannot be found are left untouched: there is nothing to re-derive
      from, so the cached title is kept rather than judged.
    Does NOT mutate source JSONL files. Safe to call multiple times.
    Runs in a worker thread because the plausibility check reads
    transcript files.
    """
    def _scan() -> list[str]:
        cache = _load_title_cache()
        removed: list[str] = []

        boilerplate_title_re = re.compile(
            r"(?i)^agent\s+registration"
        )
        leaked_mailbox_re = re.compile(r"(?i)never\s+wait\s+for\s+a\s+response")
        family_prefix_re = re.compile(
            r"(?i)^("
            r"use these primitives"
            r"|\[worktree cwd"
            r"|user: you are claude"
            r"|the `bash` tool is blocked"
            r")"
        )
        # →2894 family 5: brief completion-rule sentence titles.
        brief_rule_title_re = re.compile(r"(?i)^exiting\s+after\s+analysis\s+only")

        # Map session ids to transcript files once so the plausibility
        # check below is a dict lookup, not a per-entry directory walk.
        jsonl_by_session: dict[str, Path] = {}
        for project_dir in _find_all_project_dirs():
            try:
                for jsonl_file in project_dir.glob("*.jsonl"):
                    jsonl_by_session.setdefault(jsonl_file.stem, jsonl_file)
            except OSError:
                continue

        for session_id, title in list(cache.items()):
            drop = bool(
                not title
                or boilerplate_title_re.match(title)
                or leaked_mailbox_re.search(title)
                or family_prefix_re.match(title)
                or _looks_like_refusal(title)
                or brief_rule_title_re.match(title)
                # →2894 family 6: bare speaker labels and labels with a
                # too-thin trailer reduce to "" here.
                or _strip_speaker_label(title) == ""
                # →2894 family 7 plus the dated cluster: instruction-sheet
                # paraphrases and old-format "[YYYY-MM-DD] …" titles.
                or _looks_like_stale_generated_title(title)
            )
            if not drop:
                # Plausibility (→2894): a cached title whose transcript is
                # on disk but now extracts to no usable context cannot
                # derive from anything; it was generated from junk context
                # before the extraction fixes landed.
                jsonl_path = jsonl_by_session.get(session_id)
                if jsonl_path is not None:
                    context = _extract_context(jsonl_path)
                    if not context or len(context.strip()) < _MIN_TITLE_CONTEXT_CHARS:
                        drop = True
            if drop:
                del cache[session_id]
                removed.append(session_id)

        if removed:
            _save_title_cache(cache)
        return removed

    removed = await asyncio.to_thread(_scan)

    return {
        "removed": len(removed),
        "message": (
            f"Cleared {len(removed)} boilerplate title(s) from cache. "
            "They will be re-derived on the next transcript fetch."
            if removed
            else "No boilerplate titles found in cache."
        ),
    }


# ---- Session-task links ----------------------------------------------------
#
# These endpoints let the SessionStart hook (or any other caller) connect a
# Claude Code session UUID to an already-filed task so the Tasks page can
# show a "View transcript" link on the session row and count how many tasks
# were created during that session.


class _LinkTaskBody(BaseModel):
    task_id: str


@router.post("/sessions/{session_id}/link-task")
async def link_session_task(session_id: str, body: _LinkTaskBody):
    """Attach an existing task_id to this Claude Code session.

    The task becomes the "session task" for this session: the row on the
    Tasks page that represents the session itself. Used by the
    SessionStart hook right after it files its auto task, so later
    renders can link back to the transcript.
    """
    if not session_id or not body.task_id:
        raise HTTPException(status_code=422, detail="session_id and task_id are required")
    session_task_map.link_session_to_task(session_id, body.task_id)
    return {"session_id": session_id, "task_id": body.task_id, "linked": True}


class _LinkChildBody(BaseModel):
    task_id: str
    parent_session_id: str


@router.post("/sessions/{session_id}/link-child-task")
async def link_child_task_endpoint(session_id: str, body: _LinkChildBody):
    """Record that ``task_id`` was created during ``session_id``.

    The session_id in the path and body must match so the record is
    unambiguous. This powers the "N tasks created in this session"
    count shown on the session row.
    """
    if body.parent_session_id != session_id:
        raise HTTPException(
            status_code=422,
            detail="parent_session_id in body does not match session_id in path",
        )
    if not body.task_id:
        raise HTTPException(status_code=422, detail="task_id is required")
    session_task_map.link_child_task(body.task_id, session_id)
    return {
        "session_id": session_id,
        "task_id": body.task_id,
        "child_task_count": session_task_map.count_children(session_id),
    }


@router.get("/sessions/{session_id}/child-tasks")
async def get_child_tasks(session_id: str):
    """Return the count of tasks created during this session.

    Lightweight companion to ``/link-child-task`` so the Tasks page can
    refresh the count without refetching every task row.
    """
    return {
        "session_id": session_id,
        "count": session_task_map.count_children(session_id),
    }

"""Auto-draft standing instructions from the user's patterns.

The Settings page has a "Standing instructions" card where the user writes
once what every chat, agent, and task should follow. Writing that from
scratch is a blank page problem. This service observes the signals we
already have (recent chat history, memory feedback files, agent patterns,
connected integrations) and asks Haiku to draft 5-10 one-liners the user
can accept or tweak.

Signals pulled:
- Recent chat messages from ~/.youros/chat_history.json (last 30 days).
- Feedback entries from the user's memory dir
  ~/.claude/projects/<slug>/memory/feedback_*.md (correction patterns).
- Connected integrations (Gmail, Google Calendar, Slack, GitHub tokens).
- Agent file names the user has used recently.

Returns a list of short bullet strings, each a candidate standing
instruction. Falls back to a sensible empty list if Haiku is unavailable
or all signals are empty so the UI can show "no suggestions yet".
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import PROJECT_ROOT

MYOS_DIR = Path.home() / ".youros"
CHAT_HISTORY_PATH = MYOS_DIR / "chat_history.json"
# Claude Code stores per-project memory under ~/.claude/projects/<slug>/memory
# where <slug> is the absolute repo path with "/" replaced by "-" (so it
# keeps the leading dash from the leading "/" of the absolute path).
_PROJECT_SLUG = str(PROJECT_ROOT).replace("/", "-")
MEMORY_DIR = (
    Path.home()
    / ".claude"
    / "projects"
    / _PROJECT_SLUG
    / "memory"
)

_MODEL = "claude-haiku-4-5"
_CHAT_LOOKBACK_DAYS = 30
_MAX_CHAT_SAMPLES = 40
_MAX_FEEDBACK_FILES = 30


def _read_chat_samples(path: Path = CHAT_HISTORY_PATH) -> list[str]:
    """Return short user messages from the last 30 days of chat history.

    We only pull the user's own messages (not assistant replies) because
    the goal is to detect *how the user writes*: tone, corrections,
    recurring asks. Caps at 40 samples so the prompt stays small.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, dict):
        return []
    tabs = raw.get("tabs") or []
    if not isinstance(tabs, list):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_CHAT_LOOKBACK_DAYS)
    samples: list[str] = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        msgs = tab.get("messages")
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            content = m.get("content", "")
            if role != "user":
                continue
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue
            # Age filter. Messages without a timestamp are allowed.
            ts = m.get("created_at") or m.get("timestamp") or ""
            if isinstance(ts, str) and ts:
                try:
                    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if parsed < cutoff:
                        continue
                except ValueError:
                    pass
            # Keep the first 240 chars so the prompt stays lean.
            samples.append(text[:240])
    # Most recent first, capped.
    return samples[-_MAX_CHAT_SAMPLES:]


def _read_feedback_summaries(memory_dir: Path = MEMORY_DIR) -> list[str]:
    """Return one-line summaries from the user's feedback_*.md memory files.

    Each file captures a correction pattern ("always do X", "never do Y").
    We pull the first non-empty line under the title which is usually the
    crisp summary the user remembered.
    """
    if not memory_dir.exists() or not memory_dir.is_dir():
        return []
    summaries: list[str] = []
    try:
        files = sorted(memory_dir.glob("feedback_*.md"))
    except OSError:
        return []
    for fp in files[:_MAX_FEEDBACK_FILES]:
        try:
            content = fp.read_text()
        except OSError:
            continue
        # Grab the first meaningful line after any heading.
        first_paragraph = ""
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            first_paragraph = stripped
            break
        if first_paragraph:
            summaries.append(first_paragraph[:240])
    return summaries


def _read_integration_flags(myos_dir: Path = MYOS_DIR) -> dict[str, bool]:
    """Return which integrations are connected on disk.

    Presence of the token file is the signal. We do not try to validate
    the token here, the generator just wants to know which apps the user
    already uses so it can suggest instructions like "prefer Google
    Calendar for scheduling".
    """
    return {
        "gmail": (myos_dir / "google_token.json").exists(),
        "calendar": (myos_dir / "google_token.json").exists(),
        "slack": (myos_dir / "slack_token.json").exists(),
        "github": (myos_dir / "github_token.json").exists(),
        "drive": (myos_dir / "google_token.json").exists(),
    }


def _read_agent_aliases(myos_dir: Path = MYOS_DIR) -> list[str]:
    """Return recent agent file aliases from the templates store.

    Helps Haiku detect "user runs a doc-writer agent often, likely wants
    concise tone" style patterns. Silent on errors, empty list is fine.
    """
    templates_path = myos_dir / "agent_templates.json"
    if not templates_path.exists():
        return []
    try:
        data = json.loads(templates_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        templates = data.get("templates") or data.get("items") or []
    elif isinstance(data, list):
        templates = data
    else:
        return []
    aliases: list[str] = []
    for t in templates:
        if isinstance(t, dict):
            alias = t.get("alias") or t.get("name") or t.get("title")
            if isinstance(alias, str) and alias.strip():
                aliases.append(alias.strip())
    return aliases[:20]


async def _resolve_api_key() -> str:
    """Find an Anthropic API key from the usual places. Empty string if none."""
    from services.ostk_secrets import get_anthropic_key
    return await get_anthropic_key()


def _build_prompt(
    chat_samples: list[str],
    feedback: list[str],
    integrations: dict[str, bool],
    agent_aliases: list[str],
) -> str:
    """Assemble the Haiku prompt from observed signals."""
    connected = [name for name, on in integrations.items() if on]
    chat_block = "\n".join(f"- {s}" for s in chat_samples[-20:]) or "(none)"
    fb_block = "\n".join(f"- {s}" for s in feedback[:20]) or "(none)"
    agents_block = ", ".join(agent_aliases) or "(none)"
    connected_block = ", ".join(connected) or "(none)"

    return (
        "You are drafting standing instructions for a personal OS user. "
        "These instructions will be prepended to every chat, agent spawn, "
        "and task so the AI picks up the user's tone, tool preferences, "
        "and house rules without them having to repeat anything.\n\n"
        "Here are signals from this user's actual patterns:\n\n"
        "RECENT CHAT MESSAGES (how they write and what they ask for):\n"
        f"{chat_block}\n\n"
        "CORRECTION PATTERNS (things they have corrected before):\n"
        f"{fb_block}\n\n"
        f"CONNECTED APPS: {connected_block}\n"
        f"AGENTS THEY USE: {agents_block}\n\n"
        "Draft 5 to 10 one-line standing instructions based on these "
        "patterns. Focus on: tone preferences, which apps they want "
        "preferred by default, how they want code and answers explained, "
        "pet peeves (things they correct), and recurring context. Each "
        "instruction is a single plain sentence under 18 words. No "
        "em-dashes, use periods or commas. No jargon. Output as a "
        "bullet list with one instruction per line, starting each with "
        "'- '. No preamble, no closing. Just the bullets."
    )


def _parse_bullets(text: str) -> list[str]:
    """Extract bullet lines from Haiku's response."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        elif len(line) > 2 and line[0].isdigit() and line[1] in ".)":
            line = line[2:].strip()
        else:
            # Skip anything that does not look like a bullet.
            continue
        # Strip any em-dashes the model slipped in despite instructions.
        # Replace with ", " so the sentence still reads cleanly.
        line = line.replace("\u2014", ", ").replace("\u2013", ", ")
        if line:
            out.append(line)
    return out


def _fallback_suggestions(
    integrations: dict[str, bool],
    feedback: list[str],
) -> list[str]:
    """Safe deterministic suggestions for when Haiku is unavailable.

    We still want the user to see something actionable on click, even
    offline or without an API key. These reflect real signals: which
    apps are connected and whether any feedback patterns exist.
    """
    out: list[str] = [
        "Always explain things in plain language, no jargon.",
        "Never use em-dashes. Use periods, commas, or rewrite the sentence.",
        "Keep replies short unless I ask for detail.",
    ]
    if integrations.get("calendar"):
        out.append("Prefer Google Calendar over other calendar apps.")
    if integrations.get("gmail"):
        out.append("Prefer Gmail for email drafts and replies.")
    if integrations.get("slack"):
        out.append("Use Slack for quick team messages, email for longer notes.")
    if integrations.get("github"):
        out.append("Push code changes to GitHub, never commit straight to main.")
    if feedback:
        out.append("Check my correction patterns before assuming defaults.")
    return out[:10]


async def suggest_standing_instructions(
    chat_path: Optional[Path] = None,
    memory_dir_override: Optional[Path] = None,
    myos_dir: Optional[Path] = None,
) -> list[str]:
    """Return 5 to 10 suggested standing instructions for the user.

    Pulls signals, asks Haiku, parses bullets. On any failure returns a
    sensible deterministic fallback so the UI is never empty.
    """
    chat_samples = _read_chat_samples(chat_path or CHAT_HISTORY_PATH)
    feedback = _read_feedback_summaries(memory_dir_override or MEMORY_DIR)
    integrations = _read_integration_flags(myos_dir or MYOS_DIR)
    agent_aliases = _read_agent_aliases(myos_dir or MYOS_DIR)

    api_key = await _resolve_api_key()
    if not api_key:
        return _fallback_suggestions(integrations, feedback)

    prompt = _build_prompt(chat_samples, feedback, integrations, agent_aliases)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return _fallback_suggestions(integrations, feedback)

    if not response.content:
        return _fallback_suggestions(integrations, feedback)

    text = ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            text = getattr(block, "text", "") or ""
            if text:
                break

    bullets = _parse_bullets(text)
    if not bullets:
        return _fallback_suggestions(integrations, feedback)
    return bullets[:10]


def collect_signals_summary(
    chat_path: Optional[Path] = None,
    memory_dir_override: Optional[Path] = None,
    myos_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Return the raw signals (for tests and debug endpoints)."""
    return {
        "chat_samples": _read_chat_samples(chat_path or CHAT_HISTORY_PATH),
        "feedback": _read_feedback_summaries(memory_dir_override or MEMORY_DIR),
        "integrations": _read_integration_flags(myos_dir or MYOS_DIR),
        "agent_aliases": _read_agent_aliases(myos_dir or MYOS_DIR),
    }

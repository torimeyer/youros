"""
receipts_check — post-reply evidence gate (→1534).

check(message_text, tool_results) -> "present" | "missing" | "no_trigger"

Scans the assistant reply for completion trigger words ("done", "fixed", …).
If found, checks the message text and same-turn tool call results for at
least one piece of evidence: a commit hash, test output, a file:line
citation, or a testid marker.
"""
from __future__ import annotations

import re
from typing import Literal

# Words that assert work is finished.
_TRIGGER_WORDS = (
    "done", "fixed", "landed", "committed", "passing",
    "shipped", "complete", "resolved", "merged",
)

_TRIGGER_RE = re.compile(
    r"(?<!\w)(" + "|".join(_TRIGGER_WORDS) + r")(?!\w)",
    re.IGNORECASE,
)

# Strip backtick-quoted spans before trigger scanning so "done" inside
# `output: done` or `"status": "complete"` does not fire.
_BACKTICK_RE = re.compile(r"`[^`\n]*`")

# Evidence patterns — any one clears the trigger.
# 7-to-40 lowercase hex chars (standalone — not part of a longer word).
_COMMIT_RE = re.compile(r"(?<!\w)[0-9a-f]{7,40}(?!\w)", re.IGNORECASE)
# file/path:linenumber  e.g.  api/services/chat.py:42
_FILE_LINE_RE = re.compile(r"[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,6}:\d+")
# Common test-output signals.
_TEST_OUTPUT_RE = re.compile(
    r"\d+ passed|\d+ failed|PASSED|FAILED|exit 0|exit 1|✓|✗|pytest",
    re.IGNORECASE,
)
# DOM testid / aria-label verification markers.
_TESTID_RE = re.compile(r"data-testid=|testid|aria-label=", re.IGNORECASE)

ReceiptsStatus = Literal["present", "missing", "no_trigger"]


def check(message_text: str, tool_results: list[str]) -> ReceiptsStatus:
    """Scan a reply message for trigger words and evidence.

    Args:
        message_text:  Full assistant reply text.
        tool_results:  Texts from tool call results during the same turn.
                       Each is capped at 500 chars to keep detection fast.

    Returns:
        "no_trigger"  No completion claim found — gate is silent.
        "present"     Claim found with supporting evidence.
        "missing"     Claim found, no evidence — show warning pill.
    """
    scannable = _BACKTICK_RE.sub("", message_text)
    if not _TRIGGER_RE.search(scannable):
        return "no_trigger"

    # Check full message text (evidence may live in backtick spans, e.g. `abc1234`).
    if _has_evidence(message_text):
        return "present"

    # Also check each tool result from this turn.
    for tr in tool_results:
        if _has_evidence(tr[:500]):
            return "present"

    return "missing"


def _has_evidence(text: str) -> bool:
    return bool(
        _COMMIT_RE.search(text)
        or _FILE_LINE_RE.search(text)
        or _TEST_OUTPUT_RE.search(text)
        or _TESTID_RE.search(text)
    )

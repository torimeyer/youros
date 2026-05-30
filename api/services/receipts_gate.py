"""
receipts_gate — post-reply verification hook (→1397).

check_receipts(reply_text) returns a ReceiptsWarning when the reply uses a
completion word ("done", "fixed", …) but does NOT include evidence such as a
commit hash, verbatim test output, a file:line reference, or a testid.

Returns None when no trigger word is found or when evidence is present.
"""
import re
from dataclasses import dataclass
from typing import Optional

# Words that assert work is finished.
_TRIGGER_WORDS = (
    "done", "fixed", "landed", "committed", "passing",
    "shipped", "complete", "resolved",
)

_TRIGGER_RE = re.compile(
    r"(?<!\w)(" + "|".join(_TRIGGER_WORDS) + r")(?!\w)",
    re.IGNORECASE,
)

# Evidence patterns — any one of these clears the warning.

# 7-to-40 hex char commit hash (standalone word boundary).
# NOTE: single-char lookbehind covers `abc1234` (direct) but not mid-span cases
# like `commit abc1234`. Pre-strip code spans via _strip_code_spans before matching.
_COMMIT_RE = re.compile(r"(?<![`'\"])(?<!\w)[a-f0-9]{7,40}(?!\w)", re.IGNORECASE)

# Matches markdown inline-code (`...`) and fenced code blocks (```...```).
# Used to strip code spans before hash searching so hashes inside spans
# (e.g. `run abc1234`) are never counted as receipt evidence.
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def _strip_code_spans(text: str) -> str:
    return _CODE_SPAN_RE.sub("", text)

# file/path:linenumber  e.g.  api/services/chat.py:42
_FILE_LINE_RE = re.compile(
    r"[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,6}:\d+"
)

# Common verbatim test-output signals.
_TEST_OUTPUT_RE = re.compile(
    r"PASSED|FAILED|\d+ passed|\d+ failed|exit 0|exit 1|✓|✗|errors found|pytest|tests? (run|passed|failed)",
    re.IGNORECASE,
)

# data-testid or aria-label markers (indicates DOM verification).
_TESTID_RE = re.compile(r'data-testid=|testid|aria-label=', re.IGNORECASE)

# Code-context signals — at least one must be present before the gate fires (→1608).
# A reply with no code signals used a completion word in a non-code context.
_CODE_CONTEXT_RE = re.compile(
    r"`|"                                                    # backtick = code formatting
    r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_./-]+\.[a-zA-Z]{2,6}|"   # path/to/file.ext pattern
    r"\bcommit|\bPR\b|\bbranch\b|\bdiff\b|\bpatch\b",      # git vocabulary
    re.IGNORECASE,
)


@dataclass
class ReceiptsWarning:
    trigger_word: str
    message: str


def check_receipts(reply_text: str) -> Optional[ReceiptsWarning]:
    """Return a ReceiptsWarning if the reply claims completion without evidence."""
    m = _TRIGGER_RE.search(reply_text)
    if not m:
        return None

    bare_text = _strip_code_spans(reply_text)
    if (
        _COMMIT_RE.search(bare_text)
        or _FILE_LINE_RE.search(reply_text)
        or _TEST_OUTPUT_RE.search(reply_text)
        or _TESTID_RE.search(reply_text)
    ):
        return None

    # Only warn if the reply also has code-context signals — completion words in
    # brainstorming/conversational replies must not fire the gate (→1608).
    if not _CODE_CONTEXT_RE.search(reply_text):
        return None

    trigger_word = m.group(1).lower()
    return ReceiptsWarning(
        trigger_word=trigger_word,
        message=(
            f"I said '{trigger_word}' but didn't show any proof. "
            "Don't take my word for it. Ask me to back it up."
        ),
    )


def check_brief_receipts(brief_text: str) -> Optional[ReceiptsWarning]:
    """Like check_receipts but skips the _CODE_CONTEXT_RE guard — spawn briefs are
    always a code context so the guard must not suppress warnings (→1397)."""
    m = _TRIGGER_RE.search(brief_text)
    if not m:
        return None
    bare_text = _strip_code_spans(brief_text)
    if (
        _COMMIT_RE.search(bare_text)
        or _FILE_LINE_RE.search(brief_text)
        or _TEST_OUTPUT_RE.search(brief_text)
        or _TESTID_RE.search(brief_text)
    ):
        return None
    trigger_word = m.group(1).lower()
    return ReceiptsWarning(
        trigger_word=trigger_word,
        message=(
            f"This task says '{trigger_word}' but shows no proof. "
            "Ask for the change, test result, or file before trusting it."
        ),
    )

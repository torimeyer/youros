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
_COMMIT_RE = re.compile(r"(?<![`'\"])(?<!\w)[a-f0-9]{7,40}(?!\w)", re.IGNORECASE)

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


@dataclass
class ReceiptsWarning:
    trigger_word: str
    message: str


def check_receipts(reply_text: str) -> Optional[ReceiptsWarning]:
    """Return a ReceiptsWarning if the reply claims completion without evidence."""
    m = _TRIGGER_RE.search(reply_text)
    if not m:
        return None

    if (
        _COMMIT_RE.search(reply_text)
        or _FILE_LINE_RE.search(reply_text)
        or _TEST_OUTPUT_RE.search(reply_text)
        or _TESTID_RE.search(reply_text)
    ):
        return None

    trigger_word = m.group(1).lower()
    return ReceiptsWarning(
        trigger_word=trigger_word,
        message=(
            f"This reply says '{trigger_word}' but I don't see a commit hash, "
            "test output, or file reference as evidence."
        ),
    )

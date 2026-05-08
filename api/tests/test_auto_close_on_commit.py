"""Tests for auto-close-needle-on-commit hook behavior (needle →1019).

The .githooks/post-commit hook greps commit messages for →NN–→NNNN patterns
and runs `ostk work close` for each match. When no arrow pattern is present,
the hook must be a complete no-op — no close attempt, no error.
"""

import re


def _extract_needle_refs(commit_message: str) -> list[str]:
    """Extract →NN to →NNNN needle references from a commit message.

    Mirrors the shell regex used by .githooks/post-commit:
        echo "$msg" | grep -oE '→[0-9]{2,4}'

    Only the Unicode right-arrow U+2192 followed by exactly 2-4 digits
    qualifies. A bare dash-arrow (->NNN) or a single-digit suffix is
    intentionally excluded to match the hook's behaviour.
    """
    return re.findall(r'→\d{2,4}', commit_message)


def test_auto_close_no_arrow_4018():
    """Commit messages without a →NNNN pattern must yield no needle refs.

    The post-commit hook must NOT call `ostk work close` when the commit
    message has no arrow reference. This guards against the hook silently
    doing nothing useful (or, in a hypothetical bug, matching the wrong
    text and closing an unrelated needle).

    Covers →1019: "commit without arrow, verify no-op".
    """
    no_arrow_messages = [
        "fix: regular commit without any needle reference",
        "chore: update dependencies",
        "docs: update README",
        "feat: add new feature to the dashboard",
        "",
        "fix bug in login flow",
        # Single-digit arrow — below the 2-digit minimum, must not match.
        "fix: →1 single digit should not match",
        # ASCII dash-arrow is not the Unicode arrow the hook uses.
        "fix: ->1023 ascii arrow should not match",
    ]
    for msg in no_arrow_messages:
        refs = _extract_needle_refs(msg)
        assert refs == [], (
            f"Expected no needle refs from {msg!r}, got {refs!r}"
        )


def test_auto_close_with_arrow_extracts_refs():
    """Sanity check: messages WITH a →NNNN pattern do return refs.

    This is the positive control that confirms _extract_needle_refs
    actually works — if it always returned [] the no-arrow test would
    pass vacuously.
    """
    cases = [
        ("fix: →1023 closes the needle", ["→1023"]),
        ("fix(→1023): close it", ["→1023"]),
        ("feat: →99 and →1000 multi", ["→99", "→1000"]),
        ("chore: →99 two-digit", ["→99"]),
    ]
    for msg, expected in cases:
        refs = _extract_needle_refs(msg)
        assert refs == expected, (
            f"For {msg!r}: expected {expected!r}, got {refs!r}"
        )

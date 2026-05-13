"""Tests for auto-close-needle-on-commit hook behavior (needle →1019).

The .githooks/post-commit hook greps the SUBJECT LINE for →NN–→NNNN patterns
and runs `ostk work close` for each match. Two guards must BOTH pass:
  1. Commit type (first line) must be fix/feat/perf/refactor.
  2. Needle refs are extracted from the subject line ONLY — body mentions
     ("see →NNN", "found in →NNN") are not resolutions and must be ignored.

docs/test/chore/style/ci/build commits are always skipped entirely.
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


def _extract_subject_needle_refs(commit_message: str) -> list[str]:
    """Extract →NN to →NNNN refs from the SUBJECT LINE ONLY.

    Mirrors the updated hook logic: only the first line is scanned for
    needle refs to close. Body lines may mention needles for context
    without triggering closure.
    """
    subject = commit_message.splitlines()[0] if commit_message else ""
    return re.findall(r'→\d{2,4}', subject)


def _is_auto_close_subject(commit_message: str) -> bool:
    """Return True only when the commit type permits auto-close.

    Mirrors the shell logic in .githooks/post-commit:
        commit_type=$(echo "$subject" | sed -n 's/^\\([a-z][a-z]*\\)[:(].*/\\1/p')
        case "$commit_type" in fix|feat|perf|refactor) ;; *) exit 0 ;; esac

    Only fix/feat/perf/refactor subjects may auto-close needles.
    docs/test/chore/style/ci/build are tracking mentions, not resolutions.
    """
    subject = commit_message.splitlines()[0] if commit_message else ""
    m = re.match(r'^([a-z]+)[:(]', subject)
    if not m:
        return False
    return m.group(1) in {"fix", "feat", "perf", "refactor"}


def _needles_that_would_close(commit_message: str) -> list[str]:
    """Return the needle refs that the hook would actually close.

    Both guards must pass: fix-shaped type AND ref appears in subject line.
    Body-only refs are never closed even for fix-type commits.
    """
    if not _is_auto_close_subject(commit_message):
        return []
    return _extract_subject_needle_refs(commit_message)


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


def test_auto_close_subject_allows_fix_shaped_types():
    """fix/feat/perf/refactor subjects must be allowed to auto-close.

    These commit types claim to fix or ship something, so any needle
    reference in the subject is a resolution signal.
    """
    allowed = [
        "fix(auth): resolve login bug →1281",
        "fix: →1281 closes it",
        "feat(gems): ship new gems index →1023",
        "feat: add dashboard widget (closes →99)",
        "perf(api): reduce query time →1100",
        "perf: faster load →44",
        "refactor(hooks): clean up auto-close logic →1282",
        "refactor: extract helper →77",
    ]
    for msg in allowed:
        assert _is_auto_close_subject(msg), (
            f"Expected {msg!r} to be allowed for auto-close, but it was blocked"
        )


def test_auto_close_subject_blocks_non_fix_types():
    """docs/test/chore/style/ci/build subjects must NOT trigger auto-close.

    These commit types reference needles as mentions or traceability links,
    not as resolutions. Filing a needle in a docs commit (e.g. 'filed →1281')
    must never close it.

    Regression case: docs(review): →1241 action doc sanity check — filed →1281
    triggered auto-close of →1281 on 2026-05-13 before this fix.
    """
    blocked = [
        "docs(review): →1241 action doc sanity check — 5 pass, 0 needs-rewrite; filed →1281 for dead button keys",
        "docs: update README, see →1023",
        "test(rag): regression test for →1023",
        "test: add coverage for →99",
        "chore(needles): see →1281",
        "chore: bump deps, tracks →44",
        "style(ui): format fixes related to →77",
        "ci(deploy): pipeline change ref →100",
        "build(docker): update image, via →200",
    ]
    for msg in blocked:
        assert not _is_auto_close_subject(msg), (
            f"Expected {msg!r} to be BLOCKED from auto-close, but it was allowed"
        )


def test_auto_close_combined_no_close_when_docs_type():
    """End-to-end: docs commit with →NNN refs must not close any needle.

    Even though _extract_needle_refs finds refs, _is_auto_close_subject
    returns False so the hook exits early without closing anything.
    This is the exact scenario that caused →1281 to be false-closed.
    """
    msg = "docs(review): →1241 action doc sanity check — 5 pass, 0 needs-rewrite; filed →1281 for dead button keys"
    refs = _extract_needle_refs(msg)
    # The regex finds refs — the old hook would have closed them
    assert "→1281" in refs, "regression: test must find →1281 in the docs commit"
    # But the type guard must block execution
    assert _needles_that_would_close(msg) == [], (
        "docs commit must close nothing even when refs are present"
    )


def test_auto_close_combined_closes_when_fix_type_and_subject_ref():
    """End-to-end: fix commit with →NNN in the SUBJECT closes that needle.

    Both conditions must be true: fix-shaped type AND ref in subject line.
    """
    msg = "fix(hook): resolve auto-close false-positive →1282"
    assert _needles_that_would_close(msg) == ["→1282"], (
        "fix commit with subject ref must close that needle"
    )


def test_auto_close_body_mention_does_not_close():
    """Body-only needle refs must not be closed even in a fix-type commit.

    A fix commit that mentions a needle in the body for context/traceability
    must not auto-close that needle. Only subject-line refs are resolutions.

    Regression case: fix(hook): ... body mentions →1281 for context
    previously closed →1281 because the hook scanned the full message.
    """
    msg = (
        "fix(hook): commit auto-close only fires from fix/feat/perf/refactor subjects\n"
        "\n"
        "Previously the auto-close grep matched any →1281 token in the commit message,\n"
        "so a docs commit mentioning a needle would close it. Refiled as →1282."
    )
    # Body contains refs but subject does not
    assert _extract_subject_needle_refs(msg) == [], (
        "subject line must contain no refs"
    )
    assert _needles_that_would_close(msg) == [], (
        "fix commit with refs only in body must not close any needle"
    )

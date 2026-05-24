"""Enforce that tests never leak .md files into ``~/.myos/files/``.

Any test that writes a markdown file has one of two responsibilities:

1. Write into a patched ``MYOS_FILES_DIR`` / fake home (``tmp_path`` or
   ``tempfile.TemporaryDirectory()``) so the real user directory is
   untouched.
2. If it MUST write to the real ``~/.myos/files/`` (because it is
   validating surface behavior like the Recent Documents endpoint),
   clean up in a ``try/finally`` block before the test returns.

This module owns a session-scoped autouse fixture that snapshots the
contents of ``~/.myos/files/`` at suite start and asserts the same set
is there at suite end. Any leak fails the suite with the list of new
paths so the offending test is easy to find. Tori sees a test-y file
show up in Recent Documents. She should not.

The fixture snapshots by (name, mtime) so overwriting an existing user
file also flags as a leak.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _myos_files_snapshot() -> dict[str, float]:
    """Return {relative_name: mtime} for every file in ~/.myos/files/.

    Uses ``os.path.expanduser`` against the process-level HOME so the
    check always reads the real user directory, not a per-test patched
    one. Missing directory returns an empty dict.
    """
    root = Path(os.path.expanduser("~/.myos/files"))
    if not root.exists():
        return {}
    snap: dict[str, float] = {}
    for path in root.rglob("*"):
        if path.is_file():
            try:
                snap[str(path.relative_to(root))] = path.stat().st_mtime
            except OSError:
                # File disappeared between listing and stat. Ignore.
                continue
    return snap


@pytest.fixture(scope="session", autouse=True)
def _myos_files_hygiene():
    """Fail the suite if any test leaks a file into ``~/.myos/files/``.

    Runs once per suite. Captures the snapshot before collection, yields
    to let the full suite run, then diffs the snapshot after. Any
    added or modified file surfaces as an assertion failure listing
    what leaked so the offending test is obvious.
    """
    before = _myos_files_snapshot()
    yield
    after = _myos_files_snapshot()

    added = sorted(set(after) - set(before))
    modified = sorted(
        name for name in set(before) & set(after)
        if after[name] != before[name]
    )

    problems: list[str] = []
    if added:
        problems.append(
            "New files landed in ~/.myos/files/ during the test suite:\n  "
            + "\n  ".join(added)
        )
    if modified:
        problems.append(
            "Existing files in ~/.myos/files/ were modified by tests:\n  "
            + "\n  ".join(modified)
        )
    assert not problems, (
        "Test artefact leak detected. Tests must write to a patched "
        "MYOS_FILES_DIR / fake home (tmp_path or TemporaryDirectory), "
        "or clean up in a try/finally. Problems:\n\n"
        + "\n\n".join(problems)
    )


def test_hygiene_fixture_snapshot_is_defined():
    """Self-check: the session fixture above ran, so the snapshot is reachable."""
    snap = _myos_files_snapshot()
    # Either the directory exists (dict) or it does not (empty dict).
    # Both are valid. Just assert the helper returns a dict so the
    # import path is exercised and the file does not become dead code.
    assert isinstance(snap, dict)

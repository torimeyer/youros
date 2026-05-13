"""
Regression tests for →1199: [loadavg] needles count always shows 0.

Root cause: the v6.0.0 binary reads needle/fleet/nudge counts from a
per-session cache initialised to 0 at boot and never refreshed from disk.
[ctx] is correct because build_heartbeat() reads needles/issues.jsonl on
every invocation. [loadavg] must use the same live-read approach.

These tests verify that the canonical data source (needles/issues.jsonl read
live via `ostk work list --count`) returns a non-zero value that matches what
the Python API reports, ensuring the underlying data path is correct and will
produce accurate counts when the binary fix is applied.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OSTK_DIR = REPO_ROOT / ".ostk"
ISSUES_JSONL = OSTK_DIR / "needles" / "issues.jsonl"


# ── helpers ──────────────────────────────────────────────────────────────────


def _count_open_from_disk(issues_path: Path) -> int:
    """Count strictly-open needles using last-write-wins across rotated files.

    JSONL files are append-only logs: the last entry for each needle ID is
    the authoritative status. The CLI (`ostk work list --status open --count`)
    reads issues.jsonl.1 (older rotation) then issues.jsonl (current), so
    current-file entries override older ones. We mirror that here.
    """
    if not issues_path.exists():
        return 0

    # Build ordered list of files: rotated first, current last (current wins)
    rotated = issues_path.parent / (issues_path.name + ".1")
    files = [f for f in [rotated, issues_path] if f.exists()]

    state: dict = {}  # id -> last status seen
    for fpath in files:
        for line in fpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            nid = rec.get("id", "")
            if nid:
                state[nid] = rec.get("status", "")

    return sum(1 for s in state.values() if s == "open")


def _ostk_work_count() -> int:
    """Run `ostk work list --status open --count` and return the integer."""
    result = subprocess.run(
        ["ostk", "work", "list", "--status", "open", "--count"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return int(result.stdout.strip())


# ── tests ─────────────────────────────────────────────────────────────────────


def test_issues_jsonl_exists_and_has_open_needles():
    """Sanity check: needles/issues.jsonl must exist and contain open needles.

    If this fails, the data source for [loadavg] is genuinely missing — a
    different problem from the caching bug.
    """
    assert ISSUES_JSONL.exists(), (
        f"needles/issues.jsonl not found at {ISSUES_JSONL}; "
        "the [loadavg] data source is missing entirely"
    )
    count = _count_open_from_disk(ISSUES_JSONL)
    assert count > 0, (
        "[loadavg] data source (needles/issues.jsonl) shows 0 open needles; "
        "either the file is empty or all needles are closed"
    )


def test_live_disk_count_matches_ostk_cli():
    """The live disk read must agree with `ostk work list --status open --count`.

    This is the invariant [loadavg] violates: it reads a stale cache (0)
    instead of reading live from disk (N > 0). The fix is to call
    count_open_needles(issues_path) on every tool response, exactly as
    build_heartbeat() does for [ctx].
    """
    if not ISSUES_JSONL.exists():
        pytest.skip("needles/issues.jsonl not present — skipping CLI comparison")

    disk_count = _count_open_from_disk(ISSUES_JSONL)
    cli_count = _ostk_work_count()

    assert disk_count == cli_count, (
        f"Live disk count ({disk_count}) disagrees with "
        f"`ostk work list --status open --count` ({cli_count}). "
        "The two should always agree — this indicates data corruption."
    )


def test_live_count_is_nonzero_not_like_loadavg():
    """[loadavg] always shows 0; the correct answer must be > 0.

    This test fails if someone accidentally makes the count 0 in the future
    by closing all needles without updating this test. Keep at least one
    open needle in the backlog to catch regressions.
    """
    if not ISSUES_JSONL.exists():
        pytest.skip("needles/issues.jsonl not present")
    count = _count_open_from_disk(ISSUES_JSONL)
    assert count > 0, (
        f"needle count from disk is 0 — [loadavg] should show {count}, "
        "not 0; if all needles are intentionally closed, remove this assertion"
    )


def test_count_open_from_disk_unit():
    """Unit test for _count_open_from_disk with synthetic data.

    Mirrors the Rust test_generate_loadavg_line_reads_live_from_disk in
    helpers.rs — both should pass or fail together.
    """
    content = "\n".join([
        json.dumps({"id": "001", "status": "open",        "priority": "P0", "title": "a"}),
        json.dumps({"id": "002", "status": "open",        "priority": "P1", "title": "b"}),
        json.dumps({"id": "003", "status": "in_progress", "priority": "P2", "title": "c"}),
        json.dumps({"id": "004", "status": "closed",      "priority": "P0", "title": "d"}),
        json.dumps({"id": "005", "status": "closed",      "priority": "P1", "title": "e"}),
        "",
    ])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        count = _count_open_from_disk(tmp_path)
        # open(001) + open(002) = 2; in_progress and closed don't count
        assert count == 2, f"expected 2 strictly-open needles, got {count}"
    finally:
        tmp_path.unlink(missing_ok=True)

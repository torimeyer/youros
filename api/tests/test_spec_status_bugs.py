"""Tests for two display bugs fixed in compute_spec_status and compute_spec_readiness.

Bug 1 (ostk.py): a spec whose frontmatter says complete/done but whose linked
task IDs have been purged from the task store was stuck "in-progress" because
absent IDs defaulted to "open".  Fix: absent == closed/archived.

Bug 2 (gemini_ready.py): a spec where every AC is already checked (- [x]) was
reported as has_ac_checkboxes:false / needs_clarity:true because _AC_RE only
matched unchecked lines.  Fix: count all AC lines (checked + unchecked).
"""
import textwrap
import pytest


# ---------------------------------------------------------------------------
# Bug 1 — compute_spec_status
# ---------------------------------------------------------------------------

from services.ostk import OstkService  # noqa: E402


def test_complete_spec_with_purged_tasks_resolves_complete():
    """Purged task IDs (absent from task_statuses) must not keep spec in-progress."""
    # task IDs 1627-1630 exist in the spec frontmatter but are gone from the store
    task_ids = ["1627", "1628", "1629", "1630"]
    task_statuses: dict[str, str] = {}  # store is empty — all purged

    result = OstkService.compute_spec_status(
        base_status="complete",
        task_ids=task_ids,
        task_statuses=task_statuses,
    )
    assert result == "complete", (
        f"Expected 'complete' but got '{result}'. "
        "Purged task IDs should be treated as closed, not open."
    )


def test_complete_spec_with_one_present_open_task_stays_in_progress():
    """A task that is present AND open must still block completion (regression guard)."""
    task_ids = ["1627", "1628"]
    task_statuses = {"1627": "closed", "1628": "open"}  # 1628 is genuinely open

    result = OstkService.compute_spec_status(
        base_status="complete",
        task_ids=task_ids,
        task_statuses=task_statuses,
    )
    assert result == "in-progress", (
        f"Expected 'in-progress' but got '{result}'. "
        "A present-and-open task must keep the spec in-progress."
    )


def test_complete_spec_all_tasks_explicitly_closed():
    """Explicit closed entries must still resolve to complete."""
    task_ids = ["1627", "1628"]
    task_statuses = {"1627": "closed", "1628": "closed"}

    result = OstkService.compute_spec_status(
        base_status="complete",
        task_ids=task_ids,
        task_statuses=task_statuses,
    )
    assert result == "complete"


def test_done_spec_with_purged_tasks_resolves_complete():
    """Same fix applies when base_status is 'done'."""
    task_ids = ["100", "101"]
    task_statuses: dict[str, str] = {}

    result = OstkService.compute_spec_status(
        base_status="done",
        task_ids=task_ids,
        task_statuses=task_statuses,
    )
    assert result == "complete"


# ---------------------------------------------------------------------------
# Bug 2 — compute_spec_readiness
# ---------------------------------------------------------------------------

from services.gemini_ready import compute_spec_readiness  # noqa: E402


def _make_spec(tmp_path, body: str) -> str:
    """Write a minimal spec file and return its path as a string."""
    p = tmp_path / "spec_test.md"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_all_checked_ac_passes_has_ac_checkboxes(tmp_path):
    """A spec where every AC is checked must pass has_ac_checkboxes."""
    spec = _make_spec(tmp_path, """
        ---
        status: complete
        ---
        # My Spec

        ## Acceptance Criteria

        - [x] When the user submits, the form saves to the database
        - [x] When the save fails, an error toast is shown
        - [x] The confirmation modal closes after save
    """)

    r = compute_spec_readiness(spec)
    checks = {c["name"]: c for c in r.as_dict()["checks"]}

    assert checks["has_ac_checkboxes"]["passed"] is True, (
        "has_ac_checkboxes must pass when all ACs are checked (- [x])"
    )


def test_all_checked_ac_needs_clarity_false(tmp_path):
    """A spec with all ACs checked must not report needs_clarity."""
    spec = _make_spec(tmp_path, """
        ---
        status: complete
        ---
        # Complete Spec

        ## Acceptance Criteria

        - [x] When the pipeline runs, output is written to /tmp/out
        - [x] Errors are logged to stderr with a timestamp
    """)

    r = compute_spec_readiness(spec)
    # needs_clarity is derived by the router from r.ready, but here we check
    # that has_ac_checkboxes passes so the spec isn't forced to needs_clarity.
    checks = {c["name"]: c for c in r.as_dict()["checks"]}
    assert checks["has_ac_checkboxes"]["passed"] is True


def test_no_ac_section_fails_has_ac_checkboxes(tmp_path):
    """A spec with no AC section at all must still fail has_ac_checkboxes (regression)."""
    spec = _make_spec(tmp_path, """
        ---
        status: draft
        ---
        # Incomplete Spec

        Some description with no acceptance criteria at all.
    """)

    r = compute_spec_readiness(spec)
    checks = {c["name"]: c for c in r.as_dict()["checks"]}

    assert checks["has_ac_checkboxes"]["passed"] is False, (
        "A spec with no AC lines at all must fail has_ac_checkboxes"
    )


def test_mixed_ac_unchecked_items_remain(tmp_path):
    """A spec with some unchecked ACs must still pass has_ac_checkboxes (regression)."""
    spec = _make_spec(tmp_path, """
        ---
        status: building
        ---
        # Partially Done Spec

        ## Acceptance Criteria

        - [x] Step one is done
        - [ ] Step two is still pending
    """)

    r = compute_spec_readiness(spec)
    checks = {c["name"]: c for c in r.as_dict()["checks"]}

    assert checks["has_ac_checkboxes"]["passed"] is True, (
        "A spec with at least one AC line (checked or not) must pass has_ac_checkboxes"
    )

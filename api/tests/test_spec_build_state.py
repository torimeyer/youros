"""Tests for →1420: auto-promote spec from ready to building when implementation begins.

Root cause: _set_spec_status writes "building" to frontmatter when build_spec
is called, but compute_spec_status("building", [], {}) returns "ready" because
the no-tasks branch defaults everything to "ready". Specs appear stuck in
"Ready" state even while builder agents are actively running.

Fix: compute_spec_status must treat "building" as "in-progress" so the UI
shows the correct state immediately after build starts, with or without
task IDs in the frontmatter.
"""
import pytest
from services.ostk import OstkService


class TestComputeSpecStatusBuilding:
    """compute_spec_status must not revert "building" back to "ready"."""

    def test_building_with_no_tasks_returns_in_progress(self):
        """Core bug: spec flagged as building with no tasks yet must NOT show Ready.

        Scenario: build_spec called, _set_spec_status wrote "building" to
        frontmatter, but tasks haven't been linked in frontmatter yet (e.g.
        decompose is still running). The next list_docs call must show
        in-progress (displayed as "Building"), not "ready" (displayed as "Ready").
        """
        status = OstkService.compute_spec_status("building", [], {})
        assert status == "in-progress", (
            f"Expected 'in-progress' for building spec with no tasks, got '{status}'. "
            "A spec whose frontmatter says 'building' must NOT revert to 'ready' "
            "just because task IDs haven't landed in the frontmatter yet."
        )

    def test_building_with_open_tasks_returns_in_progress(self):
        """Spec in building state with open tasks must show in-progress."""
        status = OstkService.compute_spec_status(
            "building",
            ["1001", "1002"],
            {"1001": "open", "1002": "open"},
        )
        assert status == "in-progress", (
            f"Expected 'in-progress' for building spec with open tasks, got '{status}'."
        )

    def test_building_with_all_tasks_closed_returns_complete(self):
        """Spec in building state completes when all its tasks are closed."""
        status = OstkService.compute_spec_status(
            "building",
            ["1001", "1002"],
            {"1001": "closed", "1002": "closed"},
        )
        assert status == "complete", (
            f"Expected 'complete' for building spec with all tasks closed, got '{status}'."
        )

    def test_building_with_mixed_tasks_returns_in_progress(self):
        """Spec in building state with some open tasks stays in-progress."""
        status = OstkService.compute_spec_status(
            "building",
            ["1001", "1002", "1003"],
            {"1001": "closed", "1002": "open", "1003": "closed"},
        )
        assert status == "in-progress", (
            f"Expected 'in-progress' for building spec with one open task, got '{status}'."
        )

    def test_ready_with_no_tasks_still_returns_ready(self):
        """Sanity: a spec that is simply ready (not building) still shows ready."""
        status = OstkService.compute_spec_status("ready", [], {})
        assert status == "ready", (
            f"Expected 'ready' for a ready spec with no tasks, got '{status}'."
        )

    def test_draft_unchanged(self):
        """Draft status must pass through unchanged."""
        status = OstkService.compute_spec_status("draft", [], {})
        assert status == "draft"

    def test_plan_unchanged(self):
        """Plan status must pass through unchanged."""
        status = OstkService.compute_spec_status("plan", [], {})
        assert status == "plan"

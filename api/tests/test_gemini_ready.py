"""Tests for api/services/gemini_ready.py — all 6 readiness checks.

Strategy: tests that exercise checks 2-6 need a description with a path
matching the plan-path regex. We write fixture content to temp files in
~/.claude/plans/ (the canonical plan location) and clean up after each test.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gemini_ready"
PLANS_DIR = Path(os.path.expanduser("~/.claude/plans"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


@contextmanager
def _plan_file(fixture_name: str):
    """Write a fixture to ~/.claude/plans/ and yield its absolute path."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        dir=str(PLANS_DIR),
        prefix="test-gr-",
        delete=False,
    ) as f:
        f.write(_fixture_text(fixture_name))
        path = f.name
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _task(description: str = "", blocked_by: list | None = None) -> dict:
    return {
        "id": "→1467",
        "title": "Test task",
        "description": description,
        "status": "open",
        "blocked_by": blocked_by if blocked_by is not None else [],
    }


# ---------------------------------------------------------------------------
# Import (fails RED until service exists)
# ---------------------------------------------------------------------------

from services.gemini_ready import (  # noqa: E402
    compute_task_readiness,
    compute_spec_readiness,
    Readiness,
    ReadinessCheck,
)


# ---------------------------------------------------------------------------
# Check 1 — plan path extracted from task description
# ---------------------------------------------------------------------------

class TestCheck1PlanPathPresent:
    def test_pass_docs_spec_relative(self):
        task = _task(description="See docs/spec/my-feature.md for details.")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is True

    def test_pass_tilde_myos_specs(self):
        task = _task(description="Plan at ~/.myos/specs/feature.md")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is True

    def test_pass_tilde_claude_plans(self):
        task = _task(description="See ~/.claude/plans/gemini-plan.md")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is True

    def test_pass_absolute_claude_plans(self):
        home = os.path.expanduser("~")
        task = _task(description=f"See {home}/.claude/plans/gemini-plan.md")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is True

    def test_fail_no_path(self):
        task = _task(description="Just a plain description with no plan link.")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is False
        assert result.ready is False

    def test_fail_empty_description(self):
        task = _task(description="")
        result = compute_task_readiness(task)
        check = _get_check(result, "plan_path_present")
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 2 — plan file exists on disk
# ---------------------------------------------------------------------------

class TestCheck2FileExists:
    def test_pass_file_exists(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"See {path} for details.")
            result = compute_task_readiness(task)
            check = _get_check(result, "file_exists")
            assert check.passed is True

    def test_fail_file_missing(self):
        home = os.path.expanduser("~")
        task = _task(description=f"{home}/.claude/plans/does-not-exist-xyz.md")
        result = compute_task_readiness(task)
        check = _get_check(result, "file_exists")
        assert check.passed is False
        assert result.ready is False

    def test_spec_readiness_pass_existing_file(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "file_exists")
        assert check.passed is True

    def test_spec_readiness_fail_missing_file(self):
        result = compute_spec_readiness("/nonexistent/path/spec.md")
        check = _get_check(result, "file_exists")
        assert check.passed is False
        assert result.ready is False


# ---------------------------------------------------------------------------
# Check 3 — file has at least one `- [ ]` AC checkbox
# ---------------------------------------------------------------------------

class TestCheck3HasAC:
    def test_pass_has_checkboxes(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "has_ac_checkboxes")
            assert check.passed is True

    def test_fail_no_checkboxes(self):
        with _plan_file("fail_no_ac.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "has_ac_checkboxes")
            assert check.passed is False
            assert result.ready is False

    def test_spec_pass_has_checkboxes(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "has_ac_checkboxes")
        assert check.passed is True

    def test_spec_fail_no_checkboxes(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_no_ac.md"))
        check = _get_check(result, "has_ac_checkboxes")
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 4 — no vague AC (TBD / ? / "should we" / "decide" / TODO)
# ---------------------------------------------------------------------------

class TestCheck4NoVagueAC:
    def test_pass_clean_ac(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "no_vague_ac")
            assert check.passed is True

    def test_fail_tbd_in_ac(self):
        with _plan_file("fail_vague_ac.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "no_vague_ac")
            assert check.passed is False
            assert result.ready is False

    def test_spec_fail_vague_ac(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_vague_ac.md"))
        check = _get_check(result, "no_vague_ac")
        assert check.passed is False

    def test_vague_tokens_covered(self):
        """All 5 disqualifying tokens cause check 4 to fail."""
        tokens = ["TBD", "?", "should we", "decide", "TODO"]
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        for token in tokens:
            content = (
                "- [ ] implement `api/services/foo.py`\n"
                f"- [ ] {token}: something unclear\n"
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", dir=str(PLANS_DIR),
                prefix="test-gr-vague-", delete=False,
            ) as f:
                f.write(content)
                tmp = f.name
            try:
                task = _task(description=f"Plan: {tmp}")
                result = compute_task_readiness(task)
                check = _get_check(result, "no_vague_ac")
                assert check.passed is False, f"Expected fail for token '{token}'"
            finally:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass


# ---------------------------------------------------------------------------
# Check 5 — file body contains at least one explicit file path
# ---------------------------------------------------------------------------

class TestCheck5HasFilePaths:
    def test_pass_has_file_paths(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "has_file_paths")
            assert check.passed is True

    def test_fail_no_file_paths(self):
        with _plan_file("fail_no_files.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            check = _get_check(result, "has_file_paths")
            assert check.passed is False
            assert result.ready is False

    def test_spec_pass_has_file_paths(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "has_file_paths")
        assert check.passed is True

    def test_spec_fail_no_file_paths(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_no_files.md"))
        check = _get_check(result, "has_file_paths")
        assert check.passed is False


# ---------------------------------------------------------------------------
# Check 6 — task is not blocked
# ---------------------------------------------------------------------------

class TestCheck6Unblocked:
    def test_pass_empty_blocked_by(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}", blocked_by=[])
            result = compute_task_readiness(task)
            check = _get_check(result, "is_unblocked")
            assert check.passed is True

    def test_pass_all_blockers_resolved(self):
        with _plan_file("pass_all.md") as path:
            task = _task(
                description=f"Plan: {path}",
                blocked_by=[
                    {"text": "→1400 fix the thing", "resolved": True},
                    {"text": "→1401 other thing", "resolved": True},
                ],
            )
            result = compute_task_readiness(task)
            check = _get_check(result, "is_unblocked")
            assert check.passed is True

    def test_fail_open_blocker(self):
        with _plan_file("pass_all.md") as path:
            task = _task(
                description=f"Plan: {path}",
                blocked_by=[{"text": "→1400 must fix first", "resolved": False}],
            )
            result = compute_task_readiness(task)
            check = _get_check(result, "is_unblocked")
            assert check.passed is False
            assert result.ready is False

    def test_spec_check6_always_passes(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "is_unblocked")
        assert check.passed is True


# ---------------------------------------------------------------------------
# Full readiness: task and spec
# ---------------------------------------------------------------------------

class TestFullReadiness:
    def test_task_fully_ready(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan at {path}.", blocked_by=[])
            result = compute_task_readiness(task)
            assert result.ready is True
            assert result.file_path == path
            assert len(result.checks) == 6
            assert all(c.passed for c in result.checks)

    def test_task_not_ready_always_6_checks(self):
        """A task with no plan path returns ready=False with 6 check entries."""
        task = _task(description="no plan link here")
        result = compute_task_readiness(task)
        assert result.ready is False
        assert len(result.checks) == 6

    def test_spec_fully_ready(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        assert result.ready is True
        assert len(result.checks) == 6

    def test_readiness_dataclass_shape(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            assert hasattr(result, "ready")
            assert hasattr(result, "file_path")
            assert hasattr(result, "checks")
            for c in result.checks:
                assert hasattr(c, "name")
                assert hasattr(c, "passed")
                assert hasattr(c, "detail")

    def test_readiness_as_dict(self):
        with _plan_file("pass_all.md") as path:
            task = _task(description=f"Plan: {path}")
            result = compute_task_readiness(task)
            d = result.as_dict()
            assert "ready" in d
            assert "file_path" in d
            assert "checks" in d
            assert isinstance(d["checks"], list)
            for c in d["checks"]:
                assert "name" in c
                assert "passed" in c
                assert "detail" in c


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_check(result: "Readiness", name: str) -> "ReadinessCheck":
    for c in result.checks:
        if c.name == name:
            return c
    names = [c.name for c in result.checks]
    raise KeyError(f"Check '{name}' not found in {names}")

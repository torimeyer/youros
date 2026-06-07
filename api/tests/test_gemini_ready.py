"""Tests for api/services/gemini_ready.py — reduced rubric (→1564).

Tasks: 3 checks (outcome_concrete, in_repo_scope, is_unblocked).
Specs: 5 checks (has_ac_checkboxes, no_vague_ac, has_file_paths,
                  referenced_files_exist, in_repo_scope).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gemini_ready"
PLANS_DIR = Path(os.path.expanduser("~/.claude/plans"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(title: str = "Implement login with JWT tokens", description: str = "", blocked_by: list | None = None) -> dict:
    return {
        "id": "→1564",
        "title": title,
        "description": description,
        "status": "open",
        "blocked_by": blocked_by if blocked_by is not None else [],
    }


from services.gemini_ready import (  # noqa: E402
    compute_task_readiness,
    compute_spec_readiness,
    Readiness,
    ReadinessCheck,
    _VAGUE_TOKENS_RE,
)


def _get_check(result: Readiness, name: str) -> ReadinessCheck:
    for c in result.checks:
        if c.name == name:
            return c
    raise KeyError(f"Check '{name}' not found in {[c.name for c in result.checks]}")


# ---------------------------------------------------------------------------
# Task readiness — exactly 3 checks
# ---------------------------------------------------------------------------

class TestTaskReadinessShape:
    def test_task_has_exactly_3_checks(self):
        result = compute_task_readiness(_task())
        assert len(result.checks) == 3

    def test_task_check_names(self):
        result = compute_task_readiness(_task())
        names = [c.name for c in result.checks]
        assert names == ["outcome_concrete", "in_repo_scope", "is_unblocked"]

    def test_task_file_path_is_none(self):
        result = compute_task_readiness(_task())
        assert result.file_path is None

    def test_task_as_dict_shape(self):
        result = compute_task_readiness(_task())
        d = result.as_dict()
        assert "ready" in d
        assert "file_path" in d
        assert "checks" in d
        assert len(d["checks"]) == 3


# ---------------------------------------------------------------------------
# Spec readiness — exactly 5 checks
# ---------------------------------------------------------------------------

class TestSpecReadinessShape:
    def test_spec_has_exactly_5_checks(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        assert len(result.checks) == 5

    def test_spec_check_names(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        names = [c.name for c in result.checks]
        assert names == [
            "has_ac_checkboxes",
            "no_vague_ac",
            "has_file_paths",
            "referenced_files_exist",
            "in_repo_scope",
        ]

    def test_spec_missing_file_has_5_checks(self):
        result = compute_spec_readiness("/nonexistent/path/spec.md")
        assert len(result.checks) == 5
        assert result.ready is False

    def test_spec_as_dict_shape(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        d = result.as_dict()
        assert len(d["checks"]) == 5


# ---------------------------------------------------------------------------
# outcome_concrete check
# ---------------------------------------------------------------------------

class TestOutcomeConcrete:
    def test_pass_concrete_title_and_description(self):
        result = compute_task_readiness(_task(
            title="Add JWT auth to /api/login endpoint",
            description="Implement token-based authentication in api/routers/auth.py",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is True

    def test_fail_empty_title(self):
        result = compute_task_readiness(_task(title="", description="some description"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False
        assert "empty" in check.detail

    def test_fail_whitespace_only_title(self):
        result = compute_task_readiness(_task(title="   ", description="some description"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_generic_title_update_x(self):
        result = compute_task_readiness(_task(title="update README"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False
        assert "generic" in check.detail

    def test_fail_generic_title_fix_x(self):
        result = compute_task_readiness(_task(title="fix bug"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False
        assert "generic" in check.detail

    def test_pass_update_with_qualifier(self):
        result = compute_task_readiness(_task(title="update README to document new auth endpoints"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is True

    def test_fail_tbd_in_description(self):
        result = compute_task_readiness(_task(
            title="Add rate limiting to API",
            description="TBD — not yet scoped",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_todo_in_title(self):
        result = compute_task_readiness(_task(title="TODO implement auth"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_should_we_in_description(self):
        result = compute_task_readiness(_task(
            title="Implement caching layer",
            description="should we use Redis or Memcached here?",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_figure_out_in_description(self):
        result = compute_task_readiness(_task(
            title="Fix pagination bug",
            description="Need to figure out why page 2 returns duplicates",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_we_ll_see_in_description(self):
        result = compute_task_readiness(_task(
            title="Add dark mode toggle",
            description="we'll see if we need it",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_decide_what_in_description(self):
        result = compute_task_readiness(_task(
            title="Refactor auth module",
            description="Need to decide what pattern to use",
        ))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False

    def test_fail_question_mark_in_title(self):
        result = compute_task_readiness(_task(title="Is this the right approach?"))
        check = _get_check(result, "outcome_concrete")
        assert check.passed is False


# ---------------------------------------------------------------------------
# _VAGUE_TOKENS_RE: false-positive tokens no longer trigger
# ---------------------------------------------------------------------------

class TestVagueTokensNarrowed:
    def test_either_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("use either approach that fits the codebase")

    def test_consider_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("consider the performance implications")

    def test_explore_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("explore the new API surface")

    def test_review_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("review the PR before merging")

    def test_depends_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("timing depends on the release schedule")

    def test_depend_not_vague(self):
        assert not _VAGUE_TOKENS_RE.search("this feature depend on the auth module")


# ---------------------------------------------------------------------------
# _VAGUE_TOKENS_RE: real vagueness markers still trigger
# ---------------------------------------------------------------------------

class TestVagueTokensStillTrigger:
    def test_tbd_triggers(self):
        assert _VAGUE_TOKENS_RE.search("approach is TBD")

    def test_question_mark_triggers(self):
        assert _VAGUE_TOKENS_RE.search("is this right?")

    def test_should_we_triggers(self):
        assert _VAGUE_TOKENS_RE.search("should we use Redis here")

    def test_todo_triggers(self):
        assert _VAGUE_TOKENS_RE.search("TODO: decide on approach")

    def test_figure_out_triggers(self):
        assert _VAGUE_TOKENS_RE.search("need to figure out the data model")

    def test_we_ll_see_triggers(self):
        assert _VAGUE_TOKENS_RE.search("we'll see if this works")

    def test_decide_what_triggers(self):
        assert _VAGUE_TOKENS_RE.search("need to decide what format to use")

    def test_maybe_triggers(self):
        assert _VAGUE_TOKENS_RE.search("maybe add a cache layer")

    def test_clarify_triggers(self):
        assert _VAGUE_TOKENS_RE.search("need to clarify the requirements")


# ---------------------------------------------------------------------------
# in_repo_scope check (task)
# ---------------------------------------------------------------------------

class TestInRepoScopeTask:
    def test_pass_normal_task(self):
        result = compute_task_readiness(_task(
            title="Add gemini-ready chip to Tasks page",
            description="Update app/src/pages/Tasks.tsx",
        ))
        check = _get_check(result, "in_repo_scope")
        assert check.passed is True

    def test_fail_upstream_title(self):
        result = compute_task_readiness(_task(title="Upstream: fix build in ostk repo"))
        check = _get_check(result, "in_repo_scope")
        assert check.passed is False

    def test_fail_upstream_body(self):
        result = compute_task_readiness(_task(
            title="Normal title",
            description="This is upstream ostk work in a different repo",
        ))
        check = _get_check(result, "in_repo_scope")
        assert check.passed is False


# ---------------------------------------------------------------------------
# is_unblocked check (task)
# ---------------------------------------------------------------------------

class TestIsUnblockedTask:
    def test_pass_no_blockers(self):
        result = compute_task_readiness(_task(blocked_by=[]))
        check = _get_check(result, "is_unblocked")
        assert check.passed is True

    def test_pass_all_resolved(self):
        result = compute_task_readiness(_task(blocked_by=[
            {"text": "→1400 done", "resolved": True},
        ]))
        check = _get_check(result, "is_unblocked")
        assert check.passed is True

    def test_fail_open_blocker(self):
        result = compute_task_readiness(_task(blocked_by=[
            {"text": "→1400 must fix first", "resolved": False},
        ]))
        check = _get_check(result, "is_unblocked")
        assert check.passed is False
        assert result.ready is False


# ---------------------------------------------------------------------------
# Spec checks
# ---------------------------------------------------------------------------

class TestSpecChecks:
    def test_pass_has_ac_checkboxes(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "has_ac_checkboxes")
        assert check.passed is True

    def test_fail_no_ac_checkboxes(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_no_ac.md"))
        check = _get_check(result, "has_ac_checkboxes")
        assert check.passed is False

    def test_pass_no_vague_ac(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "no_vague_ac")
        assert check.passed is True

    def test_fail_vague_ac(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_vague_ac.md"))
        check = _get_check(result, "no_vague_ac")
        assert check.passed is False

    def test_pass_has_file_paths(self):
        import config
        from pathlib import Path
        real_root = Path(__file__).parents[2]
        with patch("config.PROJECT_ROOT", real_root):
            result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "has_file_paths")
        assert check.passed is True

    def test_fail_no_file_paths(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_no_files.md"))
        check = _get_check(result, "has_file_paths")
        assert check.passed is False

    def test_pass_referenced_files_exist(self):
        import config
        from pathlib import Path
        real_root = Path(__file__).parents[2]
        with patch("config.PROJECT_ROOT", real_root):
            result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "referenced_files_exist")
        assert check.passed is True

    def test_pass_in_repo_scope(self):
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        check = _get_check(result, "in_repo_scope")
        assert check.passed is True

    def test_spec_fully_ready(self):
        import config
        from pathlib import Path
        real_root = Path(__file__).parents[2]
        with patch("config.PROJECT_ROOT", real_root):
            result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        assert result.ready is True
        assert len(result.checks) == 5
        assert all(c.passed for c in result.checks)

    def test_spec_no_ac_considers_has_ac_checkboxes_only_one_needed(self):
        """Spec with missing AC fails has_ac_checkboxes (the count threshold is dropped)."""
        result = compute_spec_readiness(str(FIXTURE_DIR / "fail_no_ac.md"))
        names = [c.name for c in result.checks]
        assert "ac_count_threshold" not in names
        assert "has_ac_checkboxes" in names

    def test_dropped_checks_not_in_spec(self):
        """plan_path_present, file_exists, ac_count_threshold, is_unblocked must not appear."""
        result = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md"))
        names = {c.name for c in result.checks}
        for dropped in ("plan_path_present", "file_exists", "ac_count_threshold", "is_unblocked"):
            assert dropped not in names, f"Dropped check '{dropped}' still present"

    def test_dropped_checks_not_in_task(self):
        """plan_path_present, file_exists, has_ac_checkboxes, has_file_paths,
        ac_count_threshold, referenced_files_exist must not appear in task checks."""
        result = compute_task_readiness(_task())
        names = {c.name for c in result.checks}
        for dropped in (
            "plan_path_present", "file_exists", "has_ac_checkboxes",
            "has_file_paths", "ac_count_threshold", "referenced_files_exist",
        ):
            assert dropped not in names, f"Dropped check '{dropped}' still present in task"


# ---------------------------------------------------------------------------
# Typed-spec readiness (phase 2): per-type profiles + required/optional
# ---------------------------------------------------------------------------

class TestTypedSpecReadiness:
    def _write(self, tmp_path, body: str) -> str:
        p = tmp_path / "spec.md"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_required_field_in_as_dict(self):
        d = compute_spec_readiness(str(FIXTURE_DIR / "pass_all.md")).as_dict()
        assert all("required" in c for c in d["checks"])

    def test_unknown_type_defaults_to_engineering(self, tmp_path):
        body = "---\ntype: bogus\n---\n# Add a thing\n\n- [ ] When X happens, Y is the clear result\n"
        names = [c.name for c in compute_spec_readiness(self._write(tmp_path, body)).checks]
        assert names == [
            "has_ac_checkboxes", "no_vague_ac", "has_file_paths",
            "referenced_files_exist", "in_repo_scope",
        ]

    def test_engineering_file_paths_now_optional(self, tmp_path):
        # AC present + concrete + in-scope, but no real file paths -> still ready,
        # because file-path checks are optional for engineering specs now.
        body = (
            "---\ntype: engineering\n---\n# Add a save toast to the app\n\n"
            "- [ ] When the user clicks save, the row persists\n"
            "- [ ] When the row persists, a confirmation toast appears\n"
        )
        r = compute_spec_readiness(self._write(tmp_path, body))
        by = {c.name: c for c in r.checks}
        assert by["has_ac_checkboxes"].required is True
        assert by["has_file_paths"].required is False
        assert by["referenced_files_exist"].required is False
        assert r.ready is True

    def test_vision_skips_file_paths_requires_success_measures(self, tmp_path):
        body = (
            "---\ntype: vision\n---\n# Three year vision\n\n"
            "## Success measures\n- Onboarding time cut in half by Q4\n"
        )
        r = compute_spec_readiness(self._write(tmp_path, body))
        names = [c.name for c in r.checks]
        assert "has_file_paths" not in names
        assert "has_success_measures" in names
        assert r.ready is True

    def test_vision_missing_success_measures_not_ready(self, tmp_path):
        body = "---\ntype: vision\n---\n# Vision\n\n## Why this matters\n- it matters a lot\n"
        r = compute_spec_readiness(self._write(tmp_path, body))
        sm = _get_check(r, "has_success_measures")
        assert sm.passed is False and sm.required is True
        assert r.ready is False

    def test_customer_docs_requires_audience(self, tmp_path):
        body = "---\ntype: customer_docs\n---\n# Getting started\n\n## Outline\n- intro\n"
        r = compute_spec_readiness(self._write(tmp_path, body))
        assert _get_check(r, "has_audience").required is True
        assert r.ready is False
        body2 = body + "\n## Audience\nNew users evaluating the product for the first time.\n"
        assert compute_spec_readiness(self._write(tmp_path, body2)).ready is True

    def test_prototype_requires_learning_goal(self, tmp_path):
        body = "---\ntype: prototype\n---\n# Spike\n\n## Approach\n- just try it\n"
        assert compute_spec_readiness(self._write(tmp_path, body)).ready is False
        body2 = "---\ntype: prototype\n---\n# Spike\n\n## Learning goal\nCan we render 10k rows at 60fps?\n"
        assert compute_spec_readiness(self._write(tmp_path, body2)).ready is True

    def test_explicit_type_arg_overrides_frontmatter(self, tmp_path):
        body = "---\ntype: engineering\n---\n# Doc\n\n## Audience\nEnd users.\n"
        r = compute_spec_readiness(self._write(tmp_path, body), spec_type="customer_docs")
        assert [c.name for c in r.checks] == ["has_audience", "has_outline"]
        assert r.ready is True

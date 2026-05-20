"""Tests for receipts_check.check() — spec FR-001/FR-006 (→1534)."""
import pytest


def _check(*args, **kwargs):
    from services.receipts_check import check
    return check(*args, **kwargs)


class TestNoTrigger:
    def test_no_trigger_word_returns_no_trigger(self):
        assert _check("Here is a summary of the changes.", []) == "no_trigger"

    def test_empty_string_returns_no_trigger(self):
        assert _check("", []) == "no_trigger"

    def test_partial_word_not_matched(self):
        # "undone" should not match "done"
        assert _check("The task is undone.", []) == "no_trigger"

    def test_trigger_in_backticks_not_matched(self):
        # "done" inside backtick span must be ignored
        assert _check("The output was `done loading`.", []) == "no_trigger"

    def test_trigger_in_inline_code_not_matched(self):
        assert _check('Error message said `"status": "complete"`', []) == "no_trigger"


class TestMissing:
    """Trigger found, no evidence → 'missing'."""

    def test_done_no_hash(self):
        assert _check("The feature is done.", []) == "missing"

    def test_fixed_no_evidence(self):
        assert _check("I've fixed the bug.", []) == "missing"

    def test_shipped_no_evidence(self):
        assert _check("The release is shipped.", []) == "missing"

    def test_complete_no_evidence(self):
        assert _check("Implementation is complete.", []) == "missing"

    def test_resolved_no_evidence(self):
        assert _check("The issue is resolved.", []) == "missing"

    def test_passing_no_evidence(self):
        assert _check("All tests are passing.", []) == "missing"

    def test_landed_no_evidence(self):
        assert _check("The commit has landed.", []) == "missing"

    def test_committed_no_evidence(self):
        assert _check("Changes committed successfully.", []) == "missing"

    def test_merged_no_evidence(self):
        assert _check("The PR has been merged.", []) == "missing"

    def test_uppercase_trigger(self):
        assert _check("DONE.", []) == "missing"

    def test_mixed_case_trigger(self):
        assert _check("Fixed the issue.", []) == "missing"

    def test_empty_tool_results_does_not_help(self):
        assert _check("done", []) == "missing"

    def test_irrelevant_tool_results_still_missing(self):
        assert _check("done", ["some output without hashes or test lines"]) == "missing"


class TestPresent:
    """Trigger found with evidence → 'present'."""

    def test_done_with_commit_hash(self):
        assert _check("done, see commit abc1234", []) == "present"

    def test_done_with_long_hash(self):
        assert _check("fixed — commit a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", []) == "present"

    def test_done_with_file_line_ref(self):
        assert _check("fixed — see api/services/chat.py:42", []) == "present"

    def test_done_with_test_output_passed(self):
        assert _check("Tests are passing. 5 passed, 0 failed", []) == "present"

    def test_done_with_passed_keyword(self):
        assert _check("All tests PASSED. Implementation complete.", []) == "present"

    def test_done_with_testid(self):
        assert _check('done — verified data-testid="retry-btn" in DOM', []) == "present"

    def test_done_with_pytest_word(self):
        assert _check("done, ran pytest and everything is green", []) == "present"

    def test_evidence_in_tool_result_only(self):
        # Message has no evidence, but tool result has a commit hash.
        assert _check("fixed it", ["+ exit:0\nabc1234 fix bug in parser"]) == "present"

    def test_test_output_in_tool_result(self):
        assert _check("done", ["12 passed in 1.22s"]) == "present"

    def test_file_ref_in_tool_result(self):
        assert _check("complete", ["see api/chat.py:99 for the change"]) == "present"

    def test_tool_result_capped_at_500_chars(self):
        # Evidence at position >500 chars should NOT count.
        padding = "x" * 501
        assert _check("done", [padding + "abc1234 commit"]) == "missing"

    def test_multiple_tool_results_any_evidence_counts(self):
        assert _check("fixed", ["no evidence", "22 passed"]) == "present"

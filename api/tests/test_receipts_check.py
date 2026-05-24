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
    """Trigger found + code-context signal, no evidence → 'missing'."""

    def test_done_no_hash(self):
        assert _check("The feature is done. See `api/feature.py`.", []) == "missing"

    def test_fixed_no_evidence(self):
        assert _check("I've fixed the `bug` in chat.py.", []) == "missing"

    def test_shipped_no_evidence(self):
        assert _check("The release is shipped. Branch: release/v1.2", []) == "missing"

    def test_complete_no_evidence(self):
        assert _check("Implementation is complete. See `api/services/chat.py`.", []) == "missing"

    def test_resolved_no_evidence(self):
        assert _check("The issue is resolved. See `fix.py`.", []) == "missing"

    def test_passing_no_evidence(self):
        assert _check("All tests are passing. See `tests/test_foo.py`.", []) == "missing"

    def test_landed_no_evidence(self):
        # "commit" in the text is itself a code-context signal.
        assert _check("The commit has landed.", []) == "missing"

    def test_committed_no_evidence(self):
        # "committed" starts with the "commit" code-context token.
        assert _check("Changes committed successfully.", []) == "missing"

    def test_merged_no_evidence(self):
        # "PR" is a code-context signal.
        assert _check("The PR has been merged.", []) == "missing"

    def test_uppercase_trigger_with_code_signal(self):
        assert _check("DONE. See `api/foo.py`.", []) == "missing"

    def test_mixed_case_trigger_with_code_signal(self):
        assert _check("Fixed the `issue`.", []) == "missing"

    def test_empty_tool_results_does_not_help(self):
        assert _check("commit done", []) == "missing"

    def test_irrelevant_tool_results_still_missing(self):
        assert _check("PR done", ["some output without hashes or test lines"]) == "missing"


class TestNoCodeContext:
    """Trigger found but no code-context signals → 'no_trigger' (→1608 false-positive fix)."""

    def test_shipped_no_code_signals(self):
        # The failing case: brainstorming reply mentioning "shipped" with no code context.
        assert _check("Morning kickoff — pulls calendar, needles, agent activity. Shipped this quarter.", []) == "no_trigger"

    def test_shipped_to_version_no_code_signals(self):
        assert _check("The feature shipped to v1.2.3 last week.", []) == "no_trigger"

    def test_done_no_code_signals(self):
        assert _check("The feature is done.", []) == "no_trigger"

    def test_fixed_no_code_signals(self):
        assert _check("I've fixed the bug.", []) == "no_trigger"

    def test_complete_no_code_signals(self):
        assert _check("Implementation is complete.", []) == "no_trigger"

    def test_uppercase_trigger_no_code_signals(self):
        assert _check("DONE.", []) == "no_trigger"

    def test_tool_results_dont_supply_code_context(self):
        # Tool results with file paths (but no file:line evidence) don't make a no-code
        # brainstorming reply warn — code context is checked on message_text only.
        assert _check("The feature is done.", ["api/services/chat.py — some output"]) == "no_trigger"


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
        # Use "PR done" so the code-context gate passes; only the cap test matters.
        padding = "x" * 501
        assert _check("PR done", [padding + "abc1234 commit"]) == "missing"

    def test_multiple_tool_results_any_evidence_counts(self):
        assert _check("fixed", ["no evidence", "22 passed"]) == "present"

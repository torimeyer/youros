"""RED tests for receipts gate — check_receipts() must exist and behave correctly."""
import pytest


def test_import():
    from services.receipts_gate import check_receipts
    assert callable(check_receipts)


class TestCheckReceipts:
    def setup_method(self):
        from services.receipts_gate import check_receipts
        self.check = check_receipts

    # --- trigger word detected, no evidence → warning ---

    def test_done_no_hash_returns_warning(self):
        warning = self.check("The feature is done.")
        assert warning is not None
        assert warning.trigger_word == "done"

    def test_fixed_no_evidence_returns_warning(self):
        warning = self.check("I've fixed the bug.")
        assert warning is not None
        assert warning.trigger_word == "fixed"

    def test_shipped_no_evidence_returns_warning(self):
        warning = self.check("The release is shipped.")
        assert warning is not None
        assert warning.trigger_word == "shipped"

    def test_complete_no_evidence_returns_warning(self):
        warning = self.check("Implementation is complete.")
        assert warning is not None
        assert warning.trigger_word == "complete"

    def test_resolved_no_evidence_returns_warning(self):
        warning = self.check("The issue is resolved.")
        assert warning is not None
        assert warning.trigger_word == "resolved"

    def test_passing_no_evidence_returns_warning(self):
        warning = self.check("All tests are passing.")
        assert warning is not None
        assert warning.trigger_word == "passing"

    def test_landed_no_evidence_returns_warning(self):
        warning = self.check("The commit has landed.")
        assert warning is not None
        assert warning.trigger_word == "landed"

    def test_committed_no_evidence_returns_warning(self):
        warning = self.check("Changes committed successfully.")
        assert warning is not None
        assert warning.trigger_word == "committed"

    def test_warning_message_mentions_trigger_word(self):
        warning = self.check("done")
        assert warning is not None
        assert "done" in warning.message.lower()

    # --- evidence present → no warning ---

    def test_done_with_commit_hash_no_warning(self):
        assert self.check("done, see commit abc1234") is None

    def test_done_with_long_hash_no_warning(self):
        assert self.check("fixed — commit a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2") is None

    def test_done_with_file_reference_no_warning(self):
        assert self.check("fixed — see api/services/chat.py:42") is None

    def test_done_with_test_output_no_warning(self):
        assert self.check("Tests are passing. 5 passed, 0 failed") is None

    def test_done_with_passed_output_no_warning(self):
        assert self.check("All tests PASSED. Implementation complete.") is None

    def test_done_with_testid_no_warning(self):
        assert self.check('done — verified data-testid="retry-btn" in DOM') is None

    # --- no trigger word → no warning ---

    def test_no_trigger_word_returns_none(self):
        assert self.check("Here is a summary of the changes.") is None

    def test_empty_string_returns_none(self):
        assert self.check("") is None

    def test_partial_word_not_matched(self):
        # "undone" should not match "done"
        assert self.check("The task is undone.") is None

    # --- case insensitivity ---

    def test_uppercase_trigger_word(self):
        assert self.check("DONE.") is not None

    def test_mixed_case_trigger_word(self):
        assert self.check("Fixed the issue.") is not None


class TestCheckBriefReceipts:
    def setup_method(self):
        from services.receipts_gate import check_brief_receipts
        self.check = check_brief_receipts

    def test_check_brief_receipts_warns_on_done_without_evidence(self):
        warning = self.check(
            "The previous agent is done. Extend the feature to cover briefs."
        )
        assert warning is not None
        assert warning.trigger_word == "done"

    def test_check_brief_receipts_passes_when_evidence_present(self):
        warning = self.check(
            "The previous agent committed abc1234. Extend the feature."
        )
        assert warning is None

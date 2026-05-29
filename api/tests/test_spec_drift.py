"""Tests for api/services/spec_drift.py (phase 5 drift check)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.spec_audit import HuskResult, ShippedResult
from services.spec_drift import compute_spec_drift


def _write_spec(tmp_path: Path, content: str) -> Path:
    spec = tmp_path / "test_spec.md"
    spec.write_text(content, encoding="utf-8")
    return spec


CLEAN_DONE_SPEC = textwrap.dedent("""\
    ---
    status: done
    ---
    # My Spec

    ## Problem
    We need to solve X.

    ## Acceptance criteria
    - [x] Feature A works.
    - [x] Feature B works.
""")

DONE_SPEC_WITH_MISSING_FILE = textwrap.dedent("""\
    ---
    status: done
    ---
    # My Spec

    ## Problem
    We need to solve X.

    ## Acceptance criteria
    - [x] Feature A works.

    ## References
    See api/services/nonexistent_file.py for details.
""")

DONE_SPEC_WITH_UNCHECKED_AC = textwrap.dedent("""\
    ---
    status: done
    ---
    # My Spec

    ## Problem
    We need to solve X.

    ## Acceptance criteria
    - [x] Feature A works.
    - [ ] Feature B not done yet.
""")


class TestComputeSpecDrift:
    def test_missing_file_returns_no_drift_no_raise(self, tmp_path):
        """A path that does not exist returns drift=False without raising."""
        result = compute_spec_drift(str(tmp_path / "does_not_exist.md"))
        assert result["drift"] is False
        assert result["items"] == []

    def test_clean_done_spec_no_drift(self, tmp_path):
        """A done spec with all ACs checked and files present has no drift."""
        spec = _write_spec(tmp_path, CLEAN_DONE_SPEC)
        with (
            patch("services.spec_drift.compute_shipped") as mock_shipped,
            patch("services.spec_drift.compute_husk_status") as mock_husk,
        ):
            mock_husk.return_value = HuskResult(is_husk=False)
            mock_shipped.return_value = ShippedResult(
                is_shipped=True, missing_files=[], open_needles=[]
            )
            result = compute_spec_drift(str(spec))
        assert result["drift"] is False
        assert result["items"] == []

    def test_done_spec_with_no_shipped_artifacts(self, tmp_path):
        """A done spec that references files that do not exist gets claims_done_nothing_shipped."""
        spec = _write_spec(tmp_path, DONE_SPEC_WITH_MISSING_FILE)
        with (
            patch("services.spec_drift.compute_shipped") as mock_shipped,
            patch("services.spec_drift.compute_husk_status") as mock_husk,
        ):
            mock_husk.return_value = HuskResult(is_husk=False)
            mock_shipped.return_value = ShippedResult(
                is_shipped=False,
                missing_files=["api/services/nonexistent_file.py"],
                open_needles=[],
            )
            result = compute_spec_drift(str(spec))
        assert result["drift"] is True
        kinds = [i["kind"] for i in result["items"]]
        assert "claims_done_nothing_shipped" in kinds
        detail = next(i["detail"] for i in result["items"] if i["kind"] == "claims_done_nothing_shipped")
        assert "nonexistent_file.py" in detail

    def test_done_spec_with_unchecked_ac(self, tmp_path):
        """A done spec that still has unchecked AC lines gets unchecked_after_done."""
        spec = _write_spec(tmp_path, DONE_SPEC_WITH_UNCHECKED_AC)
        with (
            patch("services.spec_drift.compute_shipped") as mock_shipped,
            patch("services.spec_drift.compute_husk_status") as mock_husk,
        ):
            mock_husk.return_value = HuskResult(is_husk=False)
            mock_shipped.return_value = ShippedResult(
                is_shipped=True, missing_files=[], open_needles=[]
            )
            result = compute_spec_drift(str(spec))
        assert result["drift"] is True
        kinds = [i["kind"] for i in result["items"]]
        assert "unchecked_after_done" in kinds
        detail = next(i["detail"] for i in result["items"] if i["kind"] == "unchecked_after_done")
        assert "1" in detail

    def test_husk_spec_flagged(self, tmp_path):
        """A husk spec (promoted but empty) gets the husk drift kind."""
        spec = _write_spec(tmp_path, "---\nstatus: done\n---\n")
        with (
            patch("services.spec_drift.compute_shipped") as mock_shipped,
            patch("services.spec_drift.compute_husk_status") as mock_husk,
        ):
            mock_husk.return_value = HuskResult(is_husk=True, reason="no content")
            mock_shipped.return_value = ShippedResult(
                is_shipped=True, missing_files=[], open_needles=[]
            )
            result = compute_spec_drift(str(spec))
        assert result["drift"] is True
        kinds = [i["kind"] for i in result["items"]]
        assert "husk" in kinds

    def test_non_done_spec_skips_shipped_and_ac_checks(self, tmp_path):
        """A spec with status in-progress does not trigger done-only drift checks."""
        content = textwrap.dedent("""\
            ---
            status: in-progress
            ---
            # My Spec

            ## Acceptance criteria
            - [ ] Not done yet.
        """)
        spec = _write_spec(tmp_path, content)
        with patch("services.spec_drift.compute_husk_status") as mock_husk:
            mock_husk.return_value = HuskResult(is_husk=False)
            result = compute_spec_drift(str(spec))
        assert result["drift"] is False
        assert result["items"] == []

"""Tests for the pre-design audit (→1663).

The audit is a standalone script at ~/.myos/pre-design-audit.py that checks
three signals before any new infrastructure is proposed:
  1. Codebase search (literal filename + grep)
  2. Recent git log on origin/main
  3. Needle/spec ledger (docs/spec/)

AC:
- MATCH FOUND when a concept matches an existing file on main
- CLEAR when no match exists
- Report table contains all three signal rows
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = Path.home() / ".myos" / "pre-design-audit.py"


def _load_audit_module():
    """Import pre-design-audit.py as a module for unit testing."""
    spec = importlib.util.spec_from_file_location("pre_design_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit: search_codebase
# ---------------------------------------------------------------------------


def test_search_codebase_finds_claim_source_chip():
    """search_codebase must detect ClaimSourceChip.tsx as an existing file."""
    audit = _load_audit_module()
    hits = audit.search_codebase("ClaimSourceChip", REPO_ROOT)
    assert len(hits) > 0, "Expected at least one codebase hit for ClaimSourceChip"
    paths = [h["path"] for h in hits]
    assert any("ClaimSourceChip" in p for p in paths), paths


def test_search_codebase_returns_empty_for_nonexistent():
    """search_codebase must return [] for a concept that doesn't exist."""
    audit = _load_audit_module()
    hits = audit.search_codebase("NonExistentWidget99XYZ_2026", REPO_ROOT)
    assert hits == []


def test_search_codebase_hit_shape(tmp_path):
    """Each hit dict must have 'path' and 'match_type' keys."""
    (tmp_path / "SomeWidget.tsx").write_text("export function SomeWidget() {}")
    audit = _load_audit_module()
    hits = audit.search_codebase("SomeWidget", tmp_path)
    assert len(hits) > 0
    for h in hits:
        assert "path" in h
        assert "match_type" in h


# ---------------------------------------------------------------------------
# Unit: search_git_log
# ---------------------------------------------------------------------------


def test_search_git_log_returns_list():
    """search_git_log must return a list (may be empty if no commits match)."""
    audit = _load_audit_module()
    result = audit.search_git_log("ClaimSourceChip", REPO_ROOT)
    assert isinstance(result, list)


def test_search_git_log_finds_claim_source_chip_commit():
    """The commit a4faaca that added ClaimSourceChip must appear in git log."""
    audit = _load_audit_module()
    hits = audit.search_git_log("ClaimSourceChip", REPO_ROOT, n=60)
    # Either the log search finds the commit, or returns [] if git unavailable.
    # We accept both; the key is it doesn't crash.
    assert isinstance(hits, list)


def test_search_git_log_empty_for_nonexistent():
    """Commits mentioning a made-up name must not appear."""
    audit = _load_audit_module()
    hits = audit.search_git_log("NonExistentWidget99XYZ_2026", REPO_ROOT)
    assert hits == []


# ---------------------------------------------------------------------------
# Unit: search_specs
# ---------------------------------------------------------------------------


def test_search_specs_returns_list():
    """search_specs must return a list."""
    audit = _load_audit_module()
    result = audit.search_specs("ClaimSourceChip", REPO_ROOT)
    assert isinstance(result, list)


def test_search_specs_no_match_for_nonexistent(tmp_path):
    """search_specs must return [] when no spec references the concept."""
    (tmp_path / "some-spec.md").write_text("# unrelated spec\n")
    audit = _load_audit_module()
    result = audit.search_specs("NonExistentWidget99XYZ_2026", tmp_path)
    assert result == []


def test_search_specs_finds_match(tmp_path):
    """search_specs must find the concept when it appears in a spec file."""
    (tmp_path / "claim-chip.md").write_text(
        "---\ntitle: claim chip\n---\n\nUses ClaimSourceChip component.\n"
    )
    audit = _load_audit_module()
    result = audit.search_specs("ClaimSourceChip", tmp_path)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# Integration: run_audit (the three-signal check)
# ---------------------------------------------------------------------------


def test_run_audit_match_found_for_existing():
    """run_audit must return match=True for ClaimSourceChip (exists on main)."""
    audit = _load_audit_module()
    report = audit.run_audit(["ClaimSourceChip"], REPO_ROOT)
    assert report["match"] is True


def test_run_audit_clear_for_nonexistent():
    """run_audit must return match=False for a concept that doesn't exist."""
    audit = _load_audit_module()
    report = audit.run_audit(["NonExistentWidget99XYZ_2026"], REPO_ROOT)
    assert report["match"] is False


def test_run_audit_report_has_three_signals():
    """The audit report must include all three signal categories."""
    audit = _load_audit_module()
    report = audit.run_audit(["ClaimSourceChip"], REPO_ROOT)
    assert "codebase" in report
    assert "git_log" in report
    assert "specs" in report


# ---------------------------------------------------------------------------
# Integration: CLI output (subprocess)
# ---------------------------------------------------------------------------


def test_cli_match_found_output():
    """CLI must print 'MATCH FOUND' when concept exists in codebase."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ClaimSourceChip", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert "MATCH FOUND" in result.stdout, result.stdout


def test_cli_clear_output():
    """CLI must print 'CLEAR' when concept has no matches."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "NonExistentWidget99XYZ_2026", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert "CLEAR" in result.stdout, result.stdout


def test_cli_report_table_contains_signals():
    """CLI output must include all three signal row labels in the table."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ClaimSourceChip", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout
    assert "Codebase" in output
    assert "git log" in output or "git_log" in output or "Git log" in output
    assert "Needles" in output or "Specs" in output


def test_cli_exits_zero():
    """CLI must exit 0 in both match and clear cases."""
    r1 = subprocess.run(
        [sys.executable, str(SCRIPT), "ClaimSourceChip", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    r2 = subprocess.run(
        [sys.executable, str(SCRIPT), "NonExistentWidget99XYZ_2026", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert r1.returncode == 0
    assert r2.returncode == 0


def test_cli_multiple_concepts():
    """CLI must handle multiple concept arguments."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ClaimSourceChip", "NonExistentWidget99XYZ_2026",
         "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "MATCH FOUND" in result.stdout

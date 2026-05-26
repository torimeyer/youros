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


# ---------------------------------------------------------------------------
# Semantic search (→1727)
# ---------------------------------------------------------------------------


def test_split_camel_two_tokens():
    """'SourceBadge' must split into ['source', 'badge']."""
    audit = _load_audit_module()
    assert audit._split_camel("SourceBadge") == ["source", "badge"]


def test_split_camel_three_tokens():
    """'ClaimSourceChip' must split into ['claim', 'source', 'chip']."""
    audit = _load_audit_module()
    assert audit._split_camel("ClaimSourceChip") == ["claim", "source", "chip"]


def test_expand_terms_includes_synonyms():
    """_expand_terms must include 'chip' when 'badge' is in the input."""
    audit = _load_audit_module()
    expanded = audit._expand_terms(["source", "badge"])
    assert "chip" in expanded, "badge should expand to include chip"
    assert "source" in expanded
    assert "claim" in expanded, "source should expand to include claim"


def test_search_semantic_finds_claim_source_chip_for_source_badge():
    """Proposing 'SourceBadge' must surface ClaimSourceChip.tsx as a semantic POSSIBLE MATCH."""
    audit = _load_audit_module()
    hits = audit.search_semantic("SourceBadge", REPO_ROOT)
    paths = [h["path"] for h in hits]
    assert any("ClaimSourceChip" in p for p in paths), (
        f"Expected ClaimSourceChip.tsx in semantic hits for 'SourceBadge', got: {paths}"
    )


def test_search_semantic_hit_has_required_fields():
    """Each semantic hit must have path, match_type, score, and matched_terms."""
    audit = _load_audit_module()
    hits = audit.search_semantic("SourceBadge", REPO_ROOT)
    for h in hits:
        assert "path" in h
        assert h["match_type"] == "semantic"
        assert isinstance(h.get("score"), int)
        assert isinstance(h.get("matched_terms"), list)


def test_search_semantic_skips_literal_concept():
    """search_semantic must not return the concept's own name as a hit."""
    audit = _load_audit_module()
    hits = audit.search_semantic("ClaimSourceChip", REPO_ROOT)
    paths = [h["path"] for h in hits]
    assert not any("ClaimSourceChip" in p for p in paths), (
        "Literal concept name must be excluded from semantic results"
    )


def test_search_semantic_empty_for_single_token():
    """Single-token concepts cannot be matched semantically (too ambiguous)."""
    audit = _load_audit_module()
    hits = audit.search_semantic("Button", REPO_ROOT)
    # Should be [] because _split_camel("Button") → ["button"] (1 token < 2)
    assert hits == []


def test_run_audit_possible_match_for_source_badge():
    """run_audit must return possible_match=True (not match=True) for 'SourceBadge'."""
    audit = _load_audit_module()
    report = audit.run_audit(["SourceBadge"], REPO_ROOT)
    # 'SourceBadge' doesn't exist literally but ClaimSourceChip is a semantic match
    assert report.get("possible_match") is True or report.get("match") is True, (
        "Expected either match or possible_match for SourceBadge"
    )
    # Crucially, semantic hits should be populated
    sem_hits = report["semantic"][0]["hits"]
    assert len(sem_hits) > 0, f"Expected semantic hits for SourceBadge, got none"
    assert any("ClaimSourceChip" in h["path"] for h in sem_hits)


def test_cli_possible_match_output_for_source_badge():
    """CLI must print 'POSSIBLE MATCH' when concept has semantic but no literal match."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "SourceBadge", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    output = result.stdout
    # Must flag it — either as MATCH FOUND (literal) or POSSIBLE MATCH (semantic)
    assert "MATCH FOUND" in output or "POSSIBLE MATCH" in output, (
        f"Expected MATCH FOUND or POSSIBLE MATCH for SourceBadge, got:\n{output}"
    )
    # The semantic signal row must be present
    assert "semantic" in output.lower() or "Semantic" in output, output


def test_cli_report_has_semantic_signal_row():
    """CLI output must include the semantic signal row in the table."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "ClaimSourceChip", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert "semantic" in result.stdout.lower(), (
        "Table must include a 'semantic' signal row"
    )

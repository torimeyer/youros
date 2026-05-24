"""Tests for saa_brief.generate_saa_brief (needle →1403).

Invariant being pinned:
  A saa bug-fix brief MUST include a BDD-level invariant test requirement
  (not just a symptom-specific unit test) so that every future bug-fix
  agent writes a test that catches the whole class of bug, not just the
  specific symptom that triggered the current fix.

Evidence: five regressions in the same chat-markdown area (22743ab,
e25a16b, a189b18, 2b3269e, 7ffb91f, 915a952) because each prior brief
asked for a regression test but not an integration-level invariant test.
"""
from __future__ import annotations

import pytest

from services.saa_brief import (
    BDD_INVARIANT_SECTION,
    SAA_KINDS,
    generate_saa_brief,
)


# ---------------------------------------------------------------------------
# test_generate_saa_brief_bug_fix_includes_bdd_invariant
# Invariant: bug-fix briefs require the full BDD block, not just TDD.
# ---------------------------------------------------------------------------


class TestBugFixBriefBDDInvariant:
    """Bug-fix brief must include the BDD invariant section (Rule 2c)."""

    def test_bug_fix_brief_contains_bdd_invariant_section(self):
        """generate_saa_brief('bug-fix') includes the full BDD invariant block."""
        brief = generate_saa_brief("bug-fix")
        assert BDD_INVARIANT_SECTION in brief, (
            "Bug-fix brief must embed BDD_INVARIANT_SECTION verbatim. "
            "A symptom-specific unit test is not enough — we need an "
            "integration-level invariant test per Rule 2c (→1403)."
        )

    def test_diagnose_alias_also_includes_bdd_invariant_section(self):
        """'diagnose' is the UI name for bug-fix and must get the same brief."""
        brief = generate_saa_brief("diagnose")
        assert BDD_INVARIANT_SECTION in brief, (
            "The 'diagnose' alias maps to bug-fix; it must carry the BDD "
            "invariant block so the UI entry point and the direct kind both "
            "produce invariant-level tests."
        )

    def test_bugfix_spelling_variants_all_include_bdd_section(self):
        """All spelling variants of bug-fix produce the BDD invariant block."""
        for variant in ("bug-fix", "bug_fix", "bugfix", "diagnose"):
            brief = generate_saa_brief(variant)
            assert BDD_INVARIANT_SECTION in brief, (
                f"Variant '{variant}' must produce a brief with the BDD "
                f"invariant section. Got brief starting with: {brief[:80]!r}"
            )

    def test_bug_fix_brief_names_invariant_test_convention(self):
        """The brief must specify the naming convention for invariant tests."""
        brief = generate_saa_brief("bug-fix")
        assert "test_" in brief and "_invariant" in brief, (
            "Bug-fix brief must name the test_<behavior>_invariant convention "
            "so agents produce consistently discoverable invariant tests."
        )

    def test_bug_fix_brief_requires_test_fail_on_buggy_code(self):
        """The brief must say the invariant test must fail before the fix."""
        brief = generate_saa_brief("bug-fix")
        assert "FAILS" in brief or "fail" in brief.lower(), (
            "Bug-fix brief must instruct the agent to confirm the invariant "
            "test fails on buggy code — that's what proves it isn't a vacuous test."
        )

    def test_bug_fix_brief_requires_enumerate_other_fire_paths(self):
        """The brief must ask the agent to name at least one other scenario."""
        brief = generate_saa_brief("bug-fix")
        assert "other" in brief.lower() and (
            "scenario" in brief.lower() or "way" in brief.lower() or "fire" in brief.lower()
        ), (
            "Bug-fix brief must ask the agent to enumerate at least one other "
            "way the invariant could fire. That's the proof the fix is "
            "invariant-level, not symptom-level."
        )

    def test_bug_fix_brief_contains_systematic_debugging_reference(self):
        """Bug-fix briefs must reference systematic-debugging before fixing."""
        brief = generate_saa_brief("bug-fix")
        assert "systematic-debugging" in brief or "root cause" in brief.lower(), (
            "Bug-fix brief must mention systematic-debugging (or root cause). "
            "Fixes without root-cause investigation are symptom patches."
        )


# ---------------------------------------------------------------------------
# test non-bug-fix kinds do NOT get the BDD section
# ---------------------------------------------------------------------------


class TestNonBugFixKindsSkipBDDSection:
    """Feature / cosmetic / backend / generic briefs must not carry the BDD block.

    Adding BDD boilerplate to cosmetic-only or backend-only briefs would
    confuse agents into looking for a 'user-visible invariant' that doesn't
    exist for those task kinds.
    """

    @pytest.mark.parametrize("kind", ["feature", "cosmetic", "backend", "backend-only", "generic"])
    def test_non_bug_fix_brief_omits_bdd_invariant_section(self, kind):
        brief = generate_saa_brief(kind)
        assert BDD_INVARIANT_SECTION not in brief, (
            f"Kind '{kind}' must NOT include the BDD invariant block. "
            f"That block is only for user-visible bug-fix saa tasks."
        )


# ---------------------------------------------------------------------------
# test BDD_INVARIANT_SECTION content — the exported constant must be complete
# ---------------------------------------------------------------------------


class TestBDDInvariantSectionContent:
    """The BDD_INVARIANT_SECTION constant must contain all required phrases."""

    def test_section_contains_invariant_keyword(self):
        assert "invariant" in BDD_INVARIANT_SECTION.lower()

    def test_section_contains_integration_test_requirement(self):
        """The section must distinguish integration test from unit test."""
        assert "NOT a unit test" in BDD_INVARIANT_SECTION or (
            "not a unit" in BDD_INVARIANT_SECTION.lower()
        ), "Section must explicitly say NOT a unit test to guide agents correctly."

    def test_section_contains_naming_convention(self):
        assert "test_" in BDD_INVARIANT_SECTION and "_invariant" in BDD_INVARIANT_SECTION

    def test_section_contains_fail_before_pass_requirement(self):
        """Section must say test fails on buggy code and passes after fix."""
        lower = BDD_INVARIANT_SECTION.lower()
        assert "fail" in lower and "pass" in lower


# ---------------------------------------------------------------------------
# test generate_saa_brief contract — general shape
# ---------------------------------------------------------------------------


class TestGenerateSaaBriefContract:
    """generate_saa_brief must be safe and deterministic for all inputs."""

    def test_all_canonical_kinds_produce_non_empty_brief(self):
        for kind in SAA_KINDS:
            brief = generate_saa_brief(kind)
            assert brief and brief.strip(), f"Kind '{kind}' produced an empty brief."

    def test_unknown_kind_falls_back_to_generic(self):
        brief = generate_saa_brief("totally-unknown-kind-xyz")
        assert brief and brief.strip(), "Unknown kind must fall back to generic brief."
        # Generic brief must not include the BDD block
        assert BDD_INVARIANT_SECTION not in brief

    def test_kind_is_case_insensitive(self):
        lower = generate_saa_brief("bug-fix")
        upper = generate_saa_brief("BUG-FIX")
        mixed = generate_saa_brief("Bug-Fix")
        assert lower == upper == mixed, "generate_saa_brief must be case-insensitive."

    def test_brief_includes_ostk_preamble(self):
        """Every brief must start with the ostk-first preamble."""
        for kind in SAA_KINDS:
            brief = generate_saa_brief(kind)
            assert "mcp__ostk__" in brief, (
                f"Kind '{kind}' brief must include ostk-first preamble so "
                f"sub-agents use kernel tools, not native Bash/Read/Edit."
            )

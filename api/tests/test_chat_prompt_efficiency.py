"""Regression tests for →1146: chat agent doing 20+ tool calls on simple questions.

Symptom: user asked "where did these specs come from?" and the agent cascaded
through Read/Search/Browse/Run across unrelated directories instead of forming
one precise query, getting the answer, and stopping.

Fix: added ANSWERING QUESTIONS EFFICIENTLY section to _system_prompt() that
explicitly instructs the model to:
- prefer one targeted search over exploratory broadening
- use mcp__ostk__search (not bash find/grep)
- refine the query when the first search misses (not widen the area)
- stop as soon as it has a defensible answer
- ask the user instead of continuing to search after 3+ tool calls

These tests lock in those phrases so the rule cannot be silently removed.
"""

import pytest

from services.chat_providers import _system_prompt


class TestAnsweringQuestionsEfficiently:
    """_system_prompt() must contain the answering-efficiency rule."""

    def test_contains_section_heading(self):
        assert "ANSWERING QUESTIONS EFFICIENTLY" in _system_prompt()

    def test_prefers_one_targeted_search(self):
        assert "One targeted search beats" in _system_prompt()

    def test_prefers_ostk_search_over_bash(self):
        prompt = _system_prompt()
        # The new section must name mcp__ostk__search as the preferred tool
        # and contrast it with bash find/grep
        idx = prompt.find("ANSWERING QUESTIONS EFFICIENTLY")
        assert idx != -1
        after = prompt[idx:idx + 600]
        assert "mcp__ostk__search" in after, (
            "Efficiency section must name mcp__ostk__search as preferred search tool"
        )
        assert "find" in after or "grep" in after, (
            "Efficiency section must mention bash find/grep as the discouraged alternative"
        )

    def test_instructs_to_refine_not_broaden(self):
        assert "refine the query" in _system_prompt()

    def test_instructs_stop_at_defensible_answer(self):
        assert "defensible answer" in _system_prompt()

    def test_instructs_ask_user_after_3_calls(self):
        prompt = _system_prompt()
        # Should tell the model to ask rather than keep searching after 3+ calls
        assert "3 or more tool calls" in prompt, (
            "_system_prompt() must tell the agent to ask the user after 3+ tool calls "
            "without an answer, not keep searching"
        )

    def test_section_comes_after_ostk_tools_required(self):
        """The efficiency rule should follow the OSTK TOOLS REQUIRED section."""
        prompt = _system_prompt()
        idx_ostk = prompt.find("OSTK TOOLS REQUIRED")
        idx_eff = prompt.find("ANSWERING QUESTIONS EFFICIENTLY")
        assert idx_ostk != -1, "OSTK TOOLS REQUIRED section not found"
        assert idx_eff != -1, "ANSWERING QUESTIONS EFFICIENTLY section not found"
        assert idx_ostk < idx_eff, (
            "ANSWERING QUESTIONS EFFICIENTLY should come after OSTK TOOLS REQUIRED"
        )

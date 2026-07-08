"""Tests for route_provider() in api/services/chat_providers.py."""
from __future__ import annotations

import pytest

from services.chat_providers import route_provider


ALL = ["claude", "gemini"]
CLAUDE_ONLY_PROVIDERS = ["claude"]


class TestClaudeOnlyPolicy:
    def test_returns_claude(self):
        assert route_provider("hello", "claude_only", ALL) == "claude"

    def test_returns_claude_even_for_short_message(self):
        assert route_provider("hi", "claude_only", ALL) == "claude"

    def test_returns_claude_when_gemini_unavailable(self):
        assert route_provider("hello", "claude_only", CLAUDE_ONLY_PROVIDERS) == "claude"


class TestGeminiOnlyPolicy:
    def test_returns_gemini_when_available(self):
        assert route_provider("hello", "gemini_only", ALL) == "gemini"

    def test_falls_back_to_claude_when_gemini_unavailable(self):
        assert route_provider("hello", "gemini_only", CLAUDE_ONLY_PROVIDERS) == "claude"


class TestAutoPolicy:
    def test_short_simple_message_routes_to_gemini(self):
        result = route_provider("what time is it?", "auto", ALL)
        assert result == "gemini"

    def test_long_message_routes_to_claude(self):
        long_msg = "please help me " + "x" * 490
        result = route_provider(long_msg, "auto", ALL)
        assert result == "claude"

    def test_message_with_code_block_routes_to_claude(self):
        msg = "fix this: ```python\nprint('hi')\n```"
        result = route_provider(msg, "auto", ALL)
        assert result == "claude"

    def test_short_simple_falls_back_to_claude_when_gemini_unavailable(self):
        result = route_provider("what time is it?", "auto", CLAUDE_ONLY_PROVIDERS)
        assert result == "claude"

    def test_none_policy_treated_as_auto(self):
        result = route_provider("what time is it?", None, ALL)
        assert result == "gemini"


class TestPreferGeminiPolicy:
    def test_short_simple_message_routes_to_gemini(self):
        result = route_provider("what's the weather?", "prefer_gemini", ALL)
        assert result == "gemini"

    def test_long_message_routes_to_claude(self):
        long_msg = "please help me " + "y" * 490
        result = route_provider(long_msg, "prefer_gemini", ALL)
        assert result == "claude"

    def test_message_with_code_routes_to_claude(self):
        msg = "write a python function that sorts a list"
        result = route_provider(msg, "prefer_gemini", ALL)
        assert result == "claude"

    def test_falls_back_to_claude_when_gemini_unavailable(self):
        result = route_provider("hi there", "prefer_gemini", CLAUDE_ONLY_PROVIDERS)
        assert result == "claude"


class TestUserSelectedModel:
    """→2552: the thread's model selector is the source of truth.

    The chat panel sends the selected model with every turn. When the org
    policy is a cost heuristic ("auto" / "prefer_gemini"), that explicit
    selection must win — otherwise every short plain-text message silently
    goes to Gemini even though the user picked Claude, and the Claude
    warm chat process (Phase E, →2468) never runs at all.
    Hard policies ("claude_only" / "gemini_only") still force a provider.
    """

    def test_auto_policy_honors_user_selected_claude(self):
        # Real-world →2552 condition: short message, dropdown on Claude.
        assert route_provider("hi, reply with one word", "auto", ALL, user_selected="claude") == "claude"

    def test_prefer_gemini_policy_honors_user_selected_claude(self):
        assert route_provider("hi", "prefer_gemini", ALL, user_selected="claude") == "claude"

    def test_auto_policy_honors_user_selected_gemini_for_long_message(self):
        long_msg = "please help me " + "x" * 490
        assert route_provider(long_msg, "auto", ALL, user_selected="gemini") == "gemini"

    def test_claude_only_still_forces_claude_over_user_selection(self):
        assert route_provider("hi", "claude_only", ALL, user_selected="gemini") == "claude"

    def test_gemini_only_still_forces_gemini_over_user_selection(self):
        assert route_provider("hi", "gemini_only", ALL, user_selected="claude") == "gemini"

    def test_unavailable_user_selection_falls_back_to_heuristic(self):
        assert route_provider("hi", "auto", CLAUDE_ONLY_PROVIDERS, user_selected="gemini") == "claude"

    def test_no_user_selection_keeps_heuristic_behavior(self):
        assert route_provider("what time is it?", "auto", ALL) == "gemini"


class TestHeuristic:
    def test_bash_keyword_routes_to_claude(self):
        result = route_provider("run bash script", "auto", ALL)
        assert result == "claude"

    def test_import_keyword_routes_to_claude(self):
        result = route_provider("import this module", "auto", ALL)
        assert result == "claude"

    def test_exactly_500_chars_routes_to_claude(self):
        msg = "a" * 500
        result = route_provider(msg, "auto", ALL)
        assert result == "claude"

    def test_499_chars_no_code_routes_to_gemini(self):
        msg = "a" * 499
        result = route_provider(msg, "auto", ALL)
        assert result == "gemini"

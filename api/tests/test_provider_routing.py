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

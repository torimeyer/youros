"""Tests for ADF (Atlassian Document Format) → plain-text rendering.

These tests assert that Jira issue descriptions and comment bodies
that arrive as ADF dicts are rendered as readable plain text, not raw
Python dict literals.
"""

from __future__ import annotations

import pytest

from services.atlassian import _adf_to_plain


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MENTION_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "mention",
                    "attrs": {
                        "id": "60f7105a4c974d0069dcbe9c",
                        "text": "@Test User",
                        "accessLevel": "",
                        "localId": "1743529e-902b-4369-8b0b-3140008ed7db",
                    },
                },
                {"type": "text", "text": " - Can you evaluate this requirement?"},
            ],
            "attrs": {"localId": "a1335a0b8c43"},
        }
    ],
}

HARD_BREAK_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Line one"},
                {"type": "hardBreak"},
                {"type": "text", "text": "Line two"},
            ],
        }
    ],
}

INLINE_CARD_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "See: "},
                {
                    "type": "inlineCard",
                    "attrs": {"url": "https://example.atlassian.net/browse/PROJ-1"},
                },
            ],
        }
    ],
}

NESTED_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Item A"}],
                        }
                    ],
                },
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Item B"}],
                        }
                    ],
                },
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mention_renders_as_display_name():
    """The fixture comment from the bug report must render to human text."""
    result = _adf_to_plain(MENTION_ADF)
    assert result == "@Test User - Can you evaluate this requirement?"


def test_plain_text_paragraph():
    """Simple paragraph with text nodes."""
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello world"}],
            }
        ],
    }
    assert _adf_to_plain(adf) == "Hello world"


def test_hard_break_becomes_newline():
    result = _adf_to_plain(HARD_BREAK_ADF)
    assert "Line one" in result
    assert "Line two" in result
    # They must be on separate lines (or at least separated by whitespace)
    assert result.index("Line one") < result.index("Line two")


def test_inline_card_omitted_gracefully():
    """inlineCard nodes should not crash and should not produce dict repr."""
    result = _adf_to_plain(INLINE_CARD_ADF)
    assert "See:" in result or "See: " in result
    assert "{" not in result, f"Got raw dict in output: {result!r}"


def test_nested_list_items():
    result = _adf_to_plain(NESTED_ADF)
    assert "Item A" in result
    assert "Item B" in result
    assert "{" not in result


def test_empty_doc_returns_empty_string():
    assert _adf_to_plain({"type": "doc", "version": 1, "content": []}) == ""


def test_none_input_returns_empty_string():
    assert _adf_to_plain(None) == ""


def test_string_input_returned_as_is():
    """If body is already a string (already rendered), pass it through."""
    assert _adf_to_plain("already plain") == "already plain"


def test_raw_dict_is_not_in_output():
    """The literal string \"{'type':\" must never appear in output."""
    result = _adf_to_plain(MENTION_ADF)
    assert "{'type'" not in result
    assert "'type': 'doc'" not in result

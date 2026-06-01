"""Tests for iMessage AppleScript text escaping (→2023/B1).

Verifies that _escape_applescript_text correctly handles:
- Newlines → AppleScript return concatenation
- Double quotes → backslash-escaped
- Backslashes → doubled
- Combinations of the above
"""

import pytest
from api.services.imessage import _escape_applescript_text


def test_newline_becomes_applescript_return():
    result = _escape_applescript_text("line one\nline two")
    assert result == 'line one" & return & "line two'


def test_double_quote_escaped():
    result = _escape_applescript_text('say "hi"')
    assert result == 'say \\"hi\\"'


def test_backslash_doubled():
    result = _escape_applescript_text("path\\to\\file")
    assert result == "path\\\\to\\\\file"


def test_newline_and_quote_combined():
    result = _escape_applescript_text('he said "hi"\nthen left')
    assert result == 'he said \\"hi\\"" & return & "then left'


def test_multiple_newlines():
    result = _escape_applescript_text("a\nb\nc")
    assert result == 'a" & return & "b" & return & "c'


def test_plain_text_unchanged():
    result = _escape_applescript_text("hello world")
    assert result == "hello world"


def test_empty_string():
    result = _escape_applescript_text("")
    assert result == ""

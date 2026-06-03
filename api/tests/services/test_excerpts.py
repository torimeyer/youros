"""Tests for api/services/excerpts.py (P0)."""
import pytest
from services.excerpts import Excerpt, format_excerpts


def _exc(**kwargs):
    defaults = dict(
        text="sample text",
        source_id="src1",
        source_title="My Doc",
        deep_link=None,
        score=0.9,
        access_denied=False,
        provider="source_library",
    )
    defaults.update(kwargs)
    return Excerpt(**defaults)


class TestExcerptDataclass:
    def test_fields_present(self):
        e = _exc()
        assert e.text == "sample text"
        assert e.source_id == "src1"
        assert e.source_title == "My Doc"
        assert e.deep_link is None
        assert e.score == 0.9
        assert e.access_denied is False
        assert e.provider == "source_library"

    def test_frozen(self):
        e = _exc()
        with pytest.raises((AttributeError, TypeError)):
            e.text = "changed"  # type: ignore

    def test_deep_link_optional(self):
        e_no_link = _exc(deep_link=None)
        e_with_link = _exc(deep_link="https://example.com")
        assert e_no_link.deep_link is None
        assert e_with_link.deep_link == "https://example.com"


class TestFormatExcerpts:
    def test_empty_list(self):
        assert format_excerpts([]) == ""

    def test_single_no_deep_link(self):
        e = _exc(source_title="Doc A", text="hello world", deep_link=None)
        result = format_excerpts([e])
        assert "Reference material:" in result
        assert "[1] Doc A" in result
        assert "hello world" in result
        assert "](http" not in result

    def test_single_with_deep_link(self):
        e = _exc(source_title="PROJ-1", text="issue body", deep_link="https://acme.atlassian.net/browse/PROJ-1")
        result = format_excerpts([e])
        assert "[1] [PROJ-1](https://acme.atlassian.net/browse/PROJ-1)" in result
        assert "issue body" in result

    def test_numbering_sequential(self):
        excerpts = [_exc(source_title=f"Doc{i}", text=f"text{i}") for i in range(3)]
        result = format_excerpts(excerpts)
        assert "[1] Doc0" in result
        assert "[2] Doc1" in result
        assert "[3] Doc2" in result

    def test_access_denied_display(self):
        e = _exc(access_denied=True, text="confidential")
        result = format_excerpts([e])
        assert "Access denied" in result or "permission" in result.lower()

    def test_no_trailing_blank_lines(self):
        e = _exc()
        result = format_excerpts([e])
        assert not result.endswith("\n\n")

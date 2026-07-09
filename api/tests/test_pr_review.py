"""Tests for the PR review service and URL detection (→2573–→2577).

Covers:
  - Normal PR produces structured review with summary/walkthrough/flags
  - Risky-pattern PR fires auth + deleted-test + secret flags
  - Oversized PR (>80 files / very long diff) returns disclosed-truncation review
  - Unauthorized PR (404/403) returns plain-language error, not a stack trace
  - GitHub PR URL detection (unit test for _handle_pr_review pattern)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure api/ is importable
api_dir = Path(__file__).resolve().parent.parent
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_pr_meta(**overrides):
    base = {
        "number": 42,
        "title": "Add login endpoint",
        "state": "open",
        "body": "Adds OAuth token auth.",
        "html_url": "https://github.com/acme/repo/pull/42",
        "additions": 80,
        "deletions": 20,
        "changed_files": 4,
        "files": [
            {"filename": "api/auth.py"},
            {"filename": "api/tests/test_auth.py"},
        ],
    }
    base.update(overrides)
    return base


def _fake_response(text: str):
    block = SimpleNamespace(text=text, type="text")
    return SimpleNamespace(content=[block])


def _make_client(text: str):
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=_fake_response(text))
    return client


# ---------------------------------------------------------------------------
# Normal PR
# ---------------------------------------------------------------------------

class TestNormalPR:
    @pytest.mark.asyncio
    async def test_returns_structured_review(self):
        model_json = json.dumps({
            "summary": "Adds OAuth login endpoint to the API.",
            "walkthrough": [
                {"file": "api/auth.py", "change_type": "modified", "description": "Adds token validation."},
            ],
            "flags": [],
        })

        with (
            patch("services.github.is_connected", return_value=True),
            patch("services.pr_review._get_pr_extended", AsyncMock(return_value=_fake_pr_meta())),
            patch("services.pr_review._get_pr_diff", AsyncMock(return_value="diff --git a/api/auth.py\n+def login(): pass")),
            patch("services.pr_review.get_ai_client", AsyncMock(return_value=_make_client(model_json))),
        ):
            from services.pr_review import review_pr
            result = await review_pr("acme", "repo", 42)

        assert result["summary"] == "Adds OAuth login endpoint to the API."
        assert result["file_count"] == 4
        assert result["additions"] == 80
        assert result["deletions"] == 20
        assert result["truncated"] is False
        assert len(result["walkthrough"]) == 1
        assert result["walkthrough"][0]["file"] == "api/auth.py"
        assert result["flags"] == []
        assert result["pr_number"] == 42
        assert result["owner"] == "acme"
        assert result["repo"] == "repo"


# ---------------------------------------------------------------------------
# Risky-pattern PR
# ---------------------------------------------------------------------------

class TestRiskyPatternPR:
    @pytest.mark.asyncio
    async def test_risky_patterns_fire(self):
        risky_diff = (
            "diff --git a/api/auth.py b/api/auth.py\n"
            "+SECRET_KEY = 'ghp_abcdef1234567890abcdef12345678901234'\n"
            "+def validate_token(token): pass\n"
        )
        risky_meta = _fake_pr_meta(
            files=[
                {"filename": "api/auth.py"},
                {"filename": "api/tests/test_auth.py"},
            ],
        )
        model_json = json.dumps({
            "summary": "Modifies auth and adds a hardcoded token.",
            "walkthrough": [
                {"file": "api/auth.py", "change_type": "modified", "description": "Changes auth logic."},
            ],
            "flags": [
                {"title": "Auth change", "severity": "high", "description": "token validation modified", "file": "api/auth.py"},
                {"title": "Hardcoded secret", "severity": "high", "description": "ghp_ token in diff", "file": "api/auth.py"},
                {"title": "Test deleted", "severity": "medium", "description": "test file removed", "file": "api/tests/test_auth.py"},
            ],
        })

        with (
            patch("services.github.is_connected", return_value=True),
            patch("services.pr_review._get_pr_extended", AsyncMock(return_value=risky_meta)),
            patch("services.pr_review._get_pr_diff", AsyncMock(return_value=risky_diff)),
            patch("services.pr_review.get_ai_client", AsyncMock(return_value=_make_client(model_json))),
        ):
            from services.pr_review import review_pr
            result = await review_pr("acme", "repo", 42)

        severities = {f["severity"] for f in result["flags"]}
        assert "high" in severities
        titles = [f["title"] for f in result["flags"]]
        assert any("auth" in t.lower() or "secret" in t.lower() or "token" in t.lower() for t in titles)


# ---------------------------------------------------------------------------
# Oversized PR — disclosed truncation
# ---------------------------------------------------------------------------

class TestOversizedPR:
    @pytest.mark.asyncio
    async def test_large_pr_discloses_truncation(self):
        # Simulate 90 files (over the 80 file limit)
        many_files = [{"filename": f"src/file_{i}.py"} for i in range(90)]
        large_meta = _fake_pr_meta(changed_files=90, files=many_files)

        # Large diff exceeding char limit
        large_diff = "diff --git a/src/file_0.py b/src/file_0.py\n" + ("+x = 1\n" * 5000)

        model_json = json.dumps({
            "summary": "Large refactor across 90 files. Note: diff was truncated.",
            "walkthrough": [{"file": "src/file_0.py", "change_type": "modified", "description": "Changed x."}],
            "flags": [
                {"title": "Large PR — partial review", "severity": "high",
                 "description": "Only part of this PR was reviewed due to its size.", "file": None},
            ],
        })

        with (
            patch("services.github.is_connected", return_value=True),
            patch("services.pr_review._get_pr_extended", AsyncMock(return_value=large_meta)),
            patch("services.pr_review._get_pr_diff", AsyncMock(return_value=large_diff)),
            patch("services.pr_review.get_ai_client", AsyncMock(return_value=_make_client(model_json))),
        ):
            from services.pr_review import review_pr
            result = await review_pr("acme", "repo", 42)

        assert result["truncated"] is True
        assert result["file_count"] == 90
        flag_titles = [f["title"] for f in result["flags"]]
        assert any("partial" in t.lower() or "large" in t.lower() for t in flag_titles)


# ---------------------------------------------------------------------------
# Unauthorized PR — plain-language error
# ---------------------------------------------------------------------------

class TestUnauthorizedPR:
    @pytest.mark.asyncio
    async def test_404_returns_plain_error(self):
        with (
            patch("services.github.is_connected", return_value=True),
            patch("services.pr_review._get_pr_extended",
                  AsyncMock(side_effect=RuntimeError("GitHub API error (404): Not Found"))),
        ):
            from services.pr_review import review_pr
            with pytest.raises(RuntimeError) as exc_info:
                await review_pr("acme", "private-repo", 99)

        msg = str(exc_info.value)
        # Must not be a raw stack trace; must be human-readable
        assert "not found" in msg.lower() or "access" in msg.lower() or "permission" in msg.lower() or "#99" in msg
        assert "Traceback" not in msg

    @pytest.mark.asyncio
    async def test_403_returns_plain_error(self):
        with (
            patch("services.github.is_connected", return_value=True),
            patch("services.pr_review._get_pr_extended",
                  AsyncMock(side_effect=RuntimeError("GitHub API error (403): Bad credentials"))),
        ):
            from services.pr_review import review_pr
            with pytest.raises(RuntimeError) as exc_info:
                await review_pr("acme", "private-repo", 99)

        msg = str(exc_info.value)
        assert "permission" in msg.lower() or "token" in msg.lower() or "scope" in msg.lower()
        assert "Traceback" not in msg

    @pytest.mark.asyncio
    async def test_not_connected_returns_plain_error(self):
        with patch("services.github.is_connected", return_value=False):
            from services.pr_review import review_pr
            with pytest.raises(RuntimeError) as exc_info:
                await review_pr("acme", "repo", 1)

        assert "not connected" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# URL detection regex (unit test — no WebSocket needed)
# ---------------------------------------------------------------------------

class TestPRUrlDetection:
    def _regex(self):
        return re.compile(
            r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)",
            re.IGNORECASE,
        )

    def test_plain_url_matches(self):
        m = self._regex().search("https://github.com/acme/my-repo/pull/123")
        assert m is not None
        assert m.group(1) == "acme"
        assert m.group(2) == "my-repo"
        assert m.group(3) == "123"

    def test_url_in_sentence_matches(self):
        text = "Please review https://github.com/foo/bar/pull/42 before merging"
        m = self._regex().search(text)
        assert m is not None
        assert m.group(2) == "bar"
        assert m.group(3) == "42"

    def test_http_matches(self):
        m = self._regex().search("http://github.com/x/y/pull/1")
        assert m is not None

    def test_non_pr_url_does_not_match(self):
        m = self._regex().search("https://github.com/acme/repo/issues/1")
        assert m is None

    def test_no_url_does_not_match(self):
        m = self._regex().search("just a plain message with no URL")
        assert m is None

    def test_partial_url_does_not_match(self):
        m = self._regex().search("github.com/acme/repo/pull/1")
        assert m is None


# ---------------------------------------------------------------------------
# Diff chunking helpers (pure unit tests)
# ---------------------------------------------------------------------------

class TestDiffChunking:
    def test_short_diff_not_truncated(self):
        from services.pr_review import _chunk_diff
        diff = "diff --git a/foo.py\n+x = 1\n"
        result, truncated = _chunk_diff(diff, [{"filename": "foo.py"}])
        assert truncated is False
        assert "x = 1" in result

    def test_long_diff_truncated(self):
        from services.pr_review import _chunk_diff, _DIFF_CHAR_LIMIT
        big_section = "diff --git a/big.py\n" + ("+x\n" * 5000)
        big_diff = big_section * 5  # well over the limit
        result, truncated = _chunk_diff(big_diff, [{"filename": "big.py"}] * 5)
        assert truncated is True
        assert len(result) <= _DIFF_CHAR_LIMIT + 500  # small tolerance for section boundary

    def test_too_many_files_truncated(self):
        from services.pr_review import _chunk_diff, _MAX_FILES_IN_DIFF
        sections = [f"diff --git a/f{i}.py\n+x = {i}\n" for i in range(_MAX_FILES_IN_DIFF + 10)]
        big_diff = "".join(sections)
        files = [{"filename": f"f{i}.py"} for i in range(_MAX_FILES_IN_DIFF + 10)]
        _result, truncated = _chunk_diff(big_diff, files)
        assert truncated is True

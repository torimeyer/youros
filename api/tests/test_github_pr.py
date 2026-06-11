"""F1: spec-to-PR traceability. get_pr service function."""

from unittest.mock import AsyncMock, patch

import pytest

from services import github as github_service


def _pr_payload(**over):
    base = {
        "number": 123,
        "title": "Add dark mode",
        "state": "open",
        "draft": False,
        "merged_at": None,
        "created_at": "2026-06-01T00:00:00Z",
        "html_url": "https://github.com/acme/web/pull/123",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_get_pr_open_returns_plain_state():
    with patch.object(github_service, "_github_get", AsyncMock(return_value=_pr_payload())):
        result = await github_service.get_pr("acme", "web", 123)
    assert result["state"] == "open"
    assert result["number"] == 123
    assert result["title"] == "Add dark mode"


@pytest.mark.asyncio
async def test_get_pr_draft_is_in_review():
    with patch.object(github_service, "_github_get", AsyncMock(return_value=_pr_payload(draft=True))):
        result = await github_service.get_pr("acme", "web", 123)
    assert result["state"] == "in_review"


@pytest.mark.asyncio
async def test_get_pr_merged():
    payload = _pr_payload(state="closed", merged_at="2026-06-02T10:00:00Z")
    with patch.object(github_service, "_github_get", AsyncMock(return_value=payload)):
        result = await github_service.get_pr("acme", "web", 123)
    assert result["state"] == "merged"
    assert result["merged_at"] == "2026-06-02T10:00:00Z"


@pytest.mark.asyncio
async def test_get_pr_closed_unmerged():
    payload = _pr_payload(state="closed", merged_at=None)
    with patch.object(github_service, "_github_get", AsyncMock(return_value=payload)):
        result = await github_service.get_pr("acme", "web", 123)
    assert result["state"] == "closed"


@pytest.mark.asyncio
async def test_get_pr_calls_correct_path():
    spy = AsyncMock(return_value=_pr_payload())
    with patch.object(github_service, "_github_get", spy):
        await github_service.get_pr("acme", "web", 123)
    spy.assert_awaited_once_with("/repos/acme/web/pulls/123")

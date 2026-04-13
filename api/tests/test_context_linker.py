"""Tests for the cross-integration context linker.

Covers:
- Keyword extraction from task title/description
- Text matching score
- Linked context generation with mocked integrations
- In-memory caching with TTL
- GET /api/tasks/{task_id}/context endpoint
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def test_extract_keywords_basic():
    from services.context_linker import _extract_keywords

    keywords = _extract_keywords("Fix the login bug in auth module")
    assert "login" in keywords
    assert "bug" in keywords
    assert "auth" in keywords
    assert "module" in keywords
    # Stop words removed
    assert "the" not in keywords
    assert "in" not in keywords
    assert "fix" not in keywords


def test_extract_keywords_deduplicates():
    from services.context_linker import _extract_keywords

    keywords = _extract_keywords("deploy deploy deploy service")
    assert keywords.count("deploy") == 1


def test_extract_keywords_caps_at_10():
    from services.context_linker import _extract_keywords

    long_title = " ".join(f"keyword{i}" for i in range(20))
    keywords = _extract_keywords(long_title)
    assert len(keywords) <= 10


def test_extract_keywords_includes_description():
    from services.context_linker import _extract_keywords

    keywords = _extract_keywords("Dashboard task", description="calendar integration needed")
    assert "dashboard" in keywords
    assert "calendar" in keywords
    assert "integration" in keywords


# ---------------------------------------------------------------------------
# Text matching
# ---------------------------------------------------------------------------


def test_text_matches_counts_keywords():
    from services.context_linker import _text_matches

    assert _text_matches("Login auth service is broken", ["login", "auth", "service"]) == 3
    assert _text_matches("Hello world", ["login", "auth"]) == 0
    assert _text_matches("Login page update", ["login", "page", "auth"]) == 2


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_stores_and_retrieves():
    from services.context_linker import _cache_get, _cache_set, _cache

    # Clear any existing cache
    _cache.clear()

    data = {"emails": [], "events": [], "files": []}
    _cache_set("test-id", data)
    result = _cache_get("test-id")
    assert result == data

    _cache.clear()


def test_cache_expires_after_ttl():
    from services.context_linker import _cache_get, _cache, _CACHE_TTL_SECONDS

    _cache.clear()

    # Manually insert an expired entry
    _cache["expired-id"] = (time.time() - _CACHE_TTL_SECONDS - 1, {"emails": [], "events": [], "files": []})
    result = _cache_get("expired-id")
    assert result is None

    _cache.clear()


# ---------------------------------------------------------------------------
# get_linked_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_linked_context_with_matching_emails():
    from services.context_linker import get_linked_context, _cache

    _cache.clear()

    fake_messages = [
        {"id": "msg1", "subject": "Login bug report", "from": "user@example.com", "snippet": "The login page is broken"},
        {"id": "msg2", "subject": "Weekly standup", "from": "team@example.com", "snippet": "All is well"},
    ]

    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.gmail.get_unread_summary", new=AsyncMock(return_value=fake_messages)):
        result = await get_linked_context("task-1", "Fix login page bug")

    assert isinstance(result, dict)
    # "login" and "page" match msg1 (subject + snippet both hit)
    # msg2 has no match (no keywords from "Fix login page bug" appear in standup)
    assert len(result["emails"]) >= 1
    assert result["emails"][0]["subject"] == "Login bug report"

    _cache.clear()


@pytest.mark.asyncio
async def test_get_linked_context_empty_keywords():
    """Tasks with only stop words in the title produce no matches."""
    from services.context_linker import get_linked_context, _cache

    _cache.clear()

    result = await get_linked_context("task-2", "Do it")
    assert result == {"emails": [], "events": [], "files": []}

    _cache.clear()


@pytest.mark.asyncio
async def test_get_linked_context_uses_cache():
    """Second call for the same task returns cached data without calling integrations."""
    from services.context_linker import get_linked_context, _cache

    _cache.clear()

    call_count = 0

    async def fake_unread(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return []

    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.gmail.get_unread_summary", new=fake_unread):
        await get_linked_context("task-3", "Something specific here")
        await get_linked_context("task-3", "Something specific here")

    # Should only have called the integration once
    assert call_count == 1

    _cache.clear()


# ---------------------------------------------------------------------------
# Endpoint test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_context_endpoint_returns_200(client):
    """GET /api/tasks/{task_id}/context should return linked items."""
    from services.context_linker import _cache
    from services.ostk import ostk

    _cache.clear()

    fake_tasks = [
        {"id": "→42", "title": "Review dashboard layout", "description": "Check spacing", "status": "open", "priority": "P1"},
    ]

    with patch.object(ostk, "list_tasks", new=AsyncMock(return_value=fake_tasks)), \
         patch("services.google_auth.is_authenticated", return_value=False):
        resp = await client.get("/api/tasks/→42/context")

    assert resp.status_code == 200
    body = resp.json()
    assert "emails" in body
    assert "events" in body
    assert "files" in body

    _cache.clear()


@pytest.mark.asyncio
async def test_task_context_endpoint_404_for_unknown_task(client):
    """GET /api/tasks/{task_id}/context should 404 for unknown tasks."""
    from services.ostk import ostk

    with patch.object(ostk, "list_tasks", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/tasks/→999/context")

    assert resp.status_code == 404

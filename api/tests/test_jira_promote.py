"""Tests for POST /api/atlassian/jira/promote — Jira issue to needle conversion."""

from unittest.mock import AsyncMock, patch

import pytest

from routers.atlassian import router  # noqa: F401  — fail loudly on import errors


# ---------------------------------------------------------------------------
# POST /api/atlassian/jira/promote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_not_connected(client):
    """Returns 401 when Atlassian is not connected."""
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post(
            "/api/atlassian/jira/promote",
            json={"key": "PROJ-123"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_promote_issue_not_found(client):
    """Returns 404 when the issue key doesn't exist."""
    with patch("routers.atlassian.atlassian_service") as mock_svc, \
         patch("routers.atlassian.create_task"):
        mock_svc.is_connected.return_value = True
        mock_svc.get_issue = AsyncMock(
            side_effect=RuntimeError("Issue PROJ-123 not found.")
        )
        resp = await client.post(
            "/api/atlassian/jira/promote",
            json={"key": "PROJ-123"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_success(client):
    """200 with task_id; title from summary, body has url, source=jira, source_ref=key."""
    mock_create = AsyncMock(return_value={"result": "→1234", "task_id": "1234"})
    fake_issue = {
        "key": "PROJ-123",
        "summary": "Fix the login bug",
        "status": "Open",
        "url": "https://example.atlassian.net/browse/PROJ-123",
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc, \
         patch("routers.atlassian.create_task", mock_create):
        mock_svc.is_connected.return_value = True
        mock_svc.get_issue = AsyncMock(return_value=fake_issue)
        resp = await client.post(
            "/api/atlassian/jira/promote",
            json={"key": "PROJ-123"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "1234"

    call_body = mock_create.call_args[0][0]
    assert "Fix the login bug" in call_body.title
    assert "https://example.atlassian.net/browse/PROJ-123" in call_body.description
    assert call_body.source == "jira"
    assert call_body.source_ref == "PROJ-123"


@pytest.mark.asyncio
async def test_promote_title_truncated_at_60_chars(client):
    """Long summary is truncated to 60 chars with trailing ellipsis."""
    long_summary = "a" * 100
    mock_create = AsyncMock(return_value={"result": "→1235", "task_id": "1235"})
    fake_issue = {
        "key": "PROJ-124",
        "summary": long_summary,
        "status": "Open",
        "url": "https://example.atlassian.net/browse/PROJ-124",
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc, \
         patch("routers.atlassian.create_task", mock_create):
        mock_svc.is_connected.return_value = True
        mock_svc.get_issue = AsyncMock(return_value=fake_issue)
        resp = await client.post(
            "/api/atlassian/jira/promote",
            json={"key": "PROJ-124"},
        )

    assert resp.status_code == 200
    call_body = mock_create.call_args[0][0]
    assert len(call_body.title) <= 60
    assert call_body.title.endswith("...")

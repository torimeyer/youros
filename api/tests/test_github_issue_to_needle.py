"""F2: GitHub issue -> yourOS needle."""

from unittest.mock import AsyncMock, patch

import pytest

from routers.github import _parse_issue_to_needle


def test_parse_extracts_title_body_and_acceptance():
    issue = {
        "number": 7,
        "title": "Fix login timeout",
        "body": "Users get logged out too fast.\n\n- [ ] timeout is 30 min\n- [x] add a test\nplain line",
    }
    parsed = _parse_issue_to_needle(issue)
    assert parsed["title"] == "Fix login timeout"
    assert "Users get logged out too fast." in parsed["description"]
    assert parsed["priority"] == "P2"
    assert parsed["acceptance_criteria"] == ["timeout is 30 min", "add a test"]


def test_parse_no_checkboxes_yields_empty_criteria():
    issue = {"number": 8, "title": "Tweak", "body": "Just a plain body."}
    parsed = _parse_issue_to_needle(issue)
    assert parsed["acceptance_criteria"] == []
    assert parsed["description"] == "Just a plain body."


@pytest.mark.asyncio
async def test_issue_to_needle_endpoint_creates_needle(client):
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=[
            {"number": 7, "title": "Fix login timeout",
             "body": "Body here.\n- [ ] one\n- [ ] two", "html_url": "u"},
        ])
        mock_ostk.add_task = AsyncMock(return_value="added needle →42")
        resp = await client.post("/api/github/issue-to-needle/7", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    kwargs = mock_ostk.add_task.call_args.kwargs
    assert kwargs["priority"] == "P2"
    assert "one" in kwargs["ac"] and "two" in kwargs["ac"]


@pytest.mark.asyncio
async def test_issue_to_needle_accepts_field_overrides(client):
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=[
            {"number": 7, "title": "orig", "body": "orig body", "html_url": "u"},
        ])
        mock_ostk.add_task = AsyncMock(return_value="added needle →42")
        resp = await client.post("/api/github/issue-to-needle/7", json={
            "title": "edited title", "description": "edited desc",
            "acceptance_criteria": ["custom ac"], "priority": "P1",
        })
    assert resp.status_code == 200
    args, kwargs = mock_ostk.add_task.call_args
    assert (args and args[0] == "edited title") or kwargs.get("title") == "edited title"
    assert kwargs["priority"] == "P1"


@pytest.mark.asyncio
async def test_issue_to_needle_not_connected(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/github/issue-to-needle/7", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_mode_needle_uses_needle_for_long_bodies(client):
    long_body = "x" * 60  # > 50 chars
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=[
            {"number": 1, "title": "long one", "body": long_body, "html_url": "u",
             "labels": [], "assignee": "", "created_at": "", "updated_at": "", "state": "open"},
            {"number": 2, "title": "short one", "body": "tiny", "html_url": "u",
             "labels": [], "assignee": "", "created_at": "", "updated_at": "", "state": "open"},
        ])
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.add_task = AsyncMock(return_value="added →9")
        resp = await client.post("/api/github/sync?mode=needle", json={})
    assert resp.status_code == 200
    titles = [c.kwargs.get("title", c.args[0] if c.args else "") for c in mock_ostk.add_task.call_args_list]
    assert any(t == "long one" for t in titles)            # needle: raw title
    assert any(t.startswith("[GH#2]") for t in titles)     # task: prefixed title


@pytest.mark.asyncio
async def test_sync_default_mode_still_tasks(client):
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=[
            {"number": 3, "title": "a", "body": "x" * 60, "html_url": "u",
             "labels": [], "assignee": "", "created_at": "", "updated_at": "", "state": "open"},
        ])
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.add_task = AsyncMock(return_value="added →9")
        resp = await client.post("/api/github/sync", json={})
    titles = [c.kwargs.get("title", c.args[0] if c.args else "") for c in mock_ostk.add_task.call_args_list]
    assert titles and titles[0].startswith("[GH#3]")  # default path unchanged

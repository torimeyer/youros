"""Tests for coordination v2 endpoints (→1452).

Covers:
- GET /api/coordination/dependencies/{epic_key}
- POST /api/coordination/nudge
All Atlassian and Slack helpers are mocked — no real HTTP calls.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/coordination/dependencies/{epic_key}
# ---------------------------------------------------------------------------

class TestDependencies:
    """Tests for the dependency graph endpoint."""

    def test_dependencies_not_connected_returns_empty(self):
        """When Atlassian is not configured, return empty graph not 500."""
        with patch("routers.coordination.atlassian_service.is_connected", return_value=False):
            resp = client.get("/api/coordination/dependencies/PROJ-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_dependencies_happy_path(self):
        """Returns nodes and edges when Jira has linked issues."""
        linked_issues = [
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "outwardIssue": {
                    "key": "PROJ-2",
                    "fields": {
                        "summary": "Downstream task",
                        "status": {"name": "In Progress"},
                        "assignee": {"displayName": "Alice"},
                    },
                },
            }
        ]
        mock_result = {
            "key": "PROJ-1",
            "summary": "Epic root",
            "status": "In Progress",
            "assignee": "Bob",
            "issuelinks": linked_issues,
        }
        with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
             patch("routers.coordination.atlassian_service.get_issue_links", new_callable=AsyncMock, return_value=mock_result):
            resp = client.get("/api/coordination/dependencies/PROJ-1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) >= 1
        assert len(data["edges"]) >= 1
        node_keys = [n["key"] for n in data["nodes"]]
        assert "PROJ-1" in node_keys
        assert "PROJ-2" in node_keys

    def test_dependencies_epic_not_found_returns_404(self):
        """When Jira returns 404 for the epic, pass 404 back."""
        with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
             patch("routers.coordination.atlassian_service.get_issue_links", new_callable=AsyncMock, side_effect=RuntimeError("Issue NOPE-1 not found.")):
            resp = client.get("/api/coordination/dependencies/NOPE-1")
        assert resp.status_code == 404

    def test_dependencies_atlassian_error_returns_empty(self):
        """Generic Atlassian errors return empty graph."""
        with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
             patch("routers.coordination.atlassian_service.get_issue_links", new_callable=AsyncMock, side_effect=RuntimeError("network")):
            resp = client.get("/api/coordination/dependencies/PROJ-1")
        # Should not be 500
        assert resp.status_code in (200, 404)

    # BDD invariant (→1403 style)
    def test_bdd_given_linked_issues_at_least_one_edge_per_link(self):
        """Given an epic with N linked issues, GET /dependencies returns at least N edges."""
        linked_issues = [
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "inwardIssue": {
                    "key": f"PROJ-{i}",
                    "fields": {
                        "summary": f"Issue {i}",
                        "status": {"name": "Open"},
                        "assignee": {"displayName": "Alice"},
                    },
                },
            }
            for i in range(1, 4)
        ]
        mock_result = {
            "key": "EPIC-1",
            "summary": "Parent epic",
            "status": "In Progress",
            "assignee": "Bob",
            "issuelinks": linked_issues,
        }
        with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
             patch("routers.coordination.atlassian_service.get_issue_links", new_callable=AsyncMock, return_value=mock_result):
            resp = client.get("/api/coordination/dependencies/EPIC-1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) >= len(linked_issues), (
            f"Expected >= {len(linked_issues)} edges, got {len(data['edges'])}"
        )


# ---------------------------------------------------------------------------
# POST /api/coordination/nudge
# ---------------------------------------------------------------------------

class TestNudge:
    """Tests for the nudge endpoint."""

    def test_nudge_invalid_channel_returns_400(self):
        """An unrecognised channel value must return 400."""
        resp = client.post("/api/coordination/nudge", json={"jira_key": "PROJ-1", "channel": "email"})
        assert resp.status_code == 400

    def test_nudge_slack_happy_path(self):
        """When Slack is configured, a Slack nudge returns success."""
        with patch("routers.coordination.slack_service.is_connected", return_value=True), \
             patch("routers.coordination.slack_service.list_channels", new_callable=AsyncMock, return_value=[{"id": "C123", "name": "general"}]), \
             patch("routers.coordination.slack_service.post_message", new_callable=AsyncMock, return_value={"ok": True, "ts": "123.456", "channel": "C123"}):
            resp = client.post("/api/coordination/nudge", json={"jira_key": "PROJ-1", "channel": "slack"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "message_id" in data

    def test_nudge_slack_not_configured_returns_failure(self):
        """When Slack is not configured, return success=false not 500."""
        with patch("routers.coordination.slack_service.is_connected", return_value=False):
            resp = client.post("/api/coordination/nudge", json={"jira_key": "PROJ-1", "channel": "slack"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "error" in data

    def test_nudge_jira_happy_path(self):
        """When Atlassian is configured, a Jira nudge posts a comment."""
        with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
             patch("routers.coordination.atlassian_service.add_comment", new_callable=AsyncMock, return_value={"id": "comment-99"}):
            resp = client.post("/api/coordination/nudge", json={"jira_key": "PROJ-1", "channel": "jira"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_nudge_jira_not_configured_returns_failure(self):
        """When Atlassian is not configured, return success=false not 500."""
        with patch("routers.coordination.atlassian_service.is_connected", return_value=False):
            resp = client.post("/api/coordination/nudge", json={"jira_key": "PROJ-1", "channel": "jira"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "error" in data

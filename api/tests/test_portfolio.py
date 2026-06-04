"""Tests for routers/portfolio.py (GET /api/portfolio/health, POST /api/portfolio/confidence/.../approve).

Covers:
  - portfolio_health service: is_configured, compute_rollup_audit, compute_health
  - GET /api/portfolio/health when unconfigured returns the empty sentinel payload
  - POST /api/portfolio/confidence/{key}/approve when unconfigured returns ok:false
  - POST /api/portfolio/confidence/{key}/approve when configured writes the field and comment
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


# ── service unit tests ────────────────────────────────────────────────────────

def test_is_configured_false_when_file_missing(tmp_path):
    from services.portfolio_health import is_configured, load_portfolio_config

    cfg = load_portfolio_config(tmp_path / "portfolio.json")
    assert is_configured(cfg) is False


def test_is_configured_true_when_confidence_field_present(tmp_path):
    cfg_file = tmp_path / "portfolio.json"
    cfg_file.write_text(json.dumps({"confidence_field_id": "customfield_10042"}))

    from services.portfolio_health import is_configured, load_portfolio_config

    cfg = load_portfolio_config(cfg_file)
    assert is_configured(cfg) is True


def test_is_configured_false_when_field_empty_string(tmp_path):
    cfg_file = tmp_path / "portfolio.json"
    cfg_file.write_text(json.dumps({"confidence_field_id": "   "}))

    from services.portfolio_health import is_configured, load_portfolio_config

    cfg = load_portfolio_config(cfg_file)
    assert is_configured(cfg) is False


def test_compute_rollup_audit_flags_all_missing():
    from services.portfolio_health import compute_rollup_audit

    issues = [{"key": "PROJ-1", "parent": None, "description": ""}]
    rows = compute_rollup_audit(issues, links={})

    assert len(rows) == 1
    audit = rows[0]["audit"]
    assert audit["missing_initiative_parent"] is True
    assert audit["missing_description"] is True
    assert audit["missing_ref_docs"] is True
    assert audit["missing_kr_link"] is True


def test_compute_rollup_audit_passes_when_complete():
    from services.portfolio_health import compute_rollup_audit

    issues = [{
        "key": "PROJ-2",
        "parent": {"key": "INIT-1"},
        "description": "See https://example.com/docs for details.",
    }]
    links = {"PROJ-2": {"issuelinks": [{"outwardIssue": {"key": "KR-1"}}]}}
    rows = compute_rollup_audit(issues, links)

    audit = rows[0]["audit"]
    assert audit["missing_initiative_parent"] is False
    assert audit["missing_description"] is False
    assert audit["missing_ref_docs"] is False
    assert audit["missing_kr_link"] is False


def test_compute_health_on_track_when_all_children_done():
    from services.portfolio_health import compute_health, HEALTH_ON_TRACK

    result = compute_health({}, {"children_total": 4, "children_done": 4})

    assert result["health"] == HEALTH_ON_TRACK


def test_compute_health_off_track_when_blocked():
    from services.portfolio_health import compute_health, HEALTH_OFF_TRACK

    result = compute_health({}, {"blocked": True})

    assert result["health"] == HEALTH_OFF_TRACK
    assert any("Blocked" in r for r in result["reasons"])


def test_compute_health_at_risk_when_due_slipped():
    from services.portfolio_health import compute_health, HEALTH_AT_RISK

    result = compute_health({}, {"due_slipped": True})

    assert result["health"] == HEALTH_AT_RISK


def test_compute_health_at_risk_when_low_completion():
    from services.portfolio_health import compute_health, HEALTH_AT_RISK

    result = compute_health({}, {"children_total": 10, "children_done": 2})

    assert result["health"] == HEALTH_AT_RISK


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_health_unconfigured_returns_empty_payload(client):
    """GET /api/portfolio/health returns configured:false with empty lists when no config."""
    with patch("routers.portfolio.build_health_report", new_callable=AsyncMock,
               return_value={"configured": False, "krs": [], "audit_findings": [], "pending_approvals": []}):
        resp = await client.get("/api/portfolio/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["krs"] == []
    assert data["audit_findings"] == []
    assert data["pending_approvals"] == []


@pytest.mark.asyncio
async def test_portfolio_health_returns_configured_report(client):
    """GET /api/portfolio/health surfaces the report from build_health_report."""
    mock_report = {
        "configured": True,
        "krs": [{"key": "KR-1", "title": "Rollup", "health": "on_track", "reasons": [], "initiatives": []}],
        "audit_findings": [],
        "pending_approvals": [],
    }
    with patch("routers.portfolio.build_health_report", new_callable=AsyncMock,
               return_value=mock_report):
        resp = await client.get("/api/portfolio/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert len(data["krs"]) == 1
    assert data["krs"][0]["key"] == "KR-1"


@pytest.mark.asyncio
async def test_approve_confidence_unconfigured_returns_ok_false(client):
    """POST approve returns ok:false with reason 'unconfigured' when portfolio.json is absent."""
    with patch("services.portfolio_health.load_portfolio_config", return_value={}), \
         patch("services.portfolio_health.is_configured", return_value=False):
        resp = await client.post(
            "/api/portfolio/confidence/PROJ-1/approve",
            json={"value": "High", "note": "", "why": ""},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["reason"] == "unconfigured"
    assert data["key"] == "PROJ-1"


@pytest.mark.asyncio
async def test_approve_confidence_writes_field_when_configured(client):
    """POST approve calls update_issue_fields with the correct field id and value."""
    cfg = {"confidence_field_id": "customfield_10042"}
    mock_update = AsyncMock(return_value=None)
    mock_comment = AsyncMock(return_value={"id": "cmt-1"})

    with patch("services.portfolio_health.load_portfolio_config", return_value=cfg), \
         patch("services.portfolio_health.is_configured", return_value=True), \
         patch("services.atlassian.update_issue_fields", mock_update), \
         patch("services.atlassian.add_comment", mock_comment):
        resp = await client.post(
            "/api/portfolio/confidence/PROJ-1/approve",
            json={"value": "High", "note": "", "why": ""},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["key"] == "PROJ-1"
    assert data["written_value"] == "High"
    mock_update.assert_awaited_once_with("PROJ-1", {"customfield_10042": "High"})


@pytest.mark.asyncio
async def test_approve_confidence_posts_comment_when_why_set(client):
    """POST approve posts a comment to the issue when 'why' is non-empty."""
    cfg = {"confidence_field_id": "customfield_10042"}
    mock_comment = AsyncMock(return_value={"id": "cmt-99"})

    with patch("services.portfolio_health.load_portfolio_config", return_value=cfg), \
         patch("services.portfolio_health.is_configured", return_value=True), \
         patch("services.atlassian.update_issue_fields", AsyncMock()), \
         patch("services.atlassian.add_comment", mock_comment):
        resp = await client.post(
            "/api/portfolio/confidence/PROJ-2/approve",
            json={"value": "Medium", "note": "", "why": "Due date slipped by two weeks."},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["comment_id"] == "cmt-99"
    mock_comment.assert_awaited_once_with("PROJ-2", "Due date slipped by two weeks.")


@pytest.mark.asyncio
async def test_approve_confidence_no_comment_when_why_empty(client):
    """POST approve skips the comment call when 'why' is empty."""
    cfg = {"confidence_field_id": "customfield_10042"}
    mock_comment = AsyncMock(return_value={"id": "cmt-1"})

    with patch("services.portfolio_health.load_portfolio_config", return_value=cfg), \
         patch("services.portfolio_health.is_configured", return_value=True), \
         patch("services.atlassian.update_issue_fields", AsyncMock()), \
         patch("services.atlassian.add_comment", mock_comment):
        resp = await client.post(
            "/api/portfolio/confidence/PROJ-3/approve",
            json={"value": "Low", "note": "", "why": ""},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["comment_id"] is None
    mock_comment.assert_not_called()

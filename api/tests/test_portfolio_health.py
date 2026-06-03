"""Tests for the Executive Summary backend (portfolio_health + portfolio router).

All hermetic: no real file I/O beyond tmp_path/monkeypatch, no network. The
atlassian client is mocked the way test_atlassian_sync.py mocks it.

The whole feature is vendor-neutral: no source-specific identifiers appear in
the code under test. Tests supply a fake mapping via a temp portfolio.json.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services import portfolio_health


# ---------------------------------------------------------------------------
# Config accessor
# ---------------------------------------------------------------------------

def _write_config(tmp_path, monkeypatch, **kwargs) -> None:
    cfg_path = tmp_path / "portfolio.json"
    cfg_path.write_text(json.dumps(kwargs))
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", cfg_path)


def test_unconfigured_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    cfg = portfolio_health.load_portfolio_config()
    assert cfg == {}
    assert portfolio_health.is_configured(cfg) is False


def test_unconfigured_when_confidence_field_missing(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, board_or_jql="BOARD-1")
    cfg = portfolio_health.load_portfolio_config()
    assert portfolio_health.is_configured(cfg) is False


def test_configured_when_confidence_field_present(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, confidence_field_id="cf_x", board_or_jql="BOARD-1")
    cfg = portfolio_health.load_portfolio_config()
    assert cfg["confidence_field_id"] == "cf_x"
    assert portfolio_health.is_configured(cfg) is True


def test_load_config_tolerates_corrupt_json(tmp_path, monkeypatch):
    cfg_path = tmp_path / "portfolio.json"
    cfg_path.write_text("{ this is not json ")
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", cfg_path)
    assert portfolio_health.load_portfolio_config() == {}


# ---------------------------------------------------------------------------
# compute_rollup_audit
# ---------------------------------------------------------------------------

def test_audit_flags_all_missing():
    # Initiative with no parent, no KR link, blank description, no ref docs.
    issues = [{
        "key": "INIT-1",
        "summary": "Some initiative",
        "type": "Initiative",
        "description_html": "",
        "parent": None,
    }]
    links = {"INIT-1": {"issuelinks": []}}
    audit = portfolio_health.compute_rollup_audit(issues, links)
    assert len(audit) == 1
    row = audit[0]
    assert row["key"] == "INIT-1"
    a = row["audit"]
    assert a["missing_initiative_parent"] is True
    assert a["missing_kr_link"] is True
    assert a["missing_description"] is True
    assert a["missing_ref_docs"] is True


def test_audit_clean_ticket_flags_nothing():
    issues = [{
        "key": "INIT-2",
        "summary": "Healthy initiative",
        "type": "Initiative",
        "description_html": "A real description with a reference doc: https://docs.example/spec",
        "parent": {"key": "KR-9"},
    }]
    links = {"INIT-2": {"issuelinks": [
        {"type": {"outward": "relates to"}, "outwardIssue": {"key": "KR-9"}}
    ]}}
    audit = portfolio_health.compute_rollup_audit(issues, links)
    a = audit[0]["audit"]
    assert a["missing_initiative_parent"] is False
    assert a["missing_kr_link"] is False
    assert a["missing_description"] is False
    assert a["missing_ref_docs"] is False


# ---------------------------------------------------------------------------
# compute_health
# ---------------------------------------------------------------------------

def test_health_off_track_when_blocked():
    issue = {"key": "T-1", "summary": "x"}
    signals = {"blocked": True, "children_total": 4, "children_done": 1, "due_slipped": False}
    res = portfolio_health.compute_health(issue, signals)
    assert res["health"] == portfolio_health.HEALTH_OFF_TRACK
    assert any("block" in r.lower() for r in res["reasons"])


def test_health_at_risk_when_due_slipped():
    issue = {"key": "T-2", "summary": "x"}
    signals = {"blocked": False, "children_total": 4, "children_done": 3, "due_slipped": True}
    res = portfolio_health.compute_health(issue, signals)
    assert res["health"] == portfolio_health.HEALTH_AT_RISK
    assert any("due" in r.lower() or "slip" in r.lower() for r in res["reasons"])


def test_health_on_track_when_progressing():
    issue = {"key": "T-3", "summary": "x"}
    signals = {"blocked": False, "children_total": 4, "children_done": 4, "due_slipped": False}
    res = portfolio_health.compute_health(issue, signals)
    assert res["health"] == portfolio_health.HEALTH_ON_TRACK
    assert isinstance(res["reasons"], list)


def test_health_enum_always_valid():
    valid = {portfolio_health.HEALTH_ON_TRACK, portfolio_health.HEALTH_AT_RISK, portfolio_health.HEALTH_OFF_TRACK}
    for sig in [
        {"blocked": True},
        {"blocked": False, "children_total": 0, "children_done": 0, "due_slipped": False},
        {"blocked": False, "children_total": 10, "children_done": 0, "due_slipped": False},
    ]:
        res = portfolio_health.compute_health({"key": "K"}, sig)
        assert res["health"] in valid


# ---------------------------------------------------------------------------
# draft_confidence_note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_draft_confidence_note_shape():
    issue = {"key": "T-9", "summary": "Migrate auth", "description_html": "desc"}
    health = {"health": portfolio_health.HEALTH_AT_RISK, "reasons": ["due date slipped"]}
    fake_text = "Mitigation: rolling out the fix. Help needed: need a reviewer."
    with patch(
        "services.portfolio_health._llm_complete",
        new=AsyncMock(return_value=fake_text),
    ):
        note = await portfolio_health.draft_confidence_note(issue, health)
    assert isinstance(note, str)
    assert "Mitigation" in note


@pytest.mark.asyncio
async def test_draft_confidence_note_fallback_without_llm():
    # When no LLM is available, returns a deterministic templated note (never empty).
    issue = {"key": "T-9", "summary": "Migrate auth"}
    health = {"health": portfolio_health.HEALTH_OFF_TRACK, "reasons": ["blocked by T-5"]}
    with patch("services.portfolio_health._llm_complete", new=AsyncMock(return_value=None)):
        note = await portfolio_health.draft_confidence_note(issue, health)
    assert isinstance(note, str) and note.strip()
    assert "Mitigation" in note


# ---------------------------------------------------------------------------
# atlassian.update_issue_fields (PUT shape)
# ---------------------------------------------------------------------------

def test_update_issue_fields_puts_fields_payload():
    import asyncio
    from services import atlassian

    captured = {}

    async def fake_request_with_refresh(product, fn):
        class FakeResp:
            status_code = 204

            def json(self):
                return {}

        class FakeClient:
            async def put(self, url, **kwargs):
                captured["url"] = url
                captured["json"] = kwargs.get("json")
                return FakeResp()

        resp = await fn(FakeClient(), {}, "https://base", "site")
        return resp, "https://base", "site"

    async def run():
        with patch.object(atlassian, "_request_with_refresh", new=fake_request_with_refresh):
            await atlassian.update_issue_fields("ABC-1", {"customfield_999": "On track"})

    asyncio.run(run())

    assert captured["url"].endswith("/rest/api/3/issue/ABC-1")
    assert captured["json"] == {"fields": {"customfield_999": "On track"}}


# ---------------------------------------------------------------------------
# GET /api/portfolio/health
# ---------------------------------------------------------------------------

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import portfolio

    app = FastAPI()
    app.include_router(portfolio.router, prefix="/api")
    return TestClient(app)


def test_get_health_unconfigured_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    client = _client()
    resp = client.get("/api/portfolio/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "configured": False,
        "krs": [],
        "audit_findings": [],
        "pending_approvals": [],
    }


def test_get_health_configured_returns_tree(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, confidence_field_id="cf_x", board_or_jql="BOARD-1")

    fake = {
        "configured": True,
        "krs": [{
            "key": "KR-1", "title": "Grow X", "health": "at_risk", "reasons": ["one initiative off track"],
            "initiatives": [{
                "key": "INIT-1", "title": "Do thing", "health": "off_track", "reasons": ["blocked"],
                "audit": {"missing_initiative_parent": False, "missing_kr_link": False,
                          "missing_description": True, "missing_ref_docs": True},
            }],
        }],
        "audit_findings": [{"key": "INIT-1", "finding": "missing_description", "detail": "No description"}],
        "pending_approvals": [{"key": "INIT-1", "title": "Do thing", "draft_value": "off_track",
                               "draft_note": "Mitigation: ...", "why": "blocked"}],
    }
    with patch(
        "routers.portfolio.build_health_report", new=AsyncMock(return_value=fake)
    ):
        client = _client()
        resp = client.get("/api/portfolio/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["krs"][0]["key"] == "KR-1"
    assert body["krs"][0]["initiatives"][0]["audit"]["missing_description"] is True
    assert body["pending_approvals"][0]["draft_value"] == "off_track"


# ---------------------------------------------------------------------------
# POST /api/portfolio/confidence/{key}/approve
# ---------------------------------------------------------------------------

def test_approve_writes_field_and_comment(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, confidence_field_id="cf_x")

    update_mock = AsyncMock()
    comment_mock = AsyncMock(return_value={"id": "10001"})

    with (
        patch("routers.portfolio.atlassian.update_issue_fields", new=update_mock),
        patch("routers.portfolio.atlassian.add_comment", new=comment_mock),
    ):
        client = _client()
        resp = client.post(
            "/api/portfolio/confidence/INIT-1/approve",
            json={"value": "on_track", "note": "Mitigation: shipping", "why": "tests green"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["key"] == "INIT-1"
    assert body["written_value"] == "on_track"
    assert body["comment_id"] == "10001"
    update_mock.assert_awaited_once()
    # The confidence field id from config must be the key written.
    _, kwargs_or_args = update_mock.await_args
    called_args = update_mock.await_args.args
    assert called_args[0] == "INIT-1"
    assert called_args[1] == {"cf_x": "on_track"}
    comment_mock.assert_awaited_once()


def test_approve_unconfigured_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    update_mock = AsyncMock()
    with patch("routers.portfolio.atlassian.update_issue_fields", new=update_mock):
        client = _client()
        resp = client.post(
            "/api/portfolio/confidence/INIT-1/approve",
            json={"value": "on_track", "note": "x", "why": "y"},
        )
    # Unconfigured: nothing is written. (Returns ok:false; never calls atlassian.)
    body = resp.json()
    assert body.get("ok") is False
    update_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Weekly job surfaces a briefing action item (not an auto-write)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weekly_job_surfaces_action_item(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, confidence_field_id="cf_x", board_or_jql="BOARD-1")
    fake_report = {
        "configured": True,
        "krs": [],
        "audit_findings": [],
        "pending_approvals": [
            {"key": "INIT-1", "title": "A", "draft_value": "at_risk", "draft_note": "Mitigation: x", "why": "z"},
            {"key": "INIT-2", "title": "B", "draft_value": "off_track", "draft_note": "Mitigation: y", "why": "w"},
        ],
    }
    with patch(
        "services.portfolio_health.build_health_report", new=AsyncMock(return_value=fake_report)
    ):
        item = await portfolio_health.build_weekly_action_item()
    assert item is not None
    assert item["type"] == "review_confidence_updates"
    assert "2" in item["label"]
    assert item["action_url"].endswith("/portfolio/health") or "portfolio" in item["action_url"]


@pytest.mark.asyncio
async def test_weekly_job_no_item_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    item = await portfolio_health.build_weekly_action_item()
    assert item is None


@pytest.mark.asyncio
async def test_refresh_weekly_merges_into_briefing(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, confidence_field_id="cf_x", board_or_jql="BOARD-1")
    from services import briefing

    state_path = tmp_path / "briefing_state.json"
    monkeypatch.setattr(briefing, "BRIEFING_STATE_PATH", state_path)
    # Seed an existing unrelated item to prove we prepend, not clobber.
    briefing._save_state({"action_items": [{"type": "close_task", "label": "x"}]})

    fake_item = {
        "type": "review_confidence_updates",
        "label": "3 confidence updates awaiting your approval",
        "action_url": "/portfolio/health",
        "context": "...",
    }
    with patch(
        "services.portfolio_health.build_weekly_action_item",
        new=AsyncMock(return_value=fake_item),
    ):
        written = await portfolio_health.refresh_weekly_briefing()

    assert written == fake_item
    items = briefing._load_state()["action_items"]
    assert items[0]["type"] == "review_confidence_updates"
    assert any(i["type"] == "close_task" for i in items)


@pytest.mark.asyncio
async def test_refresh_weekly_noop_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    written = await portfolio_health.refresh_weekly_briefing()
    assert written is None

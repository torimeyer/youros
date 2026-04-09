"""Tests for the heuristic agent pattern analyzer.

Covers:
- analyze_runs() classification
- template_stats() aggregation
- recommendations() rule set (underbudgeted, overbudgeted, wrong model,
  abandoned-fast, high-success)
- empty-history graceful behavior
- the three HTTP endpoints
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import services.agent_patterns as ap


# ---------- fixtures ----------

def _ts(seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _write_state(tmp_path: Path, state: dict) -> Path:
    path = tmp_path / "agent_state.json"
    path.write_text(json.dumps(state))
    return path


def _write_templates(tmp_path: Path, templates: list[dict]) -> Path:
    path = tmp_path / "agent_templates.json"
    path.write_text(json.dumps(templates))
    return path


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    """Point the analyzer at temp files and stub out transcript cost reads."""
    state_path = tmp_path / "agent_state.json"
    dur_path = tmp_path / "agent_durations.json"
    tmpl_path = tmp_path / "agent_templates.json"
    state_path.write_text("{}")
    dur_path.write_text(json.dumps({"durations": []}))
    tmpl_path.write_text("[]")

    monkeypatch.setattr(ap, "AGENT_STATE_PATH", state_path)
    monkeypatch.setattr(ap, "AGENT_DURATIONS_PATH", dur_path)
    monkeypatch.setattr(ap, "AGENT_TEMPLATES_PATH", tmpl_path)
    # Per-run cost uses the transcript resolver which touches the filesystem.
    # Tests inject cost directly via state metadata instead.
    monkeypatch.setattr(ap, "_cost_from_transcript", lambda name: None)

    return {
        "state": state_path,
        "durations": dur_path,
        "templates": tmpl_path,
    }


# ---------- analyze_runs ----------

def test_analyze_runs_classifies_completed_failed_abandoned(patched_paths):
    now = datetime.now(timezone.utc)
    state = {
        "done-one": {
            "spawned_at": _ts(300),
            "completed_at": _ts(100),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
        },
        "crash-one": {
            "spawned_at": _ts(400),
            "failed_at": _ts(350),
            "budget": "1.0",
            "model": "claude-haiku-4-5",
            "status": "failed",
        },
        "gave-up-one": {
            "spawned_at": _ts(500),
            "abandoned_at": _ts(480),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "abandoned",
        },
    }
    patched_paths["state"].write_text(json.dumps(state))

    rows = ap.analyze_runs()
    by_name = {r["name"]: r for r in rows}

    assert by_name["done-one"]["status"] == "completed"
    assert by_name["crash-one"]["status"] == "failed"
    assert by_name["gave-up-one"]["status"] == "abandoned"
    # Durations computed
    assert by_name["done-one"]["duration_sec"] is not None
    assert by_name["done-one"]["duration_sec"] >= 100
    # Model normalized
    assert by_name["done-one"]["model"] == "sonnet"
    assert by_name["crash-one"]["model"] == "haiku"


# ---------- template_stats ----------

def test_template_stats_computes_success_rate_and_median_duration(patched_paths):
    templates = [
        {"id": "tmpl-summarizer", "name": "Summarizer", "model": "sonnet", "budget": 2.0},
    ]
    # Fixed ISO timestamps so the computed durations are exact.
    state = {
        "summarizer-a": {
            "spawned_at": "2026-04-08T12:00:00+00:00",
            "completed_at": "2026-04-08T12:01:00+00:00",  # 60 sec
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-summarizer",
        },
        "summarizer-b": {
            "spawned_at": "2026-04-08T12:10:00+00:00",
            "completed_at": "2026-04-08T12:12:00+00:00",  # 120 sec
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-summarizer",
        },
        "summarizer-c": {
            "spawned_at": "2026-04-08T12:20:00+00:00",
            "failed_at": "2026-04-08T12:20:50+00:00",  # 50 sec
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "failed",
            "template_id": "tmpl-summarizer",
        },
    }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    stats = ap.template_stats()
    assert len(stats) == 1
    s = stats[0]
    assert s["template_name"] == "Summarizer"
    assert s["spawn_count"] == 3
    assert s["completed_count"] == 2
    assert abs(s["success_rate"] - (2 / 3)) < 1e-2
    # Median of [60, 120, 50] = 60
    assert s["median_duration_sec"] == 60


# ---------- recommendations: underbudgeted ----------

def test_recommendations_flags_underbudgeted_runs(patched_paths, monkeypatch):
    templates = [
        {"id": "tmpl-prd", "name": "PRD Draft", "model": "sonnet", "budget": 3.0},
    ]
    state = {
        "prd-a": {
            "spawned_at": _ts(500),
            "completed_at": _ts(300),
            "budget": "3.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-prd",
        },
        "prd-b": {
            "spawned_at": _ts(400),
            "completed_at": _ts(200),
            "budget": "3.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-prd",
        },
    }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    # Both runs burned 90% of budget.
    def fake_cost(name):
        return 2.7
    monkeypatch.setattr(ap, "_cost_from_transcript", fake_cost)

    recs = ap.recommendations()
    under = [r for r in recs if r["type"] == "underbudgeted"]
    assert len(under) == 1
    assert under[0]["severity"] == "warning"
    assert under[0]["related_template_id"] == "tmpl-prd"
    assert under[0]["suggested_value"] is not None
    assert under[0]["suggested_value"] > 3.0


# ---------- recommendations: overbudgeted ----------

def test_recommendations_flags_overbudgeted_runs(patched_paths, monkeypatch):
    templates = [
        {"id": "tmpl-mini", "name": "Daily Planner", "model": "sonnet", "budget": 2.0},
    ]
    state = {}
    for i, offset in enumerate([600, 500, 400]):
        state[f"planner-{i}"] = {
            "spawned_at": _ts(offset),
            "completed_at": _ts(offset - 30),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-mini",
        }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    # Every run used 10% of the budget.
    monkeypatch.setattr(ap, "_cost_from_transcript", lambda name: 0.2)

    recs = ap.recommendations()
    over = [r for r in recs if r["type"] == "overbudgeted"]
    assert len(over) == 1
    assert over[0]["severity"] == "tip"
    assert over[0]["related_template_id"] == "tmpl-mini"
    assert over[0]["suggested_value"] is not None
    assert over[0]["suggested_value"] < 2.0


# ---------- recommendations: wrong model ----------

def test_recommendations_flags_wrong_model(patched_paths):
    templates = [
        {"id": "tmpl-code", "name": "Code Review", "model": "sonnet", "budget": 2.0},
    ]
    state = {}
    # Sonnet: 3/3 completed
    for i, offset in enumerate([700, 690, 680]):
        state[f"code-sonnet-{i}"] = {
            "spawned_at": _ts(offset),
            "completed_at": _ts(offset - 30),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-code",
        }
    # Haiku: 0/3 completed
    for i, offset in enumerate([600, 590, 580]):
        state[f"code-haiku-{i}"] = {
            "spawned_at": _ts(offset),
            "failed_at": _ts(offset - 10),
            "budget": "2.0",
            "model": "claude-haiku-4-5",
            "status": "failed",
            "template_id": "tmpl-code",
        }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    recs = ap.recommendations()
    wrong = [r for r in recs if r["type"] == "wrong_model"]
    assert len(wrong) == 1
    assert wrong[0]["suggested_value"] == "sonnet"
    assert wrong[0]["related_template_id"] == "tmpl-code"


# ---------- recommendations: slow template ----------

def test_recommendations_flags_slow_template(patched_paths):
    templates = [
        {"id": "tmpl-slow", "name": "Giant Research", "model": "sonnet", "budget": 5.0},
    ]
    # Two runs, each taking 10 minutes.
    state = {
        "slow-a": {
            "spawned_at": _ts(1200),
            "completed_at": _ts(600),
            "budget": "5.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-slow",
        },
        "slow-b": {
            "spawned_at": _ts(1300),
            "completed_at": _ts(700),
            "budget": "5.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-slow",
        },
    }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    recs = ap.recommendations()
    slow = [r for r in recs if r["type"] == "slow_template"]
    assert len(slow) == 1
    assert "minutes" in slow[0]["message"]


# ---------- recommendations: abandoned fast ----------

def test_recommendations_flags_abandoned_fast_prompts(patched_paths):
    state = {
        "bad-prompt-1": {
            "spawned_at": _ts(400),
            "abandoned_at": _ts(395),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "abandoned",
        },
        "bad-prompt-2": {
            "spawned_at": _ts(300),
            "abandoned_at": _ts(280),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "abandoned",
        },
        "slow-abandon": {
            # Abandoned after 10 minutes, not "fast"
            "spawned_at": _ts(1000),
            "abandoned_at": _ts(400),
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "abandoned",
        },
    }
    patched_paths["state"].write_text(json.dumps(state))

    recs = ap.recommendations()
    fast = [r for r in recs if r["type"] == "abandoned_fast"]
    assert len(fast) == 1
    assert fast[0]["severity"] == "warning"
    assert "2 agents" in fast[0]["message"]
    assert "bad-prompt-1" in fast[0]["message"]


# ---------- recommendations: high-success highlights ----------

def test_recommendations_highlights_high_success_templates(patched_paths):
    templates = [
        {"id": "tmpl-a", "name": "Reliable Template", "model": "sonnet", "budget": 2.0},
        {"id": "tmpl-b", "name": "Other Template", "model": "sonnet", "budget": 2.0},
    ]
    state = {}
    # Template A: 3/3 completed (100%)
    for i in range(3):
        state[f"reliable template-{i}"] = {
            "spawned_at": f"2026-04-08T12:{i:02d}:00+00:00",
            "completed_at": f"2026-04-08T12:{i:02d}:30+00:00",
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "status": "completed",
            "template_id": "tmpl-a",
        }
    # Template B: 1/2 completed (50%)
    state["other template-a"] = {
        "spawned_at": "2026-04-08T13:00:00+00:00",
        "completed_at": "2026-04-08T13:00:30+00:00",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "status": "completed",
        "template_id": "tmpl-b",
    }
    state["other template-b"] = {
        "spawned_at": "2026-04-08T13:10:00+00:00",
        "failed_at": "2026-04-08T13:10:20+00:00",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "status": "failed",
        "template_id": "tmpl-b",
    }
    patched_paths["templates"].write_text(json.dumps(templates))
    patched_paths["state"].write_text(json.dumps(state))

    recs = ap.recommendations()
    highs = [r for r in recs if r["type"] == "high_success"]
    # Both eligible templates show up, sorted by success rate
    assert len(highs) == 2
    assert highs[0]["related_template_id"] == "tmpl-a"
    assert highs[0]["severity"] == "info"


# ---------- recommendations: empty history ----------

def test_recommendations_empty_when_no_history(patched_paths):
    assert ap.recommendations() == []
    assert ap.template_stats() == []
    assert ap.analyze_runs() == []


# ---------- HTTP endpoints ----------

@pytest.mark.asyncio
async def test_recommendations_endpoint_returns_200(patched_paths):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agent-patterns/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert "recommendations" in body
    assert isinstance(body["recommendations"], list)


@pytest.mark.asyncio
async def test_template_stats_endpoint_returns_200(patched_paths):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agent-patterns/template-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "stats" in body
    assert isinstance(body["stats"], list)


@pytest.mark.asyncio
async def test_runs_endpoint_returns_200_and_paginates(patched_paths):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agent-patterns/runs?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert "total" in body
    assert body["limit"] == 10
    assert body["offset"] == 0

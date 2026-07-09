"""Tests for →2534: GET /costs?journey_id= attributes chat.completion costs
to one spec journey by cross-referencing audit timestamps.

Window: earliest spec_built_start / spec_journey_started with the journey_id
opens the window; spec_journey_complete / spec_built_complete with the
journey_id closes it (falling back to the last agent.completed that names one
of the journey's agents, then to "now").
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


JOURNEY_AUDIT = [
    # Before the journey: must NOT be counted.
    {"event": "chat.completion", "model": "claude-sonnet-4-6", "input_tokens": 999,
     "output_tokens": 999, "timestamp": "2026-04-05T09:00:00+00:00"},
    # Journey markers.
    {"event": "spec_built_start", "spec_path": "docs/spec/j.md",
     "journey_id": "jrn-cost1", "ts": "2026-04-05T10:00:00+00:00"},
    {"event": "spec_journey_started", "spec_path": "docs/spec/j.md",
     "journey_id": "jrn-cost1", "agents": ["spec-j-901"],
     "ts": "2026-04-05T10:00:01+00:00"},
    # Inside the window: counted.
    {"event": "chat.completion", "model": "claude-sonnet-4-6", "input_tokens": 100,
     "output_tokens": 50, "timestamp": "2026-04-05T10:05:00+00:00"},
    {"event": "chat.completion", "model": "claude-sonnet-4-6", "input_tokens": 200,
     "output_tokens": 25, "timestamp": "2026-04-05T10:20:00+00:00"},
    # The journey's final agent run finishes here.
    {"event": "agent.completed", "name": "spec-j-901",
     "timestamp": "2026-04-05T10:30:00+00:00"},
    # After the window: must NOT be counted.
    {"event": "chat.completion", "model": "claude-sonnet-4-6", "input_tokens": 777,
     "output_tokens": 777, "timestamp": "2026-04-05T11:00:00+00:00"},
]


def _write_audit(lines: list[dict], tmpdir: Path) -> Path:
    audit_path = tmpdir / "audit.jsonl"
    with open(audit_path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return audit_path


@pytest.mark.asyncio
async def test_costs_journey_filter_sums_window_only(client):
    """Only chat.completion events between build start and the last matching
    agent.completed are attributed to the journey."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(JOURNEY_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?journey_id=jrn-cost1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["journey_id"] == "jrn-cost1"
    assert data["total_input_tokens"] == 300  # 100 + 200
    assert data["total_output_tokens"] == 75  # 50 + 25
    assert data["window_start"].startswith("2026-04-05T10:00:00")
    assert data["window_end"].startswith("2026-04-05T10:30:00")


@pytest.mark.asyncio
async def test_costs_journey_prefers_journey_complete_marker(client):
    """When a spec_journey_complete event exists, its timestamp closes the
    window even if agent.completed events come later."""
    events = list(JOURNEY_AUDIT) + [
        {"event": "spec_journey_complete", "spec_path": "docs/spec/j.md",
         "journey_id": "jrn-cost1", "completed_at": "2026-04-05T10:10:00+00:00",
         "ts": "2026-04-05T10:10:00+00:00"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?journey_id=jrn-cost1")

    assert resp.status_code == 200
    data = resp.json()
    # Only the 10:05 completion fits inside 10:00 → 10:10.
    assert data["total_input_tokens"] == 100
    assert data["total_output_tokens"] == 50
    assert data["window_end"].startswith("2026-04-05T10:10:00")


@pytest.mark.asyncio
async def test_costs_unknown_journey_is_404(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(JOURNEY_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?journey_id=jrn-nope")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_costs_without_journey_param_unchanged(client):
    """The plain /costs aggregation still sees every completion (no window)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(JOURNEY_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            with patch("routers.costs._claude_code_usage_events", return_value=[]):
                resp = await client.get("/api/costs?period=all")

    assert resp.status_code == 200
    data = resp.json()
    assert "journey_id" not in data
    assert data["total_input_tokens"] == 999 + 100 + 200 + 777

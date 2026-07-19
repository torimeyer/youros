"""Tests for →2961: agent runs count toward the savings page.

Before this change the savings window only ever counted chat: chat turns
are recorded per-turn via safe_record_chat_turn, while board agents
recorded nothing at completion, so a day of agent-only work showed the
savings card's empty state. These tests pin:

1. token_metrics.record_agent_run / safe_record_agent_run write an
   agent_run event carrying the run's REAL token total (never estimates).
2. POST /api/agents/{name}/complete records that event from the board
   row's tokens_used counter, tagged source="agent", and skips rows with
   source="chat" whose turns are already recorded (no double counting).
3. GET /api/costs/savings includes agent_run events in every window
   (available:true on agent-only days, agent_run_tokens/agent_run_count).
4. Fold-in: squash_tokens_saved is set from the real per-event
   tokens_saved values, so the tokens-saved surfaces stop silently
   adding zero.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services import token_metrics


@pytest.fixture(autouse=True)
def clear_savings_caches():
    """Invalidate savings/metrics caches before and after every test."""
    from routers import costs as costs_router
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    costs_router._savings_cache.clear()
    costs_router._savings_refresh_in_flight.clear()
    costs_router.invalidate_metrics_parse_cache()
    yield
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    costs_router._savings_cache.clear()
    costs_router._savings_refresh_in_flight.clear()
    costs_router.invalidate_metrics_parse_cache()


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


OSTK_PAYLOAD = {
    "prompt_cache": {"cache_savings_usd": 0.05, "efficiency_pct": 55.0,
                     "cost_usd": 0.04, "no_cache_cost_usd": 0.09},
    "squash": {"compression_pct": 30.0, "est_saved_usd": 0.02},
}


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_run_line(tokens: int, ts: str, name: str = "saa-test-agent") -> str:
    return json.dumps({
        "event": "agent_run",
        "agent": name,
        "model": "claude-sonnet-4",
        "tokens_used": tokens,
        "source": "agent",
        "ts": ts,
    })


def _chat_turn_line(ts: str, input_tokens: int = 500, cache_read: int = 400) -> str:
    return json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": input_tokens,
        "output_tokens": 100,
        "has_ostk_boot": True,
        "boot_context_bytes": 500,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
        "ts": ts,
    })


def _squash_line(tokens_saved: int, ts: str) -> str:
    return json.dumps({
        "event": "squash",
        "source": "kernel_counters",
        "original": 10000,
        "compressed": 7000,
        "saved": 3000,
        "tokens_saved": tokens_saved,
        "ts": ts,
    })


# ---------------------------------------------------------------------------
# 1. Recorder unit tests
# ---------------------------------------------------------------------------

def test_record_agent_run_writes_real_numbers(tmp_path):
    """record_agent_run appends one agent_run event with the run's real
    token total, the model, the agent name, and a source tag."""
    metrics = tmp_path / "metrics.jsonl"
    with patch("services.token_metrics._METRICS_PATH", metrics):
        token_metrics.record_agent_run(
            agent_name="saa-worker-1",
            model="claude-sonnet-4",
            tokens_used=8421,
        )
    lines = [l for l in metrics.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["event"] == "agent_run"
    assert ev["agent"] == "saa-worker-1"
    assert ev["model"] == "claude-sonnet-4"
    assert ev["tokens_used"] == 8421
    assert ev["source"] == "agent"
    assert ev["ts"]


def test_record_agent_run_skips_zero_tokens(tmp_path):
    """A run with no recorded tokens writes nothing. Recording a zero would
    fabricate availability for a run that did no measured work."""
    metrics = tmp_path / "metrics.jsonl"
    with patch("services.token_metrics._METRICS_PATH", metrics):
        token_metrics.record_agent_run(
            agent_name="saa-idle", model="claude-sonnet-4", tokens_used=0,
        )
    assert not metrics.exists() or metrics.read_text().strip() == ""


def test_safe_record_agent_run_never_raises():
    """The safe wrapper swallows every failure so a metrics outage can
    never break agent completion."""
    with patch.object(token_metrics, "record_agent_run",
                      side_effect=RuntimeError("boom")) as rec, \
         patch.object(token_metrics, "maybe_record_compression",
                      side_effect=RuntimeError("boom2")):
        token_metrics.safe_record_agent_run(
            agent_name="x", model="m", tokens_used=5,
        )
    assert rec.call_count == 1


# ---------------------------------------------------------------------------
# 2. Completion path records the run's real token total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_completion_records_agent_run_event(tmp_path):
    """register -> report tokens via POST /budget -> POST /complete must
    append one agent_run event carrying the board row's real tokens_used."""
    from routers.agents import agent_metadata

    metrics = tmp_path / "metrics.jsonl"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path), \
                 patch("services.token_metrics._METRICS_PATH", metrics), \
                 patch("services.token_metrics.maybe_record_compression",
                       return_value=None):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon running",
                    "daemon_running": False,
                    "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.close_task = AsyncMock(return_value=None)

                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "saa-savings-worker", "task": "2961 test",
                          "model": "sonnet", "source": "claude-code"},
                )
                assert resp.status_code == 200

                resp = await client.post(
                    "/api/agents/saa-savings-worker/budget",
                    json={"tokens_used": 123456},
                )
                assert resp.status_code == 200

                resp = await client.post(
                    "/api/agents/saa-savings-worker/complete"
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "completed"

            lines = []
            if metrics.exists():
                lines = [l for l in metrics.read_text().splitlines() if l.strip()]
            agent_events = [
                json.loads(l) for l in lines
                if json.loads(l).get("event") == "agent_run"
            ]
            assert len(agent_events) == 1, (
                "expected exactly one agent_run event after /complete, got "
                f"{lines}"
            )
            ev = agent_events[0]
            assert ev["tokens_used"] == 123456, "must be the run's REAL recorded total"
            assert ev["agent"] == "saa-savings-worker"
            assert ev["source"] == "agent"
        finally:
            agent_metadata.pop("saa-savings-worker", None)


@pytest.mark.asyncio
async def test_chat_session_completion_does_not_double_record(tmp_path):
    """Rows with source="chat" are inline chat tabs whose turns are already
    recorded per-turn by safe_record_chat_turn. /complete on them must NOT
    write an agent_run event, or their usage would be counted twice."""
    from routers.agents import agent_metadata

    metrics = tmp_path / "metrics.jsonl"
    agent_metadata["chat-tab-1"] = {
        "spawned_at": "2026-07-19T00:00:00+00:00",
        "budget": "0",
        "model": "claude-code-subscription",
        "source": "chat",
        "status": "running",
        "tokens_used": 999,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path), \
                 patch("services.token_metrics._METRICS_PATH", metrics), \
                 patch("services.token_metrics.maybe_record_compression",
                       return_value=None):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "", "daemon_running": False, "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.close_task = AsyncMock(return_value=None)

                resp = await client.post("/api/agents/chat-tab-1/complete")
                assert resp.status_code == 200

            content = metrics.read_text() if metrics.exists() else ""
            assert '"agent_run"' not in content, (
                "source=chat completions must not write agent_run events "
                f"(their turns are already recorded per-turn): {content}"
            )
        finally:
            agent_metadata.pop("chat-tab-1", None)


# ---------------------------------------------------------------------------
# 3. Savings windows include recorded agent events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_savings_today_includes_agent_runs(tmp_path, client):
    """A day with ONLY agent runs (no chat) must show real savings data:
    available:true plus the runs' token totals under agent_run_tokens."""
    metrics_path = tmp_path / "metrics.jsonl"
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _agent_run_line(120_000, now_ts) + "\n"
        + _agent_run_line(30_000, now_ts) + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp = await client.get("/api/costs/savings?period=today")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True, (
        "an agent-only day must not show the empty state; before 2961 the "
        f"savings window only counted chat: {data}"
    )
    assert data["agent_run_tokens"] == 150_000
    assert data["agent_run_count"] == 2


@pytest.mark.asyncio
async def test_savings_agent_runs_respect_window(tmp_path, client):
    """Old agent runs stay out of the week window but count in all-time."""
    metrics_path = tmp_path / "metrics.jsonl"
    old_ts = _ts(datetime.now(timezone.utc) - timedelta(days=60))
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _agent_run_line(70_000, old_ts) + "\n"
        + _agent_run_line(50_000, now_ts) + "\n"
    )

    from routers import costs as costs_router
    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp_week = await client.get("/api/costs/savings?period=week")
        costs_router._savings_cache.clear()
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp_all = await client.get("/api/costs/savings?period=all")

    week = resp_week.json()
    allp = resp_all.json()
    assert week["available"] is True
    assert week["agent_run_tokens"] == 50_000
    assert week["agent_run_count"] == 1
    assert allp["available"] is True
    assert allp["agent_run_tokens"] == 120_000
    assert allp["agent_run_count"] == 2


@pytest.mark.asyncio
async def test_savings_available_with_agent_runs_even_without_binary(tmp_path, client):
    """When the ostk binary is missing, a window that contains agent runs
    must still return available:true with the agent numbers."""
    metrics_path = tmp_path / "metrics.jsonl"
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(_agent_run_line(42_000, now_ts) + "\n")

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.side_effect = FileNotFoundError("ostk not found")
        resp = await client.get("/api/costs/savings?period=today")

    data = resp.json()
    assert data["available"] is True, (
        f"agent runs alone must make the card available: {data}"
    )
    assert data["agent_run_tokens"] == 42_000


# ---------------------------------------------------------------------------
# 4. Fold-in: squash_tokens_saved is wired from real event data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_squash_tokens_saved_wired_from_events(tmp_path, client):
    """The payload must carry squash_tokens_saved summed from the real
    per-event tokens_saved values. Before this fix nothing ever set the
    key, so /costs/explain/tokens_saved and the savings card silently
    added zero forever."""
    metrics_path = tmp_path / "metrics.jsonl"
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _chat_turn_line(now_ts) + "\n"
        + _squash_line(750, now_ts) + "\n"
        + _squash_line(250, now_ts) + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp = await client.get("/api/costs/savings?period=today")

    data = resp.json()
    assert data["available"] is True
    assert data["squash_tokens_saved"] == 1000, (
        "squash_tokens_saved must be the sum of real per-event tokens_saved: "
        f"{data}"
    )


@pytest.mark.asyncio
async def test_explain_tokens_saved_includes_real_squash(tmp_path, client):
    """/costs/explain/tokens_saved must include the wired squash figure in
    its total (conversation cache reads + squash tokens saved)."""
    metrics_path = tmp_path / "metrics.jsonl"
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _chat_turn_line(now_ts, input_tokens=500, cache_read=400) + "\n"
        + _squash_line(600, now_ts) + "\n"
    )
    empty_audit = tmp_path / "audit.jsonl"
    empty_audit.write_text("")

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path), \
         patch("routers.costs.AUDIT_PATH", empty_audit):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp = await client.get("/api/costs/explain/tokens_saved?period=today")

    assert resp.status_code == 200
    body = resp.json()
    # conversation cache 400 + squash 600 = 1000
    assert body["result"] == 1000, body

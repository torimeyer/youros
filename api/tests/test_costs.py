import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


SAMPLE_AUDIT = [
    {"event": "project.initialized", "timestamp": "2026-04-03T18:57:33Z"},
    {"event": "agent.spawned", "name": "test-agent", "model": "claude-sonnet-4-5-20250929", "budget": "0.10", "timestamp": "2026-04-04T20:01:02Z"},
    {"event": "agent.spawned", "name": "refactor-bot", "model": "claude-sonnet-4-5-20250929", "budget": "2.00", "timestamp": "2026-04-04T21:30:00Z"},
    {"event": "agent.spawned", "name": "research-agent", "model": "claude-opus-4-5-20250929", "budget": "5.00", "timestamp": "2026-04-03T10:00:00Z"},
    {"event": "task.added", "id": "t-1", "timestamp": "2026-04-04T19:48:52Z"},
    {"event": "session.shutdown", "timestamp": "2026-04-04T22:00:00Z"},
]


def _write_audit(lines: list[dict], tmpdir: Path) -> Path:
    audit_path = tmpdir / "audit.jsonl"
    with open(audit_path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return audit_path


@pytest.mark.asyncio
async def test_costs_returns_all_fields(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    assert resp.status_code == 200
    data = resp.json()
    assert "total_budget" in data
    assert "agent_count" in data
    assert "by_model" in data
    assert "by_date" in data
    assert "agents" in data
    assert "period" in data
    # New fields from the expanded tracking
    assert "total_input_tokens" in data
    assert "total_output_tokens" in data
    assert "event_count" in data
    assert "by_type" in data


@pytest.mark.asyncio
async def test_costs_aggregates_correctly(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 3
    assert data["total_budget"] == 7.10  # 0.10 + 2.00 + 5.00


@pytest.mark.asyncio
async def test_costs_model_breakdown(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    models = {m["model"]: m for m in resp.json()["by_model"]}
    assert "claude-sonnet-4-5-20250929" in models
    assert "claude-opus-4-5-20250929" in models
    assert models["claude-sonnet-4-5-20250929"]["count"] == 2
    assert models["claude-sonnet-4-5-20250929"]["total_budget"] == 2.10
    assert models["claude-opus-4-5-20250929"]["count"] == 1
    assert models["claude-opus-4-5-20250929"]["total_budget"] == 5.00


@pytest.mark.asyncio
async def test_costs_date_breakdown(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    dates = {d["date"]: d for d in resp.json()["by_date"]}
    assert "2026-04-04" in dates
    assert dates["2026-04-04"]["count"] == 2
    assert dates["2026-04-04"]["total_budget"] == 2.10
    assert "2026-04-03" in dates
    assert dates["2026-04-03"]["count"] == 1


@pytest.mark.asyncio
async def test_costs_period_filter_today(client):
    """The 'today' filter should only include events from the local calendar day."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # All sample events are from 2026-04-03/04, which is in the past.
        # With today filter, we should get 0 unless we add a "today" event.
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = SAMPLE_AUDIT + [
            {"event": "agent.spawned", "name": "today-agent", "model": "claude-sonnet-4-5-20250929", "budget": "1.00", "timestamp": now_str},
        ]
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?period=today")

    data = resp.json()
    assert data["period"] == "today"
    # Only the "today" event should be included
    assert data["agent_count"] == 1
    assert data["total_budget"] == 1.00


@pytest.mark.asyncio
async def test_costs_today_uses_local_timezone(client):
    """The 'today' filter must use local midnight, not UTC midnight.

    When the user is in a timezone behind UTC (e.g. CDT, UTC-5), the UTC
    date rolls to tomorrow while it is still evening locally. Events
    from the local morning that have a UTC timestamp of "today" must be
    included. Events from late yesterday (local) that happen to have a
    UTC date of "today" must be excluded.

    This test constructs timestamps relative to local noon so the result
    is independent of the machine's timezone.
    """
    from datetime import datetime, timezone, timedelta

    # Local noon today, converted to UTC, is guaranteed to fall on the
    # local calendar day regardless of timezone offset.
    today_local = datetime.now().strftime("%Y-%m-%d")
    local_noon = datetime.strptime(f"{today_local} 12:00:00", "%Y-%m-%d %H:%M:%S")
    noon_utc_str = local_noon.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # An event from yesterday local (23 hours before local noon).
    yesterday_utc_str = (
        (local_noon - timedelta(hours=23))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    events = [
        {"event": "agent.spawned", "name": "noon-agent", "model": "sonnet", "budget": "1.00", "timestamp": noon_utc_str},
        {"event": "agent.spawned", "name": "yesterday-agent", "model": "sonnet", "budget": "2.00", "timestamp": yesterday_utc_str},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?period=today")

    data = resp.json()
    # Only the noon event should be counted (yesterday-agent is excluded).
    assert data["agent_count"] == 1
    assert data["total_budget"] == 1.00


@pytest.mark.asyncio
async def test_costs_empty_audit(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit([], Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 0
    assert data["total_budget"] == 0.0
    assert data["by_model"] == []
    assert data["by_date"] == []
    assert data["agents"] == []


@pytest.mark.asyncio
async def test_costs_missing_audit_file(client):
    with patch("routers.costs.AUDIT_PATH", Path("/nonexistent/audit.jsonl")):
        resp = await client.get("/api/costs")

    data = resp.json()
    assert resp.status_code == 200
    assert data["agent_count"] == 0
    assert data["total_budget"] == 0.0


@pytest.mark.asyncio
async def test_costs_agents_list_has_details(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    agents = resp.json()["agents"]
    assert len(agents) == 3
    first = agents[0]
    assert "name" in first
    assert "model" in first
    assert "budget" in first
    assert "timestamp" in first


@pytest.mark.asyncio
async def test_costs_non_cost_events_ignored(client):
    """Events that are not in COST_EVENT_TYPES should not be counted."""
    events = [
        {"event": "task.added", "id": "t-1", "timestamp": "2026-04-04T19:48:52Z"},
        {"event": "hay.filed", "straw": "test idea", "timestamp": "2026-04-04T20:32:26Z"},
        {"event": "session.shutdown", "timestamp": "2026-04-04T22:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 0
    assert data["event_count"] == 0
    assert data["total_budget"] == 0.0


# --- New tests for chat.completion and expanded tracking ---


@pytest.mark.asyncio
async def test_costs_includes_chat_completions(client):
    """chat.completion events should appear in cost data with token counts."""
    events = [
        {"event": "agent.spawned", "name": "worker-1", "model": "claude-sonnet-4-20250514", "budget": "2.00", "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-20250514", "budget": 0, "input_tokens": 500, "output_tokens": 200, "provider": "anthropic", "timestamp": "2026-04-04T11:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "gemini-2.5-flash", "budget": 0, "input_tokens": 300, "output_tokens": 150, "provider": "gemini", "timestamp": "2026-04-04T12:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    # agent_count only counts agent.spawned
    assert data["agent_count"] == 1
    # event_count counts all cost events
    assert data["event_count"] == 3
    assert data["total_budget"] == 2.00
    assert data["total_input_tokens"] == 800  # 500 + 300
    assert data["total_output_tokens"] == 350  # 200 + 150


@pytest.mark.asyncio
async def test_costs_by_type_breakdown(client):
    """by_type should separate agent.spawned and chat.completion."""
    events = [
        {"event": "agent.spawned", "name": "w1", "model": "sonnet", "budget": "1.00", "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "agent.spawned", "name": "w2", "model": "sonnet", "budget": "1.00", "timestamp": "2026-04-04T10:01:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    by_type = {t["event"]: t for t in resp.json()["by_type"]}
    assert "agent.spawned" in by_type
    assert "chat.completion" in by_type
    assert by_type["agent.spawned"]["count"] == 2
    assert by_type["agent.spawned"]["total_budget"] == 2.00
    assert by_type["chat.completion"]["count"] == 1
    assert by_type["chat.completion"]["input_tokens"] == 100
    assert by_type["chat.completion"]["output_tokens"] == 50


@pytest.mark.asyncio
async def test_costs_model_breakdown_includes_tokens(client):
    """by_model should include token totals from chat completions."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-20250514", "budget": 0, "input_tokens": 400, "output_tokens": 100, "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-20250514", "budget": 0, "input_tokens": 600, "output_tokens": 200, "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    models = {m["model"]: m for m in resp.json()["by_model"]}
    assert "claude-sonnet-4-20250514" in models
    m = models["claude-sonnet-4-20250514"]
    assert m["count"] == 2
    assert m["input_tokens"] == 1000
    assert m["output_tokens"] == 300


@pytest.mark.asyncio
async def test_costs_chat_items_have_event_field(client):
    """Each item in the agents list should include the event type."""
    events = [
        {"event": "agent.spawned", "name": "w1", "model": "sonnet", "budget": "1.00", "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    items = resp.json()["agents"]
    assert len(items) == 2
    assert items[0]["event"] == "agent.spawned"
    assert items[1]["event"] == "chat.completion"
    assert items[1]["input_tokens"] == 100
    assert items[1]["output_tokens"] == 50


@pytest.mark.asyncio
async def test_costs_period_filter_with_chat_completions(client):
    """Period filter should work on chat.completion events too."""
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-01-01T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 200, "output_tokens": 80, "timestamp": now_str},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?period=today")

    data = resp.json()
    assert data["event_count"] == 1
    assert data["total_input_tokens"] == 200
    assert data["total_output_tokens"] == 80


# --- Test that _log_chat_completion writes to audit.jsonl ---


def test_log_chat_completion_writes_audit_entry(tmp_path):
    """_log_chat_completion should append a chat.completion event to audit.jsonl."""
    from services.chat_providers import _log_chat_completion
    audit_path = tmp_path / "audit.jsonl"

    with patch("services.chat_providers.write_audit_entry") as mock_write:
        _log_chat_completion(
            model="claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=200,
            provider="anthropic",
        )

    mock_write.assert_called_once()
    entry = mock_write.call_args[0][0]
    assert entry["event"] == "chat.completion"
    assert entry["name"] == "chat"
    assert entry["model"] == "claude-sonnet-4-20250514"
    assert entry["input_tokens"] == 500
    assert entry["output_tokens"] == 200
    assert entry["provider"] == "anthropic"
    assert entry["budget"] == 0
    assert "timestamp" in entry


def test_write_audit_entry_creates_file(tmp_path):
    """write_audit_entry should create audit.jsonl if it does not exist."""
    from services.ostk import write_audit_entry
    audit_path = tmp_path / "audit.jsonl"
    assert not audit_path.exists()

    entry = {
        "event": "chat.completion",
        "name": "chat",
        "model": "test-model",
        "timestamp": "2026-04-04T10:00:00Z",
    }
    write_audit_entry(entry, audit_path)

    assert audit_path.exists()
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "chat.completion"
    assert parsed["model"] == "test-model"


@pytest.mark.asyncio
async def test_costs_chat_sessions_grouped(client):
    """Chat completions within 5 minutes are grouped into a single session row."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 200, "output_tokens": 80, "timestamp": "2026-04-04T10:01:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 150, "output_tokens": 60, "timestamp": "2026-04-04T10:03:00Z"},
        # Gap > 5 min, so this starts a new session
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 300, "output_tokens": 100, "timestamp": "2026-04-04T10:10:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    items = resp.json()["agents"]
    # 4 raw events should become 2 sessions
    assert len(items) == 2
    # First session: 3 messages, tokens summed
    assert items[0]["message_count"] == 3
    assert items[0]["input_tokens"] == 450  # 100+200+150
    assert items[0]["output_tokens"] == 190  # 50+80+60
    assert items[0]["name"] == "Chat"
    # Second session: 1 message
    assert items[1]["message_count"] == 1
    assert items[1]["input_tokens"] == 300
    assert items[1]["output_tokens"] == 100


def test_write_audit_entry_appends(tmp_path):
    """write_audit_entry should append, not overwrite."""
    from services.ostk import write_audit_entry
    audit_path = tmp_path / "audit.jsonl"

    write_audit_entry({"event": "first"}, audit_path)
    write_audit_entry({"event": "second"}, audit_path)

    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "first"
    assert json.loads(lines[1])["event"] == "second"


# --- New tests: savings cache, prompt efficiency, budget caps ---


@pytest.mark.asyncio
async def test_savings_cache_returns_fast_on_second_call(client):
    """Second call to /costs/savings should be served from cache and return quickly.

    We cannot call the live ostk binary in tests, so we patch get_ostk_savings
    and verify the TTL cache avoids calling the mock a second time.
    """
    import time
    from unittest.mock import patch as _patch
    from routers import costs as costs_module

    mock_savings = {
        "available": True,
        "savings_usd": 0.05,
        "cache_efficiency_pct": 55.0,
        "compression_pct": 3.0,
        "conversation_cache_pct": 1.0,
        "conversation_cache_read_tokens": 1000,
        "period": "session",
    }

    call_count = {"n": 0}

    def fake_get_ostk_savings():
        call_count["n"] += 1
        return {
            "savings_usd": 0.05,
            "cache_efficiency_pct": 55.0,
            "compression_pct": 3.0,
            "conversation_cache_pct": 1.0,
            "conversation_cache_read_tokens": 1000,
            "period": "session",
        }

    # Clear any existing savings cache so this test is isolated.
    costs_module._savings_cache.clear()

    with _patch("services.token_metrics.get_ostk_savings", side_effect=fake_get_ostk_savings):
        with _patch("routers.costs.AUDIT_PATH", Path("/nonexistent/audit.jsonl")):
            with _patch("routers.costs._read_savings_snapshot", return_value=None):
                with _patch("routers.costs._read_metrics_events_cached", return_value=[]):
                    resp1 = await client.get("/api/costs/savings")
                    t0 = time.monotonic()
                    resp2 = await client.get("/api/costs/savings")
                    elapsed_ms = (time.monotonic() - t0) * 1000

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # The second call must be served from cache -- get_ostk_savings called only once.
    assert call_count["n"] == 1, f"expected 1 call but got {call_count['n']}"
    # Cache hit should be very fast (well under 50 ms on any hardware)
    assert elapsed_ms < 500, f"second call took {elapsed_ms:.1f} ms, expected < 500 ms (cache hit)"


@pytest.mark.asyncio
async def test_costs_cache_hit_rate_no_div_zero(client):
    """cache_hit_rate must be 0.0 when total_input_tokens is 0, not raise ZeroDivisionError."""
    events = [
        {"event": "agent.spawned", "name": "w1", "model": "sonnet", "budget": "1.00", "timestamp": "2026-04-04T10:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    # No tokens in this audit, so cache_hit_rate must be exactly 0.0 with no crash.
    assert data["cache_hit_rate"] == 0.0


@pytest.mark.asyncio
async def test_costs_no_prompt_efficiency_field(client):
    """The /api/costs endpoint should NOT return a prompt_efficiency field.

    Prompt efficiency was removed from the backend because input/output ratio
    is not a meaningful metric and displayed as '0.0:1' when output tokens
    were zero. Verify the field is absent so the UI cannot accidentally use it.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert "prompt_efficiency" not in data


# --- Aggregation cache tests ---


def test_agg_cache_hit_returns_same_object(tmp_path):
    """Calling _get_costs_cached twice with an unchanged file returns the cached dict."""
    from routers.costs import _get_costs_cached, _agg_cache
    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    _agg_cache.clear()

    result1 = _get_costs_cached(None, audit_path)
    result2 = _get_costs_cached(None, audit_path)

    assert result1 is result2, "Expected same cached object on second call"
    _agg_cache.clear()


def test_agg_cache_miss_after_file_change(tmp_path):
    """A new result is computed when the file changes (different mtime or size)."""
    from routers.costs import _get_costs_cached, _agg_cache
    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    _agg_cache.clear()

    result1 = _get_costs_cached(None, audit_path)

    # Append a new event so the file size and mtime both change.
    import time as _time
    _time.sleep(0.01)  # ensure mtime_ns advances on fast filesystems
    with open(audit_path, "a") as f:
        f.write(json.dumps({"event": "agent.spawned", "name": "new-agent", "model": "sonnet", "budget": "1.00", "timestamp": "2026-04-05T10:00:00Z"}) + "\n")

    result2 = _get_costs_cached(None, audit_path)

    assert result1 is not result2, "Expected fresh aggregation after file changed"
    assert result2["agent_count"] == 4  # 3 original + 1 new
    _agg_cache.clear()


def test_agg_cache_evicts_stale_periods(tmp_path):
    """Stale entries for other periods are removed when the file changes."""
    from routers.costs import _get_costs_cached, _agg_cache
    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    _agg_cache.clear()

    _get_costs_cached("all", audit_path)
    _get_costs_cached("week", audit_path)
    assert len(_agg_cache) == 2

    # Change the file.
    import time as _time
    _time.sleep(0.01)
    with open(audit_path, "a") as f:
        f.write(json.dumps({"event": "agent.spawned", "name": "x", "model": "s", "budget": "0", "timestamp": "2026-04-05T10:00:00Z"}) + "\n")

    _get_costs_cached("all", audit_path)
    # Old (all, old_size, old_mtime) and (week, old_size, old_mtime) should be gone.
    stale = [k for k in _agg_cache if k[0] == "week"]
    assert not stale, "Stale period entries should be evicted on file change"
    _agg_cache.clear()


def test_agg_cache_keyed_per_period(tmp_path):
    """Different periods are cached independently."""
    from routers.costs import _get_costs_cached, _agg_cache
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = SAMPLE_AUDIT + [
        {"event": "agent.spawned", "name": "today-agent", "model": "sonnet", "budget": "1.00", "timestamp": now_str},
    ]
    audit_path = _write_audit(events, tmp_path)
    _agg_cache.clear()

    result_all = _get_costs_cached("all", audit_path)
    result_today = _get_costs_cached("today", audit_path)

    assert result_all is not result_today
    assert result_all["agent_count"] == 4  # 3 old + 1 today
    assert result_today["agent_count"] == 1  # only today
    _agg_cache.clear()


def test_parse_audit_events_uses_shared_cache(tmp_path, monkeypatch):
    """_parse_audit_events delegates to read_audit_entries (shared parse cache)."""
    from routers.costs import _parse_audit_events
    from services import ostk as ostk_svc

    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    call_count = 0
    original = ostk_svc.read_audit_entries

    def counting_read(path=None):
        nonlocal call_count
        call_count += 1
        return original(path)

    monkeypatch.setattr(ostk_svc, "read_audit_entries", counting_read)
    # Patch the import in costs module too
    import routers.costs as costs_mod
    monkeypatch.setattr(costs_mod, "read_audit_entries", counting_read)

    _parse_audit_events(audit_path)
    _parse_audit_events(audit_path)

    # read_audit_entries should be called twice (costs module calls it),
    # but the underlying file read is done only once due to ostk's internal cache.
    assert call_count == 2, "read_audit_entries should be called each time"


# --- Savings TTL cache tests ---


def test_savings_ttl_cache_hit(monkeypatch):
    """get_ostk_savings returns cached result within TTL without calling subprocess."""
    from services import token_metrics
    token_metrics.invalidate_savings_cache()

    fake_savings = {"savings_usd": 1.23, "period": "session"}
    call_count = 0

    def fake_fetch():
        nonlocal call_count
        call_count += 1
        return fake_savings

    monkeypatch.setattr(token_metrics, "_fetch_ostk_savings_raw", fake_fetch)

    result1 = token_metrics.get_ostk_savings()
    result2 = token_metrics.get_ostk_savings()

    assert call_count == 1, "Subprocess should only be called once within TTL"
    assert result1 == fake_savings
    assert result2 == fake_savings
    token_metrics.invalidate_savings_cache()


def test_savings_ttl_cache_miss_after_expiry(monkeypatch):
    """get_ostk_savings re-fetches after TTL expires."""
    from services import token_metrics
    token_metrics.invalidate_savings_cache()

    call_count = 0

    def fake_fetch():
        nonlocal call_count
        call_count += 1
        return {"savings_usd": float(call_count), "period": "session"}

    # Patch monotonic so first call is at t=0, second at t=TTL+1
    times = [0.0, float(token_metrics._SAVINGS_TTL_SECONDS) + 1.0]
    time_iter = iter(times)

    def fake_monotonic():
        try:
            return next(time_iter)
        except StopIteration:
            return float(token_metrics._SAVINGS_TTL_SECONDS) + 2.0

    monkeypatch.setattr("services.token_metrics._fetch_ostk_savings_raw", fake_fetch)
    monkeypatch.setattr("time.monotonic", fake_monotonic)

    result1 = token_metrics.get_ostk_savings()
    result2 = token_metrics.get_ostk_savings()

    assert call_count == 2, "Should re-fetch after TTL expires"
    assert result1["savings_usd"] == 1.0
    assert result2["savings_usd"] == 2.0
    token_metrics.invalidate_savings_cache()


def test_savings_cache_caches_none(monkeypatch):
    """A None result (ostk unavailable) is cached to avoid repeated subprocess calls."""
    from services import token_metrics
    token_metrics.invalidate_savings_cache()

    call_count = 0

    def fake_fetch():
        nonlocal call_count
        call_count += 1
        return None

    monkeypatch.setattr(token_metrics, "_fetch_ostk_savings_raw", fake_fetch)

    r1 = token_metrics.get_ostk_savings()
    r2 = token_metrics.get_ostk_savings()

    assert call_count == 1, "None result should also be cached"
    assert r1 is None
    assert r2 is None
    token_metrics.invalidate_savings_cache()


def test_invalidate_savings_cache_forces_fresh_fetch(monkeypatch):
    """invalidate_savings_cache causes the next call to bypass the cache."""
    from services import token_metrics
    token_metrics.invalidate_savings_cache()

    call_count = 0

    def fake_fetch():
        nonlocal call_count
        call_count += 1
        return {"savings_usd": float(call_count), "period": "session"}

    monkeypatch.setattr(token_metrics, "_fetch_ostk_savings_raw", fake_fetch)

    token_metrics.get_ostk_savings()
    token_metrics.invalidate_savings_cache()
    token_metrics.get_ostk_savings()

    assert call_count == 2, "Should fetch again after explicit invalidation"
    token_metrics.invalidate_savings_cache()


# --- Cache token tracking tests (needle 352) ---


def test_log_chat_completion_includes_cache_tokens():
    """_log_chat_completion should write cache token fields to the audit entry."""
    from services.chat_providers import _log_chat_completion
    from unittest.mock import patch as _patch

    with _patch("services.chat_providers.write_audit_entry") as mock_write:
        _log_chat_completion(
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=400,
            provider="anthropic",
            cache_creation_input_tokens=200,
            cache_read_input_tokens=600,
        )

    mock_write.assert_called_once()
    entry = mock_write.call_args[0][0]
    assert entry["cache_creation_input_tokens"] == 200
    assert entry["cache_read_input_tokens"] == 600
    assert entry["input_tokens"] == 1000
    assert entry["output_tokens"] == 400


@pytest.mark.asyncio
async def test_costs_aggregates_cache_tokens(client):
    """GET /costs should return total_cache_read_tokens, total_cache_creation_tokens, and cache_hit_rate."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 1000, "output_tokens": 200,
         "cache_creation_input_tokens": 300, "cache_read_input_tokens": 500,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 800, "output_tokens": 100,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 600,
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["total_cache_read_tokens"] == 1100  # 500 + 600
    assert data["total_cache_creation_tokens"] == 400  # 300 + 100
    # cache_hit_rate = 1100 / 1800 * 100 = 61.1%
    assert data["cache_hit_rate"] == 61.1
    # Combined input total: raw input (1800) + cache creation (400) + cache reads (1100)
    assert data["total_input_tokens_with_cache"] == 3300


@pytest.mark.asyncio
async def test_context_reuse_rate_formula_is_cache_read_over_cache_plus_input(client):
    """context_reuse_pct = cache_read / (input + cache_create + cache_read).

    This is the fleet-wide "how much of the context was reused from cache"
    number shown on the Usage page. It must use the full combined-input
    denominator (raw input + cache creation + cache reads) so it stays
    consistent with the "Input tokens used" tile.
    """
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 1000, "output_tokens": 200,
         "cache_creation_input_tokens": 300, "cache_read_input_tokens": 500,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 800, "output_tokens": 100,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 600,
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    # Denominator: 1800 raw input + 400 cache creation + 1100 cache reads = 3300
    # Numerator:   1100 cache reads
    # Expected: 1100 / 3300 * 100 = 33.3%
    assert data["context_reuse_pct"] == 33.3
    # Sanity: must equal total_cache_read / total_input_tokens_with_cache
    expected = round(
        data["total_cache_read_tokens"]
        / data["total_input_tokens_with_cache"]
        * 100,
        1,
    )
    assert data["context_reuse_pct"] == expected


@pytest.mark.asyncio
async def test_context_reuse_pct_zero_when_no_tokens(client):
    """context_reuse_pct should be 0.0 when no tokens were recorded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit([], Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")
    assert resp.json()["context_reuse_pct"] == 0.0


@pytest.mark.asyncio
async def test_monthly_agents_spawned_matches_audit_log(client):
    """agent_count for period=month must equal the count of agent.spawned
    events in the windowed dataset. Regression guard for the Usage page
    'Agents spawned' tile: if it drifts from the audit log, flag it.
    """
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    recent_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old_iso = (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")

    events = [
        # Three spawns inside the 30-day window
        {"event": "agent.spawned", "name": "a1", "model": "sonnet",
         "budget": "1.00", "timestamp": recent_iso},
        {"event": "agent.spawned", "name": "a2", "model": "sonnet",
         "budget": "1.00", "timestamp": recent_iso},
        {"event": "agent.spawned", "name": "a3", "model": "sonnet",
         "budget": "1.00", "timestamp": recent_iso},
        # One spawn 45 days ago that must NOT be counted
        {"event": "agent.spawned", "name": "old", "model": "sonnet",
         "budget": "1.00", "timestamp": old_iso},
        # Chat event must not inflate agent_count
        {"event": "chat.completion", "name": "chat", "model": "sonnet",
         "budget": 0, "input_tokens": 10, "output_tokens": 5,
         "timestamp": recent_iso},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?period=month")

    data = resp.json()
    assert data["agent_count"] == 3, (
        f"Expected 3 agents spawned in the last 30 days, got "
        f"{data['agent_count']}"
    )


@pytest.mark.asyncio
async def test_monthly_input_tokens_matches_sum_of_daily(client):
    """The monthly total_input_tokens must equal the sum of per-day
    input_tokens in by_date. If the two disagree, one of them is wrong
    and the Usage page will mislead the user.
    """
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet",
         "budget": 0, "input_tokens": 1000, "output_tokens": 200,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 300,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet",
         "budget": 0, "input_tokens": 500, "output_tokens": 80,
         "cache_creation_input_tokens": 50, "cache_read_input_tokens": 200,
         "timestamp": "2026-04-05T09:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet",
         "budget": 0, "input_tokens": 300, "output_tokens": 40,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100,
         "timestamp": "2026-04-05T15:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    daily_input_sum = sum(d["input_tokens"] for d in data["by_date"])
    daily_output_sum = sum(d["output_tokens"] for d in data["by_date"])
    daily_cache_read_sum = sum(
        d["cache_read_input_tokens"] for d in data["by_date"]
    )
    daily_cache_create_sum = sum(
        d["cache_creation_input_tokens"] for d in data["by_date"]
    )

    assert daily_input_sum == data["total_input_tokens"]
    assert daily_output_sum == data["total_output_tokens"]
    assert daily_cache_read_sum == data["total_cache_read_tokens"]
    assert daily_cache_create_sum == data["total_cache_creation_tokens"]


@pytest.mark.asyncio
async def test_costs_cache_hit_rate_zero_when_no_input(client):
    """cache_hit_rate should be 0.0 when total_input_tokens is 0."""
    events = []
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["cache_hit_rate"] == 0.0
    assert data["total_cache_read_tokens"] == 0
    assert data["total_cache_creation_tokens"] == 0


@pytest.mark.asyncio
async def test_costs_cache_tokens_in_model_breakdown(client):
    """by_model entries should include cache token fields."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 500, "output_tokens": 100,
         "cache_creation_input_tokens": 150, "cache_read_input_tokens": 300,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 400, "output_tokens": 80,
         "cache_creation_input_tokens": 50, "cache_read_input_tokens": 200,
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    models = {m["model"]: m for m in resp.json()["by_model"]}
    m = models["sonnet"]
    assert m["cache_creation_input_tokens"] == 200  # 150 + 50
    assert m["cache_read_input_tokens"] == 500  # 300 + 200


@pytest.mark.asyncio
async def test_costs_backwards_compatible_without_cache_fields(client):
    """Old audit entries without cache token fields should still aggregate correctly, defaulting to 0."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 500, "output_tokens": 200,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "agent.spawned", "name": "worker", "model": "sonnet", "budget": "1.00",
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert resp.status_code == 200
    assert data["total_cache_read_tokens"] == 0
    assert data["total_cache_creation_tokens"] == 0
    assert data["cache_hit_rate"] == 0.0
    assert data["total_input_tokens"] == 500
    assert data["total_output_tokens"] == 200


# --- Two-tier cache tests (period switch performance) ---


def test_period_switch_reuses_raw_events_cache(tmp_path, monkeypatch):
    """Switching period (today -> week -> month) should NOT re-read audit file.

    After the first /costs call warms the raw events cache, subsequent calls
    with different periods must skip _parse_audit_events and only re-filter.
    """
    from routers.costs import _get_costs_cached, _agg_cache, _raw_events_cache
    import routers.costs as costs_mod

    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    _agg_cache.clear()
    _raw_events_cache.clear()

    parse_call_count = 0
    original_parse = costs_mod._parse_audit_events

    def counting_parse(path=None):
        nonlocal parse_call_count
        parse_call_count += 1
        return original_parse(path)

    monkeypatch.setattr(costs_mod, "_parse_audit_events", counting_parse)

    # First call warms the raw events cache.
    _get_costs_cached("all", audit_path)
    assert parse_call_count == 1

    # Period switches must NOT trigger another file parse.
    _get_costs_cached("week", audit_path)
    _get_costs_cached("today", audit_path)
    _get_costs_cached("month", audit_path)

    assert parse_call_count == 1, (
        "Expected 1 parse total; period switches should reuse the raw events cache"
    )

    _agg_cache.clear()
    _raw_events_cache.clear()


def test_raw_events_cache_invalidated_on_file_change(tmp_path):
    """After the audit file changes, the raw events cache must be discarded."""
    from routers.costs import _get_costs_cached, _agg_cache, _raw_events_cache

    audit_path = _write_audit(SAMPLE_AUDIT, tmp_path)
    _agg_cache.clear()
    _raw_events_cache.clear()

    result_before = _get_costs_cached("all", audit_path)
    assert result_before["agent_count"] == 3

    # Append a new event.
    import time as _time
    _time.sleep(0.01)
    with open(audit_path, "a") as f:
        f.write(json.dumps({
            "event": "agent.spawned", "name": "new-agent",
            "model": "sonnet", "budget": "1.00",
            "timestamp": "2026-04-05T10:00:00Z",
        }) + "\n")

    result_after = _get_costs_cached("all", audit_path)
    assert result_after["agent_count"] == 4

    # Raw events cache should have exactly one entry (the new file stat).
    assert len(_raw_events_cache) == 1

    _agg_cache.clear()
    _raw_events_cache.clear()


# --- Budget cap vs actual spend tests ---


def test_total_budget_is_spawn_caps_not_spend():
    """total_budget must only sum budget fields from agent.spawned events.

    chat.completion events must NOT contribute to total_budget even if they
    happen to carry a non-zero budget field (they should not, but belt-and-
    suspenders).
    """
    from routers.costs import _aggregate

    events = [
        # Two agents with budget caps
        {"event": "agent.spawned", "name": "a1", "model": "claude-sonnet-4-6", "budget": "2.0", "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "agent.spawned", "name": "a2", "model": "claude-sonnet-4-6", "budget": "2.0", "timestamp": "2026-04-04T10:01:00Z"},
        # A chat completion with a non-zero budget field (must NOT be counted)
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-6", "budget": 5.0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-04-04T11:00:00Z"},
    ]
    result = _aggregate(events)
    # total_budget must only reflect the two agent.spawned caps
    assert result["total_budget"] == 4.0, (
        f"Expected total_budget=4.0 (2 spawn caps), got {result['total_budget']}. "
        "chat.completion events must not contribute to total_budget."
    )


def test_total_cost_usd_uses_token_rates():
    """total_cost_usd should be computed from token counts at API rates.

    Uses claude-sonnet-4-6 pricing: $3/M input, $15/M output.
    """
    from routers.costs import _aggregate

    events = [
        # 1M output tokens at $15/M = $15.00
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-6", "budget": 0, "input_tokens": 0, "output_tokens": 1_000_000, "timestamp": "2026-04-04T10:00:00Z"},
    ]
    result = _aggregate(events)
    assert result["total_cost_usd"] == 15.0, (
        f"Expected total_cost_usd=15.0 (1M output tokens at $15/M Sonnet rate), "
        f"got {result['total_cost_usd']}"
    )


def test_subscription_agents_do_not_add_token_cost():
    """Agents with model 'claude-code-subscription' must not contribute to total_cost_usd.

    These sessions are covered by a flat-rate plan; counting them as API spend
    would massively overstate actual cost.
    """
    from routers.costs import _aggregate

    events = [
        {"event": "agent.spawned", "name": "sub-agent", "model": "claude-code-subscription", "budget": "2.0",
         "input_tokens": 500_000, "output_tokens": 200_000, "timestamp": "2026-04-04T10:00:00Z"},
    ]
    result = _aggregate(events)
    assert result["total_cost_usd"] == 0.0, (
        f"claude-code-subscription agents must not add to total_cost_usd, "
        f"got {result['total_cost_usd']}"
    )


def test_budget_cap_not_shown_for_chat_completions_in_by_type():
    """by_type['chat.completion'] should show 0 budget cap (it has none)."""
    from routers.costs import _aggregate

    events = [
        {"event": "agent.spawned", "name": "a1", "model": "sonnet", "budget": "2.0", "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0, "input_tokens": 100, "output_tokens": 50, "timestamp": "2026-04-04T11:00:00Z"},
    ]
    result = _aggregate(events)
    by_type = {t["event"]: t for t in result["by_type"]}
    assert by_type["agent.spawned"]["total_budget"] == 2.0
    assert by_type["chat.completion"]["total_budget"] == 0.0, (
        f"chat.completion must show 0 budget cap, got {by_type['chat.completion']['total_budget']}"
    )


@pytest.mark.asyncio
async def test_api_response_includes_total_cost_usd(client):
    """GET /api/costs response must include total_cost_usd field."""
    import tempfile as _tmpfile
    from unittest.mock import patch as _patch
    with _tmpfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with _patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    assert resp.status_code == 200
    data = resp.json()
    assert "total_cost_usd" in data, "API response must include total_cost_usd"
    # SAMPLE_AUDIT has no token-bearing events so cost should be 0
    assert data["total_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_costs_input_output_token_split(client):
    """GET /api/costs must return total_input_tokens and total_output_tokens as separate fields.

    These fields drive the Usage page input/output token tiles. Verifies the
    values are correctly aggregated and not merged into a single total.
    """
    events = [
        {"event": "chat.completion", "name": "chat", "model": "claude-sonnet-4-6",
         "budget": 0, "input_tokens": 300_000, "output_tokens": 120_000,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "agent.spawned", "name": "worker", "model": "claude-sonnet-4-6",
         "budget": "2.00", "input_tokens": 200_000, "output_tokens": 80_000,
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert resp.status_code == 200
    assert data["total_input_tokens"] == 500_000
    assert data["total_output_tokens"] == 200_000
    # Combined total is NOT a top-level field; UI computes it from the two separate fields.
    assert "total_tokens" not in data


@pytest.mark.asyncio
async def test_costs_total_input_tokens_with_cache_combines_all_sources(client):
    """GET /api/costs must return total_input_tokens_with_cache that rolls raw
    input plus cache creation plus cache reads into one honest number.

    With prompt caching on, almost all input tokens are routed through
    cache_read_input_tokens rather than input_tokens. The Input tokens used
    tile on the Costs page displays this combined value so the number is not
    misleadingly small. Raw fields remain available for the detailed breakdown.
    """
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 200, "cache_read_input_tokens": 900,
         "timestamp": "2026-04-04T10:00:00Z"},
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 50, "output_tokens": 25,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 450,
         "timestamp": "2026-04-04T11:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert resp.status_code == 200
    # Raw fields must still be present and accurate
    assert data["total_input_tokens"] == 150  # 100 + 50
    assert data["total_cache_creation_tokens"] == 300  # 200 + 100
    assert data["total_cache_read_tokens"] == 1350  # 900 + 450
    # Combined field: 150 + 300 + 1350 = 1800
    assert data["total_input_tokens_with_cache"] == 1800


@pytest.mark.asyncio
async def test_costs_total_input_tokens_with_cache_zero_when_empty(client):
    """total_input_tokens_with_cache must be 0 when there is no activity."""
    events = []
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["total_input_tokens_with_cache"] == 0


@pytest.mark.asyncio
async def test_costs_total_input_tokens_with_cache_without_cache_fields(client):
    """When audit entries lack cache fields, total_input_tokens_with_cache
    equals total_input_tokens (cache fields default to 0)."""
    events = [
        {"event": "chat.completion", "name": "chat", "model": "sonnet", "budget": 0,
         "input_tokens": 750, "output_tokens": 300,
         "timestamp": "2026-04-04T10:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["total_input_tokens"] == 750
    assert data["total_input_tokens_with_cache"] == 750


# --- Performance regression tests: binary search filtering ---


def test_filter_by_period_binary_search_correctness():
    """Binary search in _filter_by_period returns identical results to naive scan."""
    from datetime import datetime, timezone, timedelta
    from routers.costs import _filter_by_period, _enrich_timestamps

    now = datetime.now(timezone.utc)
    events = []
    for i in range(1000):
        ts = now - timedelta(days=60) + timedelta(hours=i)
        events.append({
            "event": "agent.spawned",
            "name": f"agent-{i}",
            "model": "sonnet",
            "budget": "1.00",
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    _enrich_timestamps(events)
    events.sort(key=lambda e: e.get("_epoch") or 0.0)

    for period in ("today", "week", "month", "all"):
        filtered = _filter_by_period(events, period)
        if period == "all":
            assert len(filtered) == 1000
        elif period == "today":
            # _filter_by_period uses LOCAL midnight, not UTC midnight.
            local_now = datetime.now()
            local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff = local_midnight.astimezone(timezone.utc)
            expected = sum(1 for e in events if (e.get("_epoch") or 0) >= cutoff.timestamp())
            assert len(filtered) == expected
        elif period == "week":
            cutoff = now - timedelta(days=7)
            expected = sum(1 for e in events if (e.get("_epoch") or 0) >= cutoff.timestamp())
            assert len(filtered) == expected
        elif period == "month":
            cutoff = now - timedelta(days=30)
            expected = sum(1 for e in events if (e.get("_epoch") or 0) >= cutoff.timestamp())
            assert len(filtered) == expected


def test_filter_by_period_performance():
    """Filtering 100k events should complete in under 10ms (binary search)."""
    import time as _time
    from datetime import datetime, timezone, timedelta
    from routers.costs import _filter_by_period, _enrich_timestamps

    now = datetime.now(timezone.utc)
    events = []
    for i in range(100_000):
        ts = now - timedelta(days=90) + timedelta(seconds=i * 60)
        events.append({
            "event": "agent.spawned",
            "name": f"agent-{i}",
            "model": "sonnet",
            "budget": "1.00",
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    _enrich_timestamps(events)
    events.sort(key=lambda e: e.get("_epoch") or 0.0)

    start = _time.monotonic()
    for _ in range(100):
        _filter_by_period(events, "today")
        _filter_by_period(events, "week")
        _filter_by_period(events, "month")
    elapsed = _time.monotonic() - start
    assert elapsed < 1.0, f"300 filter calls on 100k events took {elapsed:.2f}s (should be <1s)"


def test_enrich_timestamps_idempotent():
    """Calling _enrich_timestamps twice should not change results."""
    from routers.costs import _enrich_timestamps

    events = [
        {"timestamp": "2026-04-04T10:00:00Z"},
        {"timestamp": "2026-04-04T11:00:00Z"},
        {"timestamp": ""},
        {"timestamp": "not-a-date"},
    ]
    _enrich_timestamps(events)
    epochs_first = [e.get("_epoch") for e in events]
    dates_first = [e.get("_date_key") for e in events]

    _enrich_timestamps(events)
    epochs_second = [e.get("_epoch") for e in events]
    dates_second = [e.get("_date_key") for e in events]

    assert epochs_first == epochs_second
    assert dates_first == dates_second
    assert epochs_first[2] is None
    assert dates_first[3] == "unknown"


# ---------------------------------------------------------------------------
# /costs/explain/{metric} tests
# ---------------------------------------------------------------------------

EXPLAIN_SAMPLE_AUDIT = [
    {
        "event": "agent.spawned",
        "name": "agent-a",
        "model": "claude-sonnet-4-5-20250929",
        "budget": "1.00",
        "timestamp": "2026-04-04T20:01:02Z",
    },
    {
        "event": "agent.spawned",
        "name": "agent-b",
        "model": "claude-opus-4-5-20250929",
        "budget": "2.00",
        "timestamp": "2026-04-05T10:00:00Z",
    },
    {
        "event": "chat.completion",
        "model": "claude-sonnet-4-5-20250929",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 8800,
        "timestamp": "2026-04-05T11:00:00Z",
    },
]


@pytest.mark.asyncio
async def test_explain_unknown_metric_returns_404(client):
    resp = await client.get("/api/costs/explain/bogus")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_explain_agents_spawned_shape(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs/explain/agents_spawned")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "agents_spawned"
    assert "formula" in body
    assert body["numerator"]["value"] == 2
    assert body["numerator"]["source"].startswith(".ostk/audit.jsonl")
    assert body["result"] == 2
    assert "window" in body


@pytest.mark.asyncio
async def test_explain_input_tokens_breakdown(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs/explain/input_tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "input_tokens"
    # raw + create + read = 1000 + 200 + 8800 = 10000
    assert body["numerator"]["value"] == 10000
    assert body["denominator"]["value"]["raw_input_tokens"] == 1000
    assert body["denominator"]["value"]["cache_creation_tokens"] == 200
    assert body["denominator"]["value"]["cache_read_tokens"] == 8800


@pytest.mark.asyncio
async def test_explain_output_tokens(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs/explain/output_tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "output_tokens"
    assert body["numerator"]["value"] == 500
    assert body["result"] == 500
    assert body["denominator"] is None


@pytest.mark.asyncio
async def test_explain_context_reuse_pct_formula(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs/explain/context_reuse_pct")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "context_reuse_pct"
    # 8800 / 10000 = 88.0
    assert body["numerator"]["value"] == 8800
    assert body["denominator"]["value"] == 10000
    assert body["result"] == 88.0
    assert "%" in body["result_label"]


@pytest.mark.asyncio
async def test_explain_all_supported_metrics_return_200(client):
    from routers.costs import SUPPORTED_EXPLAIN_METRICS

    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            with patch("routers.costs._read_savings_snapshot", return_value=None):
                with patch("routers.costs._read_metrics_events_cached", return_value=[]):
                    with patch("services.token_metrics.get_ostk_savings", return_value=None):
                        for metric in SUPPORTED_EXPLAIN_METRICS:
                            resp = await client.get(f"/api/costs/explain/{metric}")
                            assert resp.status_code == 200, f"{metric} failed"
                            body = resp.json()
                            assert body["metric"] == metric
                            assert "formula" in body
                            assert "numerator" in body
                            assert "result" in body
                            assert "result_label" in body
                            assert "window" in body


@pytest.mark.asyncio
async def test_explain_window_label_matches_period(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs/explain/agents_spawned?period=week")
    body = resp.json()
    assert "Week" in body["window"]


@pytest.mark.asyncio
async def test_explain_input_output_pct_sum_sane(client):
    """Input % + Output % of the grand total must be roughly 100%.

    Small rounding (e.g. 99.8 + 0.22) is fine. A large gap would mean the
    explain endpoint disagrees with the tiles, which is the whole thing we
    want to avoid.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            r_in = await client.get("/api/costs/explain/input_tokens")
            r_out = await client.get("/api/costs/explain/output_tokens")
    input_val = r_in.json()["numerator"]["value"]
    output_val = r_out.json()["numerator"]["value"]
    total = input_val + output_val
    assert total > 0
    input_pct = input_val / total * 100
    output_pct = output_val / total * 100
    assert 99.0 <= input_pct + output_pct <= 101.0


@pytest.mark.asyncio
async def test_explain_context_reuse_skips_savings_compute(client):
    """Regression: /costs/explain/context_reuse_pct used to call
    _compute_savings_for_period unconditionally, which re-reads every event
    in metrics.jsonl and added 10 to 20 seconds per request under load. The
    popover sat on "Loading the math." as a result. Metrics that do not
    read the savings payload must not trigger that scan.

    Metrics that need savings: context_compression, tokens_saved, time_saved.
    Metrics that do not: agents_spawned, input_tokens, output_tokens,
    context_reuse_pct.
    """
    skips = ["agents_spawned", "input_tokens", "output_tokens", "context_reuse_pct"]
    uses = ["context_compression", "tokens_saved", "time_saved"]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            for metric in skips:
                with patch(
                    "routers.costs._compute_savings_for_period"
                ) as spy:
                    resp = await client.get(f"/api/costs/explain/{metric}")
                assert resp.status_code == 200, metric
                assert spy.call_count == 0, (
                    f"{metric} should not compute savings; called "
                    f"{spy.call_count} times"
                )
            for metric in uses:
                # Clear the shared savings TTL cache + the disk snapshot
                # between iterations so each metric's patch actually gets
                # exercised. Otherwise the previous metric's mocked result
                # is served from cache or the snapshot, and the patched
                # _compute_savings_for_period is never called.
                from routers import costs as _costs_mod
                _costs_mod._savings_cache.clear()
                try:
                    snap = _costs_mod._savings_snapshot_path()
                    if snap.exists():
                        snap.unlink()
                except OSError:
                    pass
                with patch(
                    "routers.costs._compute_savings_for_period",
                    return_value={"available": False},
                ) as spy:
                    resp = await client.get(f"/api/costs/explain/{metric}")
                assert resp.status_code == 200, metric
                assert spy.call_count == 1, (
                    f"{metric} should compute savings exactly once; called "
                    f"{spy.call_count} times"
                )


@pytest.mark.asyncio
async def test_explain_time_saved_second_call_uses_cache(client):
    """Regression: /costs/explain/time_saved must serve from the shared
    savings cache on the second request so the popover never sits on
    "Loading the math." for the demo user.

    Previously _build_explain shadowed the module-level _savings_cache with
    a local dict, so every popover open re-ran _compute_savings_for_period
    (which reads a 500 MB metrics.jsonl + 30 MB audit.jsonl + shells out
    to the ostk binary) and hung for 3-6 seconds.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            with patch(
                "routers.costs._compute_savings_for_period",
                return_value={
                    "available": True,
                    "conversation_cache_tokens": 100_000,
                    "squash_tokens_saved": 20_000,
                },
            ) as spy:
                resp_a = await client.get("/api/costs/explain/time_saved")
                resp_b = await client.get("/api/costs/explain/time_saved")
                resp_c = await client.get("/api/costs/explain/time_saved")

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_c.status_code == 200
    # Exactly one compute across three requests: first populates the
    # TTL cache, next two hit it. If this count climbs, the popover is
    # back to doing a cold scan on every open.
    assert spy.call_count == 1, (
        f"time_saved should compute savings once per TTL window; called "
        f"{spy.call_count} times across 3 requests"
    )
    # Sanity: the result reflects the mocked numerator (100k + 20k = 120k
    # tokens at 10k tokens/sec = 12.0 sec).
    body = resp_a.json()
    assert body["numerator"]["value"] == 120_000
    assert body["result"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_explain_time_saved_uses_disk_snapshot(client, tmp_path):
    """Regression: a cold request after backend restart (in-memory cache
    empty) must serve from the on-disk snapshot at
    ~/.myos/savings_snapshot.json in milliseconds, not recompute.
    """
    import json as _json
    from routers import costs as costs_router

    snapshot_path = tmp_path / "savings_snapshot.json"
    snapshot_path.write_text(_json.dumps({
        "all": {
            "available": True,
            "conversation_cache_tokens": 50_000,
            "squash_tokens_saved": 10_000,
        },
    }))
    costs_router._savings_snapshot_path = lambda: snapshot_path

    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(EXPLAIN_SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            # Raise if the compute path is taken on the synchronous request.
            # A background refresh thread may still call it, but the user
            # request itself must be served from the snapshot.
            with patch(
                "routers.costs._compute_savings_for_period",
                return_value={"available": False},
            ):
                resp = await client.get("/api/costs/explain/time_saved")

    assert resp.status_code == 200
    body = resp.json()
    # Snapshot had 50k + 10k = 60k tokens at 10k/sec = 6.0 sec.
    assert body["numerator"]["value"] == 60_000
    assert body["result"] == pytest.approx(6.0)

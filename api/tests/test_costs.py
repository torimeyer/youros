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
    """The 'today' filter should only include events from today (UTC)."""
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

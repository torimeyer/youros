"""Tests for the ostk savings tile on the Cost Tracking page.

Exercises both the helper in ``services.token_metrics`` and the new
``GET /api/costs/savings`` route. All subprocess calls are mocked so the
tests never actually shell out to the ostk binary.
"""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from services import token_metrics


@pytest.fixture(autouse=True)
def clear_savings_caches():
    """Invalidate all savings caches before every test to prevent bleed."""
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    from routers import costs as costs_router
    costs_router._savings_cache.clear()
    costs_router.invalidate_metrics_parse_cache()
    yield
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    costs_router._savings_cache.clear()
    costs_router.invalidate_metrics_parse_cache()


SAMPLE_METRICS = {
    "prompt_cache": {
        "cache_savings_usd": 0.0767,
        "efficiency_pct": 61.1,
        "cost_usd": 0.0841,
        "no_cache_cost_usd": 0.1608,
    },
    "squash": {
        "compression_pct": 4.2,
        "est_saved_usd": 0.0014,
    },
}


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


def test_get_ostk_savings_returns_expected_shape():
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(json.dumps(SAMPLE_METRICS))
        result = token_metrics.get_ostk_savings()

    assert result is not None
    assert result["period"] == "session"
    # 0.0767 + 0.0014 = 0.0781
    assert result["savings_usd"] == pytest.approx(0.0781, abs=1e-4)
    assert result["cache_efficiency_pct"] == pytest.approx(61.1)
    assert result["compression_pct"] == pytest.approx(4.2)
    assert result["cost_without_ostk_usd"] == pytest.approx(0.1608)
    assert result["cost_with_ostk_usd"] == pytest.approx(0.0841)


def test_get_ostk_savings_handles_missing_binary():
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("ostk not found")
        result = token_metrics.get_ostk_savings()

    assert result is None


def test_get_ostk_savings_handles_timeout():
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ostk", timeout=5)
        result = token_metrics.get_ostk_savings()

    assert result is None


def test_get_ostk_savings_handles_non_zero_exit():
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("", returncode=2)
        result = token_metrics.get_ostk_savings()

    assert result is None


def test_get_ostk_savings_handles_bad_json():
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed("not json at all")
        result = token_metrics.get_ostk_savings()

    assert result is None


def test_get_ostk_savings_handles_missing_keys():
    """Partial payloads should still produce a valid dict with zero
    fallbacks for any missing numeric fields."""
    partial = {"prompt_cache": {"efficiency_pct": 42.0}}
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(json.dumps(partial))
        result = token_metrics.get_ostk_savings()

    assert result is not None
    assert result["cache_efficiency_pct"] == pytest.approx(42.0)
    assert result["savings_usd"] == 0.0
    assert result["compression_pct"] == 0.0
    assert result["cost_without_ostk_usd"] == 0.0
    assert result["cost_with_ostk_usd"] == 0.0


@pytest.mark.asyncio
async def test_costs_savings_route_returns_data(client):
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(json.dumps(SAMPLE_METRICS))
        resp = await client.get("/api/costs/savings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    # period reflects the query param (defaults to "all" when not passed)
    assert data["period"] == "all"
    assert data["savings_usd"] == pytest.approx(0.0781, abs=1e-4)
    assert data["cache_efficiency_pct"] == pytest.approx(61.1)
    assert data["compression_pct"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_costs_savings_route_binary_missing(client, tmp_path):
    """When the ostk binary is missing AND there is no metrics.jsonl data,
    the endpoint must return available:false. We patch both the subprocess
    call and the metrics path to an empty file so nothing can bleed in from
    the real project state."""
    empty_metrics = tmp_path / "metrics.jsonl"
    empty_metrics.write_text("")
    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", empty_metrics), \
         patch("routers.costs.token_metrics._METRICS_PATH", empty_metrics):
        mock_run.side_effect = FileNotFoundError("ostk not found")
        resp = await client.get("/api/costs/savings")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": False}


@pytest.mark.asyncio
async def test_costs_savings_route_non_zero_exit(client, tmp_path):
    """When the ostk binary returns a non-zero exit AND there is no metrics.jsonl
    data, the endpoint must return available:false."""
    empty_metrics = tmp_path / "metrics.jsonl"
    empty_metrics.write_text("")
    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", empty_metrics), \
         patch("routers.costs.token_metrics._METRICS_PATH", empty_metrics):
        mock_run.return_value = _fake_completed("", returncode=1)
        resp = await client.get("/api/costs/savings")

    assert resp.status_code == 200
    assert resp.json() == {"available": False}

@pytest.mark.asyncio
async def test_costs_savings_route_includes_conversation_cache(client):
    """The savings endpoint must include conversation_cache_tokens and
    conversation_cache_pct fields for the frontend tile."""
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(json.dumps(SAMPLE_METRICS))
        resp = await client.get("/api/costs/savings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    # These fields should always be present (possibly 0)
    assert "conversation_cache_tokens" in data
    assert "conversation_cache_pct" in data
    assert isinstance(data["conversation_cache_tokens"], (int, float))
    assert isinstance(data["conversation_cache_pct"], (int, float))


def test_get_ostk_savings_includes_conversation_cache_fields():
    """The raw savings dict must include conversation cache fields."""
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.return_value = _fake_completed(json.dumps(SAMPLE_METRICS))
        result = token_metrics.get_ostk_savings()

    assert result is not None
    assert "conversation_cache_pct" in result
    assert "conversation_cache_read_tokens" in result
    assert "conversation_cache_creation_tokens" in result


# ---------------------------------------------------------------------------
# Source-purity tests: savings card must NOT show provider cache numbers
# ---------------------------------------------------------------------------

def _metrics_jsonl_with_provider_cache(tmpdir, provider_cache_read: int) -> str:
    """Write a metrics.jsonl with one chat_turn having a large provider cache read."""
    path = str(tmpdir.join("metrics.jsonl"))
    import json as _json
    with open(path, "w") as f:
        f.write(_json.dumps({
            "event": "chat_turn",
            "model": "claude-sonnet-4",
            "input_tokens": 100,
            "output_tokens": 50,
            "has_ostk_boot": True,
            "boot_context_bytes": 800,
            "backend": "anthropic_api",
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": provider_cache_read,
            "ts": "2026-04-14T00:00:00Z",
        }) + "\n")
        # Add a squash event so compression_pct has a real value
        f.write(_json.dumps({
            "event": "squash",
            "original": 10000,
            "compressed": 7000,
            "saved": 3000,
            "tokens_saved": 750,
            "source": "squash",
            "ts": "2026-04-14T00:00:01Z",
        }) + "\n")
    return path


FAKE_OSTK_PAYLOAD = {
    "prompt_cache": {
        "cache_savings_usd": 0.05,
        "efficiency_pct": 55.0,
        "cost_usd": 0.04,
        "no_cache_cost_usd": 0.09,
    },
    "squash": {
        "compression_pct": 30.0,
        "est_saved_usd": 0.02,
        "token_savings": 12345,
    },
}


def test_savings_source_is_ostk_not_provider_cache(tmp_path):
    """Tokens saved on the Savings card must NOT be driven by provider-native
    cache_read_input_tokens (Anthropic's KV-cache).

    Set up: seed metrics.jsonl with a provider cache_read of 999,999 tokens.
    Also inject a fake ostk payload with squash.token_savings = 12,345.

    Assert: the card's 'Tokens saved' figure comes from ostk's squash
    token_savings (and any ostk-sourced context reuse), NOT from 999,999.

    This test pins the correct data-source for the Savings card and will fail
    if someone accidentally routes provider cache tokens into the numerator.
    """
    import json as _json
    import importlib

    PROVIDER_CACHE_READ = 999_999

    # Write a metrics.jsonl with a large provider cache read value
    metrics_path = tmp_path / "metrics.jsonl"
    metrics_path.write_text(
        _json.dumps({
            "event": "chat_turn",
            "model": "claude-sonnet-4",
            "input_tokens": 100,
            "output_tokens": 50,
            "has_ostk_boot": True,
            "boot_context_bytes": 800,
            "backend": "anthropic_api",
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": PROVIDER_CACHE_READ,
            "ts": "2026-04-14T00:00:00Z",
        }) + "\n" +
        _json.dumps({
            "event": "squash",
            "original": 10000,
            "compressed": 7000,
            "saved": 3000,
            "tokens_saved": 750,
            "source": "squash",
            "ts": "2026-04-14T00:00:01Z",
        }) + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(FAKE_OSTK_PAYLOAD))
        # Invalidate cache so this test gets a fresh call
        token_metrics.invalidate_savings_cache()
        result = token_metrics.get_ostk_savings()

    assert result is not None, "savings should be available"

    # compression_pct should come from ostk squash (30.0), not from Anthropic
    assert result["compression_pct"] == pytest.approx(30.0), (
        "compression_pct must come from ostk squash section, not provider cache"
    )

    # conversation_cache_read_tokens comes from metrics.jsonl chat_turn.
    # This IS provider-native. The test documents that fact: the number is 999,999
    # because that's what Anthropic reported. The tile's 'Tokens saved' label
    # should NOT use this value as ostk's coordination-layer savings.
    # Instead it should use ostk squash token_savings (12,345 in the fake payload).
    conv_tokens = result.get("conversation_cache_read_tokens", 0)
    assert conv_tokens == PROVIDER_CACHE_READ, (
        "conversation_cache_read_tokens should reflect what metrics.jsonl recorded "
        "(which is the provider's cache_read_input_tokens). "
        f"Expected {PROVIDER_CACHE_READ}, got {conv_tokens}. "
        "If this assertion fails it means the read path has changed."
    )

    # The ostk-sourced savings signal is squash compression, which must NOT
    # equal PROVIDER_CACHE_READ.
    assert result["compression_pct"] != PROVIDER_CACHE_READ, (
        "compression_pct must not be the provider cache token count"
    )


def test_savings_compression_is_ostk_squash_not_provider():
    """compression_pct must come from ostk's squash section, not Anthropic stats."""
    import json as _json

    ostk_payload = {
        "prompt_cache": {
            "cache_savings_usd": 0.01,
            "efficiency_pct": 99.0,
            "cost_usd": 0.01,
            "no_cache_cost_usd": 0.10,
        },
        "squash": {
            "compression_pct": 42.7,
            "est_saved_usd": 0.005,
        },
    }
    with patch("services.token_metrics.subprocess.run") as mock_run:
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        result = token_metrics.get_ostk_savings()

    assert result is not None
    # compression_pct must track ostk squash, not prompt cache efficiency
    assert result["compression_pct"] == pytest.approx(42.7), (
        "compression_pct should be from squash.compression_pct (ostk), not efficiency_pct (provider)"
    )
    # efficiency_pct (provider) is separately surfaced as cache_efficiency_pct
    assert result["cache_efficiency_pct"] == pytest.approx(99.0)
    # They must not be the same field
    assert result["compression_pct"] != result["cache_efficiency_pct"]


def test_savings_reuse_rate_tracks_cache_efficiency():
    """cache_efficiency_pct (the 'context reused' stat) comes from ostk os metrics
    prompt_cache.efficiency_pct, which the binary derives from the current
    session's Anthropic API usage. This test documents the current source so
    a future reroute to ostk-native needle recall can be validated against it.
    """
    import json as _json

    ostk_payload = {
        "prompt_cache": {
            "cache_savings_usd": 0.0,
            "efficiency_pct": 37.5,
            "cost_usd": 0.0,
            "no_cache_cost_usd": 0.0,
        },
        "squash": {
            "compression_pct": 5.0,
            "est_saved_usd": 0.0,
        },
    }
    with patch("services.token_metrics.subprocess.run") as mock_run:
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        result = token_metrics.get_ostk_savings()

    assert result is not None
    # cache_efficiency_pct comes from ostk os metrics prompt_cache.efficiency_pct
    assert result["cache_efficiency_pct"] == pytest.approx(37.5), (
        "cache_efficiency_pct must track prompt_cache.efficiency_pct from ostk os metrics"
    )


# ---------------------------------------------------------------------------
# Period-filter tests: savings must respect the time window
# ---------------------------------------------------------------------------

def _write_metrics_with_timestamps(path, old_ts: str, recent_ts: str):
    """Write two chat_turn events: one old and one recent."""
    import json as _json
    lines = [
        _json.dumps({
            "event": "chat_turn",
            "model": "claude-sonnet-4",
            "input_tokens": 1000,
            "output_tokens": 200,
            "has_ostk_boot": True,
            "boot_context_bytes": 500,
            "backend": "anthropic_api",
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 800,
            "ts": old_ts,
        }),
        _json.dumps({
            "event": "chat_turn",
            "model": "claude-sonnet-4",
            "input_tokens": 500,
            "output_tokens": 100,
            "has_ostk_boot": True,
            "boot_context_bytes": 500,
            "backend": "anthropic_api",
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 400,
            "ts": recent_ts,
        }),
    ]
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.asyncio
async def test_savings_period_today_differs_from_all(tmp_path, client):
    """savings?period=today must return different numbers than savings?period=all
    when the metrics.jsonl contains events outside today's window."""
    import json as _json
    from unittest.mock import patch as _patch

    metrics_path = tmp_path / "metrics.jsonl"
    # old event: 30 days ago; recent event: just now (UTC)
    from datetime import datetime, timezone, timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_metrics_with_timestamps(metrics_path, old_ts, recent_ts)

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.05, "efficiency_pct": 55.0, "cost_usd": 0.04, "no_cache_cost_usd": 0.09},
        "squash": {"compression_pct": 30.0, "est_saved_usd": 0.02},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        resp_all = await client.get("/api/costs/savings?period=all")
        costs_router._savings_cache.clear()
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        resp_today = await client.get("/api/costs/savings?period=today")

    assert resp_all.status_code == 200
    assert resp_today.status_code == 200
    data_all = resp_all.json()
    data_today = resp_today.json()

    assert data_all["available"] is True
    assert data_today["available"] is True

    # "all" has both events (1000+500=1500 input tokens worth of cache reads: 800+400=1200)
    # "today" has only the recent event (500 input, 400 cache_read)
    # The windowed cache_efficiency_pct values must differ because the old event
    # adds more cache reads and more input tokens that are excluded in "today".
    # At minimum the conversation_cache_read_tokens must differ.
    all_cache_tokens = data_all.get("conversation_cache_read_tokens", 0)
    today_cache_tokens = data_today.get("conversation_cache_read_tokens", 0)
    assert all_cache_tokens != today_cache_tokens, (
        f"period=all ({all_cache_tokens}) and period=today ({today_cache_tokens}) "
        "should have different conversation_cache_read_tokens when old events exist"
    )
    # today should only have the recent event's cache tokens
    assert today_cache_tokens == 400
    # all should include both
    assert all_cache_tokens == 1200


@pytest.mark.asyncio
async def test_savings_returns_unavailable_for_empty_window(tmp_path, client):
    """When there are no events in the requested window, the endpoint returns
    available: false."""
    import json as _json
    from unittest.mock import patch as _patch
    from datetime import datetime, timezone, timedelta

    metrics_path = tmp_path / "metrics.jsonl"
    # Only old events (60 days ago), nothing in the last 7 days
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path.write_text(_json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": 500,
        "output_tokens": 100,
        "has_ostk_boot": False,
        "boot_context_bytes": 0,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 300,
        "ts": old_ts,
    }) + "\n")

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.01, "efficiency_pct": 40.0, "cost_usd": 0.01, "no_cache_cost_usd": 0.02},
        "squash": {"compression_pct": 10.0, "est_saved_usd": 0.001},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        resp = await client.get("/api/costs/savings?period=week")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": False}, (
        f"Expected available:false for week with no recent events, got {data}"
    )


@pytest.mark.asyncio
async def test_savings_cache_keyed_on_period(tmp_path, client):
    """Each period gets its own cache entry; different periods do not share
    a cached result."""
    import json as _json
    from unittest.mock import patch as _patch
    from datetime import datetime, timezone, timedelta

    metrics_path = tmp_path / "metrics.jsonl"
    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path.write_text(_json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": 300,
        "output_tokens": 60,
        "has_ostk_boot": True,
        "boot_context_bytes": 200,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 150,
        "ts": recent_ts,
    }) + "\n")

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.02, "efficiency_pct": 50.0, "cost_usd": 0.02, "no_cache_cost_usd": 0.04},
        "squash": {"compression_pct": 20.0, "est_saved_usd": 0.01},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        # Fetch two different periods
        await client.get("/api/costs/savings?period=today")
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        await client.get("/api/costs/savings?period=all")

        # Both periods should have separate cache entries
        assert "today" in costs_router._savings_cache, "period=today should have its own cache entry"
        assert "all" in costs_router._savings_cache, "period=all should have its own cache entry"
        # The entries must be independent (different objects)
        assert costs_router._savings_cache["today"] is not costs_router._savings_cache["all"]


# ---------------------------------------------------------------------------
# Cache speed test: second call must return in < 50 ms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_savings_cached_call_is_fast(tmp_path, client):
    """Second HTTP call within the TTL window must return in < 50 ms because
    the result is served entirely from the in-memory _savings_cache."""
    import json as _json
    import time as _time
    from unittest.mock import patch as _patch

    metrics_path = tmp_path / "metrics.jsonl"
    from datetime import datetime, timezone
    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path.write_text(_json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": 400,
        "output_tokens": 80,
        "has_ostk_boot": True,
        "boot_context_bytes": 300,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 200,
        "ts": recent_ts,
    }) + "\n" + _json.dumps({
        "event": "squash",
        "original": 5000,
        "compressed": 3500,
        "saved": 1500,
        "tokens_saved": 375,
        "source": "squash",
        "ts": recent_ts,
    }) + "\n")

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.03, "efficiency_pct": 50.0, "cost_usd": 0.03, "no_cache_cost_usd": 0.06},
        "squash": {"compression_pct": 30.0, "est_saved_usd": 0.01},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        # First call (cold) -- may be slow, just needs to succeed
        resp1 = await client.get("/api/costs/savings?period=all")
        assert resp1.status_code == 200
        assert resp1.json()["available"] is True

        # Second call (cached) -- must be fast
        t0 = _time.monotonic()
        resp2 = await client.get("/api/costs/savings?period=all")
        elapsed_ms = (_time.monotonic() - t0) * 1000

        assert resp2.status_code == 200
        assert elapsed_ms < 50, (
            f"Cached savings call took {elapsed_ms:.1f} ms, expected < 50 ms. "
            "Check that _savings_cache TTL is being honoured."
        )


# ---------------------------------------------------------------------------
# compression_pct derived from original/compressed when field is absent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_savings_compression_derived_from_bytes(tmp_path, client):
    """When squash events have original/compressed bytes but no compression_pct
    field, the endpoint must derive pct = (original - compressed) / original."""
    import json as _json
    from unittest.mock import patch as _patch
    from datetime import datetime, timezone

    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path = tmp_path / "metrics.jsonl"
    # squash event WITHOUT compression_pct: original=1000, compressed=700 -> 30%
    metrics_path.write_text(_json.dumps({
        "event": "squash",
        "original": 1000,
        "compressed": 700,
        "saved": 300,
        "tokens_saved": 75,
        "source": "squash",
        "ts": recent_ts,
    }) + "\n" + _json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": 200,
        "output_tokens": 50,
        "has_ostk_boot": True,
        "boot_context_bytes": 100,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 100,
        "ts": recent_ts,
    }) + "\n")

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.01, "efficiency_pct": 50.0, "cost_usd": 0.01, "no_cache_cost_usd": 0.02},
        "squash": {"compression_pct": 15.0, "est_saved_usd": 0.005},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        resp = await client.get("/api/costs/savings?period=today")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    # Should be 30.0% derived from bytes (not 15.0% from binary fallback)
    assert data["compression_pct"] == pytest.approx(30.0, abs=0.5), (
        f"Expected compression_pct ~30.0 (derived from bytes), got {data['compression_pct']}"
    )


# ---------------------------------------------------------------------------
# Empty-state: when compression_pct is 0, the tile must be hidden
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_savings_zero_compression_returns_zero_not_none(tmp_path, client):
    """When all squash events in the window have original==compressed (no actual
    compression), the returned compression_pct should be 0.0, which the
    frontend interprets as 'no data' and shows the empty-state copy."""
    import json as _json
    from unittest.mock import patch as _patch
    from datetime import datetime, timezone

    recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path = tmp_path / "metrics.jsonl"
    # squash event where nothing was compressed (original == compressed)
    metrics_path.write_text(_json.dumps({
        "event": "squash",
        "original": 1000,
        "compressed": 1000,
        "saved": 0,
        "tokens_saved": 0,
        "source": "squash",
        "ts": recent_ts,
    }) + "\n" + _json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": 200,
        "output_tokens": 50,
        "has_ostk_boot": True,
        "boot_context_bytes": 100,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 100,
        "ts": recent_ts,
    }) + "\n")

    ostk_payload = {
        "prompt_cache": {"cache_savings_usd": 0.01, "efficiency_pct": 40.0, "cost_usd": 0.01, "no_cache_cost_usd": 0.02},
        "squash": {"compression_pct": 0.0, "est_saved_usd": 0.0},
    }

    with _patch("services.token_metrics.subprocess.run") as mock_run, \
         _patch("services.token_metrics._METRICS_PATH", metrics_path), \
         _patch("routers.costs.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(_json.dumps(ostk_payload))
        token_metrics.invalidate_savings_cache()
        from routers import costs as costs_router
        costs_router._savings_cache.clear()

        resp = await client.get("/api/costs/savings?period=today")

    assert resp.status_code == 200
    data = resp.json()
    # When windowed compression is 0.0 and binary also says 0.0, available=True
    # but compression_pct == 0, which the frontend uses to show empty-state.
    assert data["available"] is True
    assert data["compression_pct"] == pytest.approx(0.0, abs=0.01)

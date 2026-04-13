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
    assert data["period"] == "session"
    assert data["savings_usd"] == pytest.approx(0.0781, abs=1e-4)
    assert data["cache_efficiency_pct"] == pytest.approx(61.1)
    assert data["compression_pct"] == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_costs_savings_route_binary_missing(client):
    with patch("services.token_metrics.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("ostk not found")
        resp = await client.get("/api/costs/savings")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": False}


@pytest.mark.asyncio
async def test_costs_savings_route_non_zero_exit(client):
    with patch("services.token_metrics.subprocess.run") as mock_run:
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


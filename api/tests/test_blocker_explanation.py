"""Tests for the blocker explanation service.

Cover the cache behavior (hit, miss, expiry), graceful no-key fallback,
and the disk format. The Anthropic call itself is always mocked so the
tests do not depend on a network or an API key.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services import blocker_explanation


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    """Redirect the cache file to a temp path so tests do not touch ~/.youros."""
    fake_cache = tmp_path / "blocker_explanations.json"
    monkeypatch.setattr(blocker_explanation, "CACHE_PATH", fake_cache)
    yield fake_cache


def test_cache_miss_returns_none(temp_cache: Path):
    assert blocker_explanation.get_cached("t1", "t2") is None


def test_set_then_get_round_trips(temp_cache: Path):
    blocker_explanation.set_cached("t1", "t2", "Phones first.")
    assert blocker_explanation.get_cached("t1", "t2") == "Phones first."
    assert temp_cache.exists()
    data = json.loads(temp_cache.read_text())
    assert "t1::t2" in data
    assert data["t1::t2"]["explanation"] == "Phones first."


def test_cache_expires_after_ttl(temp_cache: Path):
    blocker_explanation.set_cached("t1", "t2", "Phones first.")
    # Manually rewrite the entry to look 8 days old.
    data = json.loads(temp_cache.read_text())
    old = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["t1::t2"]["generated_at"] = old
    temp_cache.write_text(json.dumps(data))
    assert blocker_explanation.get_cached("t1", "t2") is None


def test_cache_handles_corrupt_file(temp_cache: Path):
    temp_cache.write_text("{ not valid json")
    # Should silently return None and not raise.
    assert blocker_explanation.get_cached("t1", "t2") is None


@pytest.mark.asyncio
async def test_explain_blocker_uses_cache(temp_cache: Path):
    blocker_explanation.set_cached("t1", "t2", "Cached note.")
    with patch.object(
        blocker_explanation,
        "generate_explanation",
        new=AsyncMock(return_value="Should not be called."),
    ) as mock_gen:
        result = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
    assert result == "Cached note."
    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_explain_blocker_calls_ai_on_miss(temp_cache: Path):
    with patch.object(
        blocker_explanation,
        "generate_explanation",
        new=AsyncMock(return_value="Fresh note."),
    ) as mock_gen:
        result = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
    assert result == "Fresh note."
    mock_gen.assert_called_once()
    # Result is now in cache.
    assert blocker_explanation.get_cached("t1", "t2") == "Fresh note."


@pytest.mark.asyncio
async def test_explain_blocker_returns_none_when_ai_unavailable(temp_cache: Path):
    with patch.object(
        blocker_explanation,
        "generate_explanation",
        new=AsyncMock(return_value=None),
    ):
        result = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
    assert result is None
    # Nothing was cached because we have nothing to cache.
    assert blocker_explanation.get_cached("t1", "t2") is None


@pytest.mark.asyncio
async def test_generate_explanation_skips_without_api_key(temp_cache: Path):
    """When no API key is available, generate_explanation returns None
    without raising and without trying to import anthropic."""
    with patch.object(
        blocker_explanation,
        "_resolve_api_key",
        new=AsyncMock(return_value=""),
    ):
        result = await blocker_explanation.generate_explanation(
            task_title="A",
            task_description="",
            blocker_title="B",
            blocker_description="",
        )
    assert result is None


@pytest.mark.asyncio
async def test_empty_explanation_not_cached(temp_cache: Path):
    """If Claude returns an empty string (or None), do NOT write that to
    the cache. Otherwise the empty poisoned entry would live for 7 days."""
    with patch.object(
        blocker_explanation,
        "generate_explanation",
        new=AsyncMock(return_value=""),
    ):
        result = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
    assert result in (None, "")
    # Cache file should not contain an entry for t1::t2.
    if temp_cache.exists():
        data = json.loads(temp_cache.read_text())
        assert "t1::t2" not in data


def test_poisoned_empty_cache_entry_is_ignored(temp_cache: Path):
    """If a prior run wrote an empty explanation, get_cached must return
    None so the next call tries again."""
    # Manually write a poisoned entry with a fresh timestamp.
    temp_cache.parent.mkdir(parents=True, exist_ok=True)
    temp_cache.write_text(json.dumps({
        "t1::t2": {
            "explanation": "",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }))
    assert blocker_explanation.get_cached("t1", "t2") is None


@pytest.mark.asyncio
async def test_second_call_skips_anthropic_when_cached(temp_cache: Path):
    """Once an explanation is cached, a second explain_blocker call must
    not re-invoke generate_explanation (that is the whole point of the cache)."""
    mock_gen = AsyncMock(return_value="Phones first.")
    with patch.object(blocker_explanation, "generate_explanation", new=mock_gen):
        first = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
        second = await blocker_explanation.explain_blocker(
            task_id="t1",
            task_title="A",
            task_description="",
            blocker_id="t2",
            blocker_title="B",
            blocker_description="",
        )
    assert first == "Phones first."
    assert second == "Phones first."
    assert mock_gen.call_count == 1

"""Tests for →1151, →1212: filter stale rows from GET /api/agents response.

GET /api/agents was returning 816KB / 1380 rows because every row that
ever existed was serialized.  The fix filters non-running rows whose
last_seen (last_heartbeat_at or spawned_at) is older than
_RESPONSE_STALE_SECONDS before the enrich/serialize pass.
Running rows are always kept regardless of last_seen.

→1212 widened _RESPONSE_STALE_SECONDS from 90s to 86400s (24h) so the
Recent tab shows the full day's history instead of only the last 1.5 min.
Rows older than 24h are still pruned to keep the payload bounded.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import _run_enrich_pipeline, _last_seen_dt, _RESPONSE_STALE_SECONDS

NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(seconds=30)).isoformat()       # well within 24h window
MINUTES_AGO = (NOW - timedelta(minutes=30)).isoformat()  # 30 min ago — still within 24h
STALE = (NOW - timedelta(hours=25)).isoformat()          # older than 24h — must be dropped


def _make_agent(name: str, status: str, last_heartbeat_at: str | None = None, spawned_at: str | None = None) -> dict:
    a: dict = {"name": name, "status": status}
    if last_heartbeat_at is not None:
        a["last_heartbeat_at"] = last_heartbeat_at
    if spawned_at is not None:
        a["spawned_at"] = spawned_at
    return a


def _run(agents: list) -> list:
    """Call _run_enrich_pipeline with the minimal scaffold needed to isolate the staleness filter."""
    with (
        patch("routers.agents._transcript_recently_active", return_value=False),
        patch("routers.agents._get_transcript_metrics", return_value={"transcript_bytes": 0, "transcript_lines": 0}),
        patch("routers.agents.agent_metadata", {}),
        patch("routers.agents._estimate_cost", return_value=0.0),
        patch("routers.agents.MAX_RECOVERY_ATTEMPTS", 3),
        patch("routers.agents._SANITIZE_FIELDS", []),
        patch("routers.agents._RESPONSE_STALE_SECONDS", 86400),
    ):
        return _run_enrich_pipeline(
            all_agents=agents,
            deleted_names=set(),
            now_for_sweep=NOW,
            user_spawned_filter=None,
            filter_status=None,
            filter_source=None,
            limit=None,
        )


class TestResponseStalenessFilter:
    """→1151/→1212: non-running rows older than 24h are dropped from the response."""

    def test_running_rows_always_kept(self):
        agents = [_make_agent(f"running-{i}", "running", last_heartbeat_at=STALE) for i in range(5)]
        result = _run(agents)
        assert len(result) == 5

    def test_recent_non_running_rows_kept(self):
        agents = [_make_agent(f"completed-{i}", "completed", last_heartbeat_at=RECENT) for i in range(5)]
        result = _run(agents)
        assert len(result) == 5

    def test_minutes_ago_non_running_rows_kept(self):
        """→1212: completed agents from 30 min ago must appear (old cutoff was 90s)."""
        agents = [_make_agent(f"old-{i}", "completed", last_heartbeat_at=MINUTES_AGO) for i in range(50)]
        result = _run(agents)
        assert len(result) == 50

    def test_stale_non_running_rows_dropped(self):
        """Rows older than 24h are still pruned to keep the payload bounded."""
        agents = [_make_agent(f"ancient-{i}", "completed", last_heartbeat_at=STALE) for i in range(50)]
        result = _run(agents)
        assert len(result) == 0

    def test_mixed_fixture_60_of_60_within_24h(self):
        """5 running (stale HB) + 5 recent + 50 from 30-min-ago → all 60 rows returned."""
        running = [_make_agent(f"run-{i}", "running", last_heartbeat_at=STALE) for i in range(5)]
        recent = [_make_agent(f"rec-{i}", "completed", last_heartbeat_at=RECENT) for i in range(5)]
        within_24h = [_make_agent(f"old-{i}", "completed", last_heartbeat_at=MINUTES_AGO) for i in range(50)]
        result = _run(running + recent + within_24h)
        assert len(result) == 60

    def test_mixed_fixture_drops_only_ancient_rows(self):
        """5 running + 5 recent + 50 ancient (>24h) → 10 rows, not 60."""
        running = [_make_agent(f"run-{i}", "running", last_heartbeat_at=STALE) for i in range(5)]
        recent = [_make_agent(f"rec-{i}", "completed", last_heartbeat_at=RECENT) for i in range(5)]
        ancient = [_make_agent(f"ancient-{i}", "completed", last_heartbeat_at=STALE) for i in range(50)]
        result = _run(running + recent + ancient)
        assert len(result) == 10

    def test_no_timestamp_row_kept(self):
        """Rows with no parseable timestamp are kept to avoid silent data loss."""
        agents = [_make_agent("mystery", "completed")]
        result = _run(agents)
        assert len(result) == 1

    def test_spawned_at_used_as_fallback(self):
        """When last_heartbeat_at is absent, spawned_at determines staleness."""
        recent = _make_agent("fresh", "failed", spawned_at=RECENT)
        ancient = _make_agent("ancient", "failed", spawned_at=STALE)
        result = _run([recent, ancient])
        assert len(result) == 1
        assert result[0]["name"] == "fresh"

    def test_constant_is_86400(self):
        """→1212: window widened to 24h so Recent tab shows today's full history."""
        assert _RESPONSE_STALE_SECONDS == 86400


class TestLastSeenDt:
    """Unit tests for _last_seen_dt helper."""

    def test_prefers_last_heartbeat_at(self):
        agent = {"last_heartbeat_at": RECENT, "spawned_at": STALE}
        dt = _last_seen_dt(agent)
        assert dt is not None
        assert dt.isoformat().startswith(RECENT[:19])

    def test_falls_back_to_spawned_at(self):
        agent = {"spawned_at": RECENT}
        dt = _last_seen_dt(agent)
        assert dt is not None

    def test_returns_none_when_no_timestamps(self):
        assert _last_seen_dt({}) is None

    def test_returns_none_for_malformed_timestamp(self):
        agent = {"last_heartbeat_at": "not-a-date"}
        assert _last_seen_dt(agent) is None

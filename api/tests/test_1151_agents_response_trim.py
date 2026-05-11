"""Tests for →1151: filter stale rows from GET /api/agents response.

GET /api/agents was returning 816KB / 1380 rows because every row that
ever existed was serialized.  The fix filters non-running rows whose
last_seen (last_heartbeat_at or spawned_at) is older than
_RESPONSE_STALE_SECONDS (90 s) before the enrich/serialize pass.
Running rows are always kept regardless of last_seen.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import _run_enrich_pipeline, _last_seen_dt, _RESPONSE_STALE_SECONDS

NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(seconds=30)).isoformat()      # within 90s window
STALE = (NOW - timedelta(seconds=120)).isoformat()      # older than 90s


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
    """→1151: non-running rows older than 90 s are dropped from the response."""

    def test_running_rows_always_kept(self):
        agents = [_make_agent(f"running-{i}", "running", last_heartbeat_at=STALE) for i in range(5)]
        result = _run(agents)
        assert len(result) == 5

    def test_recent_non_running_rows_kept(self):
        agents = [_make_agent(f"completed-{i}", "completed", last_heartbeat_at=RECENT) for i in range(5)]
        result = _run(agents)
        assert len(result) == 5

    def test_stale_non_running_rows_dropped(self):
        agents = [_make_agent(f"old-{i}", "completed", last_heartbeat_at=STALE) for i in range(50)]
        result = _run(agents)
        assert len(result) == 0

    def test_mixed_fixture_10_of_60(self):
        """Acceptance: 5 running + 5 recent + 50 stale → 10 rows, not 60."""
        running = [_make_agent(f"run-{i}", "running", last_heartbeat_at=STALE) for i in range(5)]
        recent = [_make_agent(f"rec-{i}", "completed", last_heartbeat_at=RECENT) for i in range(5)]
        stale = [_make_agent(f"old-{i}", "completed", last_heartbeat_at=STALE) for i in range(50)]
        result = _run(running + recent + stale)
        assert len(result) == 10

    def test_no_timestamp_row_kept(self):
        """Rows with no parseable timestamp are kept to avoid silent data loss."""
        agents = [_make_agent("mystery", "completed")]
        result = _run(agents)
        assert len(result) == 1

    def test_spawned_at_used_as_fallback(self):
        """When last_heartbeat_at is absent, spawned_at determines staleness."""
        recent = _make_agent("fresh", "failed", spawned_at=RECENT)
        stale = _make_agent("old", "failed", spawned_at=STALE)
        result = _run([recent, stale])
        assert len(result) == 1
        assert result[0]["name"] == "fresh"

    def test_constant_is_90(self):
        assert _RESPONSE_STALE_SECONDS == 90


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

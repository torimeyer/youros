"""→1687: bound the /api/agents per-request read storm.

The GET /api/agents handler annotated every agent row with
``per_agent_transcript_bytes`` by calling _get_per_agent_transcript_bytes
once per agent on EVERY request. That function resolves the transcript via
_resolve_transcript_source_uncached, which scans the (bloated) .ostk
transcript tree from disk. With many historical rows and frequent polling,
the handler re-scanned the whole tree N times per request and pegged the
CPU file-read-bound under agent activity.

The 500ms snapshot loop already guards this work (24h cutoff + the
TTL-cached _resolve_transcript_source). The request path had no such
bound. This adds a status-aware TTL cache, _get_per_agent_transcript_bytes_cached,
so repeated polls collapse to one disk scan per agent per TTL window.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_mod
from routers.agents import (
    _get_per_agent_transcript_bytes_cached,
    _reset_per_agent_bytes_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _reset_per_agent_bytes_cache()
    yield
    _reset_per_agent_bytes_cache()


def test_repeated_calls_within_ttl_scan_disk_once():
    """Two calls for the same agent within the TTL window do exactly one
    underlying (disk-scanning) resolve. This is what collapses the read storm.
    """
    calls = {"n": 0}

    def _fake_uncached(name, skip_transcript_path=False):
        calls["n"] += 1
        return None

    with patch.object(agents_mod, "_resolve_transcript_source_uncached", _fake_uncached):
        with patch.object(agents_mod, "agent_metadata", {"a1": {"status": "completed"}}):
            first = _get_per_agent_transcript_bytes_cached("a1")
            second = _get_per_agent_transcript_bytes_cached("a1")

    assert first == second == 0
    assert calls["n"] == 1, "second call within TTL must hit the cache, not disk"


def test_running_agent_uses_short_ttl():
    """Running agents get a short TTL so their growing size stays fresh,
    terminal agents get a long TTL since their transcript never changes.
    """
    with patch.object(agents_mod, "agent_metadata", {"run": {"status": "running"}}):
        ttl_running = agents_mod._per_agent_bytes_ttl_for("run")
    with patch.object(agents_mod, "agent_metadata", {"done": {"status": "completed"}}):
        ttl_terminal = agents_mod._per_agent_bytes_ttl_for("done")

    assert ttl_running < ttl_terminal
    assert ttl_running > 0

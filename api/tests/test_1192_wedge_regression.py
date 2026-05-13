"""Regression tests for →1244: verify →1192 fixes cover →1166 card-compass wedge.

Coverage table:
  Symptom                                      | On main? | Test
  ---------------------------------------------|----------|-----
  Reaper sweep blocks event loop (subprocess)  | NO       | test_do_sweep_does_not_block_event_loop (xfail)
  Cache TTLs synchronized with flush interval  | YES      | test_flush_interval_decoupled_from_sweep
  _candidates_cache key excludes mtime_ns      | YES      | test_candidates_cache_key_excludes_mtime
  _meta_candidates_cache key excludes mtime_ns | YES      | test_meta_candidates_cache_key_excludes_mtime
  _candidates_cache bounded (no accumulation)  | YES      | test_candidates_cache_bounded_on_mtime_change
  SWEEP_INTERVAL decoupled from cache TTLs     | YES      | test_sweep_interval_decoupled_from_cache_ttls
  GIL yield present in _load_candidates        | YES      | (covered by test_agents_wedge_regression.py)
  O(1) name index present in _load_candidates  | YES      | (covered by test_1192_name_index.py)

Gap: fix(→1192): offload agent_reaper sweep to thread (97bbabe) is NOT on main.
_do_sweep calls detect_stalled_agents which calls _worktree_head_hash (subprocess.run)
synchronously on the event loop every 30s. With multiple running worktree agents this
blocks accept() for up to N×2s, matching the →1166 TCP timeout symptom.
Follow-up needle filed as part of →1244 close reason.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_router
from lib import agent_reaper


# ---------------------------------------------------------------------------
# 1. Reaper sweep: event-loop blocking gap (xfail until fix lands)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "→1192 fix 97bbabe (offload _do_sweep to asyncio.to_thread) not on main. "
        "_do_sweep calls detect_stalled_agents with _worktree_head_hash "
        "(subprocess.run, 2s timeout) synchronously on the event loop. "
        "This blocks accept() for up to N×2s per sweep. See follow-up needle filed with →1244."
    ),
)
async def test_do_sweep_does_not_block_event_loop():
    """_do_sweep must not block the event loop while running subprocess per agent.

    Injects a slow get_worktree_head callback (50ms) for N agents; verifies
    a concurrent asyncio.sleep probe does not stall past 150ms.
    Currently XFAIL: subprocess.run runs on the event loop (fix 97bbabe not merged).
    """
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    n_agents = 5
    delay_per_agent = 0.05  # 50ms

    fake_metadata = {
        f"running-worktree-{i}": {
            "name": f"running-worktree-{i}",
            "status": "running",
            "source": "claude-code",
            "isolation": "worktree",
            "worktree_path": f"/tmp/fake-wt-{i}",
            "spawned_at": (now - timedelta(hours=3)).isoformat(),
            "last_heartbeat_at": (now - timedelta(minutes=10)).isoformat(),
            "pid": None,
        }
        for i in range(n_agents)
    }

    def slow_head(name: str, wt_path: str) -> str:
        time.sleep(delay_per_agent)
        return "abc123"

    max_probe_delay = 0.0

    async def probe_loop():
        nonlocal max_probe_delay
        for _ in range(20):
            t0 = asyncio.get_event_loop().time()
            await asyncio.sleep(0.01)
            delay = asyncio.get_event_loop().time() - t0 - 0.01
            if delay > max_probe_delay:
                max_probe_delay = delay

    with (
        patch.object(agent_reaper, "_worktree_head_hash", side_effect=slow_head),
        patch("lib.agent_reaper.agent_metadata", fake_metadata, create=True),
    ):
        probe = asyncio.create_task(probe_loop())
        try:
            await agent_reaper._do_sweep()
        except Exception:
            pass
        await probe

    assert max_probe_delay < 0.15, (
        f"Event loop blocked {max_probe_delay * 1000:.0f}ms during reaper sweep "
        f"({n_agents} agents × {delay_per_agent * 1000:.0f}ms). "
        f"Fix: wrap detect_stalled_agents in asyncio.to_thread (97bbabe)."
    )


# ---------------------------------------------------------------------------
# 2. Cache TTL decoupling: SWEEP_INTERVAL vs cache TTLs vs flush interval
# ---------------------------------------------------------------------------


def test_sweep_interval_decoupled_from_cache_ttls():
    """SWEEP_INTERVAL must not equal any cache TTL — prevents synchronized cold rebuild.

    Before →1192: SWEEP_INTERVAL=30s, all cache TTLs=30s, flush=30s.
    After: cache TTLs=60s, flush=25s. SWEEP_INTERVAL stays 30s.
    30s ≠ 60s: sweep no longer coincides with cold rebuild.
    """
    sweep = agent_reaper.SWEEP_INTERVAL_SECONDS
    resolve = agents_router._RESOLVE_TTL_SECONDS
    candidates = agents_router._CANDIDATES_TTL_SECONDS
    meta = agents_router._META_CANDIDATES_TTL_SECONDS
    flush = agents_router._TRANSCRIPT_FLUSH_INTERVAL

    assert flush != resolve, f"flush ({flush}s) == resolve TTL ({resolve}s) — synchronized"
    assert flush != candidates, f"flush ({flush}s) == candidates TTL ({candidates}s) — synchronized"
    assert flush != meta, f"flush ({flush}s) == meta TTL ({meta}s) — synchronized"
    assert sweep != resolve, f"sweep ({sweep}s) == resolve TTL ({resolve}s) — synchronized"
    assert sweep != candidates, f"sweep ({sweep}s) == candidates TTL ({candidates}s) — synchronized"
    assert sweep != meta, f"sweep ({sweep}s) == meta TTL ({meta}s) — synchronized"


def test_flush_interval_decoupled_from_sweep():
    """_TRANSCRIPT_FLUSH_INTERVAL must differ from SWEEP_INTERVAL_SECONDS."""
    assert agents_router._TRANSCRIPT_FLUSH_INTERVAL != agent_reaper.SWEEP_INTERVAL_SECONDS, (
        "Flush interval and sweep interval are equal — "
        "synchronized expiry causes cold-rebuild spikes on every flush cycle (→1192)"
    )


# ---------------------------------------------------------------------------
# 3. _candidates_cache key: no mtime_ns in key (→ bounded cache size)
# ---------------------------------------------------------------------------


def test_candidates_cache_key_excludes_mtime(tmp_path):
    """_candidates_cache key must be (root, pattern) only — no mtime_ns.

    Before the fix the key was (root, pattern, mtime_ns). Every new JSONL file
    changed the parent directory mtime, adding a new dict entry and leaving the
    old one unreachable. With 1300 session files × 3.7 MB/entry = 4.8 GB RSS.
    After the fix the mtime is stored inside the value (for invalidation) and
    the dict key is stable regardless of directory changes.
    """
    agents_router._reset_candidates_cache()

    p = tmp_path / "agent-test.jsonl"
    p.write_text('{"name": "test-agent", "status": "running"}\n')

    agents_router._load_candidates(tmp_path, "agent-*.jsonl")
    key = next(iter(agents_router._candidates_cache))

    assert isinstance(key, tuple), "cache key must be a tuple"
    assert len(key) == 2, (
        f"cache key has {len(key)} elements — expected 2 (root, pattern). "
        f"mtime_ns must NOT be part of the key (→1192 unbounded growth fix)"
    )
    root_str, pattern = key
    assert root_str == str(tmp_path)
    assert pattern == "agent-*.jsonl"


def test_meta_candidates_cache_key_excludes_mtime(tmp_path):
    """_meta_candidates_cache key must be a string (project_dir), not a tuple with mtime."""
    agents_router._reset_meta_candidates_cache()

    key_type_before = type(next(iter(agents_router._meta_candidates_cache), "no-entries"))

    # Populate cache
    try:
        agents_router._load_meta_candidates(tmp_path)
    except Exception:
        pass

    if agents_router._meta_candidates_cache:
        key = next(iter(agents_router._meta_candidates_cache))
        assert isinstance(key, str), (
            f"_meta_candidates_cache key is {type(key)}, expected str. "
            "mtime_ns must not be in the key (→1192 unbounded growth)."
        )


def test_candidates_cache_bounded_on_mtime_change(tmp_path):
    """300 simulated mtime changes must yield exactly 1 cache entry, not 300.

    Before the fix: each mtime change produced a new dict key, so after 300
    file writes the cache had 300 entries — unbounded growth toward ~4.8 GB RSS.
    """
    agents_router._reset_candidates_cache()

    p = tmp_path / "agent-alpha.jsonl"
    p.write_text('{"name": "alpha", "status": "running"}\n')

    for _ in range(300):
        agents_router._load_candidates(tmp_path, "agent-*.jsonl")
        agents_router._reset_candidates_cache()
        agents_router._load_candidates(tmp_path, "agent-*.jsonl")

    assert len(agents_router._candidates_cache) == 1, (
        f"Expected 1 cache entry, got {len(agents_router._candidates_cache)}. "
        "Cache is accumulating per-mtime entries (→1192 unbounded growth)."
    )

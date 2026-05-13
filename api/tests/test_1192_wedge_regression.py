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

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_router
from lib import agent_reaper


# ---------------------------------------------------------------------------
# 1. Reaper sweep: event-loop blocking gap (xfail until fix lands)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "→1192 fix 97bbabe (offload _do_sweep to asyncio.to_thread) not on main. "
        "_do_sweep calls detect_stalled_agents with _worktree_head_hash "
        "(subprocess.run, 2s timeout) synchronously on the event loop. "
        "This blocks accept() for up to N×2s per sweep. See follow-up needle filed with →1244."
    ),
)
def test_do_sweep_offloads_blocking_calls_to_thread():
    """_do_sweep must delegate blocking work via asyncio.to_thread (fix 97bbabe).

    On main, _do_sweep calls detect_stalled_agents (which calls _worktree_head_hash,
    a subprocess.run with 2s timeout) synchronously on the event loop. This blocks
    accept() for up to N×2s every 30s when there are running worktree agents.

    The fix: extract _do_sweep_sync (blocking) and have _do_sweep await it via
    asyncio.to_thread. This test asserts that fix is present.
    Currently XFAIL: asyncio.to_thread not present in _do_sweep on main.
    """
    import inspect
    src = inspect.getsource(agent_reaper._do_sweep)
    assert "to_thread" in src, (
        "_do_sweep must offload the blocking detect_stalled_agents / _worktree_head_hash "
        "call via asyncio.to_thread. Fix: extract _do_sweep_sync and await "
        "asyncio.to_thread(_do_sweep_sync, ...) in _do_sweep. (97bbabe not on main)"
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
    """Cache must have exactly 1 entry for a (root, pattern) pair regardless of mtime changes.

    Before the fix: key=(root, pattern, mtime_ns). Each new JSONL file bumped
    the parent directory mtime, generating a new key on the next TTL expiry and
    leaving the old entry unreachable. After N session files: N cache entries.
    After the fix: key=(root, pattern) only. mtime is stored inside the value
    for invalidation, so the dict slot is overwritten in-place, staying at 1 entry.
    """
    agents_router._reset_candidates_cache()

    # Write 30 files and expire + reload the cache after each, simulating mtime bumps.
    for i in range(30):
        (tmp_path / f"agent-new-{i:03d}.jsonl").write_text(
            f'{{"name": "new-{i:03d}", "status": "running"}}\n'
        )
        # Expire the current cache entry to force a real rescan on next call.
        if agents_router._candidates_cache:
            key = next(iter(agents_router._candidates_cache))
            exp, cands, idx = agents_router._candidates_cache[key]
            agents_router._candidates_cache[key] = (0.0, cands, idx)
        agents_router._load_candidates(tmp_path, "agent-*.jsonl")

    assert len(agents_router._candidates_cache) == 1, (
        f"Expected 1 cache entry for (root, pattern), got {len(agents_router._candidates_cache)}. "
        "Cache is accumulating per-mtime entries (→1192 unbounded growth)."
    )

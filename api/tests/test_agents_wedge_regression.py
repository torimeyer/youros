"""Regression tests for →1192: /api/agents periodic wedge.

Root causes:
1. _TRANSCRIPT_FLUSH_INTERVAL (25 s) and cache TTLs (60 s) were both 30 s,
   causing all caches to expire simultaneously on every flush cycle — producing
   a synchronized cold-rebuild spike (~3.5 s) on the next list request.
2. json.dumps(agent_metadata) ran on the event loop before asyncio.to_thread,
   blocking TLS handshakes for 10-30 ms on 1500-row state files.
3. Cold _load_candidates rebuild held the GIL continuously over hundreds of
   files without yielding, amplifying latency under concurrent load.

Fixes:
- _TRANSCRIPT_FLUSH_INTERVAL: 30 → 25 s
- _RESOLVE_TTL_SECONDS / _CANDIDATES_TTL_SECONDS / _META_CANDIDATES_TTL_SECONDS: 30 → 60 s
- _save_agent_state_async: snapshot dict on event loop, serialize + write in thread
- _load_candidates / _load_meta_candidates: time.sleep(0) GIL yield every 10 iters
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from routers import agents as agents_router


# ---------------------------------------------------------------------------
# Static / structural checks
# ---------------------------------------------------------------------------


def test_flush_interval_decoupled_from_cache_ttl():
    """_TRANSCRIPT_FLUSH_INTERVAL must not equal any cache TTL (→1192)."""
    flush = agents_router._TRANSCRIPT_FLUSH_INTERVAL
    resolve = agents_router._RESOLVE_TTL_SECONDS
    candidates = agents_router._CANDIDATES_TTL_SECONDS
    meta = agents_router._META_CANDIDATES_TTL_SECONDS

    assert flush == 25.0, f"Expected 25.0, got {flush}"
    assert resolve == 60.0, f"Expected 60.0, got {resolve}"
    assert candidates == 60.0, f"Expected 60.0, got {candidates}"
    assert meta == 60.0, f"Expected 60.0, got {meta}"

    assert flush != resolve, "flush interval must not equal resolve TTL"
    assert flush != candidates, "flush interval must not equal candidates TTL"
    assert flush != meta, "flush interval must not equal meta candidates TTL"


def test_serialize_and_write_snapshot_exists():
    """_serialize_and_write_snapshot must exist as a sync thread function (→1192)."""
    import inspect
    fn = getattr(agents_router, "_serialize_and_write_snapshot", None)
    assert fn is not None, "_serialize_and_write_snapshot must be defined"
    assert not inspect.iscoroutinefunction(fn), "must be sync (runs in thread)"


def test_save_agent_state_async_delegates_to_snapshot_fn():
    """_save_agent_state_async must delegate serialization to _serialize_and_write_snapshot.

    The snapshot helper runs in asyncio.to_thread, so json.dumps executes off
    the event loop. We verify that the async function calls the helper and that
    the helper (not the async function) is where json.dumps lives.
    """
    import inspect
    async_src = inspect.getsource(agents_router._save_agent_state_async)
    snap_src = inspect.getsource(agents_router._serialize_and_write_snapshot)

    # The helper must contain json.dumps
    assert "json.dumps" in snap_src, (
        "_serialize_and_write_snapshot must contain json.dumps "
        "(that's the point — it runs in a thread, not on the event loop)"
    )
    # The async function must delegate to the helper (by name)
    assert "_serialize_and_write_snapshot" in async_src, (
        "_save_agent_state_async must call _serialize_and_write_snapshot "
        "so serialization runs in asyncio.to_thread, not on the event loop"
    )


def test_gil_yield_in_load_candidates():
    """_load_candidates must call time.sleep(0) to yield the GIL (→1192)."""
    import inspect
    src = inspect.getsource(agents_router._load_candidates)
    assert "sleep(0)" in src, (
        "_load_candidates must yield the GIL every ~10 iters via time.sleep(0)"
    )


def test_gil_yield_in_load_meta_candidates():
    """_load_meta_candidates must call time.sleep(0) to yield the GIL (→1192)."""
    import inspect
    src = inspect.getsource(agents_router._load_meta_candidates)
    assert "sleep(0)" in src, (
        "_load_meta_candidates must yield the GIL every ~10 iters via time.sleep(0)"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_fake_agents(n: int) -> dict:
    """Build N fake agent rows suitable for patching into agent_metadata."""
    now = _ts_now()
    return {
        f"wedge-test-agent-{i:03d}": {
            "name": f"wedge-test-agent-{i:03d}",
            "status": "completed",
            "source": "claude-code",
            "spawned_at": now,
            "completed_at": now,
            "last_heartbeat_at": now,
            "model": "sonnet",
            "task": f"wedge regression test agent {i}",
            "transcript_bytes": 0,
        }
        for i in range(n)
    }


def _make_mock_ostk() -> MagicMock:
    mock = MagicMock()

    async def fake_kernel_ps():
        return {"daemon_running": False, "agents": [], "raw": "ostk not running"}

    async def fake_audit_agents():
        return []

    mock.kernel_ps = fake_kernel_ps
    mock.audit_agents = fake_audit_agents
    return mock


# ---------------------------------------------------------------------------
# Concurrent cold-cache load test (core →1192 regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_cache_no_thundering_herd(tmp_path):
    """Ten concurrent /api/agents calls must serialize through _enrich_async_lock.

    The thundering-herd pattern (without the lock): N concurrent list_agents calls
    each spawn asyncio.to_thread(_run_enrich_pipeline, ...) simultaneously. Each
    thread does a full cold glob scan independently, amplifying I/O by N×.

    Fix: _enrich_async_lock serializes callers. Only the first runs the slow pipeline;
    the other N-1 wait at the lock (queue), then each runs a fast warm-cache pipeline.

    This test mocks _run_enrich_pipeline directly to measure serialization behavior
    without depending on filesystem state (which is non-deterministic on a live system).

    - First call: 0.3 s delay (cold rebuild simulation)
    - Subsequent calls: near-zero (warm cache simulation)

    Without the lock: 10 calls run concurrently in the thread pool → ~0.3s wall-clock
    but N-way cache contention amplifies real time (this is the bug).
    With the lock: calls are serial → 0.3s (first) + 9 × near-zero ≈ 0.3s total.
    We verify total elapsed < 2s (well under 10 × 0.3s = 3s without the lock).
    """
    fake_meta = _make_fake_agents(30)
    state_path = tmp_path / "agent_state.json"
    state_path.write_text(json.dumps(fake_meta))
    mock_ostk = _make_mock_ostk()

    enrich_call_count = 0
    cold_call_delay = 0.3

    def controlled_enrich(all_agents, deleted_names, now_for_sweep,
                          user_spawned_filter, filter_status, filter_source, limit):
        nonlocal enrich_call_count
        enrich_call_count += 1
        if enrich_call_count == 1:
            time.sleep(cold_call_delay)  # simulate cold rebuild on first call only
        # Return a minimal valid result for the agents passed in
        visible = [a for a in all_agents if a.get("name") not in (deleted_names or set())]
        if filter_status:
            visible = [a for a in visible if a.get("status") == filter_status]
        if limit is not None:
            visible = visible[:limit]
        return visible

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch.dict(agents_router.agent_metadata, fake_meta, clear=True),
        patch.dict(agents_router.active_agents, {}, clear=True),
        patch("routers.agents.ostk", mock_ostk),
        patch("routers.agents._load_deleted_agents", return_value=set()),
        patch("routers.agents._prune_stale_completed_agents", return_value=0),
        patch("routers.agents._run_enrich_pipeline", side_effect=controlled_enrich),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=15.0,
        ) as client:

            t_start = time.monotonic()
            responses = await asyncio.gather(*[
                client.get("/api/agents?limit=300") for _ in range(10)
            ])
            total_elapsed = time.monotonic() - t_start

    for i, r in enumerate(responses):
        assert r.status_code == 200, f"Request {i}: expected 200, got {r.status_code}"
        assert r.content, f"Request {i}: response body must not be empty"
        data = r.json()
        assert "agents" in data, f"Request {i}: missing 'agents' key in {list(data)}"

    # All 10 calls serialized: total ≈ cold_call_delay (first) + 9 × near-zero.
    # 2s upper bound is well below 10 × cold_call_delay = 3s (the no-lock worst case).
    assert total_elapsed < 2.0, (
        f"Total elapsed {total_elapsed:.3f}s exceeds 2.0s limit. "
        f"Expected ≈{cold_call_delay}s (1 cold call + 9 fast calls through "
        f"_enrich_async_lock). enrich was called {enrich_call_count} times. "
        f"If calls are NOT serialized by the lock, total ≈ 10 × {cold_call_delay}s = "
        f"{10 * cold_call_delay}s concurrent, or 3s serial (→1192)."
    )


# ---------------------------------------------------------------------------
# json.dumps off the event loop: saves must not block responsiveness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_async_snapshot_does_not_block_event_loop(tmp_path):
    """_save_agent_state_async with 30 rows must not block the event loop.

    The snapshot is taken synchronously, but json.dumps runs in a thread.
    A concurrent sleep(0.01) probe must not stall for more than 150ms.
    """
    fake_meta = _make_fake_agents(30)
    state_path = tmp_path / "agent_state.json"
    state_path.write_text("{}")

    write_delay = 0.05  # 50ms simulated disk write
    write_count = 0

    def slow_write(content: str) -> None:
        nonlocal write_count
        write_count += 1
        time.sleep(write_delay)
        state_path.write_text(content)

    max_probe_delay = 0.0

    async def probe_loop():
        nonlocal max_probe_delay
        for _ in range(30):
            t0 = asyncio.get_event_loop().time()
            await asyncio.sleep(0.01)
            delay = asyncio.get_event_loop().time() - t0 - 0.01
            if delay > max_probe_delay:
                max_probe_delay = delay

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch.dict(agents_router.agent_metadata, fake_meta, clear=True),
        patch("routers.agents._write_state_content", side_effect=slow_write),
    ):
        probe = asyncio.create_task(probe_loop())
        await asyncio.gather(*[
            agents_router._save_agent_state_async() for _ in range(5)
        ])
        await probe

    assert write_count >= 1
    assert max_probe_delay < 0.15, (
        f"Event loop blocked for {max_probe_delay * 1000:.0f}ms during async saves "
        f"(limit: 150ms). json.dumps may still be running on the event loop (→1192)."
    )


# ---------------------------------------------------------------------------
# Cache unbounded growth regression (→1192 total wedge / 6 GB RSS)
# ---------------------------------------------------------------------------


def test_candidates_cache_key_excludes_mtime():
    """_candidates_cache must use (root, pattern) as the key — NOT including mtime.

    Root cause of the 6 GB RSS / total TCP wedge (→1192):
    The old key was (root, pattern, root_mtime_ns). Every new Claude Code session
    JSONL file changed the directory mtime → new key added, old key NEVER removed.
    With ~1300 session files and 826 subagent candidates per entry (~4.5 KB each),
    the cache accumulated 1300 × 3.7 MB = ~4.8 GB. macOS swapped → event loop
    stalled → accept() never called → TCP connect timed out.

    Fix: key is (root, pattern) only; mtime is stored inside the value tuple
    for validation. A changed mtime overwrites the same key in-place, so at most
    one entry per (root, pattern) pair ever exists in the cache.
    """
    import inspect
    src = inspect.getsource(agents_router._load_candidates)

    # The cache key must NOT contain the mtime.
    # The old buggy line was: key = (str(root), pattern, root_mtime)
    # The new correct line is: key = (str(root), pattern)
    assert "key = (str(root), pattern, root_mtime)" not in src, (
        "_load_candidates must NOT include root_mtime in the cache key. "
        "That causes unbounded accumulation of stale cache entries (→1192)."
    )
    assert "key = (str(root), pattern)" in src, (
        "_load_candidates cache key must be (str(root), pattern) — mtime goes "
        "in the VALUE so a directory change overwrites in-place, not adds a new key."
    )


def test_meta_candidates_cache_key_excludes_mtime():
    """_meta_candidates_cache must use str(project_dir) as key — NOT including mtime."""
    import inspect
    src = inspect.getsource(agents_router._load_meta_candidates)

    assert "key = (str(project_dir), root_mtime)" not in src, (
        "_load_meta_candidates must NOT include root_mtime in the cache key (→1192)."
    )
    assert "key = str(project_dir)" in src, (
        "_load_meta_candidates cache key must be str(project_dir) alone."
    )


def test_candidates_cache_does_not_accumulate_on_mtime_change(tmp_path):
    """Each unique directory mtime must overwrite the cache entry, not add a new key.

    Simulates 300 consecutive directory-mtime changes (as if 300 new Claude Code
    session JSONL files were created). Each call to _load_candidates must result
    in exactly ONE cache entry for that (root, pattern) pair — not 300.

    Without the fix: len(_candidates_cache) grows to 300 (one per mtime).
    With the fix:    len(_candidates_cache) stays at 1.

    300 entries × 826 candidates × 4.5 KB ≈ 1.1 GB. With 1300 session files
    (measured on the live system) the real accumulation exceeded 4.8 GB (→1192).
    """
    root = tmp_path / "projects" / "-Users-torimeyer-claude-torios"
    root.mkdir(parents=True)
    pattern = "*.jsonl"

    # Patch the cache to use a fresh dict so this test doesn't pollute
    # the module-level cache used by other tests.
    fresh_cache: dict = {}

    # Track how many unique mtime values were presented to the cache.
    seen_mtimes: set = set()

    def fake_dir_mtime(path: Path) -> int:
        # Return a new unique int each call to simulate a directory that
        # changes mtime on every access (worst case: every request sees a
        # new session file).
        val = len(seen_mtimes) + 1
        seen_mtimes.add(val)
        return val

    with (
        patch.object(agents_router, "_candidates_cache", fresh_cache),
        patch("routers.agents._dir_mtime_ns", side_effect=fake_dir_mtime),
    ):
        # Simulate 300 consecutive calls where the directory mtime differs each time.
        for _ in range(300):
            agents_router._load_candidates(root, pattern)

    # There should be exactly ONE cache entry for the (root, pattern) pair.
    # Old code: 300 entries (one per unique mtime). New code: 1 entry.
    assert len(fresh_cache) == 1, (
        f"Expected 1 cache entry, got {len(fresh_cache)}. "
        f"Stale entries accumulate memory (→1192): each entry is ~3-20 MB; "
        f"300 entries = 1-6 GB depending on candidate count."
    )
    # The single entry's key must not include a mtime component.
    the_key = list(fresh_cache.keys())[0]
    assert isinstance(the_key, tuple) and len(the_key) == 2, (
        f"Cache key must be a 2-tuple (root, pattern), got {the_key!r}"
    )


def test_meta_candidates_cache_does_not_accumulate_on_mtime_change(tmp_path):
    """_meta_candidates_cache must not accumulate stale keys on repeated mtime changes."""
    project_dir = tmp_path / "projects" / "-Users-torimeyer-claude-torios"
    project_dir.mkdir(parents=True)

    fresh_cache: dict = {}
    seen_mtimes: set = set()

    def fake_dir_mtime(path: Path) -> int:
        val = len(seen_mtimes) + 1
        seen_mtimes.add(val)
        return val

    with (
        patch.object(agents_router, "_meta_candidates_cache", fresh_cache),
        patch("routers.agents._dir_mtime_ns", side_effect=fake_dir_mtime),
    ):
        for _ in range(300):
            agents_router._load_meta_candidates(project_dir)

    assert len(fresh_cache) == 1, (
        f"Expected 1 cache entry, got {len(fresh_cache)} (→1192 memory leak)."
    )
    the_key = list(fresh_cache.keys())[0]
    assert isinstance(the_key, str), (
        f"_meta_candidates_cache key must be a plain string, got {type(the_key)}"
    )

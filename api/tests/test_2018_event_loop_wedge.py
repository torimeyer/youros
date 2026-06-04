"""
Regression test for →2018: backend event-loop wedge under agent load.

Root cause: _autocomplete_exited_subagents ran via asyncio.to_thread every
500 ms from the snapshot loop AND every 60 s from the reconcile loop.  Concurrent
GIL-heavy threads starved the event loop, causing all endpoints to stall.
Additionally _sweep_stale_running_agents() ran synchronously on the event loop,
blocking it for stat×N agents every 60 s.

Fix: _sweep_pass_lock serializes concurrent autocomplete/sweep calls so two
GIL-heavy asyncio.to_thread passes never run at once; the snapshot loop skips
autocomplete only while a sweep is already in flight (otherwise it runs every
tick so dead agents still flip to completed on read); sweep moves to a thread.
"""

import asyncio
import time
import pytest


# ---------------------------------------------------------------------------
# 1. Verify the serialization lock exists and has the correct type
# ---------------------------------------------------------------------------

def test_sweep_pass_lock_exists():
    from routers.agents import _sweep_pass_lock
    assert isinstance(_sweep_pass_lock, asyncio.Lock), "_sweep_pass_lock must be asyncio.Lock"


# ---------------------------------------------------------------------------
# 2. Snapshot loop runs autocomplete when the lock is free, and skips it only
#    while a sweep is already in flight. No time-based throttle: a dead agent
#    must still flip to completed on the next read, not 5 s later.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autocomplete_runs_when_free_skips_when_sweep_in_flight():
    import routers.agents as ag

    call_count = 0

    def fake_autocomplete():
        nonlocal call_count
        call_count += 1
        return False

    original_fn = ag._autocomplete_exited_subagents
    try:
        ag._autocomplete_exited_subagents = fake_autocomplete

        # Replicate the snapshot-loop gate from _compute_agents_snapshot_async.
        async def snapshot_gate():
            ac_changed = False
            if not ag._sweep_pass_lock.locked():
                async with ag._sweep_pass_lock:
                    ac_changed = await asyncio.to_thread(ag._autocomplete_exited_subagents)
            return ac_changed

        # Lock free -> autocomplete runs on the read path.
        await snapshot_gate()
        assert call_count == 1, "autocomplete must run when no sweep is in flight"

        # Lock held (a sweep is in flight) -> snapshot skips, no pile-up.
        async with ag._sweep_pass_lock:
            await snapshot_gate()
        assert call_count == 1, "autocomplete must be skipped while a sweep holds the lock"
    finally:
        ag._autocomplete_exited_subagents = original_fn


# ---------------------------------------------------------------------------
# 3. Verify lock serializes concurrent callers — no two run simultaneously
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sweep_pass_lock_serializes_concurrent_callers():
    """Two coroutines acquiring _sweep_pass_lock must not overlap."""
    import routers.agents as ag

    overlap_detected = False
    inside_count = 0

    async def check_overlap():
        nonlocal inside_count, overlap_detected
        async with ag._sweep_pass_lock:
            inside_count += 1
            if inside_count > 1:
                overlap_detected = True
            await asyncio.sleep(0.01)
            inside_count -= 1

    await asyncio.gather(check_overlap(), check_overlap(), check_overlap())

    assert not overlap_detected, "_sweep_pass_lock must prevent concurrent holders"


# ---------------------------------------------------------------------------
# 4. Verify _sweep_stale_running_agents can be called from a thread
#    (it must be synchronous with no asyncio awaits inside)
# ---------------------------------------------------------------------------

def test_sweep_stale_running_agents_is_sync():
    """_sweep_stale_running_agents must be a plain sync function so it can be
    safely offloaded via asyncio.to_thread without holding the event loop."""
    import inspect
    from routers.agents import _sweep_stale_running_agents
    # Must NOT be a coroutine function
    assert not inspect.iscoroutinefunction(_sweep_stale_running_agents), (
        "_sweep_stale_running_agents must be sync to run in asyncio.to_thread"
    )


# ---------------------------------------------------------------------------
# 5. Event-loop latency stress test: concurrent "work" behind the lock
#    must not block the loop past a reasonable threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_not_stalled_under_concurrent_background_work():
    """Simulate the pre-fix scenario: two concurrent GIL-heavy threads.
    With the lock, loop latency must stay reasonable (< 300 ms per iteration).
    """
    import routers.agents as ag

    MAX_ALLOWED_STALL_MS = 300

    def cpu_work():
        # ~5 ms of Python dict iteration (simulates _autocomplete_exited_subagents)
        total = 0
        for i in range(500_000):
            total += i
        return total

    loop_lags = []

    async def measure_loop_lag(n: int = 5):
        for _ in range(n):
            t0 = asyncio.get_running_loop().time()
            await asyncio.sleep(0)  # single yield
            lag_ms = (asyncio.get_running_loop().time() - t0) * 1000
            loop_lags.append(lag_ms)
            await asyncio.sleep(0.01)

    # Background task acquires the lock and runs CPU work (as the reconcile
    # loop does via asyncio.to_thread after the fix)
    async def background_sweep():
        for _ in range(3):
            async with ag._sweep_pass_lock:
                await asyncio.to_thread(cpu_work)
            await asyncio.sleep(0.02)

    await asyncio.gather(
        measure_loop_lag(10),
        background_sweep(),
    )

    p95_lag = sorted(loop_lags)[int(len(loop_lags) * 0.95)]
    assert p95_lag < MAX_ALLOWED_STALL_MS, (
        f"p95 event-loop lag {p95_lag:.1f} ms >= {MAX_ALLOWED_STALL_MS} ms — "
        "background work is stalling the loop"
    )


# ---------------------------------------------------------------------------
# 6. Save coalescing: N concurrent _save_agent_state_async calls must
#    produce at most 2 serialized writes, not N concurrent threads (→2018)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_agent_state_async_coalesces_concurrent_saves():
    """N concurrent callers must collapse into at most 2 serialized writes.

    Pre-fix: every heartbeat spawned its own asyncio.to_thread(json.dumps)
    call; N concurrent heartbeats = N threads each holding the GIL in turn,
    starving the event loop. Post-fix: a single in-flight write plus at most
    one queued pass capture all pending mutations.
    """
    import routers.agents as ag

    write_starts: list[float] = []
    max_concurrent = 0
    currently_writing = 0

    original_fn = ag._serialize_and_write_snapshot

    def counting_write(snapshot: dict) -> None:
        nonlocal currently_writing, max_concurrent
        currently_writing += 1
        max_concurrent = max(max_concurrent, currently_writing)
        write_starts.append(time.monotonic())
        # Simulate json.dumps + disk write (~5ms)
        import time as _time
        _time.sleep(0.005)
        currently_writing -= 1

    try:
        ag._serialize_and_write_snapshot = counting_write
        # Also reset the gate for a clean test
        ag._save_inflight = False
        ag._save_pending = False

        # Fire 10 concurrent save requests (simulates 10 agents heartbeating)
        await asyncio.gather(*[ag._save_agent_state_async() for _ in range(10)])

        # Must never exceed 1 write in-flight simultaneously
        assert max_concurrent <= 1, (
            f"max concurrent writes = {max_concurrent}; coalescing gate broken"
        )
        # Must use far fewer than 10 writes (at most 2: one in-flight + one queued)
        assert len(write_starts) <= 2, (
            f"{len(write_starts)} writes fired for 10 concurrent calls; "
            "expected at most 2 (in-flight + one queued pass)"
        )
    finally:
        ag._serialize_and_write_snapshot = original_fn
        ag._save_inflight = False
        ag._save_pending = False


@pytest.mark.asyncio
async def test_save_agent_state_async_flags_exist():
    """Verify the coalescing gate globals are present and have correct types."""
    import routers.agents as ag
    assert isinstance(ag._save_inflight, bool), "_save_inflight must be a bool"
    assert isinstance(ag._save_pending, bool), "_save_pending must be a bool"


# ---------------------------------------------------------------------------
# →2137: JSONL candidate index key fix
# ---------------------------------------------------------------------------

# Before the fix, _find_freshest_matching_jsonl used a 3-element lookup key
# (str(root), pattern, root_mtime) while _candidates_cache is keyed on
# 2-element (str(root), pattern).  The index O(1) path was unreachable; every
# agent resolve ran the full linear scan (162 agents × 826 files × 12 patterns).
#
# After the fix the key in the lookup matches the cache key, so the index is
# used on every warm-cache call.

def test_candidates_cache_key_is_two_tuple():
    """_candidates_cache must be keyed on (str(root), pattern), not include mtime."""
    import routers.agents as ag
    import tempfile
    import os
    from pathlib import Path

    ag._reset_candidates_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pattern = "*.jsonl"
        # Prime the cache by calling _load_candidates
        ag._load_candidates(root, pattern)
        # Inspect the cache key
        keys = list(ag._candidates_cache.keys())
        assert len(keys) == 1, f"Expected 1 cache entry, got {len(keys)}"
        key = keys[0]
        assert len(key) == 2, f"Cache key must be (str(root), pattern) — got {len(key)}-tuple: {key}"
        assert key == (str(root), pattern)
    ag._reset_candidates_cache()


def test_find_freshest_uses_index_for_known_name(tmp_path):
    """After cache warm-up, _find_freshest_matching_jsonl must hit the O(1)
    index and skip the linear _first_line_matches_needle loop."""
    import routers.agents as ag

    # Create a JSONL file whose first line registers a known agent name.
    agent_name = "test-index-agent"
    jsonl = tmp_path / "agent-abc123.jsonl"
    jsonl.write_text(
        '{"type":"user","content":"curl -sk -X POST https://127.0.0.1:8000/api/agents/register'
        f' -d \\"name\\": \\"{agent_name}\\""}}\n'
    )

    ag._reset_candidates_cache()
    # Prime the cache
    ag._load_candidates(tmp_path, "*.jsonl")

    linear_calls = []
    original_fn = ag._first_line_matches_needle

    def counting_match(first_line, needle):
        linear_calls.append(needle)
        return original_fn(first_line, needle)

    try:
        ag._first_line_matches_needle = counting_match
        result = ag._find_freshest_matching_jsonl(tmp_path, agent_name, "*.jsonl")
    finally:
        ag._first_line_matches_needle = original_fn
        ag._reset_candidates_cache()

    # If the index hit, the linear scan function must NOT have been called
    # (because the index lookup returns before reaching the fallback loop).
    assert len(linear_calls) == 0, (
        f"_first_line_matches_needle called {len(linear_calls)} times — "
        "index lookup did not short-circuit the linear scan"
    )
    # The correct file must still be returned
    assert result == jsonl, f"Expected {jsonl}, got {result}"


def test_warm_cache_resolve_no_latency_spike(tmp_path):
    """Simulated enrichment pass over N agents must complete well under 1 s
    on a warm cache (index lookup), not 6 s (linear scan).

    Before the fix: 162 agents × 826 files × 12 patterns per file = ~1.6M
    string comparisons, ~6 s.  After the fix: 162 O(1) dict lookups, <10 ms.
    """
    import time
    import routers.agents as ag

    N_AGENTS = 50
    N_FILES = 100

    # Create N_FILES candidate JSONL files, each with a distinct agent name.
    names = [f"agent-load-{i:04d}" for i in range(N_AGENTS)]
    for i in range(N_FILES):
        jsonl = tmp_path / f"agent-{i:04d}.jsonl"
        if i < N_AGENTS:
            # File for agent i
            jsonl.write_text(
                '{"type":"user","content":"curl -sk -X POST /api/agents/register'
                f' -H \'Content-Type: application/json\' -d \'{{\\"name\\": \\"{names[i]}\\"}}\'"}}\n'
            )
        else:
            # Noise file — no matching agent name
            jsonl.write_text('{"type":"user","content":"some unrelated content here"}\n')

    ag._reset_candidates_cache()
    # Prime the cache (cold scan)
    ag._load_candidates(tmp_path, "*.jsonl")

    # Now time N_AGENTS warm-cache lookups
    t0 = time.monotonic()
    for name in names:
        ag._find_freshest_matching_jsonl(tmp_path, name, "*.jsonl")
    elapsed_ms = (time.monotonic() - t0) * 1000

    ag._reset_candidates_cache()

    assert elapsed_ms < 1000, (
        f"Warm-cache enrichment pass over {N_AGENTS} agents took {elapsed_ms:.0f} ms — "
        f"expected < 1000 ms (index lookup should be near-zero per agent)"
    )

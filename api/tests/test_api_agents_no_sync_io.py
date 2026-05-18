"""Guard: GET /api/agents must not run sync filesystem I/O on the event loop.

Mirrors the pattern from →1416 (subprocess.run guard). The CC session
inference block inside _compute_agents_snapshot_async scanned
~/.claude/projects/<label>/*.jsonl (1271+ files) and called stat() on each
one synchronously on the asyncio event loop every 500ms. This blocked the
loop for ~7-8ms per cycle and starved all concurrent requests (→1445).

Fix: extracted into _infer_cc_session_agents() and called via
asyncio.to_thread so the glob + stat run in a worker thread.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# RED test 1: structural — the extracted helper must exist
# ---------------------------------------------------------------------------

def test_infer_cc_session_agents_helper_exists():
    """The CC session glob must be extracted into a standalone sync helper.

    Before →1445 fix: AttributeError — the function lives inline inside
    _compute_agents_snapshot_async and is not importable.
    After fix: _infer_cc_session_agents exists and is callable.
    """
    import routers.agents as mod
    assert hasattr(mod, "_infer_cc_session_agents"), (
        "_infer_cc_session_agents not found in routers.agents — "
        "the CC session inference block is still inline on the event loop. "
        "Extract it into a sync helper and call via asyncio.to_thread (→1445)."
    )
    assert callable(mod._infer_cc_session_agents)


# ---------------------------------------------------------------------------
# RED test 2: threading — the helper must not run on the event loop thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_infer_cc_session_agents_called_via_to_thread(tmp_path):
    """When _compute_agents_snapshot_async runs, the CC session glob must
    execute in a worker thread, not on the asyncio event loop thread.

    Strategy:
    1. Create a fake projects dir with fresh .jsonl files so the glob fires.
    2. Spy on Path.glob and record the calling thread for *.jsonl calls.
    3. Run _compute_agents_snapshot_async.
    4. Assert no glob call came from the event loop thread.

    Before fix: glob runs inline → thread id matches the main loop → FAILS.
    After fix: glob runs in asyncio.to_thread → different thread → PASSES.
    """
    import importlib
    import routers.agents as agents_mod

    main_loop_thread_id = threading.get_ident()
    glob_call_thread_ids: list[int] = []
    original_glob = Path.glob

    def spy_glob(self: Path, pattern: str):
        if str(pattern) == "*.jsonl":
            glob_call_thread_ids.append(threading.get_ident())
        return original_glob(self, pattern)

    # Build fake ~/.claude/projects/<label>/ with fresh .jsonl files.
    fake_home = tmp_path / "home"
    label = str(Path("/fake/project")).replace("/", "-")  # "-fake-project"
    fake_proj_dir = fake_home / ".claude" / "projects" / label
    fake_proj_dir.mkdir(parents=True)
    for i in range(5):
        (fake_proj_dir / f"sess-{i}.jsonl").write_text("{}\n")

    from unittest.mock import patch
    import config as config_mod

    with (
        patch.object(Path, "glob", spy_glob),
        patch.object(Path, "home", classmethod(lambda cls: fake_home)),
        patch.object(config_mod, "PROJECT_ROOT", Path("/fake/project")),
    ):
        try:
            await asyncio.wait_for(
                agents_mod._compute_agents_snapshot_async(),
                timeout=15.0,
            )
        except Exception:
            pass

    # The glob must have fired (our fake dir exists with fresh files).
    assert glob_call_thread_ids, (
        "Path.glob('*.jsonl') was never called — the CC session block did "
        "not run. Check that the test fixture matches the path construction "
        "in _compute_agents_snapshot_async (or _infer_cc_session_agents)."
    )

    for tid in glob_call_thread_ids:
        assert tid != main_loop_thread_id, (
            f"CC session *.jsonl glob ran on the asyncio event loop thread "
            f"(id {tid} == main {main_loop_thread_id}). "
            "Wrap _infer_cc_session_agents in asyncio.to_thread (→1445)."
        )


# ---------------------------------------------------------------------------
# RED test 3: timing — event loop must not be starved by the glob
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_not_starved_by_cc_session_glob(tmp_path):
    """A concurrent coroutine must stay schedulable while the snapshot runs.

    The spy adds a 20ms sleep to each *.jsonl glob so a sync call blocks the
    loop for 20ms. After the fix the glob is in a thread, so the concurrent
    monitor runs freely and its checkpoint gaps stay near its 2ms tick period.

    Before fix: max gap >> 20ms → assertion FAILS.
    After fix: max gap < 15ms → PASSES.
    """
    import routers.agents as agents_mod
    import config as config_mod

    original_glob = Path.glob

    def slow_glob(self: Path, pattern: str):
        if str(pattern) == "*.jsonl":
            time.sleep(0.020)  # 20ms simulated disk I/O
        return original_glob(self, pattern)

    # Build fake dir so the CC session block runs.
    fake_home = tmp_path / "home"
    label = str(Path("/fake/project")).replace("/", "-")
    fake_proj_dir = fake_home / ".claude" / "projects" / label
    fake_proj_dir.mkdir(parents=True)
    for i in range(3):
        (fake_proj_dir / f"sess-{i}.jsonl").write_text("{}\n")

    checkpoints: list[float] = []

    async def monitor():
        for _ in range(12):
            checkpoints.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.002)

    from unittest.mock import patch

    with (
        patch.object(Path, "glob", slow_glob),
        patch.object(Path, "home", classmethod(lambda cls: fake_home)),
        patch.object(config_mod, "PROJECT_ROOT", Path("/fake/project")),
    ):
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    agents_mod._compute_agents_snapshot_async(),
                    monitor(),
                    return_exceptions=True,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            pass

    assert len(checkpoints) >= 3, "Monitor did not run enough checkpoints"

    gaps_ms = [
        (checkpoints[i + 1] - checkpoints[i]) * 1000
        for i in range(len(checkpoints) - 1)
    ]
    max_gap_ms = max(gaps_ms)
    assert max_gap_ms < 15.0, (
        f"Event loop blocked for {max_gap_ms:.1f}ms — CC session glob "
        "is running on the event loop thread. "
        "Fix: extract into _infer_cc_session_agents and call via "
        "asyncio.to_thread (→1445)."
    )

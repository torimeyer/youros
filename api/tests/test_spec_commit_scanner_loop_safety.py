"""Regression test (->1806 follow-up): the spec-commit scanner must not run
its blocking git subprocess on the asyncio event-loop thread.

services.spec_commit_scanner.scan_and_claim() called ``_recent_commits()``
directly, and ``_recent_commits`` runs a blocking ``subprocess.run(["git",
"log", ...])``. On the event loop that blocks every other request for the
duration of the git call (and, more dangerously, forks the large backend on
the loop). The scanner ticks every 60s (first at boot+5s), so it re-wedges a
backend that was otherwise healthy. The fix offloads the call via
``await asyncio.to_thread(_recent_commits, repo_path)``.

Guard mirrors test_detect_vertex_gemini_does_not_block_event_loop: make the
git call sleep, run a heartbeat coroutine alongside scan_and_claim, and assert
the heartbeat keeps ticking. If the call runs on the loop, the heartbeat
stalls and the test fails.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from services import spec_commit_scanner


@pytest.mark.asyncio
async def test_spec_commit_scanner_git_runs_off_event_loop(monkeypatch):
    def slow_recent_commits(*_a, **_k):
        time.sleep(0.4)  # simulates a slow `git log` subprocess
        return []

    monkeypatch.setattr(spec_commit_scanner, "_recent_commits", slow_recent_commits)
    # Empty slug map so scan_and_claim reaches _recent_commits without touching
    # the real filesystem; a non-existent specs dir yields {}.
    monkeypatch.setattr(spec_commit_scanner, "_get_slug_map", lambda _d=None: {})

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(80):
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await spec_commit_scanner.scan_and_claim(
        repo_path=Path("/tmp"),
        specs_dir=Path("/nonexistent-specs"),
        seen_commits=set(),
    )
    hb.cancel()

    assert ticks >= 20, (
        f"event loop was blocked during scan_and_claim()'s git call "
        f"(only {ticks}/80 heartbeat ticks fired). "
        "Fix: await asyncio.to_thread(_recent_commits, repo_path)."
    )

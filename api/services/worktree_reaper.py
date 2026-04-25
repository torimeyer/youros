"""Periodic reaper: remove absorbed agent worktrees + sweep dead agent rows (R5).

Runs scripts/worktree-reaper.sh --apply every 15 minutes so post-merge
worktrees stop accumulating. Calls ghost_reaper._do_sweep alongside each
worktree pass so orphaned agent registry rows are cleaned up in the same
cadence.

Public surface:
  REAPER_INTERVAL_SECONDS  int (900 = 15 min) -- importable for tests
  run_once(repo_root, transcripts_dir=None)  coroutine -- one full sweep
  run_forever()                              coroutine -- boot + 15-min loop

Wiring (requires api/main.py edit, outside current lock scope):

    async def schedule_worktree_reaper():
        from services.worktree_reaper import run_forever
        asyncio.create_task(run_forever())

Add that call inside the @app.on_event("startup") handler and the loop
will start automatically on every backend restart.

Script path override: set MYOS_REAPER_SCRIPT to a full path to substitute
a different executable. The test suite uses this to avoid hitting real git.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REAPER_INTERVAL_SECONDS = 900  # 15 minutes


def _reaper_script(repo_root: Path) -> str:
    override = os.environ.get("MYOS_REAPER_SCRIPT")
    if override:
        return override
    return str(repo_root / "scripts" / "worktree-reaper.sh")


async def _call_reaper_script(script: str, repo_root: Path) -> subprocess.CompletedProcess:
    """Run worktree-reaper.sh --apply in a thread. Thin wrapper so tests can patch."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [script, "--apply"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        ),
    )


async def run_once(
    repo_root: Path,
    transcripts_dir: Optional[Path] = None,
) -> dict:
    """Run one sweep: remove absorbed worktrees then sweep ghost agent rows.

    Returns dict with keys: worktrees_ok, worktrees_removed, agents_reaped.
    Never raises -- caller gets a result dict even on partial failure.
    """
    if transcripts_dir is None:
        transcripts_dir = repo_root / "transcripts"

    # ------------------------------------------------------------------
    # Step 1: worktree sweep
    # ------------------------------------------------------------------
    script = _reaper_script(repo_root)
    wt_ok = False
    wt_removed = 0
    try:
        result = await _call_reaper_script(script, repo_root)
        wt_ok = result.returncode == 0
        for line in result.stdout.splitlines():
            if line.startswith("done. removed="):
                try:
                    wt_removed = int(line.split("removed=")[1].split()[0])
                except (IndexError, ValueError):
                    pass
        if not wt_ok:
            logger.warning(
                "worktree_reaper: script exited %d: %s",
                result.returncode,
                result.stderr[:200],
            )
    except Exception as exc:
        logger.warning("worktree_reaper: script invocation failed: %s", exc)

    # ------------------------------------------------------------------
    # Step 2: agent row sweep (reuse ghost_reaper logic)
    # ------------------------------------------------------------------
    agents_reaped = 0
    try:
        from services.ghost_reaper import _do_sweep
        agents_reaped = await _do_sweep(transcripts_dir)
    except Exception as exc:
        logger.warning("worktree_reaper: agent sweep failed: %s", exc)

    if wt_removed or agents_reaped:
        logger.info(
            "worktree_reaper: removed %d worktree(s), reaped %d agent row(s)",
            wt_removed,
            agents_reaped,
        )
    return {
        "worktrees_ok": wt_ok,
        "worktrees_removed": wt_removed,
        "agents_reaped": agents_reaped,
    }


async def run_forever() -> None:
    """Boot sweep + 15-min loop. Wire into api/main.py startup to activate."""
    from config import PROJECT_ROOT

    repo_root = PROJECT_ROOT
    transcripts_dir = repo_root / "transcripts"

    await asyncio.sleep(5)  # let other startup tasks settle
    try:
        result = await run_once(repo_root, transcripts_dir)
        logger.info("worktree_reaper: boot sweep done: %s", result)
    except Exception as exc:
        logger.warning("worktree_reaper: boot sweep failed: %s", exc)

    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        try:
            await run_once(repo_root, transcripts_dir)
        except Exception as exc:
            logger.warning("worktree_reaper: periodic sweep failed: %s", exc)

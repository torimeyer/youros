"""Periodic reaper for ghost agent-registry entries (→922).

A "ghost" is a registry row whose subprocess never produced real output
(0-byte or missing transcript) but whose status never transitioned out of
``running`` or ``completed_timeout``. Left alone, ghosts pollute the
Agents page and require manual DELETE calls to clean up.

This module provides a pure identification function ``reap_ghost_agents``
and a ``run_forever`` coroutine wired into the FastAPI startup sequence.

Ghost criteria — ALL must be true:
  1. ``status`` in ("running", "completed_timeout")
  2. ``last_heartbeat_at`` (or ``spawned_at``) is older than
     ``stale_heartbeat_seconds`` (default 300 s / 5 min)
  3. transcript file at ``{transcripts_dir}/{name}.md`` is 0 bytes or absent
  4. ``source`` == "claude-code" (never touch daemon or system entries)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

STALE_HEARTBEAT_SECONDS = 300   # 5 minutes
SWEEP_INTERVAL_SECONDS = 60     # run loop every 60 s
GHOST_STATUSES = frozenset({"running", "completed_timeout"})


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def reap_ghost_agents(
    registry: Dict[str, dict],
    transcripts_dir: Path,
    now: datetime,
    *,
    stale_heartbeat_seconds: int = STALE_HEARTBEAT_SECONDS,
) -> list[str]:
    """Return names of ghost entries that should be deleted from the registry.

    Pure function: reads ``registry`` and the filesystem, mutates nothing.
    Callers are responsible for actually removing the returned names.

    Args:
        registry: snapshot of the agent_metadata dict (name -> meta).
        transcripts_dir: directory where ``{name}.md`` transcripts live.
        now: current UTC datetime (injectable for tests).
        stale_heartbeat_seconds: how old a heartbeat must be to qualify.

    Returns:
        List of agent names that match every ghost criterion.
    """
    cutoff = now - timedelta(seconds=stale_heartbeat_seconds)
    victims: list[str] = []

    for name, meta in registry.items():
        if not isinstance(meta, dict):
            continue

        if meta.get("source") != "claude-code":
            continue

        status = (meta.get("status") or "").strip().lower()
        if status not in GHOST_STATUSES:
            continue

        # Heartbeat staleness check — fall back to spawned_at if no heartbeat
        hb_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        hb = _parse_iso(hb_raw)
        if hb is None:
            continue
        if hb >= cutoff:
            continue  # fresh — still alive

        # Transcript check — 0 bytes or absent means no real work done.
        # Bridge-spawned agents (hook_preregister=True) write their real
        # transcript to a .output or .jsonl file recorded in transcript_path,
        # not to the canonical {name}.md. Checking only .md caused the reaper
        # to delete active bridge agents after 300s of stale heartbeat, making
        # /heartbeat and /complete return 404 and forcing the agent to abort.
        transcript_ok = False
        t_path = transcripts_dir / f"{name}.md"
        try:
            if t_path.exists() and t_path.stat().st_size > 0:
                transcript_ok = True
        except OSError:
            pass
        if not transcript_ok:
            raw_tp = meta.get("transcript_path")
            if raw_tp:
                try:
                    tp = Path(raw_tp)
                    if tp.exists() and tp.stat().st_size > 0:
                        transcript_ok = True
                except OSError:
                    pass
        if transcript_ok:
            continue  # real transcript content present

        victims.append(name)
        logger.info(
            "ghost_reaper: identified ghost name=%s status=%s hb=%s",
            name, status, hb_raw,
        )

    return victims


async def _do_sweep(transcripts_dir: Path) -> int:
    """Run one sweep: identify ghosts and delete them from the live registry.

    Returns the count of entries deleted.
    """
    from routers.agents import (
        agent_metadata,
        _save_agent_state,
        _load_deleted_agents,
        _save_deleted_agents,
    )

    now = datetime.now(timezone.utc)
    victims = reap_ghost_agents(agent_metadata, transcripts_dir, now)
    if not victims:
        return 0

    for name in victims:
        if name in agent_metadata:
            del agent_metadata[name]
            logger.info("ghost_reaper: deleted ghost name=%s", name)

    _save_agent_state()

    deleted_set = _load_deleted_agents()
    deleted_set.update(victims)
    _save_deleted_agents(deleted_set)

    return len(victims)


async def run_forever() -> None:
    """Background loop: sweep on boot, then every ``SWEEP_INTERVAL_SECONDS``.

    Launched from ``api/main.py`` via asyncio.create_task inside a startup
    event handler. Never raises; logs warnings and continues.
    """
    from config import PROJECT_ROOT

    transcripts_dir = PROJECT_ROOT / "transcripts"

    # Boot-time cleanup: run immediately so leftover ghosts from prior
    # sessions are removed before the first user interaction.
    await asyncio.sleep(2)
    try:
        n = await _do_sweep(transcripts_dir)
        if n:
            logger.info("ghost_reaper: boot sweep removed %d ghost(s)", n)
    except Exception as exc:
        logger.warning("ghost_reaper: boot sweep failed: %s", exc)

    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await _do_sweep(transcripts_dir)
        except Exception as exc:
            logger.warning("ghost_reaper: periodic sweep failed: %s", exc)

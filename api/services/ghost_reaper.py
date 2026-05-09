"""Periodic reaper for ghost agent-registry entries (→922).

A "ghost" is a registry row whose subprocess never produced real output
(0-byte or missing transcript) but whose status never transitioned out of
``running`` or ``completed_timeout``. Left alone, ghosts pollute the
Agents page and require manual DELETE calls to clean up.

This module provides a pure identification function ``reap_ghost_agents``
and a ``run_forever`` coroutine wired into the FastAPI startup sequence.

Ghost criteria — ALL must be true:
  1. ``source`` == "claude-code" (never touch daemon or system entries)
  2. ``status`` in ("running", "completed_timeout")
  3. subprocess PID (if recorded) is no longer alive
  4. ``last_heartbeat_at`` (or ``spawned_at``) is older than
     ``stale_heartbeat_seconds`` (default 300 s / 5 min)
  5. transcript file at ``{transcripts_dir}/{name}.md`` is 0 bytes or absent
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STALE_HEARTBEAT_SECONDS = 300   # 5 minutes
SWEEP_INTERVAL_SECONDS = 60     # run loop every 60 s
GHOST_STATUSES = frozenset({"running", "completed_timeout"})

MAX_AGENT_WALLCLOCK_HOURS = 6    # kill agents alive longer than this
TRANSCRIPT_STALE_SECONDS = 1800  # 30 min of no transcript growth
SIGTERM_GRACE_SECONDS = 5        # wait before escalating to SIGKILL


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
    transcript_resolver: Optional[Callable[[str], Optional[Path]]] = None,
) -> list[str]:
    """Return names of ghost entries that should be deleted from the registry.

    Pure function: reads ``registry`` and the filesystem, mutates nothing.
    Callers are responsible for actually removing the returned names.

    Args:
        registry: snapshot of the agent_metadata dict (name -> meta).
        transcripts_dir: directory where ``{name}.md`` transcripts live.
        now: current UTC datetime (injectable for tests).
        stale_heartbeat_seconds: how old a heartbeat must be to qualify.
        transcript_resolver: optional callable ``(name) -> Path | None`` that
            looks up a broader set of transcript locations (e.g. JSONL files
            under ~/.claude/projects/). When provided, the reaper checks this
            as a final fallback before declaring an agent a ghost. Pass
            ``_resolve_transcript_source`` from routers.agents so that HTTP-
            registered agents whose transcripts live outside transcripts_dir
            are not incorrectly reaped (fixes →1084 registry-loss pattern).

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

        # PID-alive guard: REST-spawned agents record the subprocess PID.
        # If the process is still running, it's not a ghost regardless of
        # heartbeat age or transcript size. This prevents the ghost reaper
        # from deleting worktree-isolation agents that are mid-run inside a
        # long tool call and haven't heartbeated recently.
        pid = meta.get("pid")
        if pid is not None:
            try:
                os.kill(int(pid), 0)
                continue  # process is alive
            except ProcessLookupError:
                pass  # process is dead, continue to ghost check
            except (PermissionError, OSError):
                continue  # process exists (no permission) or unknown — keep safe
            except (ValueError, TypeError):
                pass  # invalid pid, fall through

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
        # Final fallback: use the broader resolver (e.g. JSONL under
        # ~/.claude/projects/) if one was provided. This prevents reapers
        # from deleting HTTP-registered agents that write transcripts to
        # non-standard paths the basic checks above cannot reach.
        if not transcript_ok and transcript_resolver is not None:
            try:
                resolved = transcript_resolver(name)
                if resolved is not None and resolved.exists() and resolved.stat().st_size > 0:
                    transcript_ok = True
            except Exception:
                pass
        if transcript_ok:
            continue  # real transcript content present

        victims.append(name)
        logger.info(
            "ghost_reaper: identified ghost name=%s status=%s hb=%s",
            name, status, hb_raw,
        )

    return victims


def _transcript_mtime(
    name: str,
    meta: dict,
    transcripts_dir: Path,
) -> Optional[float]:
    """Return the mtime of the agent's transcript file, or None if absent."""
    raw_tp = meta.get("transcript_path")
    if raw_tp:
        try:
            tp = Path(raw_tp)
            if tp.exists():
                return tp.stat().st_mtime
        except OSError:
            pass
    t_path = transcripts_dir / f"{name}.md"
    try:
        if t_path.exists():
            return t_path.stat().st_mtime
    except OSError:
        pass
    return None


def find_wallclock_exceeded(
    registry: Dict[str, dict],
    transcripts_dir: Path,
    now: datetime,
    *,
    max_wallclock_hours: int = MAX_AGENT_WALLCLOCK_HOURS,
    transcript_stale_seconds: int = TRANSCRIPT_STALE_SECONDS,
) -> List[Tuple[str, int]]:
    """Return (name, pid) pairs for agents past the absolute wallclock ceiling.

    Pure function: reads registry and filesystem, mutates nothing.
    Criteria (all must match):
      1. source == "claude-code"
      2. status == "running"
      3. pid recorded and process is alive
      4. spawned_at older than max_wallclock_hours
      5. transcript mtime absent or older than transcript_stale_seconds
    """
    spawn_cutoff = now - timedelta(hours=max_wallclock_hours)
    now_ts = now.timestamp()
    results: List[Tuple[str, int]] = []

    for name, meta in registry.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("source") != "claude-code":
            continue
        if (meta.get("status") or "").strip().lower() != "running":
            continue
        pid_raw = meta.get("pid")
        if pid_raw is None:
            continue
        try:
            pid = int(pid_raw)
        except (ValueError, TypeError):
            continue
        spawn_dt = _parse_iso(meta.get("spawned_at"))
        if spawn_dt is None or spawn_dt > spawn_cutoff:
            continue
        mtime = _transcript_mtime(name, meta, transcripts_dir)
        if mtime is not None and (now_ts - mtime) < transcript_stale_seconds:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue  # already dead — ghost_reaper handles this row
        except (PermissionError, OSError):
            pass  # alive but no permission — include
        results.append((name, pid))
        logger.info(
            "wallclock_reaper: candidate name=%s pid=%d spawned=%s",
            name, pid, meta.get("spawned_at"),
        )
    return results


async def _sweep_wallclock_exceeded(
    transcripts_dir: Path,
    _now: Optional[datetime] = None,
) -> int:
    """SIGTERM then SIGKILL each agent past the wallclock ceiling; update rows."""
    from routers.agents import agent_metadata, _save_agent_state

    now = _now if _now is not None else datetime.now(timezone.utc)
    victims = find_wallclock_exceeded(agent_metadata, transcripts_dir, now)
    if not victims:
        return 0

    for name, pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("wallclock_reaper: SIGTERM name=%s pid=%d", name, pid)
        except (ProcessLookupError, OSError):
            pass

    await asyncio.sleep(SIGTERM_GRACE_SECONDS)

    killed = 0
    for name, pid in victims:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            logger.info("wallclock_reaper: SIGKILL name=%s pid=%d", name, pid)
        except (ProcessLookupError, OSError):
            pass
        if name in agent_metadata:
            agent_metadata[name]["status"] = "killed"
            agent_metadata[name]["killed_reason"] = "exceeded max wallclock"
            killed += 1

    if killed:
        _save_agent_state()
    return killed


async def _do_sweep(transcripts_dir: Path) -> int:
    """Run one sweep: delete ghost rows and kill wallclock-exceeded agents.

    Returns the total count of entries deleted plus agents killed.
    """
    from routers.agents import (
        agent_metadata,
        _save_agent_state,
        _load_deleted_agents,
        _save_deleted_agents,
        _resolve_transcript_source,
    )

    now = datetime.now(timezone.utc)
    victims = reap_ghost_agents(
        agent_metadata,
        transcripts_dir,
        now,
        transcript_resolver=_resolve_transcript_source,
    )
    n_ghosts = 0
    if victims:
        for name in victims:
            if name in agent_metadata:
                del agent_metadata[name]
                logger.info("ghost_reaper: deleted ghost name=%s", name)
        _save_agent_state()
        deleted_set = _load_deleted_agents()
        deleted_set.update(victims)
        _save_deleted_agents(deleted_set)
        n_ghosts = len(victims)

    n_wallclock = await _sweep_wallclock_exceeded(transcripts_dir)
    return n_ghosts + n_wallclock


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

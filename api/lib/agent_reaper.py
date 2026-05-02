"""Liveness supervisor for running agent rows.

Catches subagents that registered, heartbeated briefly, then went silent
without marking themselves complete. The ghost_reaper (services/ghost_reaper.py)
eventually DELETES these rows after 5 min of stale heartbeat. This supervisor
acts EARLIER (180 s) and marks rows status=failed with a clear last_error, so
the Agents page shows a useful failure message instead of a stuck "running" row.

Stuck criteria (ALL must be true):
  1. status == "running"
  2. source == "claude-code"
  3. subprocess PID is known AND not alive (skip if PID unknown)
  4. now - max(last_heartbeat_at, spawned_at) > STUCK_THRESHOLD_SECONDS (180 s)
  5. transcript_bytes < TRANSCRIPT_STUCK_BYTES (1024)

Stalled criteria (live-PID variant, 2026-05-02):
  1. status == "running"
  2. source == "claude-code"
  3. PID alive or unknown (NOT handled by the stuck/dead-PID path above)
  4. transcript_bytes has not grown AND current_step has not changed for
     STALL_THRESHOLD_SECONDS (default 120 s, env-configurable)
  5. transcript_bytes < TRANSCRIPT_STUCK_BYTES

The ghost_reaper's subsequent delete pass will not touch rows in status=failed
or status=stalled (it only targets "running" and "completed_timeout"), so the
row stays visible in the Agents page for diagnosis until manually dismissed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STUCK_THRESHOLD_SECONDS = 180    # 3 min without progress → declare stuck
TRANSCRIPT_STUCK_BYTES = 1024    # < 1 KB means essentially nothing written
SWEEP_INTERVAL_SECONDS = 30      # check every 30 s
STALL_THRESHOLD_SECONDS = int(os.environ.get("STALL_THRESHOLD_SECONDS", "120"))

# Module-level progress snapshots for stall detection.
# {agent_name: (last_seen_transcript_bytes, last_progress_iso)}
_stall_snapshots: Dict[str, Tuple[int, str]] = {}


def _parse_iso(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _pid_alive(pid: object) -> Optional[bool]:
    """Return True if pid is alive, False if dead, None if unknown/invalid.

    None means "we cannot determine status" — callers treat it as skip.
    """
    if pid is None:
        return None
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return None
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # process exists but we can't signal it; keep safe


def find_stuck_agents(
    registry: Dict[str, dict],
    now: datetime,
    *,
    get_transcript_bytes: Callable[[str], int],
    stuck_threshold_seconds: int = STUCK_THRESHOLD_SECONDS,
    transcript_stuck_bytes: int = TRANSCRIPT_STUCK_BYTES,
) -> List[Tuple[str, str]]:
    """Return (name, last_error) pairs for agents that should be marked failed.

    Pure function: reads registry and calls get_transcript_bytes(name),
    mutates nothing. Callers are responsible for updating registry rows.

    Args:
        registry: snapshot of agent_metadata dict (name -> meta).
        now: current UTC datetime (injectable for tests).
        get_transcript_bytes: callable(name) -> int byte count.
        stuck_threshold_seconds: seconds without progress before declaring stuck.
        transcript_stuck_bytes: threshold below which transcript is "empty".

    Returns:
        List of (name, last_error_message) for agents that qualify.
    """
    cutoff = now - timedelta(seconds=stuck_threshold_seconds)
    victims: List[Tuple[str, str]] = []

    for name, meta in registry.items():
        if not isinstance(meta, dict):
            continue

        if meta.get("status") != "running":
            continue

        if meta.get("source") != "claude-code":
            continue

        # PID guard: only reap if PID is known and dead.
        # Unknown PID means we cannot verify subprocess state — skip.
        pid = meta.get("pid")
        alive = _pid_alive(pid)
        if alive is None:
            continue  # PID unknown or invalid — do not reap
        if alive is True:
            continue  # process is running — definitely not stuck

        # Progress time: use the most recent of last_heartbeat_at,
        # current_step_updated_at, and spawned_at so that an agent blocked
        # inside a long subprocess (no new HTTP heartbeats) is not reaped as
        # long as its step was updated recently.
        candidates = [
            _parse_iso(meta.get("last_heartbeat_at")),
            _parse_iso(meta.get("current_step_updated_at")),
            _parse_iso(meta.get("spawned_at")),
        ]
        valid = [t for t in candidates if t is not None]
        if not valid:
            continue
        last_progress = max(valid)
        if last_progress >= cutoff:
            continue  # still within threshold (not yet > threshold)

        # Transcript check
        try:
            t_bytes = get_transcript_bytes(name)
        except Exception:
            t_bytes = 0
        if t_bytes >= transcript_stuck_bytes:
            continue  # has real content — not stuck

        age_seconds = int((now - last_progress).total_seconds())
        pid_desc = f", PID {pid} dead" if pid is not None else ""
        error_msg = (
            f"stuck: registered {age_seconds}s ago, transcript empty "
            f"({t_bytes} bytes), no heartbeat progress{pid_desc}"
        )
        victims.append((name, error_msg))
        logger.info(
            "agent_reaper: stuck agent name=%s age=%ds transcript_bytes=%d pid=%s",
            name,
            age_seconds,
            t_bytes,
            pid,
        )

    return victims


def detect_stalled_agents(
    registry: Dict[str, dict],
    now: datetime,
    *,
    get_transcript_bytes: Callable[[str], int],
    stall_threshold_seconds: int = STALL_THRESHOLD_SECONDS,
) -> List[str]:
    """Return names of alive-PID agents with no transcript or step progress.

    Complements find_stuck_agents(), which requires a dead PID. This catches
    the incident pattern: PID alive, transcript_bytes=0 for > stall_threshold,
    no current_step change — agent is running but completely flatlined.

    First time an agent is observed it is seeded into _stall_snapshots and
    skipped (grace cycle). Subsequent sweeps compare against the snapshot.

    Args:
        registry: snapshot of agent_metadata dict.
        now: current UTC datetime (injectable for tests).
        get_transcript_bytes: callable(name) -> int byte count.
        stall_threshold_seconds: seconds of no progress before declaring stalled.

    Returns:
        List of agent names to flip to status="stalled".
    """
    cutoff = now - timedelta(seconds=stall_threshold_seconds)
    stalled: List[str] = []

    for name, meta in registry.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("status") != "running":
            continue
        if meta.get("source") != "claude-code":
            continue

        # Only fire when PID is alive or unknown; dead PIDs are find_stuck_agents territory.
        alive = _pid_alive(meta.get("pid"))
        if alive is False:
            continue

        try:
            t_bytes = get_transcript_bytes(name)
        except Exception:
            t_bytes = 0

        prev = _stall_snapshots.get(name)
        if prev is None:
            # First observation: seed snapshot from spawned_at (or now) and skip.
            seed = _parse_iso(meta.get("spawned_at")) or now
            _stall_snapshots[name] = (t_bytes, seed.isoformat())
            continue

        prev_bytes, last_progress_iso = prev

        if t_bytes > prev_bytes:
            # Transcript grew — progress.
            _stall_snapshots[name] = (t_bytes, now.isoformat())
            continue

        # Check current_step_updated_at as a secondary progress signal.
        step_ts = _parse_iso(meta.get("current_step_updated_at"))
        stored_ts = _parse_iso(last_progress_iso)
        if step_ts is not None and stored_ts is not None and step_ts > stored_ts:
            _stall_snapshots[name] = (t_bytes, step_ts.isoformat())
            continue

        # No growth and no step advance — update byte count but keep old time.
        _stall_snapshots[name] = (t_bytes, last_progress_iso)

        last_progress = _parse_iso(last_progress_iso)
        if last_progress is None or last_progress >= cutoff:
            continue

        if t_bytes >= TRANSCRIPT_STUCK_BYTES:
            continue  # meaningful content — not stalled

        stalled.append(name)
        logger.info(
            "agent_reaper: stalled agent name=%s bytes=%d last_progress=%s",
            name, t_bytes, last_progress_iso,
        )

    return stalled


async def _do_sweep() -> int:
    """Find stuck/stalled agents and update their status. Returns count updated."""
    from routers.agents import agent_metadata, _save_agent_state, _get_transcript_metrics

    now = datetime.now(timezone.utc)

    def _get_bytes(name: str) -> int:
        return _get_transcript_metrics(name).get("transcript_bytes", 0)

    victims = find_stuck_agents(agent_metadata, now, get_transcript_bytes=_get_bytes)
    stalled = detect_stalled_agents(agent_metadata, now, get_transcript_bytes=_get_bytes)

    if not victims and not stalled:
        return 0

    now_iso = now.isoformat()
    for name, error_msg in victims:
        meta = agent_metadata.get(name)
        if meta is None:
            continue
        meta["status"] = "failed"
        meta["last_error"] = error_msg
        meta["failed_at"] = now_iso
        logger.warning("agent_reaper: marked failed name=%s error=%s", name, error_msg)

    for name in stalled:
        meta = agent_metadata.get(name)
        if meta is None or meta.get("status") != "running":
            continue
        pid_desc = f"PID {meta.get('pid')} alive" if meta.get("pid") else "PID unknown"
        meta["status"] = "stalled"
        meta["stalled_at"] = now_iso
        meta["last_error"] = (
            f"stalled: transcript empty for >{STALL_THRESHOLD_SECONDS}s "
            f"with no step progress ({pid_desc})"
        )
        logger.warning("agent_reaper: marked stalled name=%s pid=%s", name, meta.get("pid"))

    _save_agent_state()
    return len(victims) + len(stalled)


async def run_forever() -> None:
    """Background loop: sweep every SWEEP_INTERVAL_SECONDS.

    Launched from api/main.py via asyncio.create_task in a startup event.
    Never raises; logs warnings and continues.
    """
    await asyncio.sleep(5)
    logger.info(
        "agent_reaper: liveness supervisor started (threshold=%ds, interval=%ds)",
        STUCK_THRESHOLD_SECONDS,
        SWEEP_INTERVAL_SECONDS,
    )

    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            n = await _do_sweep()
            if n:
                logger.info("agent_reaper: marked %d agent(s) failed", n)
        except Exception as exc:
            logger.warning("agent_reaper: sweep failed: %s", exc)

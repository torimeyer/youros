"""Startup-time pruning of stale rows from agent_state.json.

Keeps the cold-cache enrich pass fast by dropping terminal agent rows
older than ``retention_days``. Only rows that are both terminal AND
expired are removed; running/recent rows are always kept.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Union of every terminal status string used across the agents router.
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed",
    "failed",
    "cancelled",
    "terminated_stale",
    "completed_timeout",
    "killed",
    "stopped",
    "abandoned",
    "timeout",
})


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def prune_agent_state(
    agent_metadata: dict,
    *,
    retention_days: int = 30,
    now: Optional[datetime] = None,
) -> tuple[dict, int]:
    """Return (pruned_dict, removed_count).

    Drops rows that satisfy BOTH conditions:
      1. status is in _TERMINAL_STATUSES
      2. the row's reference timestamp is older than retention_days

    Reference timestamp = completed_at if present, else spawned_at.
    Rows with neither timestamp are kept (we cannot safely infer age).
    Running rows are always kept regardless of age.

    Pure function: no I/O, no side effects. Takes ``now`` for testability.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)

    kept: dict = {}
    removed = 0

    for name, meta in agent_metadata.items():
        status = meta.get("status", "")
        if status not in _TERMINAL_STATUSES:
            kept[name] = meta
            continue

        ref_ts = meta.get("completed_at") or meta.get("spawned_at")
        ref_dt = _parse_iso(ref_ts)
        if ref_dt is None:
            kept[name] = meta
            continue

        if ref_dt < cutoff:
            removed += 1
        else:
            kept[name] = meta

    return kept, removed


def run_startup_prune(
    agent_state_path: Path,
    agent_metadata: dict,
    retention_days: int = 30,
) -> tuple[int, int]:
    """Backup agent_state.json, prune stale rows, persist the result.

    Called once at server startup (inside asyncio.to_thread so the event
    loop stays unblocked). Returns (kept_count, removed_count).

    The backup is written BEFORE any mutation so a crash mid-write cannot
    lose data. Backup path: <original>.bak-prune-<YYYYmmddHHMMSS>.
    """
    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y%m%d%H%M%S")

    # Compute pruned set first — only backup+write when rows will be removed.
    pruned, removed = prune_agent_state(
        agent_metadata, retention_days=retention_days, now=now
    )
    kept = len(pruned)

    if removed == 0:
        logger.info(
            "agent_state prune: kept %d, dropped 0 (nothing to prune, retention=%dd)",
            kept,
            retention_days,
        )
        return kept, 0

    # Backup before overwriting so a mid-write crash cannot corrupt the file.
    if agent_state_path.exists():
        backup_path = agent_state_path.with_name(
            f"{agent_state_path.stem}.bak-prune-{ts_str}"
        )
        try:
            shutil.copy2(agent_state_path, backup_path)
        except OSError as exc:
            logger.warning("agent_state prune: backup failed (%s), skipping prune", exc)
            return len(agent_metadata), 0

    try:
        from services.atomic_io import atomic_write_json
        atomic_write_json(agent_state_path, pruned)
    except Exception as exc:
        logger.warning("agent_state prune: write failed (%s), prune not applied", exc)
        return len(agent_metadata), 0

    logger.info(
        "agent_state prune: kept %d, dropped %d (retention=%dd)",
        kept,
        removed,
        retention_days,
    )
    return kept, removed

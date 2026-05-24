"""Time primitive — v1.0.

myOS's first formal primitive: every long-running operation can publish
start / progress / finish events here and get a calibrated ETA in return.

Public surface (frozen at v1.0)
--------------------------------
``start(op_id, op_kind, hint_sec=None) -> None``
    Register a new operation.  Creates a row in ``time_runs``.  If no
    history exists for *op_kind* and *hint_sec* is None, the ETA is None
    until at least one completed run of the same kind has been recorded.

``progress(op_id, pct, current_step=None) -> None``
    Report fractional progress (0–100).  Updates ``progress_pct`` and
    ``current_step``.  Silently ignored for unknown *op_id* values.

``finish(op_id, status) -> None``
    Mark the operation done.  *status* must be one of ``"completed"``,
    ``"failed"``, or ``"cancelled"``.  Sets ``finished_at`` to now.

``status(op_id) -> TimeStatus | None``
    Return live status (ETA, elapsed, progress).  Returns None for
    unknown *op_id*.

``estimate(op_kind) -> int | None``
    Return the rolling median of the last 30 completed runs of *op_kind*
    in the last 14 days, applying the same outlier rules as
    ``agent_duration_stats.py`` (2 s floor, 2 h cap).  Returns None when
    there is no usable history.

``all_running() -> list[TimeStatus]``
    Return all currently-running operations (status IS NULL in the DB).

ETA computation
---------------
1. First call — use ``hint_sec`` if given; else derive from history via
   ``estimate(op_kind)``; else None.
2. Mid-run — ETA decays linearly: as progress approaches 100 %, the
   remaining estimate shrinks proportionally based on elapsed time.
   Formula: ``eta_sec = elapsed / (progress / 100) - elapsed`` clamped
   to ≥ 0.  We fall back to the hint-based estimate when progress is 0.
3. Outlier rules: durations < 2 s or > 2 h are excluded from the
   rolling median used by ``estimate()``.

Versioning
----------
Contract version: 1.0.  Breaking changes (renames, field removals) bump
the major.  New optional fields bump the minor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Optional

import services.primitives_db as _pdb

# ---------------------------------------------------------------------------
# Constants (matching agent_duration_stats.py outlier rules)
# ---------------------------------------------------------------------------

_MAX_DURATION_SEC = 2 * 60 * 60  # 2 hours
_MIN_DURATION_SEC = 2             # 2 seconds
_WINDOW_DAYS = 14
_MAX_HISTORY_ROWS = 30


# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------

@dataclass
class TimeStatus:
    """Snapshot of a single tracked operation.

    Fields
    ------
    op_id : str
        Unique identifier for this operation instance.
    op_kind : str
        Category of operation (e.g. ``"agent_spawn"``, ``"smoke_gate"``).
    started_at : float
        Unix timestamp (seconds) when the operation was registered.
    elapsed_sec : float
        Wall-clock seconds since ``started_at``.  Still ticking for
        running ops; frozen at ``finished_at - started_at`` for done ops.
    eta_sec : int | None
        Estimated seconds remaining.  None when there is no estimate.
    progress_pct : float
        Last reported progress, 0–100.
    current_step : str | None
        Human-readable label for the current step, if provided.
    status : str
        ``"running"`` while active; ``"completed"`` / ``"failed"`` /
        ``"cancelled"`` after ``finish()`` is called.
    """

    op_id: str
    op_kind: str
    started_at: float
    elapsed_sec: float
    eta_sec: Optional[int]
    progress_pct: float
    current_step: Optional[str]
    status: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db():
    """Indirection so tests can monkeypatch this function."""
    return _pdb.get_db()


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2)


def _compute_eta(
    hint_sec: Optional[int],
    op_kind: str,
    started_at: float,
    progress_pct: float,
) -> Optional[int]:
    """Return eta_sec given current state.

    Logic:
    1. If progress > 0, decay linearly: eta = elapsed / (pct/100) - elapsed.
    2. Otherwise use hint_sec if available.
    3. Otherwise use history median.
    4. If none of the above, return None.
    """
    now = time.time()
    elapsed = now - started_at

    if progress_pct > 0:
        # Linear decay from observed elapsed / progress fraction.
        estimated_total = elapsed / (progress_pct / 100.0)
        remaining = max(0.0, estimated_total - elapsed)
        return int(round(remaining))

    # No progress yet — fall back to hint or history.
    if hint_sec is not None:
        return max(0, hint_sec)

    hist = estimate(op_kind)
    if hist is not None:
        return max(0, hist)

    return None


def _row_to_status(row) -> TimeStatus:
    """Convert a DB row to a ``TimeStatus`` instance."""
    now = time.time()
    started_at = row["started_at"]
    finished_at = row["finished_at"]
    db_status = row["status"]  # may be None for running ops
    progress_pct = row["progress_pct"] or 0.0
    hint_sec = row["hint_sec"]
    op_kind = row["op_kind"]

    if finished_at is not None:
        elapsed = finished_at - started_at
        eta_sec = None  # done; no ETA needed
        status_str = db_status or "completed"
    else:
        elapsed = now - started_at
        status_str = "running"
        eta_sec = _compute_eta(hint_sec, op_kind, started_at, progress_pct)

    return TimeStatus(
        op_id=row["op_id"],
        op_kind=op_kind,
        started_at=started_at,
        elapsed_sec=elapsed,
        eta_sec=eta_sec,
        progress_pct=progress_pct,
        current_step=row["current_step"],
        status=status_str,
    )


# ---------------------------------------------------------------------------
# Public functions — v1.0 contract
# ---------------------------------------------------------------------------

def start(op_id: str, op_kind: str, hint_sec: Optional[int] = None) -> None:
    """Register a new operation.

    Parameters
    ----------
    op_id:
        Unique identifier for this operation instance.  Caller is
        responsible for uniqueness; duplicate calls upsert the row.
    op_kind:
        Category label used for history-based ETA (e.g. ``"agent_spawn"``).
    hint_sec:
        Optional caller-supplied duration hint in seconds.  Used as the
        initial ETA when no history exists for *op_kind*.
    """
    now = time.time()
    conn = _get_db()
    conn.execute(
        """
        INSERT INTO time_runs
            (op_id, op_kind, started_at, finished_at, status,
             hint_sec, progress_pct, current_step)
        VALUES (?, ?, ?, NULL, NULL, ?, 0.0, NULL)
        ON CONFLICT(op_id) DO UPDATE SET
            started_at   = excluded.started_at,
            finished_at  = NULL,
            status       = NULL,
            hint_sec     = excluded.hint_sec,
            progress_pct = 0.0,
            current_step = NULL
        """,
        (op_id, op_kind, now, hint_sec),
    )
    conn.commit()


def progress(
    op_id: str,
    pct: float,
    current_step: Optional[str] = None,
) -> None:
    """Report progress for a running operation.

    Silently ignored when *op_id* is not in the database.

    Parameters
    ----------
    op_id:
        The operation identifier passed to :func:`start`.
    pct:
        Progress percentage, 0–100.
    current_step:
        Optional human-readable label for the current step.
    """
    conn = _get_db()
    conn.execute(
        """
        UPDATE time_runs
           SET progress_pct  = ?,
               current_step  = ?
         WHERE op_id = ?
           AND finished_at IS NULL
        """,
        (pct, current_step, op_id),
    )
    conn.commit()


def finish(
    op_id: str,
    status: Literal["completed", "failed", "cancelled"],
) -> None:
    """Mark an operation as done.

    Parameters
    ----------
    op_id:
        The operation identifier passed to :func:`start`.
    status:
        Terminal state: ``"completed"``, ``"failed"``, or ``"cancelled"``.
    """
    now = time.time()
    conn = _get_db()
    conn.execute(
        """
        UPDATE time_runs
           SET finished_at = ?,
               status      = ?
         WHERE op_id = ?
        """,
        (now, status, op_id),
    )
    conn.commit()


def status(op_id: str) -> Optional[TimeStatus]:
    """Return the current status of an operation, or None if unknown.

    Parameters
    ----------
    op_id:
        The operation identifier passed to :func:`start`.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM time_runs WHERE op_id = ?", (op_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_status(row)


def estimate(op_kind: str) -> Optional[int]:
    """Return the rolling median duration for *op_kind* in seconds.

    Considers only ``"completed"`` runs within the last 14 days, capped
    at the most recent 30.  Applies 2 s floor and 2 h cap for outlier
    removal.  Returns None when there is no usable history.

    Parameters
    ----------
    op_kind:
        Category label to look up (e.g. ``"agent_spawn"``).
    """
    cutoff = time.time() - (_WINDOW_DAYS * 24 * 3600)
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT started_at, finished_at
          FROM time_runs
         WHERE op_kind    = ?
           AND status     = 'completed'
           AND finished_at IS NOT NULL
           AND finished_at >= ?
         ORDER BY finished_at DESC
         LIMIT ?
        """,
        (op_kind, cutoff, _MAX_HISTORY_ROWS),
    ).fetchall()

    durations: list[float] = []
    for row in rows:
        d = row["finished_at"] - row["started_at"]
        if d < _MIN_DURATION_SEC or d > _MAX_DURATION_SEC:
            continue
        durations.append(d)

    if not durations:
        return None

    return int(round(_median(durations)))


def all_running() -> list[TimeStatus]:
    """Return all currently-running operations.

    An operation is considered running when its ``finished_at`` is NULL
    (i.e. :func:`finish` has not been called yet).
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM time_runs WHERE finished_at IS NULL"
    ).fetchall()
    return [_row_to_status(r) for r in rows]

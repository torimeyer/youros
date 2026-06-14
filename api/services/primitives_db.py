"""Shared SQLite connection for the myOS primitives database.

All primitives that need persistent history use a single SQLite file at
``~/.youros/primitives.db``.  WAL mode is enabled at open so concurrent
writers (backend, subagents, ostk verbs) never hit "database is locked".

Schema migrations are applied lazily on first open using ``PRAGMA
user_version`` as a counter.  The SQL files live in ``api/migrations/``
alongside this module.

Public API
----------
``get_db() -> sqlite3.Connection``
    Return (and cache per-thread) an open, migrated connection.  The
    connection is set to ``Row`` row factory and has WAL mode enabled.

``PRIMITIVES_DB_PATH``
    The resolved ``Path`` to the database file.  Tests monkeypatch this
    attribute before calling ``get_db()`` so the real ``~/.youros/``
    directory is never touched during test runs.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from services.youros_paths import youros_home

# ---------------------------------------------------------------------------
# Database location (tests monkeypatch this before the first get_db() call)
# ---------------------------------------------------------------------------

PRIMITIVES_DB_PATH: Path = youros_home() / "primitives.db"

# ---------------------------------------------------------------------------
# Migration catalogue
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Ordered list of (target_version, sql_filename) pairs.
# A migration at index i bumps PRAGMA user_version from i to i+1.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, "001_time_runs.sql"),
]

# ---------------------------------------------------------------------------
# Per-thread connection cache
# ---------------------------------------------------------------------------

_local = threading.local()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Run any pending migrations against *conn*."""
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for target_version, filename in _MIGRATIONS:
        if current >= target_version:
            continue
        sql = (_MIGRATIONS_DIR / filename).read_text()
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {target_version}")
        conn.commit()
        current = target_version


def get_db() -> sqlite3.Connection:
    """Return a per-thread open connection to ``PRIMITIVES_DB_PATH``.

    The database file and its parent directory are created on first call.
    WAL mode and foreign-key enforcement are set at open time.  Migrations
    are applied lazily.

    Tests should monkeypatch ``PRIMITIVES_DB_PATH`` on this module AND call
    ``get_db()`` after the patch; the returned connection is cached per
    thread, so a reload or re-import is needed to pick up a new path.
    """
    # Use the module-level name so tests that monkeypatch it take effect.
    db_path: Path = PRIMITIVES_DB_PATH

    # Return cached connection for this thread if it is still open and
    # points at the current DB path.
    cached = getattr(_local, "conn", None)
    cached_path = getattr(_local, "db_path", None)
    if cached is not None and cached_path == db_path:
        try:
            cached.execute("SELECT 1")
            return cached
        except sqlite3.ProgrammingError:
            pass  # closed; fall through

    # Ensure parent directory exists.
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    _apply_migrations(conn)

    _local.conn = conn
    _local.db_path = db_path
    return conn

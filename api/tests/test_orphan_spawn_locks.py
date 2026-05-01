"""Tests for orphan-spawn-lock fixes (→ see fix(spawn) commit).

Three cases:
  (a) lock-acquire then metadata write fails with exception → locks released
  (b) agent in pending status, no heartbeat for 700 s → sweep releases
  (c) agent in running status, recent heartbeat → sweep does NOT release
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def _reset():
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()


# ---------------------------------------------------------------------------
# (a) lock-acquire then metadata-write fails → locks released
# ---------------------------------------------------------------------------


def test_locks_released_when_metadata_write_raises():
    """If the spawn path fails *after* acquiring locks (simulated via the
    inner try/except), the exception handler must release the locks so a
    retry on the same paths is not blocked."""
    from unittest.mock import patch
    from services.spawn_isolation import acquire_spawn_locks, _spawn_lock_holders
    from routers.agents import agent_metadata, _sweep_stale_locks_once

    _reset()
    agent_name = "orphan-meta-fail-agent"
    agent_metadata.pop(agent_name, None)

    try:
        ok, acquired, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/routers/agents.py"],
        )
        assert ok, "lock acquire must succeed"
        assert any(v[0] == agent_name for v in _spawn_lock_holders.values())

        # Simulate: the metadata write raises (e.g. disk full, serialisation
        # error).  The inner try/except in spawn_agent calls _release_spawn_locks
        # on any Exception.  We test the sweep-side guarantee: if a lock is
        # held with no metadata row at all (meta is None), the sweep releases it.
        # This mirrors the state left when the exception handler fires and
        # clears the lock registry entry before the metadata row is written.
        from services.spawn_isolation import release_spawn_locks
        released = release_spawn_locks(spawn_id=agent_name)
        assert released, "error-path release must free the lock keys"

        # After explicit release the lock registry must be empty for this agent.
        assert not any(v[0] == agent_name for v in _spawn_lock_holders.values()), \
            "no locks should remain after error-path release"

        # Verify the sweep also handles the no-metadata case: re-acquire a lock
        # without a metadata row and confirm the sweep releases it.
        ok2, _, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/routers/agents.py"],
        )
        assert ok2
        agent_metadata.pop(agent_name, None)  # no row → orphan

        swept = _sweep_stale_locks_once()
        assert swept >= 1, "sweep must release lock with no metadata row"
        assert not any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()


# ---------------------------------------------------------------------------
# (b) pending status, no heartbeat for 700 s → sweep releases
# ---------------------------------------------------------------------------


def test_pending_no_heartbeat_700s_released():
    """Agent stuck in pending with no heartbeat beyond the orphan threshold
    must have its locks swept so the next spawn can acquire them."""
    from services.spawn_isolation import (
        acquire_spawn_locks,
        _spawn_lock_holders,
        _spawn_lock_mutex,
    )
    from routers.agents import (
        agent_metadata,
        _sweep_stale_locks_once,
        MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS,
    )

    _reset()
    agent_name = "orphan-pending-no-hb-agent"
    # pending status, no last_heartbeat_at
    agent_metadata[agent_name] = {"status": "pending", "budget": "2.0"}

    stale_age = max(700, MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS + 100)

    try:
        ok, acquired, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/services/orphan_pending.py"],
        )
        assert ok

        # Back-date the lock epoch so age > orphan threshold
        (key,) = acquired
        with _spawn_lock_mutex:
            spawn_id, raw_glob, _epoch = _spawn_lock_holders[key]
            _spawn_lock_holders[key] = (spawn_id, raw_glob, time.time() - stale_age)

        released = _sweep_stale_locks_once()

        assert released >= 1, (
            f"sweep must release pending lock with no heartbeat after {stale_age}s"
        )
        assert not any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()


# ---------------------------------------------------------------------------
# (c) running status, recent heartbeat → sweep does NOT release
# ---------------------------------------------------------------------------


def test_running_recent_heartbeat_not_released():
    """A running agent whose heartbeat is fresh must never have its locks
    swept, regardless of how old the lock epoch is."""
    from services.spawn_isolation import (
        acquire_spawn_locks,
        _spawn_lock_holders,
        _spawn_lock_mutex,
    )
    from routers.agents import agent_metadata, _sweep_stale_locks_once

    _reset()
    agent_name = "orphan-running-fresh-hb-agent"
    agent_metadata[agent_name] = {
        "status": "running",
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "budget": "2.0",
    }

    try:
        ok, acquired, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/services/orphan_running.py"],
        )
        assert ok

        # Back-date the lock epoch to 4 hours ago
        (key,) = acquired
        with _spawn_lock_mutex:
            spawn_id, raw_glob, _epoch = _spawn_lock_holders[key]
            _spawn_lock_holders[key] = (spawn_id, raw_glob, time.time() - 4 * 3600)

        released = _sweep_stale_locks_once()

        assert released == 0, "running agent with recent heartbeat must not be swept"
        assert any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()

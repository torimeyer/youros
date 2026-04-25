"""Tests for spawn-lock TTL semantics and the GET /api/agents/spawn-locks endpoint.

Covers R2 from docs/brainstorm-throughput.md:
  * acquired_epoch is recorded in the tuple on lock acquisition
  * sweep releases locks for terminal-status agents
  * sweep does NOT release locks for running/pending agents
  * sweep releases orphan locks (no agent row) via TTL fallback
  * GET /api/agents/spawn-locks?paths= filters by path overlap
  * GET /api/agents/spawn-locks (no filter) returns all entries
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient


def _reset():
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()


# ---------------------------------------------------------------------------
# 1. acquired_epoch is recorded
# ---------------------------------------------------------------------------


def test_acquired_epoch_is_recorded():
    from services.spawn_isolation import acquire_spawn_locks, _spawn_lock_holders

    _reset()
    before = time.time()
    ok, acquired, _ = acquire_spawn_locks(
        spawn_id="ttl-epoch-agent",
        locks=["api/services/auth.py"],
    )
    after = time.time()

    assert ok
    assert acquired

    (key,) = acquired
    entry = _spawn_lock_holders[key]
    assert len(entry) == 3, "tuple must have (spawn_id, raw_glob, acquired_epoch)"
    spawn_id, raw_glob, epoch = entry
    assert spawn_id == "ttl-epoch-agent"
    assert raw_glob == "api/services/auth.py"
    assert before <= epoch <= after, f"epoch {epoch} not in [{before}, {after}]"

    _reset()


# ---------------------------------------------------------------------------
# 2. Sweep releases lock for a terminal agent
# ---------------------------------------------------------------------------


def test_release_for_terminal_agent():
    from services.spawn_isolation import acquire_spawn_locks, _spawn_lock_holders
    from routers.agents import agent_metadata, _sweep_stale_locks_once

    _reset()
    agent_name = "ttl-completed-agent"
    agent_metadata[agent_name] = {"status": "completed"}

    try:
        ok, _, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/services/auth.py"],
        )
        assert ok
        assert any(v[0] == agent_name for v in _spawn_lock_holders.values())

        released = _sweep_stale_locks_once()

        assert released >= 1
        assert not any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()


# ---------------------------------------------------------------------------
# 3. Running agent lock is NOT released even with an old epoch
# ---------------------------------------------------------------------------


def test_running_agent_lock_not_released():
    from services.spawn_isolation import (
        acquire_spawn_locks,
        _spawn_lock_holders,
        _spawn_lock_mutex,
    )
    from routers.agents import agent_metadata, _sweep_stale_locks_once

    _reset()
    agent_name = "ttl-running-agent"
    agent_metadata[agent_name] = {"status": "running"}

    try:
        ok, acquired, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/services/running.py"],
        )
        assert ok

        # Back-date the epoch to simulate a very old lock
        (key,) = acquired
        with _spawn_lock_mutex:
            spawn_id, raw_glob, _epoch = _spawn_lock_holders[key]
            _spawn_lock_holders[key] = (spawn_id, raw_glob, time.time() - 4 * 3600)

        released = _sweep_stale_locks_once()

        assert released == 0, "running agent lock must not be swept"
        assert any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()


# ---------------------------------------------------------------------------
# 4. TTL fallback releases orphan (no agent row, old epoch)
# ---------------------------------------------------------------------------


def test_ttl_fallback_releases_orphan():
    from services.spawn_isolation import (
        acquire_spawn_locks,
        _spawn_lock_holders,
        _spawn_lock_mutex,
    )
    from routers.agents import agent_metadata, _sweep_stale_locks_once

    _reset()
    agent_name = "ttl-orphan-agent"
    agent_metadata.pop(agent_name, None)  # ensure no row

    try:
        ok, acquired, _ = acquire_spawn_locks(
            spawn_id=agent_name,
            locks=["api/services/orphan.py"],
        )
        assert ok

        # Back-date the epoch to 4 hours ago
        (key,) = acquired
        with _spawn_lock_mutex:
            spawn_id, raw_glob, _epoch = _spawn_lock_holders[key]
            _spawn_lock_holders[key] = (spawn_id, raw_glob, time.time() - 4 * 3600)

        released = _sweep_stale_locks_once()

        assert released >= 1
        assert not any(v[0] == agent_name for v in _spawn_lock_holders.values())
    finally:
        agent_metadata.pop(agent_name, None)
        _reset()


# ---------------------------------------------------------------------------
# 5. GET /api/agents/spawn-locks?paths= filters by path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_locks_endpoint_filters_by_path():
    from services.spawn_isolation import acquire_spawn_locks
    from routers.agents import agent_metadata

    _reset()
    a_name, b_name = "ttl-ep-agent-foo", "ttl-ep-agent-bar"
    for n in (a_name, b_name):
        agent_metadata.pop(n, None)

    try:
        ok1, _, _ = acquire_spawn_locks(spawn_id=a_name, locks=["foo/widget.py"])
        ok2, _, _ = acquire_spawn_locks(spawn_id=b_name, locks=["bar/widget.py"])
        assert ok1 and ok2

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents/spawn-locks?paths=foo/widget.py")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        locks = body["locks"]
        spawns = [l["spawn"] for l in locks]
        assert a_name in spawns, f"foo lock missing: {spawns}"
        assert b_name not in spawns, f"bar lock should be filtered out: {spawns}"
    finally:
        for n in (a_name, b_name):
            agent_metadata.pop(n, None)
        _reset()


# ---------------------------------------------------------------------------
# 6. GET /api/agents/spawn-locks (no filter) returns all entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_locks_endpoint_returns_all_when_no_filter():
    from services.spawn_isolation import acquire_spawn_locks
    from routers.agents import agent_metadata

    _reset()
    a_name, b_name = "ttl-ep-all-alpha", "ttl-ep-all-beta"
    for n in (a_name, b_name):
        agent_metadata.pop(n, None)

    try:
        ok1, _, _ = acquire_spawn_locks(spawn_id=a_name, locks=["alpha/file.py"])
        ok2, _, _ = acquire_spawn_locks(spawn_id=b_name, locks=["beta/file.py"])
        assert ok1 and ok2

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents/spawn-locks")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        locks = body["locks"]
        spawns = [l["spawn"] for l in locks]
        assert a_name in spawns, f"alpha lock missing: {spawns}"
        assert b_name in spawns, f"beta lock missing: {spawns}"
        # Verify shape of each entry
        for lock in locks:
            assert "spawn" in lock
            assert "path" in lock
            assert "acquired_at" in lock
            assert "age_seconds" in lock
    finally:
        for n in (a_name, b_name):
            agent_metadata.pop(n, None)
        _reset()

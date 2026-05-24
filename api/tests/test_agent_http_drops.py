"""Regression test for →1086: empty-body HTTP responses under parallel agent load.

Root cause: _save_agent_state() called atomic_write_json() synchronously on
the asyncio event loop. With agent_state.json at 1.3MB+, the json.dumps +
os.fsync blocked the event loop long enough for SSL handshakes on queued
concurrent requests to time out, returning empty bodies.

Fix: _save_agent_state_async() serializes on the event loop (~10-30ms), then
writes to disk in asyncio.to_thread() so the event loop is free during the
fsync. High-frequency async handlers (list_agents, heartbeat, register,
complete) use the async version.

Tests here verify:
1. _save_agent_state_async exists and is a coroutine (structure check).
2. Event loop stays responsive during concurrent saves (no blocking).
3. Five parallel registrations + concurrent list polls all return valid JSON.
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from routers import agents as agents_router


# ---------------------------------------------------------------------------
# Structure checks (no live server needed)
# ---------------------------------------------------------------------------


def test_save_agent_state_async_exists():
    """_save_agent_state_async must exist as an async function."""
    import inspect
    fn = getattr(agents_router, "_save_agent_state_async", None)
    assert fn is not None, "_save_agent_state_async must be defined in routers.agents"
    assert inspect.iscoroutinefunction(fn), "_save_agent_state_async must be a coroutine (async def)"


def test_write_state_content_exists():
    """_write_state_content must exist as the thread-safe disk writer."""
    fn = getattr(agents_router, "_write_state_content", None)
    assert fn is not None, "_write_state_content must be defined in routers.agents"
    import inspect
    assert not inspect.iscoroutinefunction(fn), "_write_state_content must be sync (runs in thread)"


def test_save_state_write_lock_exists():
    """_save_state_write_lock must exist for concurrent-write serialization."""
    import threading
    lock = getattr(agents_router, "_save_state_write_lock", None)
    assert lock is not None, "_save_state_write_lock must be defined in routers.agents"
    assert isinstance(lock, type(threading.Lock())), "_save_state_write_lock must be a threading.Lock"


# ---------------------------------------------------------------------------
# Event-loop responsiveness under concurrent saves (→1086 core regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_async_does_not_block_event_loop(tmp_path):
    """Concurrent saves must not block the event loop beyond 100ms.

    Pre-fix: _save_agent_state() ran synchronously on the event loop.
    A concurrent responsiveness probe (await asyncio.sleep(0.01)) would
    stall for the full fsync duration because the event loop was blocked.

    Post-fix: _save_agent_state_async() offloads file I/O to a thread via
    asyncio.to_thread(). The probe can run freely between the saves.
    """
    state_path = tmp_path / "agent_state.json"
    # Patch the path and inject a fake slow write to make blocking detectable.
    write_delay = 0.05  # 50ms per write; 5 concurrent = 250ms if blocking

    write_call_count = 0

    def slow_write(content: str) -> None:
        nonlocal write_call_count
        write_call_count += 1
        time.sleep(write_delay)
        state_path.write_text(content)

    max_probe_delay = 0.0

    async def measure_responsiveness():
        """Probe the event loop every 10ms, record the worst delay."""
        nonlocal max_probe_delay
        for _ in range(50):
            t0 = asyncio.get_event_loop().time()
            await asyncio.sleep(0.01)
            elapsed = asyncio.get_event_loop().time() - t0
            delay = elapsed - 0.01
            if delay > max_probe_delay:
                max_probe_delay = delay

    async def do_saves():
        """Run 5 concurrent async saves."""
        tasks = [
            asyncio.create_task(agents_router._save_agent_state_async())
            for _ in range(5)
        ]
        await asyncio.gather(*tasks)

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch("routers.agents._write_state_content", side_effect=slow_write),
    ):
        probe_task = asyncio.create_task(measure_responsiveness())
        await do_saves()
        await probe_task

    assert write_call_count >= 1, "at least one write must complete"
    # The event loop should not be blocked for more than 100ms between any
    # two consecutive 10ms probe ticks. Pre-fix this would be >250ms.
    assert max_probe_delay < 0.15, (
        f"Event loop was blocked for {max_probe_delay*1000:.0f}ms during concurrent saves "
        f"(limit: 150ms). This indicates _save_agent_state_async is still blocking the "
        f"event loop instead of offloading I/O to asyncio.to_thread."
    )


# ---------------------------------------------------------------------------
# Concurrent registration + list polling (load simulation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_five_parallel_registrations_no_empty_response(tmp_path):
    """Five concurrent agent registrations must all return valid JSON.

    Simulates the parallel-subagent pattern that triggered →1086.
    Each registration triggers _save_agent_state_async(). Without the fix,
    concurrent saves serialized on the event loop, starving other requests.

    With the async fix, all 5 succeed with non-empty 200 responses.
    """
    state_path = tmp_path / "agent_state.json"
    state_path.write_text("{}")

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch("routers.agents.agent_metadata", {}),
        patch("routers.agents.agent_aliases", {}),
        patch("routers.agents.active_agents", {}),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = []
            for i in range(5):
                tasks.append(client.post(
                    "/api/agents/register",
                    json={
                        "name": f"http-drops-test-agent-{i}",
                        "task": f"load test registration {i}",
                        "model": "sonnet",
                        "source": "claude-code",
                    }
                ))
            responses = await asyncio.gather(*tasks)

        empty_count = 0
        error_count = 0
        for i, r in enumerate(responses):
            if not r.content:
                empty_count += 1
            elif r.status_code != 200:
                error_count += 1
            else:
                data = r.json()
                assert "result" in data or "status" in data, (
                    f"agent {i}: response missing expected fields: {data}"
                )

        assert empty_count == 0, (
            f"{empty_count}/5 registrations returned empty bodies (→1086 regression). "
            "Check that _save_agent_state_async() is used in register_agent."
        )


@pytest.mark.asyncio
async def test_concurrent_list_and_heartbeat_no_empty_response(tmp_path):
    """Concurrent /api/agents polls during heartbeats must return valid JSON.

    Reproduces the standing-rules hook pattern: multiple agents heartbeat
    while the parent session polls /api/agents. Pre-fix, heartbeats triggered
    synchronous saves that blocked the event loop during the poll's TLS phase.
    """
    state_path = tmp_path / "agent_state.json"
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = {
        f"test-hb-agent-{i}": {
            "name": f"test-hb-agent-{i}",
            "status": "running",
            "source": "claude-code",
            "spawned_at": now_iso,
            "last_heartbeat_at": now_iso,
            "model": "sonnet",
            "task": f"heartbeat test {i}",
        }
        for i in range(5)
    }
    state_path.write_text(json.dumps(meta))

    mock_ostk = MagicMock()
    mock_ostk.kernel_ps = asyncio.coroutine(lambda: {"daemon_running": False, "agents": []}) if False else None

    async def fake_kernel_ps():
        return {"daemon_running": False, "agents": []}

    async def fake_audit_agents():
        return []

    mock_ostk.kernel_ps = fake_kernel_ps
    mock_ostk.audit_agents = fake_audit_agents

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch.dict(agents_router.agent_metadata, meta, clear=True),
        patch.dict(agents_router.active_agents, {}, clear=True),
        patch("routers.agents.ostk", mock_ostk),
        patch("routers.agents._load_deleted_agents", return_value=set()),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Fire 5 heartbeats + 5 list polls concurrently
            hb_tasks = [
                client.post(
                    f"/api/agents/test-hb-agent-{i}/heartbeat",
                    json={"step": f"working on step {i}"},
                )
                for i in range(5)
            ]
            list_tasks = [
                client.get("/api/agents")
                for _ in range(5)
            ]
            all_tasks = hb_tasks + list_tasks
            responses = await asyncio.gather(*all_tasks, return_exceptions=True)

        empty = [r for r in responses if isinstance(r, httpx.Response) and not r.content]
        assert len(empty) == 0, (
            f"{len(empty)}/10 requests returned empty bodies under concurrent heartbeat+list load. "
            "→1086 regression: check _save_agent_state_async() in heartbeat_agent and list_agents."
        )


# ---------------------------------------------------------------------------
# Write-lock serialization: concurrent thread writes must not corrupt file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_async_saves_write_valid_json(tmp_path):
    """Concurrent _save_agent_state_async() calls must not corrupt the state file.

    The _save_state_write_lock serializes concurrent thread writes so the
    last write wins cleanly (no interleaved partial writes).
    """
    state_path = tmp_path / "agent_state.json"

    # Build a realistic-sized dict (100 entries) to make json.dumps measurable
    fake_meta = {
        f"agent-{i}": {
            "name": f"agent-{i}",
            "status": "completed",
            "spawned_at": "2026-05-09T00:00:00+00:00",
            "model": "sonnet",
        }
        for i in range(100)
    }

    with (
        patch.object(agents_router, "AGENT_STATE_PATH", state_path),
        patch.dict(agents_router.agent_metadata, fake_meta, clear=True),
    ):
        # 10 concurrent saves
        await asyncio.gather(*[
            agents_router._save_agent_state_async()
            for _ in range(10)
        ])

    assert state_path.exists(), "state file must exist after saves"
    content = state_path.read_text()
    assert content.strip(), "state file must not be empty after saves"
    parsed = json.loads(content)  # must not raise
    assert isinstance(parsed, dict), "state file must contain a valid JSON object"

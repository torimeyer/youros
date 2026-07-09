"""Tests for agent-list scan timeout and skip-if-running guard.

→2224: _agents_snapshot_loop wraps scan in a 5-second timeout.
→2225: on timeout, logs timestamp + how many agents were processed.
→2226: if a previous scan is still running, next cycle marks itself skipped.
"""
import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_scan_timeout_cancels_slow_scan():
    """→2224: snapshot loop cancels the scan after _SCAN_TIMEOUT_SECONDS."""
    import routers.agents as agents_mod

    # Reset snapshot to cold state.
    async with agents_mod._snapshot_lock:
        agents_mod._cached_snapshot.update(
            {"agents": [], "computed_at": None, "daemon_running": False}
        )
    agents_mod._snapshot_scan_active = False

    scan_started = asyncio.Event()

    async def slow_scan(run_autocomplete=True):
        scan_started.set()
        await asyncio.sleep(999)
        return {"agents": [], "computed_at": "never", "daemon_running": False}

    with patch.object(agents_mod, "_compute_agents_snapshot_async", new=slow_scan), \
         patch.object(agents_mod, "_SCAN_TIMEOUT_SECONDS", 0.05):
        # Run the loop just long enough for the timeout to fire (> 0.05s).
        try:
            await asyncio.wait_for(agents_mod._agents_snapshot_loop(), timeout=0.4)
        except asyncio.TimeoutError:
            pass

    # Scan was started but timed out — snapshot should never have been updated.
    assert scan_started.is_set(), "scan was never started"
    async with agents_mod._snapshot_lock:
        assert agents_mod._cached_snapshot.get("computed_at") is None, (
            "snapshot was updated despite timeout — scan should have been cancelled"
        )


@pytest.mark.asyncio
async def test_scan_timeout_logs_timestamp_and_agent_count(caplog):
    """→2225: on timeout the log includes an ISO timestamp and agents_processed count."""
    import routers.agents as agents_mod

    agents_mod._snapshot_scan_active = False
    agents_mod._scan_agents_processed = 7  # pre-load a non-zero value to assert

    async def slow_scan(run_autocomplete=True):
        # Simulate 7 agents already processed (counter set before sleep).
        agents_mod._scan_agents_processed = 7
        await asyncio.sleep(999)
        return {"agents": [], "computed_at": "never", "daemon_running": False}

    with patch.object(agents_mod, "_compute_agents_snapshot_async", new=slow_scan), \
         patch.object(agents_mod, "_SCAN_TIMEOUT_SECONDS", 0.05), \
         caplog.at_level(logging.WARNING, logger="routers.agents"):
        try:
            await asyncio.wait_for(agents_mod._agents_snapshot_loop(), timeout=0.4)
        except asyncio.TimeoutError:
            pass

    timeout_records = [r for r in caplog.records if "scan.timeout" in r.getMessage()]
    assert timeout_records, "no scan.timeout log entry found after timeout"
    msg = timeout_records[0].getMessage()
    # Must contain a timestamp fragment and the agent count.
    assert "agents_processed=7" in msg or "7" in msg, (
        f"log message missing agent count: {msg!r}"
    )
    # Must contain an ISO-ish timestamp (at minimum the year).
    assert "2026" in msg or "2025" in msg or "ts=" in msg, (
        f"log message missing timestamp: {msg!r}"
    )


@pytest.mark.asyncio
async def test_scan_skips_cycle_when_previous_scan_running(caplog):
    """→2226: if _snapshot_scan_active is True, the next cycle skips the scan."""
    import routers.agents as agents_mod

    scan_call_count = 0

    async def counting_scan(run_autocomplete=True):
        nonlocal scan_call_count
        scan_call_count += 1
        return {"agents": [], "computed_at": "x", "daemon_running": False}

    # Simulate a scan already in flight.
    agents_mod._snapshot_scan_active = True

    try:
        with patch.object(agents_mod, "_compute_agents_snapshot_async", new=counting_scan), \
             patch.object(agents_mod, "_SCAN_TIMEOUT_SECONDS", 5.0), \
             caplog.at_level(logging.WARNING, logger="routers.agents"):
            try:
                await asyncio.wait_for(agents_mod._agents_snapshot_loop(), timeout=0.3)
            except asyncio.TimeoutError:
                pass
    finally:
        agents_mod._snapshot_scan_active = False

    assert scan_call_count == 0, (
        f"scan was called {scan_call_count} time(s) even though _snapshot_scan_active was True"
    )
    skip_records = [r for r in caplog.records if "scan.skipped" in r.getMessage() or "skipped" in r.getMessage().lower()]
    assert skip_records, "no skip log entry found when previous scan was running"

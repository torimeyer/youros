"""Regression tests for the in-flight call coalescing in services.ostk.

Before the coalescer, two concurrent handlers that each called
``list_tasks()`` or ``kernel_ps()`` would each spawn their own
``ostk`` subprocess. On Tori's workspace with ~280 tasks that meant
every page load paid two or three subprocess spawns instead of one.

These tests lock in:
1. Two concurrent calls with identical args share ONE underlying call.
2. Serial calls are independent (no stale-result leak).
3. Exceptions in the shared call propagate to every awaiter.
"""

from __future__ import annotations

import asyncio

import pytest

from services.ostk import OstkService, _coalesce_call, _inflight_calls


@pytest.fixture(autouse=True)
def _reset_inflight_after_test():
    """Guarantee a clean coalescing state between tests."""
    _inflight_calls.clear()
    yield
    _inflight_calls.clear()


@pytest.mark.asyncio
async def test_coalesce_shares_one_call_between_concurrent_waiters():
    call_count = {"n": 0}

    async def producer() -> int:
        call_count["n"] += 1
        await asyncio.sleep(0.05)
        return 42

    # Fire four concurrent callers with the same key. All four must
    # receive the same result and only ONE producer invocation must
    # have happened.
    key = ("test", "same-key")
    results = await asyncio.gather(
        _coalesce_call(key, producer),
        _coalesce_call(key, producer),
        _coalesce_call(key, producer),
        _coalesce_call(key, producer),
    )

    assert results == [42, 42, 42, 42]
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_coalesce_serial_calls_do_not_share_state():
    """A serial call after the first finishes must trigger a fresh
    producer run. Otherwise the coalescer would act like a permanent
    cache and leak stale results.
    """
    call_count = {"n": 0}

    async def producer() -> int:
        call_count["n"] += 1
        return call_count["n"]

    key = ("test", "serial")
    first = await _coalesce_call(key, producer)
    second = await _coalesce_call(key, producer)

    assert first == 1
    assert second == 2
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_coalesce_propagates_exception_to_all_waiters():
    class Boom(Exception):
        pass

    async def producer() -> int:
        await asyncio.sleep(0.01)
        raise Boom("the producer died")

    key = ("test", "exceptional")
    results = await asyncio.gather(
        _coalesce_call(key, producer),
        _coalesce_call(key, producer),
        _coalesce_call(key, producer),
        return_exceptions=True,
    )

    assert all(isinstance(r, Boom) for r in results)
    # After the failure the key is freed so subsequent callers try
    # their own producer.
    assert key not in _inflight_calls


@pytest.mark.asyncio
async def test_list_tasks_coalesces_concurrent_calls(monkeypatch, tmp_path):
    """Two concurrent list_tasks() calls on the same OstkService must
    result in exactly ONE call to the underlying _run_json subprocess
    wrapper.
    """
    # Isolate from the real .ostk/needles/issues.jsonl: list_tasks filters its
    # results against the active store, which would drop these fake ids.
    svc = OstkService(cwd=str(tmp_path))
    run_count = {"n": 0}

    async def fake_run_json(*args: str):
        run_count["n"] += 1
        await asyncio.sleep(0.02)
        return [{"id": "1", "title": "t"}]

    monkeypatch.setattr(svc, "_run_json", fake_run_json)

    results = await asyncio.gather(
        svc.list_tasks(),
        svc.list_tasks(),
        svc.list_tasks(),
    )

    assert all(len(r) == 1 and r[0]["id"] == "1" for r in results)
    assert run_count["n"] == 1


@pytest.mark.asyncio
async def test_list_tasks_returns_independent_copies(monkeypatch, tmp_path):
    """If one caller mutates the returned list, the other caller's
    copy must not see the mutation. Protects against the coalesced
    callers accidentally sharing mutable state.
    """
    # Isolate from the real .ostk/needles/issues.jsonl: list_tasks filters its
    # results against the active store, which would drop these fake ids.
    svc = OstkService(cwd=str(tmp_path))

    async def fake_run_json(*args: str):
        return [{"id": "a"}, {"id": "b"}]

    monkeypatch.setattr(svc, "_run_json", fake_run_json)

    r1, r2 = await asyncio.gather(svc.list_tasks(), svc.list_tasks())
    r1.clear()
    # r2 must still have its contents.
    assert len(r2) == 2

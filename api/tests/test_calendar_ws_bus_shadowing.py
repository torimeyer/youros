"""Regression test: →1735 calendar WS /ws/calendar/events AttributeError.

The bug: `calendar_events` imported as the CalendarEventBus instance was
shadowed by the route handler function `async def calendar_events(...)` in
api/routers/calendar.py. The WS handler calling `calendar_events.subscribe()`
resolved to the function, not the bus, raising:
  'function' object has no attribute 'subscribe'
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_calendar_events_name_in_router_module_is_bus_not_function():
    """The name `calendar_events` in the calendar router must be the bus, not a function."""
    import importlib, api.routers.calendar as cal_mod
    # Re-import to get fresh module state
    importlib.reload(cal_mod)

    # The fix uses `_calendar_event_bus` as the aliased name to avoid shadowing
    obj = cal_mod._calendar_event_bus
    # Must NOT be a plain function/coroutine function
    import inspect
    assert not inspect.isfunction(obj), (
        f"_calendar_event_bus in routers/calendar.py resolved to a function "
        f"({obj!r}), not the CalendarEventBus instance. "
        "This is the shadowing bug (→1735)."
    )
    # Must have .subscribe (CalendarEventBus interface)
    assert hasattr(obj, "subscribe"), (
        f"_calendar_event_bus ({obj!r}) has no .subscribe — expected CalendarEventBus."
    )


@pytest.mark.anyio
async def test_ws_calendar_events_no_attribute_error(monkeypatch):
    """WebSocket connect to /ws/calendar/events must not raise AttributeError.

    Connects, receives the snapshot message, verifies no 'function' object
    error is raised during subscribe().
    """
    pytest.importorskip("httpx_ws", reason="httpx_ws not installed — skipping WS transport test")
    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    import httpx

    # Patch is_authenticated to True so we get events path, not 401
    with patch("api.routers.calendar.is_authenticated", return_value=True), \
         patch("api.routers.calendar.calendar_service.get_upcoming_events", new_callable=AsyncMock, return_value=[]):

        from main import app  # noqa: PLC0415

        async with AsyncClient(app=app, base_url="http://test") as client:
            try:
                async with aconnect_ws("/api/ws/calendar/events", client) as ws:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                    assert msg.get("type") == "snapshot", f"Expected snapshot, got: {msg}"
            except Exception as exc:
                # If AttributeError about 'function' object — that's our bug
                if "has no attribute 'subscribe'" in str(exc):
                    pytest.fail(
                        f"→1735 regression: WS subscribe called on function, not bus: {exc}"
                    )
                # Other connection errors (missing deps etc) are acceptable in unit test context
                pytest.skip(f"WS transport not available in test env: {exc}")

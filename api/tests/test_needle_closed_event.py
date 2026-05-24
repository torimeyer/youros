"""Tests that closing a task publishes a needle_closed event on the bus."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_close_task_publishes_needle_closed(monkeypatch):
    """close_task must publish needle_closed on the notifications bus."""
    import routers.tasks as tasks_mod

    fake_ostk = MagicMock()
    fake_ostk.close_task = AsyncMock(return_value="closed")
    monkeypatch.setattr(tasks_mod, "ostk", fake_ostk, raising=False)

    monkeypatch.setattr(
        tasks_mod,
        "_advance_spec_status_if_all_builder_tasks_closed_async",
        AsyncMock(),
        raising=False,
    )

    publish_calls: list[tuple] = []

    async def fake_publish(event_type: str, payload: dict) -> None:
        publish_calls.append((event_type, payload))

    monkeypatch.setattr(
        tasks_mod._notifications_events_bus, "publish", fake_publish, raising=False
    )

    from models.schemas import TaskClose
    await tasks_mod.close_task("1618", TaskClose(reason="completed"), source="user")

    assert len(publish_calls) == 1
    event_type, payload = publish_calls[0]
    assert event_type == "needle_closed"
    assert "→1618" in payload["task_id"]


@pytest.mark.asyncio
async def test_close_task_needle_closed_payload_has_task_id(monkeypatch):
    """needle_closed payload always includes task_id."""
    import routers.tasks as tasks_mod

    fake_ostk = MagicMock()
    fake_ostk.close_task = AsyncMock(return_value="closed")
    monkeypatch.setattr(tasks_mod, "ostk", fake_ostk, raising=False)

    monkeypatch.setattr(
        tasks_mod,
        "_advance_spec_status_if_all_builder_tasks_closed_async",
        AsyncMock(),
        raising=False,
    )

    received: list[dict] = []

    async def fake_publish(event_type: str, payload: dict) -> None:
        if event_type == "needle_closed":
            received.append(payload)

    monkeypatch.setattr(
        tasks_mod._notifications_events_bus, "publish", fake_publish, raising=False
    )

    from models.schemas import TaskClose
    await tasks_mod.close_task("42", TaskClose(), source="user")

    assert received, "no needle_closed event published"
    assert "task_id" in received[0]

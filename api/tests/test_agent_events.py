import asyncio
import pytest
from services.agent_events import AgentEvent, AgentEventBus


@pytest.mark.asyncio
async def test_publish_delivers_to_single_subscriber():
    bus = AgentEventBus()
    async with bus.subscribe() as q:
        await bus.publish("delta", {"running_count": 1})
        event = q.get_nowait()
    assert event.type == "delta"
    assert event.payload == {"running_count": 1}


@pytest.mark.asyncio
async def test_publish_delivers_to_multiple_subscribers():
    bus = AgentEventBus()
    async with bus.subscribe() as q1:
        async with bus.subscribe() as q2:
            await bus.publish("snapshot", {"agents": []})
            e1 = q1.get_nowait()
            e2 = q2.get_nowait()
    assert e1.type == e2.type == "snapshot"
    assert e1.payload == e2.payload == {"agents": []}


@pytest.mark.asyncio
async def test_unsubscribed_queue_gets_no_events():
    bus = AgentEventBus()
    async with bus.subscribe() as q:
        pass  # exited: subscriber removed
    await bus.publish("delta", {"running_count": 0})
    assert q.empty()


@pytest.mark.asyncio
async def test_queue_full_does_not_raise():
    bus = AgentEventBus()
    async with bus.subscribe() as q:
        # fill to maxsize
        for i in range(100):
            await bus.publish("delta", {"i": i})
        # one more beyond maxsize — must not raise
        await bus.publish("delta", {"overflow": True})
        assert q.full()


@pytest.mark.asyncio
async def test_subscriber_count_reflects_live_subscriptions():
    bus = AgentEventBus()
    assert bus.subscriber_count == 0
    async with bus.subscribe():
        assert bus.subscriber_count == 1
        async with bus.subscribe():
            assert bus.subscriber_count == 2
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0

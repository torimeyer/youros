"""Tests for the inline chat acknowledgment bot.

The ack bot guarantees that every user nudge to a running agent is
acknowledged within two seconds, even when the real subagent is locked
inside a long tool call. These tests lock that contract:

* The bot replies within two seconds of a nudge landing.
* The canned responses rotate rather than repeating the same sentence.
* The bot exits as soon as the agent reaches a terminal status.
* The bot never shadow-acks a real subagent reply that arrives after
  the user's last nudge.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Import path setup matches the other agent tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_router  # noqa: E402
from services import chat_ack_bot  # noqa: E402


def _reset(name: str) -> None:
    agents_router.agent_metadata.pop(name, None)
    agents_router.nudge_history.pop(name, None)
    agents_router.nudge_replies.pop(name, None)
    chat_ack_bot._last_ack_ts.pop(name, None)
    chat_ack_bot._acked_nudge_ids.pop(name, None)
    existing = chat_ack_bot._agent_ack_tasks.pop(name, None)
    if existing is not None and not existing.done():
        existing.cancel()


@pytest.mark.asyncio
async def test_ack_bot_acks_within_2s_of_nudge():
    """A nudge posted while the bot is running must be acked fast.

    We register an agent, start the bot, then drop a nudge into
    ``nudge_history`` to simulate a user message landing. The bot
    polls every two seconds, so we expect a reply in under 2.5s.
    """
    name = "ack-fast"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
        with patch.object(
            agents_router.ostk,
            "append_nudge_reply",
            new=AsyncMock(
                side_effect=lambda agent, message, in_reply_to=None: {
                    "message": message,
                    "timestamp": "2026-04-15T12:00:01+00:00",
                    "in_reply_to": in_reply_to,
                    "source": "agent",
                }
            ),
        ):
            task = chat_ack_bot.start(name)
            try:
                # Drop a nudge after the bot has started. The bot's
                # first cycle runs immediately, so queue this on a
                # fresh timestamp newer than whatever it might see.
                agents_router.nudge_history[name] = [
                    {
                        "message": "hey, any update?",
                        "timestamp": "2026-04-15T12:00:00+00:00",
                        "source": "ui",
                    }
                ]

                start_t = time.monotonic()
                # Wait up to three seconds for the ack to appear. In
                # practice the first cycle runs right away so this
                # usually returns in well under a second.
                deadline = start_t + 3.0
                while time.monotonic() < deadline:
                    replies = agents_router.nudge_replies.get(name, [])
                    if replies:
                        break
                    await asyncio.sleep(0.05)
                elapsed = time.monotonic() - start_t
            finally:
                chat_ack_bot.stop(name)
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

    replies = agents_router.nudge_replies.get(name, [])
    _reset(name)

    assert replies, "ack bot never replied within 3s"
    assert replies[0]["kind"] == "ack", "reply must be tagged kind=ack"
    assert replies[0]["message"] in chat_ack_bot.ACK_RESPONSES
    assert elapsed < 2.5, f"ack took {elapsed:.2f}s, expected under 2s"


@pytest.mark.asyncio
async def test_ack_bot_rotates_canned_responses():
    """Back to back nudges must get different canned responses.

    We drive the ack helper directly so the rotation is deterministic
    without waiting on the real poll interval.
    """
    import itertools

    name = "ack-rotate"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    async def _append(agent, message, in_reply_to=None, kind=None):
        return {
            "message": message,
            "timestamp": in_reply_to or "",
            "source": "agent",
        }

    with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
        with patch.object(
            agents_router.ostk,
            "append_nudge_reply",
            new=AsyncMock(side_effect=_append),
        ):
            counter = itertools.count()
            # First nudge
            agents_router.nudge_history[name] = [
                {"message": "one", "timestamp": "2026-04-15T12:00:01+00:00", "source": "ui"},
            ]
            await chat_ack_bot._ack_once(name, counter)
            # Second nudge (newer timestamp so the bot sees it as new)
            agents_router.nudge_history[name] = [
                {"message": "one", "timestamp": "2026-04-15T12:00:01+00:00", "source": "ui"},
                {"message": "two", "timestamp": "2026-04-15T12:00:02+00:00", "source": "ui"},
            ]
            await chat_ack_bot._ack_once(name, counter)
            # Third nudge
            agents_router.nudge_history[name].append(
                {"message": "three", "timestamp": "2026-04-15T12:00:03+00:00", "source": "ui"},
            )
            await chat_ack_bot._ack_once(name, counter)

    replies = agents_router.nudge_replies.get(name, [])
    _reset(name)

    assert len(replies) == 3, f"expected three acks, got {len(replies)}"
    messages = [r["message"] for r in replies]
    assert messages[0] == chat_ack_bot.ACK_RESPONSES[0]
    assert messages[1] == chat_ack_bot.ACK_RESPONSES[1]
    assert messages[2] == chat_ack_bot.ACK_RESPONSES[2]
    # All distinct because the rotation advances each cycle.
    assert len(set(messages)) == 3


@pytest.mark.asyncio
async def test_ack_bot_exits_when_agent_completes():
    """Flipping the agent's status to completed must end the loop.

    The bot reads ``agent_metadata`` on every cycle. When the status
    becomes terminal the coroutine returns and its task finishes.
    """
    name = "ack-exit"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
        # Use a short poll interval so the test finishes quickly.
        original_interval = chat_ack_bot.ACK_POLL_INTERVAL_SECONDS
        chat_ack_bot.ACK_POLL_INTERVAL_SECONDS = 0.05
        try:
            task = chat_ack_bot.start(name)
            # Let the bot run a couple of cycles, then mark complete.
            await asyncio.sleep(0.15)
            assert not task.done(), "bot should still be running while agent is running"
            agents_router.agent_metadata[name]["status"] = "completed"
            # Wait for the next cycle to notice and exit.
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()
                raise AssertionError("ack bot did not exit after status flipped to completed")
        finally:
            chat_ack_bot.ACK_POLL_INTERVAL_SECONDS = original_interval
            _reset(name)

    assert task.done()
    # The task must not be in an error state either.
    if task.exception() is not None:
        raise AssertionError(f"ack bot raised: {task.exception()!r}")


@pytest.mark.asyncio
async def test_ack_bot_does_not_shadow_agent_real_reply():
    """A second call with no newer nudge must not post another ack.

    Simulates the flow where the user nudges once, the bot acks, the
    real subagent finally posts its own answer, and no new user nudge
    has arrived. The bot's next cycle must be a no-op.
    """
    import itertools

    name = "ack-no-shadow"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    async def _append(agent, message, in_reply_to=None, kind=None):
        return {
            "message": message,
            "timestamp": "2026-04-15T12:00:30+00:00",
            "source": "agent",
            "in_reply_to": in_reply_to,
        }

    with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
        with patch.object(
            agents_router.ostk,
            "append_nudge_reply",
            new=AsyncMock(side_effect=_append),
        ):
            counter = itertools.count()

            # First user nudge lands.
            agents_router.nudge_history[name] = [
                {
                    "message": "quick question",
                    "timestamp": "2026-04-15T12:00:05+00:00",
                    "source": "ui",
                }
            ]
            await chat_ack_bot._ack_once(name, counter)

            # Real subagent's answer lands via the normal /reply path.
            # We model that by appending to nudge_replies directly.
            agents_router.nudge_replies[name].append(
                {
                    "message": "the real detailed answer",
                    "timestamp": "2026-04-15T12:00:20+00:00",
                    "source": "agent",
                    "kind": "real",
                }
            )

            # Bot runs another cycle. No new nudge has arrived, so it
            # must not post another reply.
            await chat_ack_bot._ack_once(name, counter)
            await chat_ack_bot._ack_once(name, counter)

    replies = agents_router.nudge_replies.get(name, [])
    _reset(name)

    # Exactly one ack plus the real reply. No shadow acks.
    ack_replies = [r for r in replies if r.get("kind") == "ack"]
    real_replies = [r for r in replies if r.get("kind") == "real"]
    assert len(ack_replies) == 1, (
        f"expected one ack, got {len(ack_replies)}: "
        f"{[r['message'] for r in ack_replies]}"
    )
    assert len(real_replies) == 1, "real agent reply should be preserved"


@pytest.mark.asyncio
async def test_ack_bot_startup_backfills_running_agents():
    """A cold-start backend must start bots for every running subagent.

    Simulates the uvicorn restart flow: ``agent_metadata`` is already
    populated (the disk hydrate happens at router import), the ack
    bot registry is empty, and the startup hook calls
    ``start_for_running_agents``. Every running subagent row should
    end up with an active bot and the returned dict should list them.
    """
    names = ["backfill-cc", "backfill-sub"]
    for n in names:
        _reset(n)
    # A running Claude Code subagent, a running "subagent" source row,
    # a completed row that must be skipped, and an unknown-source row
    # that must also be skipped.
    agents_router.agent_metadata["backfill-cc"] = {
        "status": "running", "source": "claude-code",
    }
    agents_router.agent_metadata["backfill-sub"] = {
        "status": "running", "source": "subagent",
    }
    agents_router.agent_metadata["backfill-done"] = {
        "status": "completed", "source": "claude-code",
    }
    agents_router.agent_metadata["backfill-cli"] = {
        "status": "running", "source": "cli",
    }

    try:
        with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
            result = chat_ack_bot.start_for_running_agents()
            # Let the event loop schedule the tasks so is_active is true
            await asyncio.sleep(0)

            # Must include our two test agents. Other real agents may
            # also be in agent_metadata from the running backend so we
            # check subset rather than exact equality.
            started_or_active = set(result["started"]) | set(result["already_active"])
            assert set(names) <= started_or_active
            # Both test agents must be freshly started (they had no prior bot).
            assert set(names) <= set(result["started"])
            for n in names:
                assert chat_ack_bot.is_active(n), f"{n} should have an active ack bot"
            assert not chat_ack_bot.is_active("backfill-done")
            assert not chat_ack_bot.is_active("backfill-cli")
    finally:
        for n in names + ["backfill-done", "backfill-cli"]:
            chat_ack_bot.stop(n)
            agents_router.agent_metadata.pop(n, None)
            _reset(n)


@pytest.mark.asyncio
async def test_ack_bot_start_is_idempotent():
    """Calling start twice must return the same task and not race.

    The backfill path blindly calls ``start`` for every running agent
    so it can be invoked safely alongside spawn_agent's own start
    call. The second call must return the existing task without
    leaking a second coroutine.
    """
    name = "ack-idem"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    try:
        with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
            first = chat_ack_bot.start(name)
            second = chat_ack_bot.start(name)
            third = chat_ack_bot.start(name)
            assert first is second is third, "start must return the same task"
            assert chat_ack_bot._agent_ack_tasks[name] is first
    finally:
        chat_ack_bot.stop(name)
        try:
            await asyncio.wait_for(first, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _reset(name)


@pytest.mark.asyncio
async def test_ack_bot_backfill_endpoint_starts_missing_bots():
    """The admin endpoint must turn the feature on without a restart.

    Posts to ``/api/agents/ack-bot/backfill`` and checks that the
    endpoint returns a partitioned report of which agents got a fresh
    bot and which already had one. The second post on the same fleet
    must mark every bot as already_active because the first call is
    sticky.
    """
    from httpx import ASGITransport, AsyncClient
    from main import app

    name = "ack-backfill-endpoint"
    _reset(name)
    agents_router.agent_metadata[name] = {
        "status": "running", "source": "claude-code",
    }

    try:
        with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post("/api/agents/ack-bot/backfill")
                assert first.status_code == 200
                body = first.json()
                assert name in body["started"]
                assert chat_ack_bot.is_active(name)

                second = await client.post("/api/agents/ack-bot/backfill")
                assert second.status_code == 200
                body2 = second.json()
                assert name in body2["already_active"]
                assert name not in body2["started"]
    finally:
        chat_ack_bot.stop(name)
        agents_router.agent_metadata.pop(name, None)
        _reset(name)


@pytest.mark.asyncio
async def test_ack_bot_does_not_re_ack_same_nudge_on_subsequent_polls():
    """One user nudge must produce exactly one ack, no matter how many
    polls cycle past it.

    This is the regression test for the screenshot Tori captured where
    a single "how's it going?" message was acknowledged three times
    with three identical "On it, give me a sec." replies. Even when
    the same nudge appears in both the in-memory ``nudge_history`` and
    the on-disk ``list_nudges`` view (which is the production path,
    not just a test artifact), and even when the timestamp gate is
    forced into a degraded state, the bot must rely on the per-agent
    acked-id set to refuse a second ack for the same nudge.
    """
    import itertools

    name = "ack-no-reack"
    _reset(name)
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}

    nudge = {
        "message": "how's it going?",
        "timestamp": "2026-04-15T12:00:00+00:00",
        "source": "ui",
    }

    async def _append(agent, message, in_reply_to=None, kind=None):
        return {
            "message": message,
            "timestamp": "2026-04-15T12:00:00.500+00:00",
            "in_reply_to": in_reply_to,
            "source": "agent",
        }

    try:
        # The on-disk view returns the same record as the in-memory
        # view. This mirrors production where ``write_nudge`` writes
        # to disk and the router mirrors the same record into
        # ``nudge_history``.
        with patch.object(
            agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[nudge])
        ):
            with patch.object(
                agents_router.ostk,
                "append_nudge_reply",
                new=AsyncMock(side_effect=_append),
            ):
                counter = itertools.count()
                agents_router.nudge_history[name] = [nudge]

                # Three back to back poll cycles. The first acks the
                # nudge. The next two must be no-ops because the id
                # is already in the acked set.
                await chat_ack_bot._ack_once(name, counter)
                await chat_ack_bot._ack_once(name, counter)
                await chat_ack_bot._ack_once(name, counter)

                replies = agents_router.nudge_replies.get(name, [])
                assert len(replies) == 1, (
                    f"expected exactly one ack for one nudge, got {len(replies)}: "
                    f"{[r['message'] for r in replies]}"
                )
                assert replies[0]["kind"] == "ack"

                # Now simulate the failure mode the screenshot showed:
                # the timestamp gate gets reset (process restart or
                # any path that wipes ``_last_ack_ts``) but the agent
                # is still the same and the bot is still alive. The
                # set must hold the line and refuse a second ack for
                # the same on-disk nudge.
                chat_ack_bot._last_ack_ts.pop(name, None)
                await chat_ack_bot._ack_once(name, counter)
                await chat_ack_bot._ack_once(name, counter)

                replies = agents_router.nudge_replies.get(name, [])
                assert len(replies) == 1, (
                    f"timestamp gate reset must not allow re-ack, "
                    f"got {len(replies)}: {[r['message'] for r in replies]}"
                )
    finally:
        _reset(name)


@pytest.mark.asyncio
async def test_ack_bot_writes_only_one_reply_per_unique_nudge(tmp_path, monkeypatch):
    """The on-disk store must refuse a second identical reply.

    This is the write-time dedupe regression guard. The ack bot already
    keeps an in-memory set of acked nudge ids. That set is process
    local: a backend restart or a reload-watchdog replay wipes it.
    When the router boots with on-disk nudges still present, the bot
    can try to re-ack the same nudge and write a second identical
    reply file. The inline chat then paints two "On it, give me a sec."
    bubbles for a single user message, which is exactly the screenshot
    Tori captured.

    ``append_nudge_reply`` must treat two identical
    ``(agent, in_reply_to, message, kind)`` writes within a 30 second
    window as the same logical reply. The second call returns the
    first record unchanged and does not touch disk.
    """
    import services.ostk as ostk_module

    # Redirect the nudges dir to a scratch path so the test never
    # touches the user's real myOS data.
    monkeypatch.setattr(ostk_module, "NUDGES_DIR", tmp_path / "nudges")

    ostk_svc = ostk_module.ostk
    agent_name = "ack-write-dedupe"

    first = await ostk_svc.append_nudge_reply(
        agent_name,
        "On it, give me a sec.",
        in_reply_to="2026-04-15T12:00:00+00:00",
        kind="ack",
    )
    second = await ostk_svc.append_nudge_reply(
        agent_name,
        "On it, give me a sec.",
        in_reply_to="2026-04-15T12:00:00+00:00",
        kind="ack",
    )
    third = await ostk_svc.append_nudge_reply(
        agent_name,
        "On it, give me a sec.",
        in_reply_to="2026-04-15T12:00:00+00:00",
        kind="ack",
    )

    replies_dir = tmp_path / "nudges" / agent_name / "replies"
    files_on_disk = sorted(replies_dir.glob("*.json"))
    assert len(files_on_disk) == 1, (
        f"write-time dedupe must collapse identical replies, "
        f"got {len(files_on_disk)} files: {[f.name for f in files_on_disk]}"
    )
    # Every caller gets the same record back so the UI sees one reply.
    assert first["timestamp"] == second["timestamp"] == third["timestamp"]
    assert first["message"] == "On it, give me a sec."

    # A reply with a different in_reply_to (a new user nudge) must still
    # land: the dedupe is scoped to identical fingerprints, not blanket
    # rate-limiting.
    other = await ostk_svc.append_nudge_reply(
        agent_name,
        "On it, give me a sec.",
        in_reply_to="2026-04-15T12:00:05+00:00",
        kind="ack",
    )
    files_on_disk = sorted(replies_dir.glob("*.json"))
    assert len(files_on_disk) == 2, (
        "new in_reply_to must produce a fresh reply file"
    )
    assert other["in_reply_to"] == "2026-04-15T12:00:05+00:00"

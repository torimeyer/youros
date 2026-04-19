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


# ---------------------------------------------------------------------------
# Honest ack copy: no invented time estimates
# ---------------------------------------------------------------------------


def test_ack_copy_without_timing_data_never_promises_a_time():
    """With no measurements on file, the ack must not quote a number.

    This is the anchor test for the honest-ack contract. When the
    latency store is empty the helper must fall back to one of the
    no-estimate canned strings. No "seconds", no "minutes", no "within
    Ns" phrasing. The user reading this line should understand we are
    working on it and nothing more.
    """
    msg = chat_ack_bot.build_ack_message(None)
    assert msg in chat_ack_bot._ACK_RESPONSES_NO_ESTIMATE
    lowered = msg.lower()
    assert "second" not in lowered
    assert "minute" not in lowered
    assert "within" not in lowered


def test_ack_copy_for_fast_agent_does_not_quote_a_number():
    """If the measured reply is already under ten seconds, the ack
    should stay plain. Quoting "within 2 seconds" would be noise and
    could still be wrong by a factor of two on the next turn.
    """
    msg = chat_ack_bot.build_ack_message(2.0)
    assert msg in chat_ack_bot._ACK_RESPONSES_NO_ESTIMATE
    assert "second" not in msg.lower()


def test_ack_copy_for_slow_agent_quotes_rounded_up_seconds():
    """When the measurement is in the "usually under Ns" band the ack
    must quote a number that is at least the measured value. Rounding
    is always UP so we never promise a ceiling we cannot hit.
    """
    # 12 measured seconds rounds up to 15.
    msg = chat_ack_bot.build_ack_message(12.0)
    assert "15 seconds" in msg
    # 31 measured seconds lands in the 10-step band and rounds up to 40.
    msg = chat_ack_bot.build_ack_message(31.0)
    assert "40 seconds" in msg


def test_ack_copy_for_slow_agent_uses_minutes_when_over_90s():
    """A reply that regularly takes more than 90 seconds should be
    phrased in minutes, rounded up. Anything else feels like
    engineer-speak ("120 seconds").
    """
    msg = chat_ack_bot.build_ack_message(125.0)
    assert "minute" in msg
    # 125s rounds up to 150s, which is 3 minutes (ceiling of 2.5).
    assert "3 minutes" in msg


def test_ack_copy_for_very_slow_agent_says_may_take_a_while():
    """If the measurement is above the long-tail cap we must not quote
    a number, because we do not have enough confidence that the cap is
    representative. Promise the user we are on it and nothing more.
    """
    msg = chat_ack_bot.build_ack_message(600.0)
    assert "may take a while" in msg.lower()
    assert "minute" not in msg.lower()
    assert "second" not in msg.lower()


def test_ack_copy_never_contains_em_dash():
    """Project rule: no em-dashes in user-facing strings. Check every
    candidate path."""
    for measured in [None, 2.0, 15.0, 120.0, 600.0]:
        msg = chat_ack_bot.build_ack_message(measured)
        assert "\u2014" not in msg, f"em-dash in ack copy for {measured}s: {msg!r}"


def test_measured_p95_returns_none_until_enough_samples():
    """With fewer than the minimum samples, the aggregate must be
    ``None`` so the ack falls back to no-estimate copy. Guessing off
    one sample is what "inventing a number" looks like.
    """
    chat_ack_bot.reset_latency_store()
    name = "p95-coldstart"
    # No samples.
    assert chat_ack_bot.measured_p95_seconds(name) is None
    # One sample is still not enough.
    chat_ack_bot.record_reply_latency(name, 5.0)
    assert chat_ack_bot.measured_p95_seconds(name) is None
    # Two samples cross the floor.
    chat_ack_bot.record_reply_latency(name, 8.0)
    p95 = chat_ack_bot.measured_p95_seconds(name)
    assert p95 is not None
    assert 5.0 <= p95 <= 8.0
    chat_ack_bot.reset_latency_store()


def test_measured_p95_matches_rough_rank():
    """With a populated store the p95 must land near the top of the
    distribution. This anchors the rank math so we do not accidentally
    regress to a mean or a median.
    """
    chat_ack_bot.reset_latency_store()
    name = "p95-rank"
    for value in [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]:
        chat_ack_bot.record_reply_latency(name, float(value))
    p95 = chat_ack_bot.measured_p95_seconds(name)
    # The tail value is 100s; 95th percentile on nearest-rank lands on
    # the top of the sorted list for a 10-sample set.
    assert p95 == 100.0
    chat_ack_bot.reset_latency_store()


def test_build_ack_grounded_in_measured_p95_end_to_end():
    """Given a measured 8s aggregate, the ack must stay plain (fast
    path). Given a measured 45s aggregate it must quote seconds. Given
    a measured 200s aggregate it must quote minutes. This is the full
    contract the product team wrote the rule for: any number the user
    sees corresponds to a real number in our telemetry.
    """
    chat_ack_bot.reset_latency_store()
    fast = "end-to-end-fast"
    slow = "end-to-end-slow"
    very_slow = "end-to-end-very-slow"
    # Fast agent: two samples under the floor.
    for v in [3.0, 8.0]:
        chat_ack_bot.record_reply_latency(fast, v)
    # Slow agent: samples that round up to 45s band.
    for v in [30.0, 40.0, 45.0]:
        chat_ack_bot.record_reply_latency(slow, v)
    # Very slow agent: samples in the minute range.
    for v in [180.0, 200.0, 210.0]:
        chat_ack_bot.record_reply_latency(very_slow, v)

    fast_msg = chat_ack_bot.build_ack_message(chat_ack_bot.measured_p95_seconds(fast))
    slow_msg = chat_ack_bot.build_ack_message(chat_ack_bot.measured_p95_seconds(slow))
    very_slow_msg = chat_ack_bot.build_ack_message(
        chat_ack_bot.measured_p95_seconds(very_slow)
    )

    assert fast_msg in chat_ack_bot._ACK_RESPONSES_NO_ESTIMATE
    assert "second" in slow_msg.lower() and "minute" not in slow_msg.lower()
    assert "minute" in very_slow_msg.lower()
    chat_ack_bot.reset_latency_store()


def test_infer_latency_from_reply_skips_ack_kind_and_missing_fields():
    """The router only feeds the store with real (not ack) replies that
    carry an ``in_reply_to``. Anything else must return ``None`` so a
    stray ack or an orphan real reply cannot poison the aggregate.
    """
    # ack kind: ignored.
    assert chat_ack_bot._infer_latency_from_reply([], {
        "kind": "ack",
        "timestamp": "2026-04-15T12:00:10+00:00",
        "in_reply_to": "2026-04-15T12:00:00+00:00",
    }) is None
    # Missing in_reply_to: ignored.
    assert chat_ack_bot._infer_latency_from_reply([], {
        "kind": "real",
        "timestamp": "2026-04-15T12:00:10+00:00",
    }) is None
    # Clock skew (reply before nudge): ignored.
    assert chat_ack_bot._infer_latency_from_reply([], {
        "kind": "real",
        "timestamp": "2026-04-15T12:00:00+00:00",
        "in_reply_to": "2026-04-15T12:00:10+00:00",
    }) is None
    # Happy path.
    delta = chat_ack_bot._infer_latency_from_reply([], {
        "kind": "real",
        "timestamp": "2026-04-15T12:00:15+00:00",
        "in_reply_to": "2026-04-15T12:00:00+00:00",
    })
    assert delta == 15.0


@pytest.mark.asyncio
async def test_ack_bot_with_measured_slow_p95_ships_honest_time():
    """End-to-end: the bot running for an agent whose measured p95 is
    45 seconds must write an ack bubble that quotes a specific number
    (rounded up from 45s). This is the regression test that catches
    "the ack says 'give me a sec' but the real reply takes a minute".
    """
    import itertools

    name = "ack-measured-slow"
    _reset(name)
    chat_ack_bot.reset_latency_store()
    for v in [30.0, 40.0, 45.0]:
        chat_ack_bot.record_reply_latency(name, v)

    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}
    captured: list[str] = []

    async def _append(agent, message, in_reply_to=None, kind=None):
        captured.append(message)
        return {
            "message": message,
            "timestamp": "2026-04-15T12:00:46+00:00",
            "in_reply_to": in_reply_to,
            "source": "agent",
        }

    try:
        with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
            with patch.object(
                agents_router.ostk,
                "append_nudge_reply",
                new=AsyncMock(side_effect=_append),
            ):
                agents_router.nudge_history[name] = [
                    {
                        "message": "how's it going?",
                        "timestamp": "2026-04-15T12:00:00+00:00",
                        "source": "ui",
                    }
                ]
                await chat_ack_bot._ack_once(name, itertools.count())
    finally:
        _reset(name)
        chat_ack_bot.reset_latency_store()

    assert captured, "bot did not post an ack"
    msg = captured[0]
    # The quoted number is the rounded-up p95. 45s rounds up to 50s.
    assert "50 seconds" in msg, f"expected a 50s promise, got: {msg!r}"
    assert "minute" not in msg.lower()


@pytest.mark.asyncio
async def test_ack_bot_with_no_measurement_never_promises_a_time():
    """The regression test for the original bug: a cold-start agent
    with no latency history on file must NEVER land an ack bubble that
    quotes a specific time. The bot must read ``None`` from the store
    and pick a no-estimate string.
    """
    import itertools

    name = "ack-measured-cold"
    _reset(name)
    chat_ack_bot.reset_latency_store()

    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}
    captured: list[str] = []

    async def _append(agent, message, in_reply_to=None, kind=None):
        captured.append(message)
        return {
            "message": message,
            "timestamp": "2026-04-15T12:00:01+00:00",
            "in_reply_to": in_reply_to,
            "source": "agent",
        }

    try:
        with patch.object(agents_router.ostk, "list_nudges", new=AsyncMock(return_value=[])):
            with patch.object(
                agents_router.ostk,
                "append_nudge_reply",
                new=AsyncMock(side_effect=_append),
            ):
                agents_router.nudge_history[name] = [
                    {
                        "message": "quick one",
                        "timestamp": "2026-04-15T12:00:00+00:00",
                        "source": "ui",
                    }
                ]
                await chat_ack_bot._ack_once(name, itertools.count())
    finally:
        _reset(name)
        chat_ack_bot.reset_latency_store()

    assert captured, "bot did not post an ack"
    msg = captured[0]
    lowered = msg.lower()
    assert "second" not in lowered, f"no-data ack must not quote seconds: {msg!r}"
    assert "minute" not in lowered, f"no-data ack must not quote minutes: {msg!r}"
    assert msg in chat_ack_bot._ACK_RESPONSES_NO_ESTIMATE

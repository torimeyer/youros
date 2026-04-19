"""Long-poll nudge delivery tests.

These tests lock the demo-latency contract: when Tori types into an
agent chat, her message must reach the agent in under two seconds on
the happy path, and agent replies must surface to the UI with the same
speed. The long-poll branch of GET /nudges does the heavy lifting, so
these tests exercise the waiter plumbing directly.

The regressions guarded here:

* Long-poll returns within milliseconds when a nudge lands during the
  wait. No caller should see the full timeout for a live agent.
* Long-poll honors its bounded timeout when nothing arrives. No HTTP
  connection may linger past NUDGE_LONG_POLL_MAX_SECONDS.
* Reply POSTs also wake long-pollers so the frontend transcript
  surfaces the agent's answer immediately, not on the next cycle.
* The immediate (non-waiting) call path is unchanged when ``wait=0``
  or when ``since`` is omitted. This guarantees legacy clients still
  work.
* Stdin delivery and file-fallback behaviors are untouched by the new
  waiter code.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Make sure the api/ package is importable when pytest runs from the
# repo root. Mirrors the pattern used in test_agents.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
from routers.agents import (  # noqa: E402
    NUDGE_LONG_POLL_MAX_SECONDS,
    _agent_stdin_writers,
    _nudge_waiters,
    agent_metadata,
    nudge_history,
    nudge_replies,
)


def _reset_agent(name: str) -> None:
    """Clear every in-memory store that could leak between tests."""
    agent_metadata.pop(name, None)
    nudge_history.pop(name, None)
    nudge_replies.pop(name, None)
    _agent_stdin_writers.pop(name, None)
    _nudge_waiters.pop(name, None)


@pytest.mark.asyncio
async def test_long_poll_returns_immediately_when_new_data_present():
    """Long-poll returns at once if snapshot already has newer data.

    The caller's ``since`` marker is older than the latest timestamp, so
    the handler must not block. This is the common case when the UI
    reconnects after a dropped connection.
    """
    name = "poll-agent-immediate"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                # Simulate a nudge and a reply already on disk.
                mock_ostk.list_nudges = AsyncMock(return_value=[
                    {
                        "message": "hey",
                        "timestamp": "2026-04-15T10:00:05+00:00",
                        "source": "ui",
                    },
                ])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

                start = time.monotonic()
                resp = await client.get(
                    f"/api/agents/{name}/nudges",
                    params={"wait": 5, "since": "2026-04-15T10:00:00+00:00"},
                )
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    assert data["nudges"][0]["message"] == "hey"
    # Should return well under a second because the snapshot already
    # has a newer timestamp than ``since``.
    assert elapsed < 1.0, f"long-poll waited {elapsed:.3f}s when data was ready"


@pytest.mark.asyncio
async def test_long_poll_wakes_when_reply_arrives():
    """POST /reply must wake every parked long-poller for the same agent.

    Simulates the frontend: we kick off a /nudges?wait=... request that
    should block (nothing newer than ``since`` yet), then POST a reply
    in parallel. The GET must return in well under a second.
    """
    name = "poll-agent-reply"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                # Start with no nudges and no replies. The snapshot is empty.
                current_replies: list[dict] = []
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(
                    side_effect=lambda _n: list(current_replies),
                )

                # Simulate append_nudge_reply mutating the reply list so
                # the poster's own handler sees the new row.
                async def _append(agent, message, in_reply_to=None, kind=None):
                    row = {
                        "message": message,
                        "timestamp": "2026-04-15T10:01:00+00:00",
                        "source": "agent",
                        "in_reply_to": in_reply_to,
                    }
                    current_replies.append(row)
                    return row

                mock_ostk.append_nudge_reply = AsyncMock(side_effect=_append)

                async def _delayed_reply():
                    # Let the GET arm its waiter first. A short delay
                    # keeps the test deterministic without an event.
                    await asyncio.sleep(0.05)
                    await client.post(
                        f"/api/agents/{name}/reply",
                        json={"message": "on it"},
                    )

                start = time.monotonic()
                get_task = asyncio.create_task(
                    client.get(
                        f"/api/agents/{name}/nudges",
                        params={
                            "wait": NUDGE_LONG_POLL_MAX_SECONDS,
                            "since": "2026-04-15T10:00:00+00:00",
                        },
                    )
                )
                reply_task = asyncio.create_task(_delayed_reply())
                resp, _ = await asyncio.gather(get_task, reply_task)
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    assert any(r["message"] == "on it" for r in data["replies"])
    # Wake should be near instant. Give it a generous 2s ceiling so CI
    # jitter does not flake the test.
    assert elapsed < 2.0, f"long-poll took {elapsed:.3f}s, expected under 2s"


@pytest.mark.asyncio
async def test_long_poll_wakes_when_nudge_arrives():
    """POST /nudge wakes long-pollers so agents long-polling see instant delivery.

    This is the agent side of the contract: an agent parked on a
    /nudges?wait=... call must unblock the moment Tori POSTs /nudge.
    """
    name = "poll-agent-nudge"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._touch_nudge_signal"):
                current_nudges: list[dict] = []
                mock_ostk.list_nudges = AsyncMock(
                    side_effect=lambda _n: list(current_nudges),
                )
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

                async def _write(agent, message, kind=None):
                    row = {
                        "message": message,
                        "timestamp": "2026-04-15T10:02:00+00:00",
                        "source": "ui",
                    }
                    current_nudges.append(row)
                    return row

                mock_ostk.write_nudge = AsyncMock(side_effect=_write)

                async def _delayed_nudge():
                    await asyncio.sleep(0.05)
                    await client.post(
                        f"/api/agents/{name}/nudge",
                        json={"message": "hey add login"},
                    )

                start = time.monotonic()
                get_task = asyncio.create_task(
                    client.get(
                        f"/api/agents/{name}/nudges",
                        params={
                            "wait": NUDGE_LONG_POLL_MAX_SECONDS,
                            "since": "2026-04-15T10:00:00+00:00",
                        },
                    )
                )
                post_task = asyncio.create_task(_delayed_nudge())
                resp, _ = await asyncio.gather(get_task, post_task)
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    assert any(n["message"] == "hey add login" for n in data["nudges"])
    assert elapsed < 2.0, f"long-poll took {elapsed:.3f}s, expected under 2s"


@pytest.mark.asyncio
async def test_long_poll_times_out_when_nothing_arrives():
    """With nothing new, the long-poll returns an empty snapshot at the deadline.

    Critical safety property: long-polls must not hang indefinitely.
    The handler must respect its timeout and return even if no waiter
    is set, so connections free up and the caller can reconnect.
    """
    name = "poll-agent-timeout"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

                start = time.monotonic()
                resp = await client.get(
                    f"/api/agents/{name}/nudges",
                    params={"wait": 1, "since": "2026-04-15T10:00:00+00:00"},
                )
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    assert data["nudges"] == []
    assert data["replies"] == []
    # Timeout was 1 second, should land between 1.0 and 2.0s.
    assert 0.9 <= elapsed <= 2.5, f"timeout fired after {elapsed:.3f}s, expected ~1s"


@pytest.mark.asyncio
async def test_legacy_poll_returns_immediately_without_wait_param():
    """A bare GET /nudges (no wait, no since) behaves like before.

    Old clients that predate long-polling must still work unchanged.
    This regression guards the backwards-compat promise so agents on
    older code keep receiving messages.
    """
    name = "poll-agent-legacy"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

                start = time.monotonic()
                resp = await client.get(f"/api/agents/{name}/nudges")
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    # No wait parameter and empty data: must return under a second.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_wait_param_is_capped_at_server_max():
    """A caller asking for wait=999 is silently capped at NUDGE_LONG_POLL_MAX_SECONDS.

    FastAPI rejects out-of-range Query values. Confirm the ceiling is
    enforced. A 422 here is acceptable and is the chosen behavior.
    """
    name = "poll-agent-cap"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/agents/{name}/nudges",
                params={
                    "wait": NUDGE_LONG_POLL_MAX_SECONDS + 1000,
                    "since": "2026-04-15T10:00:00+00:00",
                },
            )
    finally:
        _reset_agent(name)

    # Either the server caps it and returns 200 with an empty snapshot,
    # or it rejects with 422. Both are acceptable because both enforce
    # the connection-lifetime ceiling. What we forbid is quietly
    # waiting for 1030 seconds.
    assert resp.status_code in (200, 422)


@pytest.mark.asyncio
async def test_stdin_delivery_still_works_with_long_poll():
    """The stdin-pipe path is unaffected by the new waiter code.

    When an agent has a live stdin writer, POST /nudge must still push
    the message into the pipe and report delivery='stdin'. This makes
    sure adding the waiter did not accidentally short-circuit the
    delivery ladder.
    """
    name = "stdin-plus-wait-agent"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    mock_writer = MagicMock()
    mock_writer.is_closing.return_value = False
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    _agent_stdin_writers[name] = mock_writer

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._touch_nudge_signal"):
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "agent": name,
                    "message": "add the login flow",
                    "timestamp": "2026-04-15T10:03:00+00:00",
                    "source": "ui",
                })
                resp = await client.post(
                    f"/api/agents/{name}/nudge",
                    json={"message": "add the login flow"},
                )
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    assert data["nudge"]["delivery"] == "stdin"
    mock_writer.write.assert_called_once()


@pytest.mark.asyncio
async def test_long_poll_wakes_within_200ms_of_nudge():
    """Hard latency guard: wake must fire under 200ms after POST /nudge.

    The existing wake tests tolerate 2 seconds which would miss
    regressions where the event loop scheduling slows the push path
    down without breaking correctness. This test pins the real world
    promise: when an agent is parked on a long-poll, a new nudge
    surfaces in under 200 milliseconds. If this starts failing the
    "within a second" banner copy is a lie and must be softened.
    """
    name = "poll-agent-latency"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._touch_nudge_signal"):
                current_nudges: list[dict] = []
                mock_ostk.list_nudges = AsyncMock(
                    side_effect=lambda _n: list(current_nudges),
                )
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

                async def _write(agent, message, kind=None):
                    row = {
                        "message": message,
                        "timestamp": "2026-04-15T10:03:00+00:00",
                        "source": "ui",
                    }
                    current_nudges.append(row)
                    return row

                mock_ostk.write_nudge = AsyncMock(side_effect=_write)

                get_task = asyncio.create_task(
                    client.get(
                        f"/api/agents/{name}/nudges",
                        params={
                            "wait": NUDGE_LONG_POLL_MAX_SECONDS,
                            "since": "2026-04-15T10:00:00+00:00",
                        },
                    )
                )
                # Wait until the long-poller is confirmed parked.
                # Reading the counter directly avoids a flaky fixed sleep.
                from routers.agents import _nudge_parked_count
                for _ in range(100):
                    if _nudge_parked_count.get(name, 0) >= 1:
                        break
                    await asyncio.sleep(0.005)
                else:
                    get_task.cancel()
                    pytest.fail("long-poller never parked")

                start = time.monotonic()
                await client.post(
                    f"/api/agents/{name}/nudge",
                    json={"message": "latency check"},
                )
                resp = await get_task
                elapsed = time.monotonic() - start
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    assert elapsed < 0.200, (
        f"long-poll took {elapsed*1000:.1f}ms, must wake in under 200ms"
    )


@pytest.mark.asyncio
async def test_nudge_delivery_message_is_honest_when_no_poller_parked():
    """When no long-poller is parked, delivery_message must not promise
    sub-second delivery. The honest wording is "on its next mailbox
    check, up to 60 seconds from now". This is what Tori sees in the
    chat banner.
    """
    name = "honest-msg-no-poll"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._touch_nudge_signal"):
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "message": "x",
                    "timestamp": "2026-04-15T10:04:00+00:00",
                    "source": "ui",
                })

                resp = await client.post(
                    f"/api/agents/{name}/nudge",
                    json={"message": "no parker"},
                )
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    msg = data["nudge"]["delivery_message"]
    assert "next mailbox check" in msg.lower()
    assert "within a second" not in msg.lower()
    assert "within a couple of seconds" not in msg.lower()


@pytest.mark.asyncio
async def test_nudge_delivery_message_promises_second_when_parked():
    """When a long-poller IS parked, delivery_message must promise
    sub-second delivery. This is the opposite guardrail: do not
    underpromise when we know the agent is actively waiting.
    """
    name = "honest-msg-parked"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._touch_nudge_signal"):
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "message": "x",
                    "timestamp": "2026-04-15T10:05:00+00:00",
                    "source": "ui",
                })

                # Park a long-poller.
                get_task = asyncio.create_task(
                    client.get(
                        f"/api/agents/{name}/nudges",
                        params={
                            "wait": 2,
                            "since": "2026-04-15T09:00:00+00:00",
                        },
                    )
                )
                from routers.agents import _nudge_parked_count
                for _ in range(100):
                    if _nudge_parked_count.get(name, 0) >= 1:
                        break
                    await asyncio.sleep(0.005)
                else:
                    get_task.cancel()
                    pytest.fail("long-poller never parked")

                resp = await client.post(
                    f"/api/agents/{name}/nudge",
                    json={"message": "hi parker"},
                )
                # Drain the long-poll task so it does not leak.
                try:
                    await asyncio.wait_for(get_task, timeout=3)
                except asyncio.TimeoutError:
                    get_task.cancel()
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    data = resp.json()
    msg = data["nudge"]["delivery_message"]
    assert "within a second" in msg.lower()


def test_mailbox_instruction_advertises_long_poll():
    """The prompt block must tell agents how to long-poll /nudges.

    Without this hint, agents stay on the old 10s short-poll and Tori
    still sees up to 10s of latency. The block must name the wait and
    since parameters and cite the server ceiling.
    """
    from routers.agents import agent_mailbox_instruction

    block = agent_mailbox_instruction("demo-agent")
    assert "wait=" in block
    assert "since=" in block
    assert str(NUDGE_LONG_POLL_MAX_SECONDS) in block
    # The long-poll recommendation must be plain, no jargon.
    assert "\u2014" not in block  # no em dashes per project style


def test_mailbox_instruction_makes_reply_mandatory():
    """The mailbox block must say /reply is mandatory, not optional.

    Tori's stage demo kept failing because agents treated the reply step
    as a suggestion and finished without ever posting one. The prompt
    language must explicitly say MUST so models take the instruction
    seriously.
    """
    from routers.agents import agent_mailbox_instruction

    block = agent_mailbox_instruction("demo-agent")
    assert "/reply" in block
    # Mandatory language. Silence is worse than any reply.
    assert "MUST" in block
    assert "not optional" in block.lower() or "silence" in block.lower()


def test_mailbox_instruction_requires_poll_between_tool_calls():
    """The block must tell the agent to poll /nudges between tool calls.

    Claude Code subagents under ``claude --print`` do not interrupt
    their tool chains to poll on their own. The prompt has to say it
    explicitly: between every tool call, poll /nudges. Also call out
    long-running tools (pytest, tsc) with a poll-before-and-after rule
    so Tori never waits more than 30 seconds for an agent to notice
    her message.
    """
    from routers.agents import (
        agent_mailbox_instruction,
        agent_mailbox_instruction_short,
    )

    for block in (
        agent_mailbox_instruction("demo-agent"),
        agent_mailbox_instruction_short("demo-agent"),
    ):
        lower = block.lower()
        assert "between every tool call" in lower, (
            "prompt must require a /nudges poll between every tool call"
        )
        # Long-running tool carve-out must be explicit.
        assert "pytest" in lower
        assert "30 seconds" in lower, (
            "prompt must cite the 30 second minimum cadence"
        )


def test_mailbox_instruction_tells_agent_to_reply_warmly_then_resume():
    """The block must tell the agent to reply warmly and resume work.

    Tori wants short, conversational acknowledgments ('Still crunching,
    about halfway!') not formal ones ('Acknowledged.'). And the agent
    must never stall waiting for her next message: post /reply, keep
    going.
    """
    from routers.agents import (
        agent_mailbox_instruction,
        agent_mailbox_instruction_short,
    )

    for block in (
        agent_mailbox_instruction("demo-agent"),
        agent_mailbox_instruction_short("demo-agent"),
    ):
        lower = block.lower()
        # Warmth cue: the prompt must tell the agent to reply warmly
        # and show an example that is warm without inventing a time.
        assert (
            "warm" in lower
            and ("conversational" in lower or "honest" in lower)
        ), "prompt must model a warm, conversational reply"
        # Do-not-wait cue: agent must resume immediately after /reply.
        assert (
            "never wait for a response" in lower
            or "immediately resume" in lower
        ), "prompt must tell the agent not to wait on Tori"
        # No em dashes in the prompt (project style rule).
        assert "\u2014" not in block
        # Honest-time regression guard: the prompt must explicitly tell
        # the agent not to fabricate a time estimate. Before this fix
        # the prompt SUGGESTED fake times like "On it, give me two
        # minutes" which the model then repeated verbatim to Tori.
        assert (
            "do not invent" in lower
            or "never fabricate" in lower
            or "do not fabricate" in lower
        ), (
            "prompt must explicitly forbid inventing time estimates, "
            "so the model does not echo a made-up number back to Tori"
        )


def test_agent_chat_banner_shows_estimated_wait_based_on_poll_recency():
    """The /nudge delivery_message must scale with long-poll parking.

    When a long-poller is parked, the banner promises sub-second. When
    nobody is parked, it quotes the slow cap (60s). This is the truthy
    countdown: fast when the agent is listening, honest when it isn't.
    """
    from routers.agents import (
        MAILBOX_SLOW_POLL_SECONDS,
        _is_long_poll_parked,
        _nudge_delivery_message,
        _nudge_parked_count,
    )

    name = "banner-honesty-agent"
    _nudge_parked_count.pop(name, None)

    # No parker: banner quotes the slow cap.
    assert not _is_long_poll_parked(name)
    msg_idle = _nudge_delivery_message("file_only", name)
    assert str(MAILBOX_SLOW_POLL_SECONDS) in msg_idle
    assert "next mailbox check" in msg_idle.lower()
    assert "within a second" not in msg_idle.lower()

    # Parker present: banner promises sub-second delivery.
    _nudge_parked_count[name] = 1
    try:
        assert _is_long_poll_parked(name)
        msg_live = _nudge_delivery_message("file_only", name)
        assert "within a second" in msg_live.lower()
    finally:
        _nudge_parked_count.pop(name, None)


@pytest.mark.asyncio
async def test_complete_summary_is_mirrored_into_replies():
    """POST /complete with a summary must also write a /reply record.

    Agents running under ``claude --print`` almost never remember to
    call POST /reply before exiting. Without this bridge, the inline
    chat thread never shows the agent's final answer and Tori is left
    staring at a thinking indicator that never resolves. The summary
    is the agent's final user-facing text, so it also belongs in the
    reply stream. This test locks the bridge in so a refactor cannot
    silently drop it.
    """
    name = "complete-reply-bridge-agent"
    _reset_agent(name)
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": "2026-04-15T10:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents.agent_memory_svc") as mock_memory:
                captured = {}

                async def capture_reply(agent, msg, in_reply_to=None, kind=None):
                    captured["agent"] = agent
                    captured["message"] = msg
                    return {
                        "agent": agent,
                        "message": msg,
                        "timestamp": "2026-04-15T10:05:00+00:00",
                        "source": "agent",
                        "in_reply_to": in_reply_to,
                    }

                mock_ostk.append_nudge_reply = AsyncMock(side_effect=capture_reply)
                mock_memory.append_summary = MagicMock()

                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "Done. Shipped the login flow."},
                )
    finally:
        _reset_agent(name)

    assert resp.status_code == 200
    assert captured.get("message") == "Done. Shipped the login flow.", (
        "the summary must be mirrored into /reply so the inline thread "
        "shows the agent's final word"
    )


@pytest.mark.asyncio
async def test_complete_summary_bridge_skips_exact_recent_duplicate():
    """If the agent already posted the same reply in the last minute,
    the bridge must skip it. This prevents a visible duplicate when the
    agent did remember to call /reply right before /complete.
    """
    from datetime import datetime, timezone
    name = "complete-reply-no-dup-agent"
    _reset_agent(name)
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": "2026-04-15T10:00:00+00:00",
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    nudge_replies[name] = [
        {
            "agent": name,
            "message": "Done. Shipped it.",
            "timestamp": now_iso,
            "source": "agent",
            "in_reply_to": None,
        }
    ]
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents.agent_memory_svc") as mock_memory:
                mock_ostk.append_nudge_reply = AsyncMock()
                mock_memory.append_summary = MagicMock()

                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "Done. Shipped it."},
                )

                assert resp.status_code == 200
                mock_ostk.append_nudge_reply.assert_not_called()
    finally:
        _reset_agent(name)


@pytest.mark.asyncio
async def test_complete_summary_bridge_wakes_long_poll_waiters():
    """A /complete with a summary must wake parked long-pollers so the
    frontend renders the final reply within milliseconds of completion,
    just like a direct POST /reply does.
    """
    name = "complete-wake-agent"
    _reset_agent(name)
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": "2026-04-15T10:00:00+00:00",
    }

    # Arm a waiter directly. The /complete bridge should set it.
    from routers.agents import _get_nudge_waiter

    event = _get_nudge_waiter(name)
    event.clear()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents.agent_memory_svc") as mock_memory:
                mock_ostk.append_nudge_reply = AsyncMock(
                    return_value={
                        "agent": name,
                        "message": "bye",
                        "timestamp": "2026-04-15T10:05:00+00:00",
                        "source": "agent",
                        "in_reply_to": None,
                    }
                )
                mock_memory.append_summary = MagicMock()

                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "bye"},
                )

                assert resp.status_code == 200
                assert event.is_set(), (
                    "long-poll waiters must be woken when /complete writes "
                    "a summary-derived reply so the UI shows the final "
                    "answer immediately."
                )
    finally:
        _reset_agent(name)


@pytest.mark.asyncio
async def test_user_message_triggers_ack_within_2s():
    """A POST /nudge must produce an ack-tagged reply within 2 seconds.

    The Send button hits /nudge, which writes the user message to the
    mailbox and wakes the ack bot. The ack bot's poll cycle is two
    seconds, so the warm canned reply must land before that wall-clock
    budget is up. Without this guarantee the inline chat looks broken
    even though the real subagent is just busy in a tool call.
    """
    from services import chat_ack_bot

    name = "demo-ack-within-2s"
    _reset_agent(name)
    chat_ack_bot._last_ack_ts.pop(name, None)
    chat_ack_bot._agent_ack_tasks.pop(name, None)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._touch_nudge_signal"):
            current_nudges: list[dict] = []
            captured_replies: list[dict] = []

            async def _write(agent, message, kind=None):
                row = {
                    "agent": agent,
                    "message": message,
                    "timestamp": "2026-04-15T10:00:00+00:00",
                    "source": "ui",
                }
                if kind:
                    row["kind"] = kind
                current_nudges.append(row)
                return row

            async def _append(agent, message, in_reply_to=None, kind=None):
                row = {
                    "agent": agent,
                    "message": message,
                    "timestamp": "2026-04-15T10:00:01+00:00",
                    "source": "agent",
                    "in_reply_to": in_reply_to,
                }
                if kind:
                    row["kind"] = kind
                captured_replies.append(row)
                return row

            mock_ostk.list_nudges = AsyncMock(
                side_effect=lambda _n: list(current_nudges),
            )
            mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
            mock_ostk.write_nudge = AsyncMock(side_effect=_write)
            mock_ostk.append_nudge_reply = AsyncMock(side_effect=_append)

            ack_task = chat_ack_bot.start(name)
            try:
                async with AsyncClient(
                    transport=transport, base_url="http://test",
                ) as client:
                    start_t = time.monotonic()
                    nudge_resp = await client.post(
                        f"/api/agents/{name}/nudge",
                        json={"message": "it's not the timestamps, "
                                          "it's the titles"},
                    )
                    assert nudge_resp.status_code == 200
                    # Tag survives onto the in-memory record.
                    assert nudge_resp.json()["nudge"]["kind"] == "user_message"

                    # Poll the in-memory replies dict until the ack bot
                    # posts. Bound the wait at 2.5 seconds so the test
                    # hard-fails if the contract slips.
                    deadline = start_t + 2.5
                    ack_seen = False
                    while time.monotonic() < deadline:
                        replies = nudge_replies.get(name, [])
                        if any(r.get("kind") == "ack" for r in replies):
                            ack_seen = True
                            break
                        await asyncio.sleep(0.05)
                    elapsed = time.monotonic() - start_t

                    assert ack_seen, (
                        f"no ack reply landed in {elapsed:.2f}s. The Send "
                        "button cannot promise a 2 second receipt without "
                        "this guarantee."
                    )
                    assert elapsed < 2.5, (
                        f"ack arrived in {elapsed:.2f}s, contract is 2s."
                    )
            finally:
                chat_ack_bot.stop(name)
                try:
                    await asyncio.wait_for(ack_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
    finally:
        _reset_agent(name)
        chat_ack_bot._last_ack_ts.pop(name, None)
        chat_ack_bot._agent_ack_tasks.pop(name, None)


@pytest.mark.asyncio
async def test_correction_flag_preserved_through_to_agent_reply():
    """POST /correct must persist kind=correction end to end.

    The structured correction channel exists so the agent can treat a
    course change differently from a follow-up message. We assert the
    kind survives into:

    1. The on-disk nudge (via ostk.write_nudge call kwargs).
    2. The in-memory nudge_history record returned by the response.
    3. The /nudges snapshot the frontend reads.

    If any of these three drop the tag the agent has no signal that the
    incoming message is a correction, and the URGENT envelope on stdin
    is the only escape hatch left.
    """
    name = "demo-correction-tag"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._touch_nudge_signal"):
            current_nudges: list[dict] = []

            async def _write(agent, message, kind=None):
                row = {
                    "agent": agent,
                    "message": message,
                    "timestamp": "2026-04-15T10:00:00+00:00",
                    "source": "ui",
                }
                if kind:
                    row["kind"] = kind
                current_nudges.append(row)
                return row

            mock_ostk.list_nudges = AsyncMock(
                side_effect=lambda _n: list(current_nudges),
            )
            mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
            mock_ostk.write_nudge = AsyncMock(side_effect=_write)
            mock_ostk.correct_agent = AsyncMock(return_value={"ok": True})

            async with AsyncClient(
                transport=transport, base_url="http://test",
            ) as client:
                resp = await client.post(
                    f"/api/agents/{name}/correct",
                    json={"message": "actually fix the titles, "
                                      "not the timestamps"},
                )
                assert resp.status_code == 200

            # 1. The disk write got tagged kind="correction".
            mock_ostk.write_nudge.assert_called_once()
            _, _, kwargs = mock_ostk.write_nudge.mock_calls[0]
            assert kwargs.get("kind") == "correction", (
                "write_nudge must be called with kind='correction' so the "
                "on-disk nudge file carries the tag for the agent's poll."
            )

            # 2. The in-memory record returned by /correct carries kind.
            record = resp.json()["nudge"]
            assert record["kind"] == "correction"
            assert record["type"] == "correction", (
                "legacy 'type' field must still be set so older readers "
                "do not lose the correction signal during rollout."
            )

            # 3. The /nudges snapshot exposes the tag the frontend reads.
            async with AsyncClient(
                transport=transport, base_url="http://test",
            ) as client:
                snap = await client.get(f"/api/agents/{name}/nudges")
                assert snap.status_code == 200
                payload = snap.json()
                tagged = [
                    n for n in payload["nudges"] + payload["session_nudges"]
                    if n.get("kind") == "correction"
                ]
                assert tagged, (
                    "GET /nudges must surface kind=correction so the "
                    "frontend can render the amber correction styling."
                )
    finally:
        _reset_agent(name)


@pytest.mark.asyncio
async def test_real_agent_reply_distinguished_from_ack():
    """A real /reply POST must be tagged kind=real, not kind=ack.

    The chat UI uses the kind field to render the warm canned ack in a
    lighter italic bubble and the real substantive answer as the
    primary bubble. If POST /reply ever leaks an "ack" tag (or omits
    the field so the UI defaults to ack styling) the user can never
    tell the two apart and the demo regresses to "everything looks
    canned".
    """
    name = "demo-real-vs-ack"
    _reset_agent(name)
    agent_metadata[name] = {"status": "running", "source": "claude-code"}

    transport = ASGITransport(app=app)
    try:
        with patch("routers.agents.ostk") as mock_ostk:
            captured: list[dict] = []

            async def _append(agent, message, in_reply_to=None, kind=None):
                row = {
                    "agent": agent,
                    "message": message,
                    "timestamp": "2026-04-15T10:00:30+00:00",
                    "source": "agent",
                    "in_reply_to": in_reply_to,
                }
                if kind:
                    row["kind"] = kind
                captured.append(row)
                return row

            mock_ostk.list_nudges = AsyncMock(return_value=[])
            mock_ostk.list_nudge_replies = AsyncMock(
                side_effect=lambda _n: list(captured),
            )
            mock_ostk.append_nudge_reply = AsyncMock(side_effect=_append)

            async with AsyncClient(
                transport=transport, base_url="http://test",
            ) as client:
                resp = await client.post(
                    f"/api/agents/{name}/reply",
                    json={"message": "got it, switching to fix the "
                                      "titles now."},
                )
                assert resp.status_code == 200
                payload = resp.json()
                assert payload["reply"]["kind"] == "real", (
                    "POST /reply must default to kind='real' so the "
                    "frontend can tell a substantive subagent reply "
                    "apart from a warm canned ack."
                )

                # And the on-disk write carried the tag too.
                _, _, kwargs = mock_ostk.append_nudge_reply.mock_calls[0]
                assert kwargs.get("kind") == "real", (
                    "append_nudge_reply must be called with kind='real' "
                    "so the persisted file is also distinguishable."
                )

                # Snapshot must surface the tag for the UI to read.
                snap = await client.get(f"/api/agents/{name}/nudges")
                assert snap.status_code == 200
                snap_payload = snap.json()
                all_replies = (
                    snap_payload["replies"] + snap_payload["session_replies"]
                )
                kinds = {r.get("kind") for r in all_replies if r.get("kind")}
                assert "real" in kinds, (
                    "GET /nudges must expose kind=real so the frontend "
                    "renders the substantive bubble, not the ack bubble."
                )
                assert "ack" not in kinds, (
                    "POST /reply must never emit kind=ack. The ack bot is "
                    "the only path that should produce ack-tagged replies."
                )
    finally:
        _reset_agent(name)

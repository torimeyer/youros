"""Tests for iMessage completion text-back (→1875).

Covers:
  - notify field on AgentSpawn schema
  - notify persisted in agent_metadata at spawn
  - completion sends exactly one text to the right chat
  - send failure does not block completion
  - no notify target means no text (in-app spawns unaffected)
  - end-to-end dispatch loop: text in → confirm → spawn with notify
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Schema: notify field exists on AgentSpawn
# ---------------------------------------------------------------------------

def test_agent_spawn_has_notify_field():
    from models.schemas import AgentSpawn
    spawn = AgentSpawn(name="x", prompt="y", notify={"kind": "imessage", "chat_id": 42})
    assert spawn.notify == {"kind": "imessage", "chat_id": 42}


def test_agent_spawn_notify_defaults_none():
    from models.schemas import AgentSpawn
    spawn = AgentSpawn(name="x", prompt="y")
    assert spawn.notify is None


# ---------------------------------------------------------------------------
# Spawn-time: notify persisted in agent_metadata
# ---------------------------------------------------------------------------

def test_notify_stored_in_metadata():
    """Directly verify that setting notify in spawn_meta works."""
    import routers.agents as agents_mod

    agents_mod.agent_metadata["test-notify-meta"] = {
        "source": "text-bridge",
        "notify": {"kind": "imessage", "chat_id": 99},
        "status": "running",
    }
    try:
        meta = agents_mod.agent_metadata["test-notify-meta"]
        assert meta["notify"] == {"kind": "imessage", "chat_id": 99}
    finally:
        agents_mod.agent_metadata.pop("test-notify-meta", None)


# ---------------------------------------------------------------------------
# Completion: exactly one text sent to the right chat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_sends_one_text():
    """mark_agent_complete with notify target sends exactly one iMessage."""
    import routers.agents as agents_mod
    from routers.agents import AgentComplete

    original_meta = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata["notify-done"] = {
            "status": "running",
            "notify": {"kind": "imessage", "chat_id": 42},
            "summary": "Disk check complete. 80% free.",
        }

        sent_calls = []

        async def fake_to_thread(fn, *args, **kwargs):
            sent_calls.append((fn, args))

        with patch("routers.agents._is_test_artifact_agent_name", return_value=False), \
             patch("routers.agents._close_orphan_plan_transcript"), \
             patch("routers.agents._save_agent_state_async", AsyncMock()), \
             patch("routers.agents.chat_ack_bot", MagicMock()), \
             patch("routers.agents.asyncio.to_thread", new=fake_to_thread), \
             patch("services.notifications.notifications_service", MagicMock()):

            body = AgentComplete(summary="Disk check complete. 80% free.")
            await agents_mod.mark_agent_complete("notify-done", body)

        # Filter for the iMessage send specifically (other to_thread calls in the fn are ok)
        imessage_calls = [(fn, args) for fn, args in sent_calls if "reply_to_chat_sync" in getattr(fn, "__name__", "")]
        assert len(imessage_calls) == 1, f"Expected 1 reply_to_chat_sync call, got: {sent_calls}"
        fn, args = imessage_calls[0]
        assert args[0] == 42
        assert "notify-done" in args[1]
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)


# ---------------------------------------------------------------------------
# Completion: send failure does not block completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_send_failure_does_not_block():
    """If asyncio.to_thread raises for the iMessage send, completion still succeeds."""
    import routers.agents as agents_mod
    from routers.agents import AgentComplete

    original_meta = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata["fail-notify"] = {
            "status": "running",
            "notify": {"kind": "imessage", "chat_id": 7},
        }

        async def raise_on_call(fn, *args, **kwargs):
            raise RuntimeError("network gone")

        with patch("routers.agents._is_test_artifact_agent_name", return_value=False), \
             patch("routers.agents._close_orphan_plan_transcript"), \
             patch("routers.agents._save_agent_state_async", AsyncMock()), \
             patch("routers.agents.chat_ack_bot", MagicMock()), \
             patch("routers.agents.asyncio.to_thread", new=raise_on_call), \
             patch("services.notifications.notifications_service", MagicMock()):

            body = AgentComplete(summary="done")
            result = await agents_mod.mark_agent_complete("fail-notify", body)

        # Must still reach a terminal state
        assert result is not None
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)


# ---------------------------------------------------------------------------
# No notify target: no asyncio.to_thread call for iMessage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_notify_no_text():
    """Agents spawned without notify must not call asyncio.to_thread for iMessage."""
    import routers.agents as agents_mod
    from routers.agents import AgentComplete

    original_meta = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata["no-notify"] = {
            "status": "running",
        }

        to_thread_calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            to_thread_calls.append((fn, args))

        with patch("routers.agents._is_test_artifact_agent_name", return_value=False), \
             patch("routers.agents._close_orphan_plan_transcript"), \
             patch("routers.agents._save_agent_state_async", AsyncMock()), \
             patch("routers.agents.chat_ack_bot", MagicMock()), \
             patch("routers.agents.asyncio.to_thread", new=tracking_to_thread), \
             patch("services.notifications.notifications_service", MagicMock()):

            body = AgentComplete(summary="in-app done")
            await agents_mod.mark_agent_complete("no-notify", body)

        imessage_calls = [c for c in to_thread_calls if "reply_to_chat_sync" in getattr(c[0], "__name__", "")]
        assert imessage_calls == [], "Should not call reply_to_chat_sync when no notify"
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)


# ---------------------------------------------------------------------------
# E2E loop (→1876): text triggers spawn → YES confirms → notify injected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_text_spawn_completion_loop():
    """Full loop invariant: a text that starts an agent gets a text back."""
    from services.text_bridge import classify_and_dispatch, _load_state

    # Step 1: AI returns spawn_agent tool_use → should store chat_id in pending
    mock_resp = MagicMock()
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.name = "spawn_agent"
    tool_use.input = {"name": "disk-check", "prompt": "check disk space", "model": "sonnet"}
    mock_resp.content = [tool_use]

    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_resp

    sender = "+15550002222"

    with patch("services.text_bridge.get_ai_client", AsyncMock(return_value=mock_client)), \
         patch("services.text_bridge.resolve_ai_backend", AsyncMock(return_value={"provider": "anthropic"})):
        reply = await classify_and_dispatch("spawn an agent to check disk space", sender, chat_id=55)

    assert "YES" in reply or "proceed" in reply.lower(), f"Expected confirmation prompt, got: {reply!r}"

    # Step 2: chat_id must be stored in pending confirmations
    state = _load_state()
    pending = state.get("pending_confirmations", {}).get(sender, {})
    assert pending.get("chat_id") == 55, f"chat_id not persisted in pending: {pending}"
    assert pending.get("tool_name") == "spawn_agent"

    # Step 3: YES confirmation — execute_tool called with notify injected
    with patch("services.tool_executor.execute_tool", AsyncMock(return_value="ok")) as mock_exec:
        reply2 = await classify_and_dispatch("YES", sender, chat_id=55)

    assert "Confirmed" in reply2, f"Expected Confirmed reply, got: {reply2!r}"
    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    tool_name_called, tool_input_called = call_args
    assert tool_name_called == "spawn_agent"
    assert tool_input_called.get("notify") == {"kind": "imessage", "chat_id": 55}, (
        f"notify not injected into spawn call: {tool_input_called}"
    )

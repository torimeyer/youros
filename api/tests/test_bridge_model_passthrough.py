"""Regression tests for →1030: bridge model passthrough.

Root cause: agent_mailbox_instruction() hardcoded "model":"sonnet" in its
register curl example. When a bridge-spawned Haiku agent followed the
mailbox instructions and called POST /api/agents/register, it announced
itself as sonnet, overwriting the spawn-time haiku model.

Two-layer fix:
1. agent_mailbox_instruction and agent_mailbox_instruction_short now accept
   a model parameter; spawn_agent passes body.model so the register command
   in the prompt carries the right model string.
2. register_agent preserves the existing spawn-time model when the agent
   row already has a pid (meaning it was REST-spawned with a known model).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Layer 1: mailbox instruction embeds the right model
# ---------------------------------------------------------------------------

def test_mailbox_instruction_default_is_sonnet():
    from routers.agents import agent_mailbox_instruction
    block = agent_mailbox_instruction("my-agent")
    assert '"model": "sonnet"' in block


def test_mailbox_instruction_uses_haiku_when_passed():
    from routers.agents import agent_mailbox_instruction
    block = agent_mailbox_instruction("my-agent", model="haiku")
    assert '"model": "haiku"' in block
    assert '"model": "sonnet"' not in block


def test_mailbox_instruction_short_default_is_sonnet():
    from routers.agents import agent_mailbox_instruction_short
    block = agent_mailbox_instruction_short("my-agent")
    assert '"model":"sonnet"' in block


def test_mailbox_instruction_short_uses_haiku_when_passed():
    from routers.agents import agent_mailbox_instruction_short
    block = agent_mailbox_instruction_short("my-agent", model="haiku")
    assert '"model":"haiku"' in block
    assert '"model":"sonnet"' not in block


def test_mailbox_instruction_uses_opus_when_passed():
    from routers.agents import agent_mailbox_instruction
    block = agent_mailbox_instruction("my-agent", model="opus")
    assert '"model": "opus"' in block


# ---------------------------------------------------------------------------
# Layer 2: register_agent preserves spawn-time model (belt-and-suspenders)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_preserves_haiku_model_from_spawn(client, monkeypatch):
    """When a haiku agent was REST-spawned and then calls /register with
    model='sonnet' (old mailbox template), the /api/agents row must still
    show the haiku model, not sonnet.
    """
    import routers.agents as agents_mod

    agent_name = "test-bridge-passthrough-haiku"
    # Simulate what spawn_agent writes into agent_metadata before the
    # subprocess boots and calls /register.
    agents_mod.agent_metadata[agent_name] = {
        "status": "running",
        "spawned_at": "2026-05-08T00:00:00+00:00",
        "model": "claude-haiku-4-5-20251001",
        "pid": 99999,  # presence of pid = REST-spawned
        "source": "task-bridge",
        "budget": "5",
        "last_heartbeat_at": "2026-05-08T00:00:00+00:00",
    }
    monkeypatch.setattr(agents_mod, "_save_agent_state", lambda: None)

    # Agent boots and calls /register with the OLD mailbox template (sonnet hardcoded).
    resp = await client.post("/api/agents/register", json={
        "name": agent_name,
        "model": "sonnet",
        "task": "fix the thing",
        "source": "claude-code",
    })
    assert resp.status_code == 200, resp.text

    # The spawn-time model must be preserved.
    stored = agents_mod.agent_metadata[agent_name]
    assert stored["model"] == "claude-haiku-4-5-20251001", (
        f"Expected haiku model to be preserved; got {stored['model']!r}"
    )


@pytest.mark.asyncio
async def test_register_without_spawn_still_uses_caller_model(client, monkeypatch):
    """A self-registered agent (no prior spawn record) uses the model it
    declares in its register call.
    """
    import routers.agents as agents_mod

    agent_name = "test-bridge-passthrough-fresh"
    agents_mod.agent_metadata.pop(agent_name, None)
    monkeypatch.setattr(agents_mod, "_save_agent_state", lambda: None)

    resp = await client.post("/api/agents/register", json={
        "name": agent_name,
        "model": "haiku",
        "task": "self-registered haiku agent",
        "source": "claude-code",
    })
    assert resp.status_code == 200, resp.text

    stored = agents_mod.agent_metadata[agent_name]
    assert stored["model"] == "claude-haiku-4-5-20251001", (
        f"Expected haiku to resolve to full model ID; got {stored['model']!r}"
    )

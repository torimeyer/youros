"""Tests for services.model_routing — tier resolution and auto-escalation.

Covers the four required cases from the routing spec:
1. model_tier="sonnet" resolves to claude-sonnet-4-6.
2. model_tier="opus" resolves to claude-opus-4-7.
3. No tier passed defaults to Sonnet.
4. Auto-escalation triggers on simulated Sonnet failure and returns Opus id.

Integration-ish tests (spawn endpoint tier dispatch) require the running
FastAPI app and are marked with ``@pytest.mark.asyncio`` + the shared
``client`` fixture from conftest.
"""

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Unit tests — pure logic, no I/O
# ---------------------------------------------------------------------------

def test_resolve_sonnet_tier():
    from services.model_routing import resolve_model
    assert resolve_model("sonnet") == "claude-sonnet-4-6"


def test_resolve_opus_tier():
    from services.model_routing import resolve_model
    assert resolve_model("opus") == "claude-opus-4-7"


def test_resolve_haiku_tier():
    from services.model_routing import resolve_model
    assert resolve_model("haiku") == "claude-haiku-4-5-20251001"


def test_resolve_none_defaults_to_sonnet():
    from services.model_routing import DEFAULT_MODEL, resolve_model
    assert resolve_model(None) == "claude-sonnet-4-6"
    assert resolve_model(None) == DEFAULT_MODEL


def test_resolve_empty_string_defaults_to_sonnet():
    from services.model_routing import resolve_model
    assert resolve_model("") == "claude-sonnet-4-6"


def test_resolve_full_model_id_passthrough():
    """A caller that already has a full model id should get it back unchanged."""
    from services.model_routing import resolve_model
    assert resolve_model("claude-opus-4-7") == "claude-opus-4-7"
    assert resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_resolve_unknown_tier_falls_back_to_sonnet():
    from services.model_routing import resolve_model
    assert resolve_model("gpt-99") == "claude-sonnet-4-6"


def test_default_tier_is_sonnet():
    from services.model_routing import DEFAULT_TIER, DEFAULT_MODEL, TIER_MODEL_MAP
    assert DEFAULT_TIER == "sonnet"
    assert DEFAULT_MODEL == TIER_MODEL_MAP["sonnet"]
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Escalation logic
# ---------------------------------------------------------------------------

def test_escalation_needed_nonzero_exit():
    from services.model_routing import escalation_needed
    assert escalation_needed("all good", exit_code=1) is True
    assert escalation_needed("", exit_code=127) is True


def test_escalation_needed_needs_opus_signal():
    from services.model_routing import escalation_needed, ESCALATION_SIGNAL
    assert escalation_needed(f"task done {ESCALATION_SIGNAL} please retry", exit_code=0) is True


def test_escalation_not_needed_clean_run():
    from services.model_routing import escalation_needed
    assert escalation_needed("completed successfully", exit_code=0) is False


def test_escalate_to_opus_returns_opus_model():
    from services.model_routing import escalate_to_opus, TIER_MODEL_MAP
    result = escalate_to_opus("test-agent", "exit_code=1")
    assert result == TIER_MODEL_MAP["opus"]
    assert result == "claude-opus-4-7"


def test_escalate_to_opus_writes_log(tmp_path, monkeypatch):
    """escalate_to_opus must append a JSON line to the escalation log."""
    log_file = tmp_path / ".ostk" / "escalation_log.jsonl"
    log_file.parent.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    from services import model_routing
    # Reload to pick up the new env var path.
    import importlib
    importlib.reload(model_routing)

    model_routing.escalate_to_opus("my-agent", "NEEDS_OPUS in output")
    model_routing.escalate_to_opus("my-agent", "exit_code=2")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    event = json.loads(lines[0])
    assert event["agent"] == "my-agent"
    assert event["escalated_to"] == "claude-opus-4-7"
    assert "ts" in event


# ---------------------------------------------------------------------------
# Integration-ish: spawn endpoint honours model_tier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_endpoint_default_tier_is_sonnet(client):
    """AgentSpawn schema default routes to Sonnet; MODEL_MAP in agents resolves it."""
    from models.schemas import AgentSpawn
    from services.model_routing import resolve_model
    import routers.agents as _agents_mod

    body = AgentSpawn(name="test-default", prompt="do something")
    assert body.model == "sonnet"
    assert body.model_tier is None

    # The MODEL_MAP in agents.py must resolve "sonnet" to claude-sonnet-4-6.
    assert _agents_mod.MODEL_MAP["sonnet"] == "claude-sonnet-4-6"
    # resolve_model agrees.
    assert resolve_model(body.model) == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_spawn_schema_accepts_opus_tier():
    """model_tier='opus' in the spawn schema resolves to claude-opus-4-7."""
    from models.schemas import AgentSpawn
    from services.model_routing import resolve_model
    body = AgentSpawn(name="hard-task", prompt="complex reasoning", model_tier="opus")
    assert body.model_tier == "opus"
    assert resolve_model(body.model_tier) == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_full_escalation_flow():
    """Simulate a Sonnet failure that triggers escalation to Opus."""
    from services.model_routing import (
        escalation_needed,
        escalate_to_opus,
        TIER_MODEL_MAP,
        ESCALATION_SIGNAL,
    )

    # Step 1: run on Sonnet — agent emits NEEDS_OPUS
    sonnet_output = f"Partial result. {ESCALATION_SIGNAL}"
    sonnet_exit = 0

    assert escalation_needed(sonnet_output, sonnet_exit) is True

    # Step 2: escalate — get Opus id
    opus_id = escalate_to_opus("my-complex-agent", "NEEDS_OPUS in output")
    assert opus_id == TIER_MODEL_MAP["opus"]
    assert opus_id == "claude-opus-4-7"

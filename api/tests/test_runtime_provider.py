"""Tests for the runtime-provider abstraction (Wave 8, →1891, →1892, →1896).

Background (plain language)
---------------------------
myOS spawns "subagents" — child processes that go off and do a piece of
work (research, a code edit, a build). Today that spawn logic is wired
directly to one runtime: the local ``claude`` Code program. Wave 8 wants
myOS to be able to run on more than one runtime (for example a future
Gemini CLI), and to know up front WHICH features each runtime supports —
some runtimes have plan mode and a live monitor, others do not.

``services.runtime_provider`` introduces that seam:

  * ``Feature`` — the named capabilities a runtime may or may not have
    (subagents, hooks, streaming, isolation, worktrees, plan_mode,
    monitor) — exactly the set enumerated in needle →1892.
  * ``RuntimeProvider`` — a Protocol (an interface) with two members:
      - ``spawn_subagent(...)`` — start a child agent.
      - ``features()`` — return the set of Feature values this runtime
        supports, so callers can adapt instead of crashing.
  * ``DefaultRuntimeProvider`` — the concrete provider that wraps the
    CURRENT claude-code spawn behaviour and reports the full feature set.
  * ``ReducedRuntimeProvider`` — a deliberately smaller reference
    provider (no plan_mode, no monitor) used to prove that switching
    providers changes the capability map without breaking spawn (→1896).

These tests do NOT touch the real spawn path in ``api/routers/agents.py``;
the default provider's spawn function is injectable so we exercise the
seam without launching any subprocess.
"""
from __future__ import annotations

import inspect

import pytest

from services.runtime_provider import (
    DefaultRuntimeProvider,
    Feature,
    ReducedRuntimeProvider,
    RuntimeProvider,
    SpawnRequest,
    SpawnResult,
    default_provider,
)


# --------------------------------------------------------------------------
# Feature enum (→1892)
# --------------------------------------------------------------------------

def test_feature_enum_has_exactly_the_seven_wave8_capabilities():
    """→1892 enumerates the capability set verbatim."""
    expected = {
        "subagents",
        "hooks",
        "streaming",
        "isolation",
        "worktrees",
        "plan_mode",
        "monitor",
    }
    assert {f.value for f in Feature} == expected


def test_feature_members_are_accessible_by_name():
    assert Feature.SUBAGENTS.value == "subagents"
    assert Feature.PLAN_MODE.value == "plan_mode"
    assert Feature.MONITOR.value == "monitor"


# --------------------------------------------------------------------------
# Protocol shape (→1891)
# --------------------------------------------------------------------------

def test_runtime_provider_is_a_runtime_checkable_protocol():
    # A class implementing both methods should satisfy isinstance(), and a
    # class missing them should not. This is the contract callers rely on.
    assert isinstance(DefaultRuntimeProvider(), RuntimeProvider)

    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), RuntimeProvider)


def test_protocol_declares_spawn_subagent_and_features():
    members = set(dir(RuntimeProvider))
    assert "spawn_subagent" in members
    assert "features" in members


def test_default_provider_factory_returns_default_provider():
    assert isinstance(default_provider(), DefaultRuntimeProvider)
    assert isinstance(default_provider(), RuntimeProvider)


# --------------------------------------------------------------------------
# features() per provider (→1896 — each provider reports correct set)
# --------------------------------------------------------------------------

def test_default_provider_reports_the_full_feature_set():
    feats = DefaultRuntimeProvider().features()
    assert feats == set(Feature)
    # supports() is the ergonomic query helper.
    assert DefaultRuntimeProvider().supports(Feature.PLAN_MODE)
    assert DefaultRuntimeProvider().supports(Feature.MONITOR)


def test_reduced_provider_drops_plan_mode_and_monitor():
    feats = ReducedRuntimeProvider().features()
    assert Feature.PLAN_MODE not in feats
    assert Feature.MONITOR not in feats
    # It still supports the core spawn-loop features.
    assert Feature.SUBAGENTS in feats
    assert Feature.HOOKS in feats
    assert Feature.STREAMING in feats
    assert not ReducedRuntimeProvider().supports(Feature.PLAN_MODE)


def test_features_returns_a_set_not_a_mutable_alias():
    """Mutating a returned feature set must not corrupt the provider."""
    p = DefaultRuntimeProvider()
    feats = p.features()
    feats.discard(Feature.MONITOR)
    # Second call is unaffected by the caller's mutation.
    assert Feature.MONITOR in p.features()


def test_switching_providers_changes_capability_map_without_error():
    """→1896: switching providers does not break spawn; capability map differs."""
    providers = [DefaultRuntimeProvider(), ReducedRuntimeProvider()]
    maps = [p.features() for p in providers]
    assert maps[0] != maps[1]
    # Both can still describe their own capabilities and spawn.
    for p in providers:
        assert isinstance(p.features(), set)
        assert p.supports(Feature.SUBAGENTS)


# --------------------------------------------------------------------------
# spawn_subagent (→1891) — seam wraps current behaviour, injectable
# --------------------------------------------------------------------------

def test_spawn_request_carries_the_core_spawn_fields():
    req = SpawnRequest(name="agent-x", prompt="do the thing")
    assert req.name == "agent-x"
    assert req.prompt == "do the thing"
    # Sensible defaults mirroring AgentSpawn.
    assert req.model == "sonnet"
    assert req.isolation is None


def test_default_provider_spawn_delegates_to_injected_spawn_fn():
    """The default provider wraps current behaviour through an injected
    spawn callable so we can model the seam without launching a process."""
    calls = []

    def fake_spawn(req: SpawnRequest) -> SpawnResult:
        calls.append(req)
        return SpawnResult(name=req.name, pid=4242, status="running")

    provider = DefaultRuntimeProvider(spawn_fn=fake_spawn)
    req = SpawnRequest(name="agent-y", prompt="research X", model="opus")
    result = provider.spawn_subagent(req)

    assert len(calls) == 1
    assert calls[0] is req
    assert isinstance(result, SpawnResult)
    assert result.name == "agent-y"
    assert result.pid == 4242
    assert result.status == "running"


def test_default_provider_spawn_accepts_kwargs_form():
    """Callers may pass fields directly instead of building a SpawnRequest."""
    seen = {}

    def fake_spawn(req: SpawnRequest) -> SpawnResult:
        seen["name"] = req.name
        seen["isolation"] = req.isolation
        return SpawnResult(name=req.name, status="running")

    provider = DefaultRuntimeProvider(spawn_fn=fake_spawn)
    result = provider.spawn_subagent(name="agent-z", prompt="p", isolation="worktree")
    assert seen == {"name": "agent-z", "isolation": "worktree"}
    assert result.name == "agent-z"


def test_default_provider_spawn_without_injected_fn_raises_not_implemented():
    """With no spawn_fn wired, the default provider must fail loudly rather
    than silently no-op. Wiring the real spawn path is a later (deferred)
    pass (→1895); until then an unwired provider raises."""
    provider = DefaultRuntimeProvider()
    with pytest.raises(NotImplementedError):
        provider.spawn_subagent(name="agent-q", prompt="p")


def test_spawn_subagent_signature_is_stable():
    sig = inspect.signature(DefaultRuntimeProvider.spawn_subagent)
    # (self, request=None, /, **fields) — request optional, kwargs accepted.
    params = list(sig.parameters)
    assert params[0] == "self"
    assert "request" in params

"""Vendor switch for agent spawning (→2945, spec S007 AC4).

Agent spawning must pick its runtime from the user's saved setting, not a
hidden environment variable, and the provider seam must never route a
non-Claude runtime into the Claude-specific spawn code.

Four contracts:

(a) ``default_provider()`` honours the saved ``default_provider`` setting,
    the value the Settings page provider toggle writes ("claude" |
    "gemini"), over the env default. ``YOUROS_RUNTIME`` stays as a
    test-only override for suites that need to pin a runtime.
(b) With the saved setting on claude, POST /agents/spawn routes through
    ``ClaudeCodeRuntimeProvider.spawn_subagent`` and the endpoint holds
    no vendor branch of its own.
(c) With the saved setting on gemini, the Gemini provider fails loudly in
    plain language and never silently runs the Claude spawn internals.
(d) The →2944 safety rails (metadata row, pid receipt) still hold when a
    spawn reaches the bespoke internals through the provider seam.

All subprocess side effects are mocked; no real agent processes spawn.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services import settings_store as settings_store_module

# Shared endpoint plumbing from the →2944 rails suite (same directory, runs
# in the same targeted batch): a POST helper and the fake-subprocess doubles.
from test_2944_ostk_run_safety_rails import _install_bespoke_doubles, _post_spawn

_TEST_NAMES = {
    "t2945-claude-seam",
    "t2945-gemini-block",
}


@pytest.fixture(autouse=True)
def _clean_agent_metadata():
    import routers.agents as agents_mod

    for n in _TEST_NAMES:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in _TEST_NAMES:
        agents_mod.agent_metadata.pop(n, None)


def _patch_saved_runtime(monkeypatch, value):
    """Make the saved ``default_provider`` setting read as *value*."""

    def fake_get(key, default=None):
        if key == "default_provider":
            return value
        return default

    monkeypatch.setattr(settings_store_module.settings_store, "get", fake_get)


# ---------------------------------------------------------------------------
# (a) The switch reads the saved setting, not the env default
# ---------------------------------------------------------------------------


def test_saved_setting_gemini_selects_gemini_provider(monkeypatch):
    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "gemini")
    from services.gemini_cli_provider import GeminiCliRuntimeProvider
    from services.runtime_provider import default_provider

    assert isinstance(default_provider(), GeminiCliRuntimeProvider)


def test_saved_setting_claude_selects_claude_provider(monkeypatch):
    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "claude")
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.runtime_provider import default_provider

    assert isinstance(default_provider(), ClaudeCodeRuntimeProvider)


def test_missing_setting_defaults_to_claude(monkeypatch):
    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, None)
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.runtime_provider import default_provider

    assert isinstance(default_provider(), ClaudeCodeRuntimeProvider)


def test_settings_read_failure_falls_back_to_claude(monkeypatch):
    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)

    def boom(key, default=None):
        raise RuntimeError("settings file unreadable")

    monkeypatch.setattr(settings_store_module.settings_store, "get", boom)
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.runtime_provider import default_provider

    assert isinstance(default_provider(), ClaudeCodeRuntimeProvider)


def test_env_var_stays_as_test_only_override(monkeypatch):
    """An explicitly set YOUROS_RUNTIME wins over the saved setting, in both
    directions, so suites can pin a runtime without editing user settings
    (S007 verification: "Repeat with YOUROS_RUNTIME=gemini")."""
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.gemini_cli_provider import GeminiCliRuntimeProvider
    from services.runtime_provider import default_provider

    _patch_saved_runtime(monkeypatch, "claude")
    monkeypatch.setenv("YOUROS_RUNTIME", "gemini")
    assert isinstance(default_provider(), GeminiCliRuntimeProvider)

    _patch_saved_runtime(monkeypatch, "gemini")
    monkeypatch.setenv("YOUROS_RUNTIME", "claude")
    assert isinstance(default_provider(), ClaudeCodeRuntimeProvider)


# ---------------------------------------------------------------------------
# (c) Gemini fails loudly, never silently runs the Claude internals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_provider_spawn_fails_loudly_and_never_calls_spawn_fn():
    from services.gemini_cli_provider import GeminiCliRuntimeProvider
    from services.runtime_provider import (
        SpawnNotSupportedError,
        SpawnRequest,
        SpawnResult,
    )

    calls = []

    async def claude_internals(req: SpawnRequest) -> SpawnResult:
        calls.append(req)
        return SpawnResult(name=req.name)

    provider = GeminiCliRuntimeProvider(spawn_fn=claude_internals)
    with pytest.raises(SpawnNotSupportedError) as exc:
        await provider.spawn_subagent(SpawnRequest(name="t2945-unit", prompt="p"))

    message = str(exc.value)
    assert "not yet supported" in message
    assert "Gemini" in message
    assert "Settings" in message, "the error must tell the user where to fix it"
    assert calls == [], "gemini must never silently run the claude spawn internals"


@pytest.mark.asyncio
async def test_gemini_setting_spawn_endpoint_refuses_in_plain_language(monkeypatch):
    import routers.agents as agents_mod

    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "gemini")

    exec_calls = []

    async def _record_exec(*args, **kwargs):
        exec_calls.append(args)
        raise AssertionError("no spawn subprocess may start on the gemini runtime")

    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _record_exec)

    with patch("services.agentfile_parser._find_any_agentfile", return_value=None):
        resp = await _post_spawn(
            {
                "name": "t2945-gemini-block",
                "prompt": "look through the logs and report",
                "model": "sonnet",
                "budget": 1.0,
                "source": "test",
            }
        )

    assert resp.status_code == 501, resp.text
    detail = str(resp.json().get("detail", ""))
    assert "not yet supported" in detail
    assert "Gemini" in detail
    assert exec_calls == [], "the claude spawn path ran despite the gemini setting"
    assert "t2945-gemini-block" not in agents_mod.agent_metadata


# ---------------------------------------------------------------------------
# (b) + (d) Claude spawns through the provider seam with its rails intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_setting_spawns_through_provider_with_2944_rails(monkeypatch):
    import routers.agents as agents_mod
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "claude")
    _install_bespoke_doubles(monkeypatch)

    seen = {}
    real_spawn = ClaudeCodeRuntimeProvider.spawn_subagent

    async def spy(self, request=None, /, **fields):
        seen["provider"] = type(self).__name__
        return await real_spawn(self, request, **fields)

    monkeypatch.setattr(ClaudeCodeRuntimeProvider, "spawn_subagent", spy)

    with patch("services.agentfile_parser._find_any_agentfile", return_value=None):
        resp = await _post_spawn(
            {
                "name": "t2945-claude-seam",
                "prompt": "look through the logs and report",
                "model": "sonnet",
                "budget": 1.0,
                "source": "test",
            }
        )

    assert resp.status_code == 200, resp.text
    assert seen.get("provider") == "ClaudeCodeRuntimeProvider", (
        "the spawn must route through the provider seam, not around it"
    )
    data = resp.json()
    assert "pid" in data, "bespoke internals return the subprocess pid (→2944 receipt)"
    meta = agents_mod.agent_metadata.get("t2945-claude-seam")
    assert meta is not None, "→2944 metadata rail must survive the provider seam"
    assert meta.get("status") == "running"


def test_spawn_endpoint_holds_no_vendor_branch():
    """agents.py may only pick a runtime through default_provider(); it must
    not name or branch on concrete vendor provider classes."""
    import routers.agents as agents_mod

    src = inspect.getsource(agents_mod.spawn_agent)
    assert "default_provider(" in src, "the provider seam must be the spawn route"
    assert "GeminiCliRuntimeProvider" not in src
    assert "ClaudeCodeRuntimeProvider" not in src

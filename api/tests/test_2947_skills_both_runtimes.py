"""Chat skills work on both AI runtimes (→2947, spec S007).

Spec box: "Invoke a skill in chat (`/build`, `/diagnose`) works in both
runtimes." The chat panel posts a skill id to POST /api/skills/run, the
router asks ``default_provider()`` for the active runtime, and that
provider runs the skill its own way: Claude runs a native slash-command
when one exists, otherwise the skill's agentfile recipe; Gemini always
runs the recipe. Switching the saved provider setting changes which
runtime runs the skill with no code change.

Tests land in the same targeted batch as test_skills_runtime_agnostic.py.
All CLI subprocesses are mocked; no real claude or gemini process starts.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.runtime_provider import resolve_skill_agentfile


def _make_proc(returncode: int = 0, stdout: bytes = b"done", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# The two skills the spec names resolve to a recipe on disk
# ---------------------------------------------------------------------------


def test_diagnose_resolves_to_recipe():
    path = resolve_skill_agentfile("/diagnose")
    assert path is not None, "diagnose must resolve to an agentfile recipe"
    assert path.name == "diagnose.agent"
    assert path.exists()


def test_build_resolves_to_builder_recipe():
    """The chat command is /build; the recipe on disk is agents/builder.agent.

    The resolver owns that mapping so both runtimes agree on what /build runs.
    """
    path = resolve_skill_agentfile("build")
    assert path is not None, "build must resolve to an agentfile recipe"
    assert path.name == "builder.agent"
    assert path.exists()


# ---------------------------------------------------------------------------
# The /api/skills/run endpoint accepts the spec-named skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_id", ["build", "diagnose", "/build", "/diagnose"])
async def test_endpoint_accepts_build_and_diagnose(client, skill_id):
    def _close_coro(coro):
        coro.close()

    with patch("asyncio.create_task", side_effect=_close_coro):
        resp = await client.post("/api/skills/run", json={"skill_id": skill_id})
    assert resp.status_code == 200, f"{skill_id} must be an allowed chat skill"
    assert resp.json()["status"] == "started"


@pytest.mark.asyncio
async def test_endpoint_still_rejects_unknown_skills(client):
    resp = await client.post("/api/skills/run", json={"skill_id": "not-a-skill-xyz"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Claude runtime: native slash-command when one exists, recipe otherwise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_runs_diagnose_via_recipe_not_valueerror():
    """Claude has no native /diagnose command, so it must run the agentfile
    recipe through ``claude --print`` instead of crashing. A skill that works
    on Gemini must not raise on Claude."""
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    captured_cmd: list | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _make_proc()

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await ClaudeCodeRuntimeProvider().invoke_skill("diagnose")

    assert captured_cmd is not None, "claude CLI must be invoked for diagnose"
    assert "--print" in captured_cmd
    joined = " ".join(str(a) for a in captured_cmd)
    assert "root cause" in joined.lower(), (
        "the diagnose recipe prompt must reach the claude CLI"
    )


@pytest.mark.asyncio
async def test_claude_runs_build_via_recipe():
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    captured_cmd: list | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _make_proc()

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await ClaudeCodeRuntimeProvider().invoke_skill("build")

    assert captured_cmd is not None, "claude CLI must be invoked for build"
    joined = " ".join(str(a) for a in captured_cmd)
    assert "build agent" in joined.lower(), (
        "the builder recipe prompt must reach the claude CLI"
    )


@pytest.mark.asyncio
async def test_claude_keeps_native_slash_command_for_handoff():
    """Skills with a native Claude command still use it (no recipe detour)."""
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    captured_cmd: list | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _make_proc()

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await ClaudeCodeRuntimeProvider().invoke_skill("handoff")

    assert captured_cmd == ["/usr/local/bin/claude", "--print", "/handoff"]


@pytest.mark.asyncio
async def test_claude_unknown_skill_without_recipe_raises():
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    with pytest.raises(ValueError) as exc:
        await ClaudeCodeRuntimeProvider().invoke_skill("bogus-skill-zzz")
    assert "bogus-skill-zzz" in str(exc.value)


@pytest.mark.asyncio
async def test_claude_recipe_path_strips_api_key_from_env():
    """The recipe fallback must keep subscription billing: no API key leaks."""
    import os

    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    os.environ["ANTHROPIC_API_KEY"] = "sk-test-key"
    captured_env: dict | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env", {})
        return _make_proc()

    try:
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await ClaudeCodeRuntimeProvider().invoke_skill("diagnose")
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    assert captured_env is not None
    assert "ANTHROPIC_API_KEY" not in captured_env


# ---------------------------------------------------------------------------
# Gemini runtime: both spec-named skills run through the recipe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_id, prompt_marker",
    [("diagnose", "root cause"), ("build", "build agent")],
)
async def test_gemini_runs_spec_named_skills_via_recipe(skill_id, prompt_marker):
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    fake_proc = _make_proc()

    with patch(
        "services.gemini_cli_provider._find_gemini_binary",
        return_value="/usr/bin/gemini",
    ):
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ) as mock_exec:
            await GeminiCliRuntimeProvider().invoke_skill(skill_id)

    assert mock_exec.called, f"gemini CLI must be invoked for {skill_id}"
    args = list(mock_exec.call_args.args)
    assert args[0] == "/usr/bin/gemini"
    assert "-p" in args
    joined = " ".join(str(a) for a in args)
    assert prompt_marker in joined.lower(), (
        f"the {skill_id} recipe prompt must reach the gemini CLI"
    )


# ---------------------------------------------------------------------------
# The saved provider setting picks which runtime runs the skill,
# with no code change (the spec's "works in both runtimes" switch)
# ---------------------------------------------------------------------------


def _patch_saved_runtime(monkeypatch, value):
    from services import settings_store as settings_store_module

    def fake_get(key, default=None):
        if key == "default_provider":
            return value
        return default

    monkeypatch.setattr(settings_store_module.settings_store, "get", fake_get)


@pytest.mark.asyncio
async def test_saved_setting_routes_skill_to_gemini(client, monkeypatch):
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "gemini")

    gemini_spy = AsyncMock()
    claude_spy = AsyncMock()
    monkeypatch.setattr(GeminiCliRuntimeProvider, "invoke_skill", gemini_spy)
    monkeypatch.setattr(ClaudeCodeRuntimeProvider, "invoke_skill", claude_spy)

    resp = await client.post("/api/skills/run", json={"skill_id": "diagnose"})
    assert resp.status_code == 200

    # Let the fire-and-forget background task run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert gemini_spy.await_count == 1, "gemini must run the skill when selected"
    assert claude_spy.await_count == 0, "claude must not run when gemini is selected"
    assert gemini_spy.await_args.args[-1] == "diagnose" or (
        "diagnose" in gemini_spy.await_args.args
    )


@pytest.mark.asyncio
async def test_saved_setting_routes_skill_to_claude(client, monkeypatch):
    from services.claude_code_provider import ClaudeCodeRuntimeProvider
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    monkeypatch.delenv("YOUROS_RUNTIME", raising=False)
    _patch_saved_runtime(monkeypatch, "claude")

    gemini_spy = AsyncMock()
    claude_spy = AsyncMock()
    monkeypatch.setattr(GeminiCliRuntimeProvider, "invoke_skill", gemini_spy)
    monkeypatch.setattr(ClaudeCodeRuntimeProvider, "invoke_skill", claude_spy)

    resp = await client.post("/api/skills/run", json={"skill_id": "diagnose"})
    assert resp.status_code == 200

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert claude_spy.await_count == 1, "claude must run the skill when selected"
    assert gemini_spy.await_count == 0, "gemini must not run when claude is selected"

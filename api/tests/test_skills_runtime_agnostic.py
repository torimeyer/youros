"""Runtime-agnostic skills (→1891/1892 follow-up).

Skills (handoff, review, init) must work on any runtime, not just Claude.
Claude runs them as native slash-commands (`claude --print /skill`). Other
runtimes (Gemini) run the skill's *agentfile recipe* through their own CLI.
The skill agentfile under agents/<skill>.agent is the model-neutral source
of truth for what the skill does.

These tests mock the CLI subprocess so they do not require a real gemini
binary to be installed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.runtime_provider import (
    ReducedRuntimeProvider,
    resolve_skill_agentfile,
)


# ---- skill resolution (shared base helper) ---------------------------------


def test_resolve_skill_agentfile_finds_builtin():
    """A built-in skill resolves to agents/<skill>.agent on disk."""
    path = resolve_skill_agentfile("review")
    assert path is not None, "review skill must resolve to a recipe"
    assert path.name == "review.agent"
    assert path.exists()


def test_resolve_skill_agentfile_unknown_returns_none():
    """An unknown skill_id resolves to None (no recipe found)."""
    assert resolve_skill_agentfile("definitely-not-a-skill-xyz") is None


def test_resolve_skill_strips_leading_slash():
    """A slash-prefixed skill id (e.g. '/review') still resolves."""
    path = resolve_skill_agentfile("/review")
    assert path is not None
    assert path.name == "review.agent"


# ---- base provider: clean failure, not a half-spawn ------------------------


@pytest.mark.asyncio
async def test_base_invoke_skill_raises_clearly():
    """A provider with no native skill handler raises a clear NotImplementedError.

    ReducedRuntimeProvider does not override invoke_skill, so it uses the
    base. The base must fail loudly and name the skill, not silently spawn a
    placeholder.
    """
    provider = ReducedRuntimeProvider()
    with pytest.raises(NotImplementedError) as exc:
        await provider.invoke_skill("review")
    assert "review" in str(exc.value)


# ---- Gemini provider: runs the recipe through the gemini CLI ---------------


@pytest.mark.asyncio
async def test_gemini_invoke_skill_runs_recipe_via_gemini_cli():
    """Gemini runs the skill's agentfile recipe prompt through `gemini -p`."""
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    fake_proc.returncode = 0

    with patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/bin/gemini"):
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ) as mock_exec:
            await GeminiCliRuntimeProvider().invoke_skill("review")

    assert mock_exec.called, "gemini CLI must be invoked"
    args = list(mock_exec.call_args.args)
    assert args[0] == "/usr/bin/gemini"
    assert "-p" in args, "must pass the recipe via -p"
    # The recipe prompt from agents/review.agent must reach the CLI.
    joined = " ".join(str(a) for a in args)
    assert "code review" in joined.lower(), "review recipe prompt must be passed to gemini"


@pytest.mark.asyncio
async def test_gemini_invoke_skill_unknown_raises():
    """An unknown skill id raises a clear ValueError naming the skill."""
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    with pytest.raises(ValueError) as exc:
        await GeminiCliRuntimeProvider().invoke_skill("bogus-skill-zzz")
    assert "bogus-skill-zzz" in str(exc.value)


@pytest.mark.asyncio
async def test_gemini_invoke_skill_no_binary_is_graceful():
    """When the gemini binary is missing, invoke_skill logs and returns (no crash)."""
    from services.gemini_cli_provider import GeminiCliRuntimeProvider

    with patch("services.gemini_cli_provider._find_gemini_binary", return_value=None):
        # Must not raise, and returns None (no-op) when the binary is absent.
        result = await GeminiCliRuntimeProvider().invoke_skill("review")
    assert result is None


# ---- the three allowed skills all have a recipe on disk --------------------


@pytest.mark.parametrize("skill_id", ["handoff", "review", "init"])
def test_allowed_skills_have_recipes(skill_id):
    """Every skill the /skills/run endpoint allows must have an agentfile recipe."""
    path = resolve_skill_agentfile(skill_id)
    assert path is not None, f"{skill_id} must have agents/{skill_id}.agent recipe"
    assert path.exists()

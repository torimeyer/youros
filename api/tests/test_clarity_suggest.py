"""Unit tests for services.clarity_suggest (→1565)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(text: str):
    """Return a minimal fake Anthropic response object."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _json_response(proposed_fix: str, rationale: str) -> str:
    return json.dumps({"proposed_fix": proposed_fix, "rationale": rationale})


# ---------------------------------------------------------------------------
# _parse_suggestion
# ---------------------------------------------------------------------------

def test_parse_suggestion_valid_json():
    from services.clarity_suggest import _parse_suggestion
    raw = json.dumps({"proposed_fix": "Do X instead", "rationale": "X is clearer"})
    result = _parse_suggestion(raw, "outcome_concrete")
    assert result["proposed_fix"] == "Do X instead"
    assert result["rationale"] == "X is clearer"


def test_parse_suggestion_strips_markdown_fences():
    from services.clarity_suggest import _parse_suggestion
    raw = "```json\n" + json.dumps({"proposed_fix": "Fix A", "rationale": "Because B"}) + "\n```"
    result = _parse_suggestion(raw, "outcome_concrete")
    assert result["proposed_fix"] == "Fix A"


def test_parse_suggestion_falls_back_on_invalid_json():
    from services.clarity_suggest import _parse_suggestion
    raw = "just some plain text"
    result = _parse_suggestion(raw, "has_ac_checkboxes")
    assert result["proposed_fix"] == raw
    assert "has_ac_checkboxes" in result["rationale"]


def test_parse_suggestion_missing_proposed_fix_key_falls_back():
    from services.clarity_suggest import _parse_suggestion
    raw = json.dumps({"something_else": "value"})
    result = _parse_suggestion(raw, "in_repo_scope")
    assert result["proposed_fix"] == raw


# ---------------------------------------------------------------------------
# suggest_clarification — prompt routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_clarification_uses_outcome_concrete_template():
    """outcome_concrete check gets its dedicated prompt (not the fallback)."""
    from services import clarity_suggest

    captured_prompt: list[str] = []

    async def fake_create(**kwargs):
        captured_prompt.append(kwargs["messages"][0]["content"])
        return _fake_response(_json_response("Fixed description", "Was vague"))

    fake_client = MagicMock()
    fake_client.messages.create = fake_create

    with (
        patch("services.chat_providers._resolve_api_key", AsyncMock(return_value="sk-test")),
        patch("services.chat_providers._get_anthropic_client", return_value=fake_client),
    ):
        result = await clarity_suggest.suggest_clarification(
            "outcome_concrete",
            {"kind": "task", "title": "Fix something", "description": "TBD", "file_text": ""},
        )

    assert result["proposed_fix"] == "Fixed description"
    assert result["rationale"] == "Was vague"
    assert "outcome_concrete" in captured_prompt[0]


@pytest.mark.asyncio
async def test_suggest_clarification_uses_has_ac_checkboxes_template():
    from services import clarity_suggest

    captured: list[str] = []

    async def fake_create(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        return _fake_response(_json_response("- [ ] AC line", "Added ACs"))

    fake_client = MagicMock()
    fake_client.messages.create = fake_create

    with (
        patch("services.chat_providers._resolve_api_key", AsyncMock(return_value="sk-test")),
        patch("services.chat_providers._get_anthropic_client", return_value=fake_client),
    ):
        result = await clarity_suggest.suggest_clarification(
            "has_ac_checkboxes",
            {"kind": "spec", "title": "My Spec", "description": "", "file_text": "# My Spec\n"},
        )

    assert result["proposed_fix"] == "- [ ] AC line"
    assert "has_ac_checkboxes" in captured[0]


@pytest.mark.asyncio
async def test_suggest_clarification_unknown_check_uses_fallback_prompt():
    from services import clarity_suggest

    captured: list[str] = []

    async def fake_create(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        return _fake_response(_json_response("Some fix", "Some reason"))

    fake_client = MagicMock()
    fake_client.messages.create = fake_create

    with (
        patch("services.chat_providers._resolve_api_key", AsyncMock(return_value="sk-test")),
        patch("services.chat_providers._get_anthropic_client", return_value=fake_client),
    ):
        result = await clarity_suggest.suggest_clarification(
            "unknown_check_xyz",
            {"kind": "task", "title": "T", "description": "D", "file_text": ""},
        )

    assert result["proposed_fix"] == "Some fix"
    assert "unknown_check_xyz" in captured[0]


@pytest.mark.asyncio
async def test_suggest_clarification_raises_on_missing_api_key():
    from services import clarity_suggest

    with patch("services.chat_providers._resolve_api_key", AsyncMock(return_value="")):
        with pytest.raises(ValueError, match="No Anthropic API key"):
            await clarity_suggest.suggest_clarification(
                "outcome_concrete",
                {"kind": "task", "title": "T", "description": "D", "file_text": ""},
            )


@pytest.mark.asyncio
async def test_suggest_clarification_passes_correct_model():
    from services import clarity_suggest

    used_model: list[str] = []

    async def fake_create(**kwargs):
        used_model.append(kwargs.get("model", ""))
        return _fake_response(_json_response("Fix", "Reason"))

    fake_client = MagicMock()
    fake_client.messages.create = fake_create

    with (
        patch("services.chat_providers._resolve_api_key", AsyncMock(return_value="sk-test")),
        patch("services.chat_providers._get_anthropic_client", return_value=fake_client),
    ):
        await clarity_suggest.suggest_clarification(
            "in_repo_scope",
            {"kind": "task", "title": "T", "description": "D", "file_text": ""},
            model="claude-sonnet-4-5",
        )

    assert used_model[0] == "claude-sonnet-4-5"


@pytest.mark.asyncio
@pytest.mark.parametrize("check_name", [
    "outcome_concrete", "in_repo_scope", "is_unblocked",
    "has_ac_checkboxes", "no_vague_ac", "has_file_paths", "referenced_files_exist",
])
async def test_each_check_has_dedicated_prompt_template(check_name):
    """Every known check_name maps to a non-empty prompt template."""
    from services.clarity_suggest import _PROMPTS
    assert check_name in _PROMPTS
    assert len(_PROMPTS[check_name]) > 50

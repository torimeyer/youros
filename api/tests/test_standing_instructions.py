"""Tests for the standing instructions auto-draft generator.

The Settings page "Suggest for me" button and the Usage page
"Save standing instructions to raise this" link both call this
service so the user does not have to write their instructions
from a blank textarea. These tests pin:

- The generator actually reads the user's recent chat messages.
- The endpoint returns a list of non-empty suggestions.
- The deterministic fallback kicks in with no API key so the UI
  is never empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import standing_instructions_generator as gen


def _write_chat_history(tmp_path: Path, messages: list[dict]) -> Path:
    """Helper: write a chat_history.json with the given user messages."""
    path = tmp_path / "chat_history.json"
    payload = {
        "tabs": [
            {
                "id": "tab-1",
                "name": "Recent chat",
                "messages": messages,
            }
        ],
        "active_tab_id": "tab-1",
    }
    path.write_text(json.dumps(payload))
    return path


def test_standing_instructions_generator_pulls_recent_chat(tmp_path):
    """The generator's signal collector must include the user's own recent
    chat messages. Without that signal Haiku cannot personalize the
    suggestions so the whole feature becomes generic.
    """
    chat_path = _write_chat_history(
        tmp_path,
        [
            {"role": "user", "content": "Always explain things in plain language."},
            {"role": "assistant", "content": "Got it."},
            {"role": "user", "content": "Prefer Google Calendar for scheduling."},
        ],
    )
    empty_memory = tmp_path / "memory_empty"
    empty_memory.mkdir(exist_ok=True)
    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)

    signals = gen.collect_signals_summary(
        chat_path=chat_path,
        memory_dir_override=empty_memory,
        myos_dir=myos_dir,
    )

    assert "Always explain things in plain language." in signals["chat_samples"]
    assert "Prefer Google Calendar for scheduling." in signals["chat_samples"]
    # Assistant replies must not leak in as user patterns.
    assert "Got it." not in signals["chat_samples"]


def test_standing_instructions_generator_reads_feedback_memory(tmp_path):
    """Memory feedback files are the richest correction signal we have.
    Each feedback_*.md captures a rule the user already taught us. The
    generator must surface the first non-empty line so Haiku sees the
    crisp summary, not the filename.
    """
    memory = tmp_path / "memory"
    memory.mkdir(exist_ok=True)
    (memory / "feedback_plain_language.md").write_text(
        "# Plain language\n\nUser asked me to stop using jargon on the Usage page.\n"
    )
    (memory / "feedback_no_emdash.md").write_text(
        "Never use em-dashes in any user-facing text.\n"
    )
    chat_path = tmp_path / "chat_history.json"  # does not exist, that's fine
    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)

    signals = gen.collect_signals_summary(
        chat_path=chat_path,
        memory_dir_override=memory,
        myos_dir=myos_dir,
    )

    joined = " \n ".join(signals["feedback"])
    assert "stop using jargon" in joined
    assert "Never use em-dashes" in joined


def test_standing_instructions_fallback_when_no_api_key(tmp_path):
    """With no API key the endpoint must still return useful bullets so
    the user sees an editable checklist rather than a dead button."""
    chat_path = _write_chat_history(tmp_path, [])
    memory = tmp_path / "memory"
    memory.mkdir(exist_ok=True)
    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)
    # Connected integrations: only Google (gmail + calendar).
    (myos_dir / "google_token.json").write_text("{}")

    async def _run():
        with patch.object(gen, "_resolve_api_key", AsyncMock(return_value="")):
            return await gen.suggest_standing_instructions(
                chat_path=chat_path,
                memory_dir_override=memory,
                myos_dir=myos_dir,
            )

    import asyncio

    bullets = asyncio.run(_run())
    assert len(bullets) >= 3
    assert any("plain language" in b.lower() for b in bullets)
    assert any("em-dash" in b.lower() or "em dash" in b.lower() for b in bullets)
    # Should respect the connected Google integration signal.
    assert any("google" in b.lower() or "gmail" in b.lower() for b in bullets)


def test_standing_instructions_uses_haiku_when_available(tmp_path):
    """When Haiku is reachable, the generator must send the prompt and
    parse bullets out of the response. This pins the happy path."""
    chat_path = _write_chat_history(
        tmp_path,
        [{"role": "user", "content": "Keep replies short."}],
    )
    memory = tmp_path / "memory"
    memory.mkdir(exist_ok=True)
    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)

    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = (
        "- Keep replies short unless I ask for detail.\n"
        "- Prefer Google Calendar for scheduling.\n"
        "- Never use em-dashes.\n"
    )
    fake_response = MagicMock()
    fake_response.content = [fake_block]

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    fake_anthropic = MagicMock()
    fake_anthropic.AsyncAnthropic = MagicMock(return_value=fake_client)

    async def _run():
        with (
            patch.object(gen, "_resolve_api_key", AsyncMock(return_value="sk-test")),
            patch.dict("sys.modules", {"anthropic": fake_anthropic}),
        ):
            return await gen.suggest_standing_instructions(
                chat_path=chat_path,
                memory_dir_override=memory,
                myos_dir=myos_dir,
            )

    import asyncio

    bullets = asyncio.run(_run())
    assert "Keep replies short unless I ask for detail." in bullets
    assert "Prefer Google Calendar for scheduling." in bullets
    assert "Never use em-dashes." in bullets


@pytest.mark.asyncio
async def test_standing_instructions_suggest_endpoint_returns_list(client):
    """POST /api/settings/standing-instructions/suggest must return a
    JSON object with a ``suggestions`` list. The Settings page renders
    one checkbox per item, so the shape is load-bearing."""
    fake_bullets = [
        "Always explain things in plain language.",
        "Never use em-dashes.",
        "Prefer Google Calendar for scheduling.",
    ]
    with patch(
        "routers.settings.suggest_standing_instructions",
        AsyncMock(return_value=fake_bullets),
    ):
        resp = await client.post("/api/settings/standing-instructions/suggest")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert "suggestions" in body
    assert isinstance(body["suggestions"], list)
    assert body["suggestions"] == fake_bullets


@pytest.mark.asyncio
async def test_standing_instructions_suggest_endpoint_fallback_shape(client):
    """Even with no API key and no signals, the endpoint must never
    return an empty list or an error. The UI treats an empty response
    as "nothing to suggest" which defeats the whole point."""
    with patch(
        "services.standing_instructions_generator._resolve_api_key",
        AsyncMock(return_value=""),
    ):
        resp = await client.post("/api/settings/standing-instructions/suggest")

    assert resp.status_code == 200
    body = resp.json()
    assert "suggestions" in body
    assert isinstance(body["suggestions"], list)
    assert len(body["suggestions"]) >= 3


def test_standing_instructions_parse_bullets_strips_em_dashes():
    """If Haiku returns an em-dash despite instructions, the parser
    must replace it so no em-dash ever reaches the UI."""
    text = "- Keep replies short\u2014unless I ask for detail.\n- Prefer Google."
    out = gen._parse_bullets(text)
    # em-dash gets replaced with ", " so the sentence still reads cleanly.
    assert "\u2014" not in out[0]
    assert out[0].startswith("Keep replies short,")
    assert out[1] == "Prefer Google."


def test_standing_instructions_integration_signals(tmp_path):
    """The integration flags must reflect which token files exist on
    disk. Haiku uses this to bias suggestions toward apps the user
    actually has connected."""
    myos_dir = tmp_path / "myos"
    myos_dir.mkdir(exist_ok=True)
    (myos_dir / "google_token.json").write_text("{}")
    (myos_dir / "slack_token.json").write_text("{}")

    flags = gen._read_integration_flags(myos_dir)
    assert flags["gmail"] is True
    assert flags["calendar"] is True
    assert flags["slack"] is True
    assert flags["github"] is False

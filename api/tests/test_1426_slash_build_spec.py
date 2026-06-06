"""Tests for →1426: /build-spec slash handler in the chat router.

Verifies that the /build-spec <slug> slash command:
- Resolves slug → spec path and calls claim_spec with source=slash
- Returns a human-readable "Building" confirmation with task count
- Returns usage help when no slug or a path-traversal slug is given
- Returns a not-found message when the slug doesn't match any file
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _text_sent(mock_ws: AsyncMock) -> str:
    """Return the text payload from the first 'text' frame sent to the websocket."""
    for call in mock_ws.send_json.call_args_list:
        msg = call.args[0]
        if msg.get("type") == "text":
            return msg["data"]
    return ""


@pytest.mark.asyncio
async def test_build_spec_slash_claims_with_source_slash(tmp_path, monkeypatch):
    """'/build-spec <slug>' resolves the file and records a slash claim."""
    from routers.chat import _handle_slash_command

    specs_dir = tmp_path / ".myos" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "my-feature.md").write_text(
        "---\ntitle: my feature\nstatus: spec\n---\n\n- [ ] do a thing\n"
    )
    monkeypatch.setenv("MYOS_USER_SPECS_DIR", str(specs_dir))

    mock_ws = AsyncMock()
    fake_resp = {"task_ids": ["1234", "5678"], "claim": {"source": "slash"}}

    with patch("routers.specs.claim_spec", new=AsyncMock(return_value=fake_resp)) as mock_claim:
        handled = await _handle_slash_command("/build-spec my-feature", mock_ws, tab_id="abc12345")

    assert handled is True
    mock_claim.assert_called_once()
    args = mock_claim.call_args[0]
    resolved_path, body = args[0], args[1]
    assert "my-feature.md" in resolved_path
    assert body.source == "slash"
    assert body.agent == "chat-abc12345"

    text = _text_sent(mock_ws)
    assert "Building" in text
    assert "my-feature" in text
    assert "2 tasks" in text


@pytest.mark.asyncio
async def test_build_spec_slash_no_slug_returns_usage():
    """/build-spec with no slug returns a usage message without calling claim."""
    from routers.chat import _handle_slash_command

    mock_ws = AsyncMock()

    with patch("routers.specs.claim_spec") as mock_claim:
        handled = await _handle_slash_command("/build-spec", mock_ws)

    assert handled is True
    mock_claim.assert_not_called()
    assert "Usage" in _text_sent(mock_ws)


@pytest.mark.asyncio
async def test_build_spec_slash_path_traversal_rejected():
    """/build-spec ../evil is rejected as invalid slug."""
    from routers.chat import _handle_slash_command

    mock_ws = AsyncMock()

    with patch("routers.specs.claim_spec") as mock_claim:
        handled = await _handle_slash_command("/build-spec ../evil", mock_ws)

    assert handled is True
    mock_claim.assert_not_called()
    text = _text_sent(mock_ws)
    assert "Building" not in text


@pytest.mark.asyncio
async def test_build_spec_slash_not_found(tmp_path, monkeypatch):
    """/build-spec nonexistent returns a not-found message."""
    from routers.chat import _handle_slash_command

    empty_dir = tmp_path / "no-specs"
    empty_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("MYOS_USER_SPECS_DIR", str(empty_dir))

    mock_ws = AsyncMock()

    with patch("routers.specs.claim_spec") as mock_claim:
        handled = await _handle_slash_command("/build-spec no-such-spec-xqzw", mock_ws)

    assert handled is True
    mock_claim.assert_not_called()
    text = _text_sent(mock_ws).lower()
    assert "not found" in text or "no-such-spec" in text


@pytest.mark.asyncio
async def test_build_spec_slash_tab_id_used_in_agent_name(tmp_path, monkeypatch):
    """agent name is 'chat-<tab_id[:8]>' when tab_id is supplied."""
    from routers.chat import _handle_slash_command

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    (specs_dir / "agent-name-spec.md").write_text(
        "---\ntitle: agent name spec\nstatus: spec\n---\n"
    )
    monkeypatch.setenv("MYOS_USER_SPECS_DIR", str(specs_dir))

    mock_ws = AsyncMock()
    fake_resp = {"task_ids": [], "claim": {"source": "slash"}}

    with patch("routers.specs.claim_spec", new=AsyncMock(return_value=fake_resp)) as mock_claim:
        await _handle_slash_command("/build-spec agent-name-spec", mock_ws, tab_id="LONGTABID999")

    body = mock_claim.call_args[0][1]
    assert body.agent == "chat-LONGTABI"  # tab_id[:8]

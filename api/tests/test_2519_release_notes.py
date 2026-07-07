"""Tests for →2519: plain-language release notes in the spec-complete modal.

Covers:
- _fallback_release_notes: deterministic filter when LLM unavailable
- generate_release_notes: happy-path (mocked Haiku) and error fallback
- _fire_spec_complete_notification: persists release_notes to spec frontmatter
- _parse_doc_frontmatter: reads release_notes list from frontmatter
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── _fallback_release_notes ─────────────────────────────────────────────────

def test_fallback_keeps_clean_ac():
    from routers.specs import _fallback_release_notes

    acs = ["Users can now chat with two models at once"]
    result = _fallback_release_notes(acs)
    assert result == ["Users can now chat with two models at once"]


def test_fallback_strips_file_path_acs():
    from routers.specs import _fallback_release_notes

    acs = [
        "api/routers/specs.py is updated",
        "Users can see the dashboard",
    ]
    result = _fallback_release_notes(acs)
    assert not any("specs.py" in r for r in result)
    assert any("dashboard" in r for r in result)


def test_fallback_strips_commit_hash_acs():
    from routers.specs import _fallback_release_notes

    acs = [
        "abc1234f fixes the routing bug",
        "Click the button to open the panel",
    ]
    result = _fallback_release_notes(acs)
    assert not any("abc1234f" in r for r in result)
    assert any("button" in r for r in result)


def test_fallback_strips_test_name_acs():
    from routers.specs import _fallback_release_notes

    acs = [
        "test_spec_complete passes",
        "ReleaseNotesWatcher.test.tsx covers the modal",
        "You can dismiss the modal with Escape",
    ]
    result = _fallback_release_notes(acs)
    assert not any("test_spec" in r for r in result)
    assert not any(".test." in r for r in result)
    assert any("Escape" in r for r in result)


def test_fallback_caps_at_five():
    from routers.specs import _fallback_release_notes

    acs = [f"Clean bullet number {i}" for i in range(10)]
    result = _fallback_release_notes(acs)
    assert len(result) <= 5


def test_fallback_returns_generic_when_all_filtered():
    from routers.specs import _fallback_release_notes

    acs = [
        "api/routers/test_foo.py updated",
        "abc1234def5678 is the commit",
        "test_my_function passes now",
    ]
    result = _fallback_release_notes(acs)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(b, str) and b for b in result)


def test_fallback_truncates_to_first_sentence():
    from routers.specs import _fallback_release_notes

    acs = ["You can use multi-model chat. The old interface is gone. This is new."]
    result = _fallback_release_notes(acs)
    assert result[0] == "You can use multi-model chat"


# ─── generate_release_notes ──────────────────────────────────────────────────

def test_generate_release_notes_returns_bullets_from_llm():
    from routers.specs import generate_release_notes

    fake_text = "You can now chat with Claude and Gemini side by side\nSwitch between models any time\nCompare answers to the same question"
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=fake_text)]

    with patch("routers.specs._make_anthropic_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        mock_factory.return_value = mock_client

        result = generate_release_notes(
            "Multi model chat",
            ["Chat input has Both toggle", "Claude on left", "Gemini on right"],
        )

    assert isinstance(result, list)
    assert 1 <= len(result) <= 5
    assert all(isinstance(b, str) and b for b in result)
    assert any("Claude" in b or "Gemini" in b or "chat" in b.lower() for b in result)


def test_generate_release_notes_falls_back_on_api_error():
    from routers.specs import generate_release_notes

    with patch("routers.specs._make_anthropic_client") as mock_factory:
        mock_factory.side_effect = Exception("no key")

        result = generate_release_notes(
            "My spec",
            ["Users can now do the thing", "It works across all pages"],
        )

    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(b, str) and b for b in result)


def test_generate_release_notes_falls_back_when_llm_call_fails():
    from routers.specs import generate_release_notes

    with patch("routers.specs._make_anthropic_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("rate limit")
        mock_factory.return_value = mock_client

        result = generate_release_notes(
            "My spec",
            ["You can click the button to open settings"],
        )

    assert isinstance(result, list)
    assert len(result) >= 1


# ─── frontmatter persistence ─────────────────────────────────────────────────

def test_fire_spec_complete_notification_writes_release_notes(tmp_path, monkeypatch):
    """_fire_spec_complete_notification persists release_notes to spec frontmatter."""
    import services.ostk as ostk_module
    import config

    spec_file = tmp_path / "my-feature.md"
    spec_file.write_text(
        "---\ntitle: My Feature\nstatus: complete\n---\n\n"
        "## Acceptance Criteria\n\n- [x] Users can do the thing\n- [x] It works across pages\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)

    generated = ["You can now do the thing", "It works across all pages"]

    with patch("routers.specs.generate_release_notes", return_value=generated), \
         patch("routers.specs._fire_spec_complete_notification.__wrapped__", create=True):
        # Patch the notifications service so the function doesn't fail on missing notif store
        from unittest.mock import MagicMock as _MM
        mock_notif = _MM()
        with patch("services.notifications.notifications_service", mock_notif):
            from routers.specs import _fire_spec_complete_notification
            _fire_spec_complete_notification(str(spec_file))

    text = spec_file.read_text()
    assert "release_notes:" in text
    assert "You can now do the thing" in text


# ─── _parse_doc_frontmatter reads release_notes ───────────────────────────────

def test_parse_doc_frontmatter_reads_release_notes_list(tmp_path):
    """When the frontmatter has release_notes: [...], it appears in the doc dict."""
    from services.ostk import OstkService

    spec_file = tmp_path / "my-feature.md"
    spec_file.write_text(
        '---\ntitle: My Feature\nstatus: complete\n'
        'release_notes: ["You can now do the thing", "It works across pages"]\n'
        '---\n\n## Body\n'
    )

    svc = OstkService(cwd=str(tmp_path))
    doc = svc._parse_doc_frontmatter(spec_file, "spec")

    assert "release_notes" in doc
    assert doc["release_notes"] == ["You can now do the thing", "It works across pages"]


def test_parse_doc_frontmatter_release_notes_absent_returns_empty(tmp_path):
    """When frontmatter has no release_notes, doc gets an empty list."""
    from services.ostk import OstkService

    spec_file = tmp_path / "basic.md"
    spec_file.write_text(
        "---\ntitle: Basic\nstatus: draft\n---\n\n## Body\n"
    )

    svc = OstkService(cwd=str(tmp_path))
    doc = svc._parse_doc_frontmatter(spec_file, "draft")

    assert doc.get("release_notes", []) == []

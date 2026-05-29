"""Tests for api/services/spec_constitution.py."""
from __future__ import annotations

from pathlib import Path

import pytest

import api.services.spec_constitution as sc_mod
from api.services.spec_constitution import check_spec_text, load_constitution


@pytest.fixture()
def fake_claude_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PROJECT_ROOT at a tmp dir containing a minimal CLAUDE.md."""
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        "# yourOS\n\n"
        "## Writing Style\n\n"
        "- Never use em-dashes. Use periods, commas, or rewrite the sentence instead.\n"
        "- All user-facing text must be in plain language.\n\n"
        "## Development rules\n\n"
        "- ostk commands first, raw shell as fallback.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc_mod, "PROJECT_ROOT", tmp_path)
    return claude


def test_load_constitution_nonempty(fake_claude_md: Path) -> None:
    result = load_constitution()
    assert len(result) >= 1
    sources = {item["source"] for item in result}
    assert "CLAUDE.md" in sources
    sections = {item["section"] for item in result}
    assert "Writing Style" in sections
    texts = [item["text"] for item in result]
    assert any("em-dash" in t.lower() for t in texts)


def test_load_constitution_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc_mod, "PROJECT_ROOT", tmp_path)
    result = load_constitution()
    assert result == []


def test_check_spec_text_em_dash() -> None:
    result = check_spec_text("This is a spec—with an em-dash.")
    assert len(result) == 1
    assert result[0]["principle"] == "no em-dashes"
    assert "em-dash" in result[0]["detail"].lower()


def test_check_spec_text_clean() -> None:
    result = check_spec_text("This spec text is clean and has no violations.")
    assert result == []

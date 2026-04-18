"""Tests for the /files/beautify-deck router.

We do not call python-pptx or Claude here. The goal is to lock in the
validation layer and cache key so a regression shows up in CI, not in a
live demo.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import main
from routers import beautify


client = TestClient(main.app)


def test_validate_beautified_coerces_unknown_layout():
    out = beautify._validate_beautified({"layout": "funky", "title": "X"})
    assert out["layout"] == "title_and_content"
    assert out["title"] == "X"


def test_validate_beautified_falls_back_on_bad_color():
    out = beautify._validate_beautified({"accent_color": "not-a-color"})
    assert out["accent_color"] == "#3b82f6"


def test_validate_beautified_rejects_unallowed_fonts():
    out = beautify._validate_beautified(
        {"font_pairing": {"heading": "Comic Sans", "body": "Papyrus"}}
    )
    assert out["font_pairing"] == {"heading": "Inter", "body": "Source Sans"}


def test_validate_beautified_keeps_allowed_fonts():
    out = beautify._validate_beautified(
        {"font_pairing": {"heading": "Playfair", "body": "Merriweather"}}
    )
    assert out["font_pairing"] == {"heading": "Playfair", "body": "Merriweather"}


def test_validate_beautified_provides_default_rationale():
    out = beautify._validate_beautified({})
    assert out["rationale"] == "Cleaner layout and clearer wording."


def test_parse_ai_json_handles_fenced_code():
    raw = """```json
{"layout": "quote", "title": "Hello"}
```"""
    parsed = beautify._parse_ai_json(raw)
    assert parsed == {"layout": "quote", "title": "Hello"}


def test_parse_ai_json_raises_on_no_object():
    import pytest

    with pytest.raises(ValueError):
        beautify._parse_ai_json("this is not json")


def test_beautify_rejects_non_pptx_path(tmp_path, monkeypatch):
    fake = tmp_path / "notes.txt"
    fake.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(
        "routers.beautify._resolve_safe_path", lambda p: fake
    )

    resp = client.post("/api/files/beautify-deck", json={"path": str(fake)})
    assert resp.status_code == 400
    assert "pptx" in resp.json()["detail"].lower()


def test_beautify_rejects_missing_file(tmp_path, monkeypatch):
    fake = tmp_path / "missing.pptx"

    monkeypatch.setattr(
        "routers.beautify._resolve_safe_path", lambda p: fake
    )

    resp = client.post("/api/files/beautify-deck", json={"path": str(fake)})
    assert resp.status_code == 400
    assert "not a file" in resp.json()["detail"].lower()

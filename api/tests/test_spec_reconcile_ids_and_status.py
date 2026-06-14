"""Tests for the spec board reconcile endpoints (spec-board cleanup, 2026-06-14).

Two new endpoints in routers/specs.py:

- POST /api/specs/reconcile-ids  -> reconcile_spec_ids()
    Assigns a sequential spec_id (S001, S002, ...) to every spec in
    ~/.youros/specs/ that lacks one. Idempotent: specs that already carry a
    spec_id are skipped. The archive/ subdir is never touched.

- PATCH /api/specs/{spec_path:path}/status -> patch_spec_status(spec_path, body)
    Sets a spec's frontmatter `status:` line. Manual override for cases the
    auto-advance rules do not cover. Validates against the known status set.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import services.ostk as ostk_module
from routers.specs import (
    reconcile_spec_ids,
    patch_spec_status,
    SpecStatusUpdate,
    _read_frontmatter_value,
)
from fastapi import HTTPException


def _spec(spec_id: str | None = None, status: str = "spec") -> str:
    fm = ["---", f"status: {status}", "title: Example"]
    if spec_id:
        fm.append(f"spec_id: {spec_id}")
    fm += ["---", "", "## Problem", "Body text.", ""]
    return "\n".join(fm)


# --------------------------------------------------------------------------
# reconcile_spec_ids
# --------------------------------------------------------------------------

def test_reconcile_assigns_id_to_spec_without_one(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    f = tmp_path / "no-id.md"
    f.write_text(_spec(spec_id=None))

    result = asyncio.run(reconcile_spec_ids())

    assert len(result["assigned"]) == 1
    assert result["assigned"][0]["file"] == "no-id.md"
    assert result["assigned"][0]["spec_id"] == "S001"
    assert _read_frontmatter_value(f.read_text(), "spec_id") == "S001"


def test_reconcile_skips_spec_that_already_has_id(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    f = tmp_path / "has-id.md"
    original = _spec(spec_id="S005")
    f.write_text(original)

    result = asyncio.run(reconcile_spec_ids())

    assert result["assigned"] == []
    assert result["skipped"] == 1
    assert f.read_text() == original  # untouched


def test_reconcile_is_sequential_above_existing_max(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    (tmp_path / "a-existing.md").write_text(_spec(spec_id="S006"))
    (tmp_path / "b-new.md").write_text(_spec(spec_id=None))
    (tmp_path / "c-new.md").write_text(_spec(spec_id=None))

    result = asyncio.run(reconcile_spec_ids())

    ids = sorted(a["spec_id"] for a in result["assigned"])
    # Next two IDs after the existing S006 are S007 and S008.
    assert ids == ["S007", "S008"]


def test_reconcile_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    (tmp_path / "one.md").write_text(_spec(spec_id=None))

    first = asyncio.run(reconcile_spec_ids())
    second = asyncio.run(reconcile_spec_ids())

    assert len(first["assigned"]) == 1
    assert second["assigned"] == []  # nothing left to assign


def test_reconcile_ignores_archive_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    archive = tmp_path / "archive"
    archive.mkdir()
    archived = archive / "old.md"
    archived.write_text(_spec(spec_id=None))

    result = asyncio.run(reconcile_spec_ids())

    assert result["assigned"] == []
    assert _read_frontmatter_value(archived.read_text(), "spec_id") == ""


# --------------------------------------------------------------------------
# patch_spec_status
# --------------------------------------------------------------------------

def test_status_patch_sets_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    f = tmp_path / "s.md"
    f.write_text(_spec(status="ready"))

    result = asyncio.run(
        patch_spec_status(str(f), SpecStatusUpdate(status="done"))
    )

    assert result.get("ok") is True
    assert _read_frontmatter_value(f.read_text(), "status") == "done"


def test_status_patch_rejects_invalid_status(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    f = tmp_path / "s.md"
    f.write_text(_spec(status="ready"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(patch_spec_status(str(f), SpecStatusUpdate(status="bogus")))
    assert exc.value.status_code == 422
    # File left untouched.
    assert _read_frontmatter_value(f.read_text(), "status") == "ready"


def test_status_patch_resolves_tilde_path(tmp_path, monkeypatch):
    """A ``~/.youros/specs/...`` path must resolve (regression: _set_spec_status
    joined the raw ~-path onto PROJECT_ROOT without expanduser, so the file was
    never found and the flip 404'd even though the spec existed)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    specs_dir = tmp_path / ".youros" / "specs"
    specs_dir.mkdir(parents=True)
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    (specs_dir / "tilde.md").write_text(_spec(status="done"))

    result = asyncio.run(
        patch_spec_status(
            "~/.youros/specs/tilde.md", SpecStatusUpdate(status="building")
        )
    )

    assert result.get("ok") is True
    assert _read_frontmatter_value(
        (specs_dir / "tilde.md").read_text(), "status"
    ) == "building"


def test_status_patch_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", tmp_path)
    missing = tmp_path / "nope.md"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(patch_spec_status(str(missing), SpecStatusUpdate(status="done")))
    assert exc.value.status_code == 404

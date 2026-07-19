"""→2955: a spec must not claim complete while its own checkboxes contradict it.

Seven spec files drifted: frontmatter said ``status: complete`` while
their acceptance boxes sat unchecked, because progress used to live in
hidden per-box ledger rows that got closed while the files went
unmaintained (fixed for the future by →2938; the files are now the
single source of truth).

The guard: the /specs listing attaches a truthful warning to any spec
that presents as complete while it still has unchecked boxes, so the
Specs page can show the contradiction instead of a clean Done chip.
Status itself is never rewritten — a spec the user marked done stays
done (informs, never blocks; same precedent as b4fba786).
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import services.ostk as ostk_module
from services.ostk import ostk


def _write_spec(name: str, status: str, boxes: str) -> Path:
    """Write a minimal real spec file into the isolated user specs dir.

    The body references needle →9999 (unknown, so treated as open) to keep
    the auto-archive scan from silently moving the file mid-test.
    """
    specs_dir: Path = ostk_module.USER_SPECS_DIR
    specs_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"title: {name}\n"
        f"status: {status}\n"
        "promoted_at: 2026-07-01T00:00:00Z\n"
        "spec_id: S901\n"
        "---\n\n"
        "## Problem\n\nSomething real. Tracked alongside →9999.\n\n"
        "## Acceptance criteria\n\n"
        f"{boxes}\n"
    )
    path = specs_dir / f"{name}.md"
    path.write_text(text)
    return path


@pytest.mark.asyncio
async def test_complete_spec_with_open_boxes_carries_warning(client, monkeypatch):
    """status:complete + unchecked boxes → the listing keeps the status but
    attaches ac_open_count and a plain-language status_warning."""
    _write_spec(
        "rollup-widgets",
        "complete",
        "- [x] first thing shipped\n- [ ] second thing\n- [ ] third thing",
    )
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = {d["filename"]: d for d in resp.json()["docs"]}
    doc = docs["rollup-widgets.md"]

    # Never blocks: the user's complete stays complete.
    assert doc["status"] == "complete"
    # But it must not present clean: the contradiction is attached.
    assert doc["ac_open_count"] == 2
    assert "2" in doc["status_warning"]
    assert "unchecked" in doc["status_warning"]


@pytest.mark.asyncio
async def test_fully_checked_complete_spec_presents_clean(client, monkeypatch):
    """status:complete with every box checked carries no warning fields."""
    _write_spec(
        "rollup-clean",
        "complete",
        "- [x] first thing shipped\n- [x] second thing shipped",
    )
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = {d["filename"]: d for d in resp.json()["docs"]}
    doc = docs["rollup-clean.md"]

    assert doc["status"] == "complete"
    assert "status_warning" not in doc
    assert "ac_open_count" not in doc


@pytest.mark.asyncio
async def test_building_spec_with_open_boxes_has_no_warning(client, monkeypatch):
    """Open boxes are the normal state while building — no warning."""
    _write_spec(
        "rollup-building",
        "building",
        "- [x] first thing shipped\n- [ ] second thing",
    )
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = {d["filename"]: d for d in resp.json()["docs"]}
    doc = docs["rollup-building.md"]

    assert doc["status"] != "complete"
    assert "status_warning" not in doc


@pytest.mark.asyncio
async def test_singular_warning_wording(client, monkeypatch):
    """Exactly one open box reads as singular, plain language."""
    _write_spec(
        "rollup-one-open",
        "complete",
        "- [x] first thing shipped\n- [ ] one live check pending",
    )
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    docs = {d["filename"]: d for d in resp.json()["docs"]}
    doc = docs["rollup-one-open.md"]

    assert doc["ac_open_count"] == 1
    assert "1 checkbox is" in doc["status_warning"]

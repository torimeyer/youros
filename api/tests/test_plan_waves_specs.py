"""Tests for →1428: include_specs toggle on /tasks/waves.

Asserting that GET /tasks/waves?include_specs=true merges open specs
from ~/.youros/specs/ (excluding archive/) alongside needles.
"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Helpers ---

def _make_task(id="t-1", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


def _write_spec(specs_dir: Path, filename: str, title: str, status: str = "spec") -> None:
    content = (
        f"---\ntitle: {title}\nstatus: {status}\n"
        "created_at: 2026-05-01T00:00:00Z\n---\n\n"
        f"# {title}\n\nSpec body.\n"
    )
    (specs_dir / filename).write_text(content)


@contextmanager
def _patch_plan_waves(**ostk_attrs):
    """Patch ostk + task_labels_store for plan_waves tests."""
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls:
        mock_tls.get_all_assignments = MagicMock(return_value={})
        for attr, val in ostk_attrs.items():
            setattr(mock_ostk, attr, val)
        yield mock_ostk


# --- Tests ---

@pytest.mark.asyncio
async def test_plan_waves_no_include_specs_returns_only_needles(client, tmp_path, monkeypatch):
    """Default (include_specs omitted) returns only needles, no spec items."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rt, "_SPECS_DIR", specs_dir)
    _write_spec(specs_dir, "my-spec.md", "My Feature Spec")

    tasks = [_make_task(id="n1", title="Fix bug")]
    with _patch_plan_waves(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves")

    assert resp.status_code == 200
    body = resp.json()
    all_items = [n for w in body["waves"] for n in w["needles"]]
    spec_items = [n for n in all_items if n.get("type") == "spec"]
    assert spec_items == [], "No specs expected when include_specs is not set"


@pytest.mark.asyncio
async def test_plan_waves_include_specs_true_adds_spec_items(client, tmp_path, monkeypatch):
    """include_specs=true merges open specs into waves alongside needles."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rt, "_SPECS_DIR", specs_dir)
    _write_spec(specs_dir, "my-spec.md", "My Feature Spec")

    tasks = [_make_task(id="n1", title="Fix bug")]
    with _patch_plan_waves(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves?include_specs=true")

    assert resp.status_code == 200
    body = resp.json()
    all_items = [n for w in body["waves"] for n in w["needles"]]
    spec_items = [n for n in all_items if n.get("type") == "spec"]
    assert len(spec_items) == 1, f"Expected 1 spec item, got {len(spec_items)}"
    assert spec_items[0]["title"] == "My Feature Spec"


@pytest.mark.asyncio
async def test_plan_waves_include_specs_needles_have_type_field(client, tmp_path, monkeypatch):
    """When include_specs=true, needle items carry type='needle'."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")
    monkeypatch.setattr(rt, "_SPECS_DIR", tmp_path / "no-specs")

    tasks = [_make_task(id="n1", title="Fix bug")]
    with _patch_plan_waves(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves?include_specs=true")

    assert resp.status_code == 200
    body = resp.json()
    all_items = [n for w in body["waves"] for n in w["needles"]]
    needle_items = [n for n in all_items if n.get("type") == "needle"]
    assert len(needle_items) == 1
    assert needle_items[0]["id"] == "n1"


@pytest.mark.asyncio
async def test_plan_waves_include_specs_excludes_archive(client, tmp_path, monkeypatch):
    """Specs inside archive/ subdirectory are excluded even with include_specs=true."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    archive_dir = specs_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(rt, "_SPECS_DIR", specs_dir)
    _write_spec(archive_dir, "old-spec.md", "Old Archived Spec")
    _write_spec(specs_dir, "active-spec.md", "Active Spec")

    tasks = [_make_task(id="n1", title="Fix bug")]
    with _patch_plan_waves(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves?include_specs=true")

    assert resp.status_code == 200
    body = resp.json()
    all_items = [n for w in body["waves"] for n in w["needles"]]
    spec_titles = [n["title"] for n in all_items if n.get("type") == "spec"]
    assert "Old Archived Spec" not in spec_titles, "Archived specs must be excluded"
    assert "Active Spec" in spec_titles, "Active spec must be included"


@pytest.mark.asyncio
async def test_plan_waves_include_specs_missing_dir_is_noop(client, tmp_path, monkeypatch):
    """When the specs dir does not exist, include_specs=true silently returns only needles."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")
    monkeypatch.setattr(rt, "_SPECS_DIR", tmp_path / "nonexistent-specs")

    tasks = [_make_task(id="n1", title="Fix bug")]
    with _patch_plan_waves(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves?include_specs=true")

    assert resp.status_code == 200
    body = resp.json()
    all_items = [n for w in body["waves"] for n in w["needles"]]
    assert len(all_items) == 1, "Only the needle should be present when specs dir is missing"

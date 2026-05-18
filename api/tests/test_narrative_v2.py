"""Tests for narrative router v2 — promote + list (summary) + single draft (→1451).

AC SC-007: POST /api/narrative/draft/{id}/promote
AC SC-008: GET /api/narrative/drafts (summary fields), GET /api/narrative/draft/{id}

Tests use tmp_path via MYOS_DIR env var — never touch real ~/.myos/.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_draft(tmp_path: Path, draft_id: str, audience: str = "exec", source_count: int = 2) -> dict:
    """Write a fake draft JSON into tmp_path/narratives/ and return the data."""
    narratives_dir = tmp_path / "narratives"
    narratives_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "draft_id": draft_id,
        "audience": audience,
        "window_days": 7,
        "markdown": f"# Exec Update\n\nTest draft {draft_id}",
        "source_refs": [{"kind": "spec", "id": f"s{i}", "title": f"Spec {i}", "meta": {}} for i in range(source_count)],
        "created_at": "2026-05-18T02:00:00+00:00",
    }
    (narratives_dir / f"{draft_id}.json").write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# SC-008: GET /api/narrative/drafts — summary fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_drafts_returns_summary_fields(client, tmp_path):
    """List endpoint returns summary fields: draft_id, created_at, audience, source_count."""
    draft_id = "aaaa-1111-test"
    _write_draft(tmp_path, draft_id, audience="board", source_count=3)

    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    data = resp.json()
    assert "drafts" in data
    assert len(data["drafts"]) == 1
    summary = data["drafts"][0]
    assert summary["draft_id"] == draft_id
    assert summary["audience"] == "board"
    assert summary["source_count"] == 3
    assert "created_at" in summary
    # Must NOT return full markdown in list response
    assert "markdown" not in summary


@pytest.mark.asyncio
async def test_list_drafts_newest_first(client, tmp_path):
    """Drafts are returned newest first (by file mtime)."""
    import time

    narratives_dir = tmp_path / "narratives"
    narratives_dir.mkdir(parents=True, exist_ok=True)

    _write_draft(tmp_path, "older-draft")
    time.sleep(0.05)  # ensure different mtime
    _write_draft(tmp_path, "newer-draft")

    with patch("routers.narrative.NARRATIVES_DIR", narratives_dir):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    ids = [d["draft_id"] for d in resp.json()["drafts"]]
    assert ids[0] == "newer-draft"
    assert ids[1] == "older-draft"


@pytest.mark.asyncio
async def test_list_drafts_empty_when_no_files(client, tmp_path):
    """Returns empty list (not 500) when no drafts exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    assert resp.json()["drafts"] == []


# ---------------------------------------------------------------------------
# SC-008: GET /api/narrative/draft/{id} — single draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_single_draft_returns_full_json(client, tmp_path):
    """Single draft endpoint returns complete draft JSON including markdown."""
    draft_id = "bbbb-2222-test"
    data = _write_draft(tmp_path, draft_id)

    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get(f"/api/narrative/draft/{draft_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["draft_id"] == draft_id
    assert "markdown" in body
    assert body["markdown"] == data["markdown"]
    assert "source_refs" in body


@pytest.mark.asyncio
async def test_get_single_draft_404_when_not_found(client, tmp_path):
    """Returns 404 when draft ID does not exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/draft/no-such-id")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SC-007: POST /api/narrative/draft/{id}/promote
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promote_writes_spec_file(client, tmp_path):
    """Promote writes a markdown spec to ~/.myos/specs/narrative-{id}.md."""
    draft_id = "cccc-3333-test"
    _write_draft(tmp_path, draft_id, audience="exec")

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "999"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    body = resp.json()
    assert "spec_path" in body

    spec_file = Path(body["spec_path"])
    assert spec_file.exists(), "Spec file must be written to disk"

    content = spec_file.read_text()
    assert "title:" in content
    assert "status: spec" in content
    assert f"source: narrative-{draft_id}" in content
    assert "created_at:" in content


@pytest.mark.asyncio
async def test_promote_creates_specs_dir_if_missing(client, tmp_path):
    """Promote creates ~/.myos/specs/ if it doesn't already exist."""
    draft_id = "dddd-4444-test"
    _write_draft(tmp_path, draft_id)

    specs_dir = tmp_path / "specs"
    assert not specs_dir.exists()

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "888"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    assert specs_dir.exists(), "specs/ directory must be created automatically"


@pytest.mark.asyncio
async def test_promote_returns_task_id(client, tmp_path):
    """Promote returns task_id from the created tracking task."""
    draft_id = "eeee-5555-test"
    _write_draft(tmp_path, draft_id)

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "42"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "42"


@pytest.mark.asyncio
async def test_promote_404_when_draft_not_found(client, tmp_path):
    """Promote returns 404 when draft does not exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.post("/api/narrative/draft/no-such-draft/promote")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_creates_exec_update_labeled_task(client, tmp_path):
    """Promote calls create_task with exec-update-appropriate title and source."""
    draft_id = "ffff-6666-test"
    _write_draft(tmp_path, draft_id, audience="board")

    captured_calls = []

    async def _fake_create(body):
        captured_calls.append(body)
        return {"task_id": "77"}

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", side_effect=_fake_create),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    assert len(captured_calls) == 1
    task_body = captured_calls[0]
    assert task_body.source == "narrative"
    assert task_body.source_ref == draft_id
    assert "exec" in task_body.title.lower() or "board" in task_body.title.lower() or "update" in task_body.title.lower()


# ---------------------------------------------------------------------------
# BDD invariant: After promote, spec list includes new spec AND tracking task exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bdd_promote_then_spec_exists_and_task_created(client, tmp_path):
    """BDD invariant: POST /promote → spec file on disk AND tracking task recorded.

    Given a persisted narrative draft,
    When POST /api/narrative/draft/{id}/promote is called,
    Then a spec file at ~/.myos/specs/narrative-{id}.md exists with valid frontmatter,
    And a tracking task was created with source='narrative' and source_ref=draft_id.
    """
    draft_id = "bdd-invariant-test"
    _write_draft(tmp_path, draft_id, audience="exec", source_count=2)

    task_calls = []

    async def _record_task(body):
        task_calls.append(body)
        return {"task_id": "bdd-task-id"}

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", side_effect=_record_task),
    ):
        promote_resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert promote_resp.status_code == 200
    body = promote_resp.json()

    # 1. Spec file exists with valid frontmatter
    spec_path = Path(body["spec_path"])
    assert spec_path.exists()
    content = spec_path.read_text()
    assert "status: spec" in content
    assert f"source: narrative-{draft_id}" in content

    # 2. Tracking task was created
    assert len(task_calls) == 1
    assert task_calls[0].source == "narrative"
    assert task_calls[0].source_ref == draft_id

    # 3. task_id is echoed back
    assert body["task_id"] == "bdd-task-id"

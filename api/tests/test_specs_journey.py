"""End-to-end journey tests for the Specs demo flow.

Covers the full lifecycle from Tori's stage script:

1. Create a draft from a title.
2. Promote the draft to a spec.
3. Break the spec into tasks (decompose).
4. Close every linked task.
5. Verify the spec.
6. Assert the spec's final status is Complete.

Also pins down the specific bug where a spec stayed at ``in-progress``
after every task was closed. The root cause was an ID-format mismatch:
ostk returns task IDs like ``\u2192407`` while the spec front matter
stores bare ``407``. Without normalization the lookup always missed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService


@pytest.fixture
def svc_with_tmp():
    """An OstkService pointed at a fresh tmpdir with ``docs/`` scaffolded."""
    tmpdir = tempfile.mkdtemp()
    (Path(tmpdir) / "docs" / "draft").mkdir(parents=True)
    (Path(tmpdir) / "docs" / "spec").mkdir(parents=True)
    svc = OstkService(cwd=tmpdir)
    return svc, Path(tmpdir)


@pytest.mark.asyncio
async def test_specs_user_journey_full_flow(svc_with_tmp):
    """Draft -> promote -> decompose -> close all tasks -> verify -> complete."""
    svc, tmp = svc_with_tmp

    # --- Step 1: Draft ---
    # Simulate what the API does: ostk creates the draft file, then
    # acceptance criteria are appended. We emulate by writing the file
    # directly so the test does not need a live ostk binary.
    draft_path = tmp / "docs" / "draft" / "e2e-journey-spec.md"
    draft_path.write_text(
        "---\n"
        "title: e2e journey spec\n"
        "status: draft\n"
        "created_at: 2026-04-15T00:00:00Z\n"
        "---\n\n"
        "## What we want\n"
        "A working spec-driven-development wizard.\n\n"
        "## Acceptance criteria\n"
        "- [ ] Wizard starts from a title\n"
        "- [ ] Wizard generates acceptance criteria\n"
        "- [ ] Wizard breaks into tasks\n"
    )

    docs = await svc.list_docs()
    draft = next(d for d in docs if d["filename"] == "e2e-journey-spec.md")
    assert draft["status"] == "draft"
    assert len(draft["acceptance_criteria"]) == 3

    # --- Step 2: Promote to spec ---
    spec_path_rel = "docs/spec/e2e-journey-spec.md"
    spec_path = tmp / "docs" / "spec" / "e2e-journey-spec.md"
    # Move and flip status: what doc_promote does on disk.
    text = draft_path.read_text().replace("status: draft", "status: spec")
    text += "\npromoted_at: 2026-04-15T00:01:00Z\n"
    spec_path.write_text(text)
    draft_path.unlink()

    docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    # No tasks yet -> ready
    assert spec["status"] == "ready"
    assert spec["task_summary"]["total"] == 0

    # --- Step 3: Decompose ---
    # Pretend ostk created needles 901, 902, 903. doc_decompose parses the
    # arrow-prefixed output and writes bare numeric IDs to front matter.
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (
            "\u2192901 Wizard starts from a title\n"
            "\u2192902 Wizard generates acceptance criteria\n"
            "\u2192903 Wizard breaks into tasks"
        )
        decompose_result = await svc.doc_decompose(spec_path_rel)
    assert decompose_result["task_ids"] == ["901", "902", "903"]

    # All three tasks still open: in-progress.
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = [
            {"id": "\u2192901", "title": "one", "status": "open", "priority": "P1"},
            {"id": "\u2192902", "title": "two", "status": "open", "priority": "P1"},
            {"id": "\u2192903", "title": "three", "status": "open", "priority": "P1"},
        ]
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    assert spec["status"] == "in-progress"
    assert spec["task_summary"] == {"total": 3, "open": 3, "closed": 0}

    # --- Step 4: Close every task ---
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = [
            {"id": "\u2192901", "title": "one", "status": "closed", "priority": "P1"},
            {"id": "\u2192902", "title": "two", "status": "closed", "priority": "P1"},
            {"id": "\u2192903", "title": "three", "status": "closed", "priority": "P1"},
        ]
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)

    # The bug: before the fix, this assertion would show closed == 0 and
    # status == "in-progress" because the arrow-prefixed IDs never matched
    # the bare front-matter IDs. After the fix the summary reflects reality.
    assert spec["task_summary"] == {"total": 3, "open": 0, "closed": 3}
    # All tasks closed flips to complete; the Verify step is a separate
    # quality gate and no longer part of the spec status computation.
    assert spec["status"] == "complete"

    # --- Step 5: Verify ---
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = [
            {"id": "\u2192901", "title": "one", "status": "closed", "priority": "P1"},
            {"id": "\u2192902", "title": "two", "status": "closed", "priority": "P1"},
            {"id": "\u2192903", "title": "three", "status": "closed", "priority": "P1"},
        ]
        verify_result = await svc.spec_verify(spec_path_rel)
    assert verify_result["all_met"] is True
    assert verify_result["task_summary"]["closed"] == 3

    # --- Step 6: Final status is Complete ---
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = [
            {"id": "\u2192901", "title": "one", "status": "closed", "priority": "P1"},
            {"id": "\u2192902", "title": "two", "status": "closed", "priority": "P1"},
            {"id": "\u2192903", "title": "three", "status": "closed", "priority": "P1"},
        ]
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    assert spec["status"] == "complete"


@pytest.mark.asyncio
async def test_spec_status_flips_to_complete_when_all_tasks_closed_and_verified(
    svc_with_tmp,
):
    """Regression test for the reported bug.

    Before the fix the spec stayed at in-progress forever because the ID
    comparison between the bare numeric IDs in the front matter and the
    arrow-prefixed IDs from ostk always missed. After the fix, closing
    every task plus running Verify flips the status to Complete.
    """
    svc, tmp = svc_with_tmp
    spec_path_rel = "docs/spec/e2e-bug-repro.md"
    (tmp / "docs" / "spec" / "e2e-bug-repro.md").write_text(
        "---\n"
        "title: e2e bug repro\n"
        "status: spec\n"
        "tasks:\n"
        '  - "501"\n'
        '  - "502"\n'
        "---\n\n"
        "- [ ] Criterion one\n"
        "- [ ] Criterion two\n"
    )

    closed_tasks = [
        {"id": "\u2192501", "title": "A", "status": "closed", "priority": "P1"},
        {"id": "\u2192502", "title": "B", "status": "closed", "priority": "P1"},
    ]

    # All tasks closed flips the spec to complete; Verify is a separate
    # quality gate and no longer gates this status.
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = closed_tasks
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    assert spec["status"] == "complete"
    assert spec["task_summary"]["closed"] == 2

    # Run Verify: this should flip every AC checkbox on disk.
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = closed_tasks
        verify = await svc.spec_verify(spec_path_rel)
    assert verify["all_met"] is True

    # The spec body should now show checked boxes.
    body = (tmp / "docs" / "spec" / "e2e-bug-repro.md").read_text()
    assert "- [x] Criterion one" in body
    assert "- [x] Criterion two" in body

    # After Verify: status is complete.
    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = closed_tasks
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    assert spec["status"] == "complete"


@pytest.mark.asyncio
async def test_spec_status_flips_to_complete_on_all_closed(svc_with_tmp):
    """All builder tasks closed flips the spec to complete.

    Verify is a separate quality step and no longer gates status.
    """
    svc, tmp = svc_with_tmp
    spec_path_rel = "docs/spec/e2e-all-closed.md"
    (tmp / "docs" / "spec" / "e2e-all-closed.md").write_text(
        "---\n"
        "title: e2e all closed\n"
        "status: spec\n"
        "tasks:\n"
        '  - "601"\n'
        "---\n\n"
        "- [ ] Still needs verify\n"
    )

    with patch.object(svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = [
            {"id": "\u2192601", "title": "only", "status": "closed", "priority": "P1"},
        ]
        docs = await svc.list_docs()
    spec = next(d for d in docs if d["path"] == spec_path_rel)
    assert spec["status"] == "complete"


# --- HTTP journey test via the TestClient ---


@pytest.mark.asyncio
async def test_specs_journey_via_http(client, tmp_path, monkeypatch):
    """Walk the demo flow through the real FastAPI routes.

    We mock ``ostk._run`` so the test does not shell out, but every layer
    above that (router -> service -> status computation) runs for real.
    """
    from services import ostk as ostk_module

    # Point the ostk service at an isolated tmpdir so test artifacts do
    # not touch the live project.
    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    # --- Draft ---
    draft_file = tmp_path / "docs" / "draft" / "e2e-http-journey.md"

    async def fake_run(*args, **kwargs):
        if args[:2] == ("doc", "draft"):
            draft_file.write_text(
                "---\n"
                "title: e2e http journey\n"
                "status: draft\n"
                "---\n\n"
                "- [ ] One\n"
                "- [ ] Two\n"
            )
            return str(draft_file.relative_to(tmp_path))
        if args[:2] == ("doc", "promote"):
            spec_file = tmp_path / "docs" / "spec" / "e2e-http-journey.md"
            text = draft_file.read_text().replace("status: draft", "status: spec")
            spec_file.write_text(text)
            draft_file.unlink()
            return str(spec_file.relative_to(tmp_path))
        if args[:2] == ("doc", "decompose"):
            return "\u2192701 One\n\u2192702 Two"
        raise AssertionError(f"unexpected ostk call: {args}")

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    # Also stub the anthropic call in the draft route so no network hits.
    monkeypatch.setattr(
        "services.chat_providers._resolve_api_key",
        AsyncMock(return_value=None),
    )

    resp = await client.post("/api/specs/draft", json={"title": "e2e http journey"})
    assert resp.status_code == 200

    # --- Promote ---
    resp = await client.post(
        "/api/specs/promote",
        json={"path": "docs/draft/e2e-http-journey.md"},
    )
    assert resp.status_code == 200

    # --- Decompose ---
    resp = await client.post(
        "/api/specs/decompose",
        json={"path": "docs/spec/e2e-http-journey.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["task_ids"] == ["701", "702"]

    # --- Mock tasks for list/verify calls ---
    closed_tasks = [
        {"id": "\u2192701", "title": "One", "status": "closed", "priority": "P1"},
        {"id": "\u2192702", "title": "Two", "status": "closed", "priority": "P1"},
    ]

    with patch.object(ostk_module.ostk, "list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_tasks.return_value = closed_tasks

        # All tasks closed flips the spec straight to complete; Verify is
        # a separate quality gate and no longer gates this status.
        resp = await client.get("/api/specs")
        assert resp.status_code == 200
        spec = next(
            d for d in resp.json()["docs"]
            if d["path"] == "docs/spec/e2e-http-journey.md"
        )
        assert spec["task_summary"]["closed"] == 2
        assert spec["status"] == "complete"

        # Verify flips the boxes on disk.
        resp = await client.post(
            "/api/specs/docs/spec/e2e-http-journey.md/verify"
        )
        assert resp.status_code == 200
        assert resp.json()["all_met"] is True

        # After Verify, status is complete.
        resp = await client.get("/api/specs")
        spec = next(
            d for d in resp.json()["docs"]
            if d["path"] == "docs/spec/e2e-http-journey.md"
        )
        assert spec["status"] == "complete"

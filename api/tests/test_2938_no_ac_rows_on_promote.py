"""→2938: promoting a spec must not mint AC rows into the task ledger.

Two invariants:
  (a) doc_promote must never call doc_decompose.
  (b) The progress reported via list_docs must derive from the spec file's
      own checkboxes, not from task-ledger rows.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# (a) doc_promote must not call doc_decompose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_promote_does_not_call_decompose(tmp_path, monkeypatch):
    """doc_promote must move the file and flip front matter without ever
    calling doc_decompose. Before the fix this call minted one ledger row
    per AC checkbox (exhibit A: →2939–2943 from the one-definition spec)."""
    from services import ostk as ostk_module

    specs_dir = tmp_path / "myos_specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    draft = tmp_path / "my-spec.md"
    draft.write_text(
        "---\ntitle: No AC rows test\nstatus: draft\n---\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] First criterion\n"
        "- [ ] Second criterion\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)

    decompose_called = []

    async def spy_decompose(path, auto=False):
        decompose_called.append(path)
        return {"result": "", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", spy_decompose)

    result = await ostk_module.ostk.doc_promote(str(draft))

    assert decompose_called == [], (
        f"doc_promote must not call doc_decompose, but it called it with: {decompose_called}"
    )
    assert Path(result).exists(), "promoted spec file must exist"


# ---------------------------------------------------------------------------
# (b) per-spec progress from list_docs matches file checkboxes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_docs_progress_derives_from_file_checkboxes(tmp_path, monkeypatch):
    """list_docs acceptance_criteria must reflect the file's - [ ] / - [x]
    lines regardless of what (if anything) is in the task ledger.

    A spec with 3 checkboxes (2 unchecked, 1 checked) must be reported as
    [{checked: False}, {checked: False}, {checked: True}], not as empty
    and not derived from decomposed task rows."""
    from services import ostk as ostk_module

    specs_dir = tmp_path / "myos_specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec = specs_dir / "progress-test.md"
    spec.write_text(
        "---\ntitle: Progress test\nstatus: spec\n---\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Criterion one\n"
        "- [ ] Criterion two\n"
        "- [x] Criterion three (done)\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(ostk_module, "USER_DRAFTS_DIR", tmp_path / "myos_drafts")

    # Task ledger is empty — no decomposed rows exist.
    async def empty_list_tasks():
        return []

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", empty_list_tasks)

    docs = await ostk_module.ostk.list_docs()
    matched = [d for d in docs if "progress-test" in d.get("path", "")]
    assert matched, "progress-test spec must appear in list_docs results"

    doc = matched[0]
    ac = doc.get("acceptance_criteria", [])
    assert len(ac) == 3, f"Expected 3 ACs from file checkboxes, got {len(ac)}: {ac}"

    unchecked = [c for c in ac if not c["checked"]]
    checked = [c for c in ac if c["checked"]]
    assert len(unchecked) == 2, f"Expected 2 unchecked, got {unchecked}"
    assert len(checked) == 1, f"Expected 1 checked, got {checked}"
    assert checked[0]["text"] == "Criterion three (done)"


# ---------------------------------------------------------------------------
# (c) /specs/decompose endpoint does NOT call doc_decompose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decompose_endpoint_does_not_call_doc_decompose(client, monkeypatch):
    """POST /specs/decompose must return a plain success message without
    calling ostk.doc_decompose (which mints ledger rows)."""
    from services import ostk as ostk_module

    decompose_called = []

    async def spy_decompose(path, auto=False):
        decompose_called.append(path)
        return {"result": "minted rows", "task_ids": ["999"]}

    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", spy_decompose)

    resp = await client.post(
        "/api/specs/decompose",
        json={"path": "docs/spec/some-spec.md"},
    )
    assert resp.status_code == 200, resp.text
    assert decompose_called == [], (
        f"POST /specs/decompose must not call doc_decompose but called it with: {decompose_called}"
    )


# ---------------------------------------------------------------------------
# (d) _ensure_decomposed does NOT call doc_decompose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_decomposed_does_not_call_doc_decompose(tmp_path, monkeypatch):
    """_ensure_decomposed must return existing tasks (or empty) without ever
    calling doc_decompose when no tasks exist."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    specs_dir = tmp_path / "myos_specs"
    specs_dir.mkdir(parents=True)
    spec = specs_dir / "claim-test.md"
    spec.write_text(
        "---\ntitle: Claim test\nstatus: spec\n---\n\n- [ ] Build it\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", specs_dir)

    async def empty_spec_tasks(path):
        return []

    monkeypatch.setattr(ostk_module.ostk, "spec_tasks", empty_spec_tasks)

    decompose_called = []

    async def spy_decompose(path, auto=False):
        decompose_called.append(path)
        return {"result": "", "task_ids": []}

    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", spy_decompose)

    result = await specs_router._ensure_decomposed(str(spec))

    assert decompose_called == [], (
        f"_ensure_decomposed must not call doc_decompose; called with: {decompose_called}"
    )
    assert result == [], "should return empty list when no tasks exist"

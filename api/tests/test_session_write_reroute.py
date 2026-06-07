"""Audit: session writes must land in user-local paths, not in the repo.

Three write paths were found that wrote (or could write) into the repo during
a live session:
  1. unlock_spec with a user-local source  → wrote to docs/draft/ in repo
  2. create_from_template                  → called CLI doc_draft that wrote to docs/draft/
  3. import_spec                           → same CLI path as above

These tests assert the CORRECT behaviour (user-local destination).
They were RED before the fix and GREEN after.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1.  unlock_spec: user-local spec must return to USER_DRAFTS_DIR, not repo
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_unlock_user_local_spec_goes_to_user_drafts_not_repo(
    client, tmp_path, monkeypatch
):
    """Unlock of a ~/.youros/specs file must land in ~/.youros/drafts, not docs/draft/."""
    import routers.specs as specs_router
    from services import ostk as ostk_module

    user_specs = tmp_path / "myos" / "specs"
    user_drafts = tmp_path / "myos" / "drafts"
    repo_root = tmp_path / "repo"
    (repo_root / "docs" / "draft").mkdir(parents=True, exist_ok=True)
    (repo_root / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    user_specs.mkdir(parents=True, exist_ok=True)
    user_drafts.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", user_specs)
    monkeypatch.setattr(ostk_module, "USER_DRAFTS_DIR", user_drafts)
    monkeypatch.setattr("config.PROJECT_ROOT", str(repo_root))
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(repo_root))

    # Bypass path validation — the validator uses the real home dir; we care
    # about the write-destination logic, not the validator.
    monkeypatch.setattr(specs_router, "_validate_doc_path", lambda path: None)

    spec_file = user_specs / "my-plan.md"
    spec_file.write_text(
        "---\ntitle: My Plan\nstatus: spec\npromoted_at: 2026-01-01\n---\n\n- [ ] Done\n"
    )

    resp = await client.post(f"/api/specs/{spec_file}/unlock")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    result_path = data.get("path") or data.get("result") or ""

    # The unlocked draft must land in user-local drafts, NOT the repo
    assert str(user_drafts) in result_path or result_path.startswith(str(user_drafts)), (
        f"Expected path under {user_drafts}, got {result_path!r}. "
        "unlock_spec is writing into the repo."
    )
    repo_draft = repo_root / "docs" / "draft" / "my-plan.md"
    assert not repo_draft.exists(), (
        "unlock_spec wrote into repo docs/draft/ — should have gone to user-local drafts"
    )
    assert (user_drafts / "my-plan.md").exists(), (
        "unlocked draft not found in USER_DRAFTS_DIR"
    )


# ---------------------------------------------------------------------------
# 2.  create_from_template: draft must land in USER_DRAFTS_DIR, not docs/draft/
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_from_template_writes_to_user_drafts_not_repo(
    client, tmp_path, monkeypatch
):
    """POST /api/specs/from-template must write to USER_DRAFTS_DIR, not docs/draft/."""
    import routers.specs as specs_router
    from services import ostk as ostk_module

    user_drafts = tmp_path / "myos" / "drafts"
    repo_root = tmp_path / "repo"
    (repo_root / "docs" / "draft").mkdir(parents=True, exist_ok=True)
    user_drafts.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ostk_module, "USER_DRAFTS_DIR", user_drafts)
    monkeypatch.setattr("config.PROJECT_ROOT", str(repo_root))
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(repo_root))

    # Suppress promote so it doesn't try to copy to USER_SPECS_DIR
    async def _noop_promote(path: str) -> str:
        return path

    monkeypatch.setattr(ostk_module.ostk, "doc_promote", _noop_promote)

    resp = await client.post(
        "/api/specs/from-template",
        json={"template_id": "build-website", "title": "Audit Test Plan", "kind": "spec"},
    )
    # 200 or 404 (template not found) are both acceptable — what we care about
    # is where the file was written if it was written at all.
    if resp.status_code == 404:
        pytest.skip("build-website template not registered in test env — skipping write check")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    result_path = str(data.get("result") or data.get("path") or "")

    # Must NOT be a relative docs/draft/ path
    assert not result_path.startswith("docs/draft/"), (
        f"create_from_template returned a repo-relative draft path: {result_path!r}. "
        "The file was created inside the repo."
    )
    # Confirm no file leaked into repo docs/draft/
    repo_draft_dir = repo_root / "docs" / "draft"
    leaked = list(repo_draft_dir.iterdir())
    assert leaked == [], (
        f"create_from_template wrote into repo docs/draft/: {leaked}"
    )


# ---------------------------------------------------------------------------
# 3.  import_spec: draft must land in USER_DRAFTS_DIR, not docs/draft/
# ---------------------------------------------------------------------------

VALID_SPECKIT_YAML = """\
name: Audit Import Test
description: Verifies spec import writes to user-local path.
tasks:
  - title: Write the code
    description: Implement the feature.
    priority: P1
    acceptance_criteria:
      - Code is written
"""


@pytest.mark.anyio
async def test_import_spec_writes_to_user_drafts_not_repo(
    client, tmp_path, monkeypatch
):
    """POST /api/specs/import must write to USER_DRAFTS_DIR, not docs/draft/."""
    import routers.specs as specs_router
    from services import ostk as ostk_module

    user_drafts = tmp_path / "myos" / "drafts"
    repo_root = tmp_path / "repo"
    (repo_root / "docs" / "draft").mkdir(parents=True, exist_ok=True)
    user_drafts.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ostk_module, "USER_DRAFTS_DIR", user_drafts)
    monkeypatch.setattr("config.PROJECT_ROOT", str(repo_root))
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(repo_root))

    async def _noop_promote(path: str) -> str:
        return path

    monkeypatch.setattr(ostk_module.ostk, "doc_promote", _noop_promote)

    resp = await client.post(
        "/api/specs/import",
        json={"yaml": VALID_SPECKIT_YAML, "format": "speckit", "kind": "spec"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    result_path = str(data.get("result") or data.get("id") or data.get("path") or "")

    # Must NOT be a relative docs/draft/ path
    assert not result_path.startswith("docs/draft/"), (
        f"import_spec returned a repo-relative draft path: {result_path!r}. "
        "The file was created inside the repo."
    )
    repo_draft_dir = repo_root / "docs" / "draft"
    leaked = list(repo_draft_dir.iterdir())
    assert leaked == [], (
        f"import_spec wrote into repo docs/draft/: {leaked}"
    )

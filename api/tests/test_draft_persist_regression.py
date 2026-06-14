"""Regression tests for the create-draft / create-from-template stale-path bug.

Root cause: when auto-promote was active, doc_promote() moved the file from
USER_DRAFTS_DIR to USER_SPECS_DIR but the endpoint returned the original
USER_DRAFTS_DIR path in `result`. Any follow-up PATCH /body or POST /promote
call using that stale result path got 404 / 400 because the file no longer
existed there.

Fix: create_draft now never auto-promotes (draft stays in USER_DRAFTS_DIR).
     create_from_template returns `promoted_path or draft_path` so result
     always points to the actual file.
"""
from pathlib import Path

import httpx
import pytest

from main import app


# ---------------------------------------------------------------------------
# create_draft: POST /api/specs/draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_draft_result_file_exists():
    """File must exist at the result path immediately after creation."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/specs/draft",
            json={"title": "Regression Draft Exists", "kind": "spec", "fallback_ac": True},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data
    assert Path(data["result"]).exists(), (
        f"Draft file missing at result path: {data['result']}"
    )


@pytest.mark.asyncio
async def test_create_draft_patch_body_succeeds():
    """PATCH /api/specs/{result}/body must return 200, not 404."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/specs/draft",
            json={"title": "Regression Draft Patch", "kind": "spec", "fallback_ac": True},
        )
        assert create_resp.status_code == 200, create_resp.text
        result = create_resp.json()["result"]

        patch_resp = await client.patch(
            f"/api/specs/{result}/body",
            json={"body": "# Updated\n\n- [ ] regression test item\n"},
        )
    assert patch_resp.status_code == 200, (
        f"PATCH body returned {patch_resp.status_code}: {patch_resp.text}. "
        f"Draft result path was: {result}"
    )


@pytest.mark.asyncio
async def test_create_draft_promote_succeeds():
    """POST /api/specs/promote on the result path must return 200, not 400."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/specs/draft",
            json={"title": "Regression Draft Promote", "kind": "spec", "fallback_ac": True},
        )
        assert create_resp.status_code == 200, create_resp.text
        result = create_resp.json()["result"]

        promote_resp = await client.post(
            "/api/specs/promote",
            json={"path": result},
        )
    assert promote_resp.status_code == 200, (
        f"Promote returned {promote_resp.status_code}: {promote_resp.text}. "
        f"Draft result path was: {result}"
    )
    promoted = promote_resp.json().get("result") or promote_resp.json().get("promoted_path")
    assert promoted and Path(promoted).exists(), (
        f"Promoted file missing at: {promoted}"
    )


# ---------------------------------------------------------------------------
# create_from_template: POST /api/specs/from-template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_template_result_file_exists():
    """Result path from from-template must point to an existing file.

    When AC criteria are present in the template the endpoint auto-promotes,
    so result must equal promoted_path (not the stale draft path).
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/specs/from-template",
            json={"template_id": "build-a-website", "title": "Regression Template Test", "kind": "spec"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    result = data.get("result")
    assert result, "No result in response"
    assert Path(result).exists(), (
        f"Result path missing after from-template: {result}. "
        f"promoted_path was: {data.get('promoted_path')}"
    )

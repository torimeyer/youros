"""Regression tests for Drive + Files backend routes.

Verifies that the Drive and Files API endpoints remain mounted. A prior
attempt to unify these under a Documents page was reverted: the
`documents`, `exports`, `diagrams`, and `pdf` routers were orphans with
no committed source, removed in eaa7ffa with provenance. Tests for
those removed routers were deleted in this file as part of the same
cleanup.
"""

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Drive endpoints (api/routers/drive.py)
# ---------------------------------------------------------------------------

async def test_drive_auth_status_still_works(client):
    """GET /api/drive/auth/status should return a valid JSON response, not 404."""
    resp = await client.get("/api/drive/auth/status")
    # 200 if authed, or another non-404 code if not authed
    assert resp.status_code != 404, f"Drive auth/status returned 404: {resp.text}"
    data = resp.json()
    assert "authenticated" in data or "status" in data or "error" in data


async def test_drive_files_endpoint_still_works(client):
    """GET /api/drive/files should return a list (possibly empty when not authed)."""
    resp = await client.get("/api/drive/files")
    # May return 200 with empty list, or 401/403 if not authed.
    # Must not be 404.
    assert resp.status_code != 404, f"Drive files endpoint returned 404: {resp.text}"


async def test_drive_oauth_callback_redirects_to_drive(client):
    """The OAuth callback redirect URL should point to /drive.

    The Documents page was removed with provenance in eaa7ffa, so Drive
    is again its own top-level page. The redirect target must match.
    """
    import sys

    # The drive router is already imported via main.py -> conftest
    drive_mod = sys.modules.get("routers.drive")
    if drive_mod is None:
        pytest.skip("routers.drive not importable")

    url = getattr(drive_mod, "FRONTEND_DRIVE_URL", None)
    assert url is not None, "FRONTEND_DRIVE_URL not defined in drive module"
    assert "/drive" in url, f"FRONTEND_DRIVE_URL does not contain /drive: {url}"


# ---------------------------------------------------------------------------
# Files endpoints (api/routers/files.py)
# ---------------------------------------------------------------------------

async def test_files_preview_endpoint_still_works(client):
    """GET /api/files/preview should accept a path param and respond."""
    resp = await client.get("/api/files/preview", params={"path": "README.md"})
    # The endpoint returns 200 for supported files or 400 for markdown
    # (markdown is handled differently). Either way, not 404.
    assert resp.status_code != 404, f"Files preview returned 404: {resp.text}"


async def test_files_raw_endpoint_still_works(client):
    """GET /api/files/raw should accept a path param and respond."""
    resp = await client.get("/api/files/raw", params={"path": "README.md"})
    assert resp.status_code != 404, f"Files raw returned 404: {resp.text}"

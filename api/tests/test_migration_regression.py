"""Regression tests for Drive + Files → Documents migration.

Verifies that the old Drive and Files API endpoints still respond after
the frontend was unified into a single Documents page. The backend
routers (drive.py, files.py, documents.py, exports.py, diagrams.py,
pdf.py) must all remain registered and reachable.
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


async def test_drive_oauth_callback_redirects_to_documents(client):
    """The OAuth callback redirect URL should point to /documents?tab=drive."""
    # We cannot follow the full OAuth flow, but we can verify the
    # FRONTEND_DRIVE_URL constant in the drive module.
    import importlib
    import sys

    # The drive router is already imported via main.py -> conftest
    drive_mod = sys.modules.get("routers.drive")
    if drive_mod is None:
        pytest.skip("routers.drive not importable")

    url = getattr(drive_mod, "FRONTEND_DRIVE_URL", None)
    assert url is not None, "FRONTEND_DRIVE_URL not defined in drive module"
    assert "/documents" in url, f"FRONTEND_DRIVE_URL does not contain /documents: {url}"
    assert "tab=drive" in url, f"FRONTEND_DRIVE_URL does not contain tab=drive: {url}"


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


# ---------------------------------------------------------------------------
# New routers (documents, exports, diagrams, pdf)
# ---------------------------------------------------------------------------

async def test_documents_router_registered(client):
    """GET /api/documents should return 200 with a list of documents.

    The endpoint returns a bare JSON array (possibly empty) of document
    metadata objects. The page was deprecated but the backend router is
    kept so FCP document flows still work; what matters for this
    regression test is that the route is mounted and returns 200.
    """
    resp = await client.get("/api/documents")
    assert resp.status_code == 200, f"Documents list returned {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list), f"Expected list from /api/documents, got {type(data).__name__}: {data}"


async def test_exports_router_registered(client):
    """POST /api/exports/sheets/costs must be a mounted route.

    We deliberately do NOT invoke the handler. The handler spawns a real
    fcp-sheets subprocess (FCPExporter.export), which hangs in test
    environments and blocked the pre-commit hook. Asserting that the
    route is registered is enough to prove the router was not lost
    during the Drive+Files to Documents migration.
    """
    from main import app

    paths = {r.path for r in app.routes}
    assert "/api/exports/sheets/costs" in paths, (
        f"Exports router did not register /api/exports/sheets/costs. "
        f"Registered paths matching /exports: "
        f"{sorted(p for p in paths if '/exports' in p)}"
    )


async def test_diagrams_router_registered(client):
    """POST /api/diagrams/architecture should respond (not 404)."""
    resp = await client.post(
        "/api/diagrams/architecture",
        json={},
    )
    assert resp.status_code != 404, f"Diagrams router returned 404: {resp.text}"


async def test_pdf_router_registered(client):
    """POST /api/pdf/memo should respond (not 404)."""
    resp = await client.post(
        "/api/pdf/memo",
        json={"title": "regression-test", "content": "smoke"},
    )
    assert resp.status_code != 404, f"PDF router returned 404: {resp.text}"

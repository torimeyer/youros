"""Tests for file sharing with provenance attribution (F5).

Covers:
- POST /api/files/share creates a file share token and URL
- GET /api/shares/{token} returns the file share with provenance data
- Missing file returns 404
- Provenance sidecar is included in the snapshot when present
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.shares import SharesStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store(tmp_path: Path) -> SharesStore:
    return SharesStore(path=tmp_path / "shares.json")


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/files/share
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_file_share_returns_token_and_url(client, tmp_path):
    """POST /api/files/share creates a share and returns token + url."""
    test_file = tmp_path / "report.md"
    test_file.write_text("# Hello\nThis is a test file.")

    store = make_store(tmp_path)

    with (
        patch("routers.shares.shares_store", store),
        patch("routers.projects._resolve_readable_path", return_value=test_file),
        patch("services.provenance.read_sidecar", return_value=None),
    ):
        resp = await client.post(f"/api/files/share?path={test_file}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "token" in data
    assert "url" in data
    assert "/share/" in data["url"]
    assert len(data["token"]) > 8


@pytest.mark.asyncio
async def test_file_share_snapshot_contains_content(client, tmp_path):
    """The share snapshot stores the file content."""
    test_file = tmp_path / "notes.txt"
    test_file.write_text("Line one\nLine two")

    store = make_store(tmp_path)

    with (
        patch("routers.shares.shares_store", store),
        patch("routers.projects._resolve_readable_path", return_value=test_file),
        patch("services.provenance.read_sidecar", return_value=None),
    ):
        resp = await client.post(f"/api/files/share?path={test_file}")

    assert resp.status_code == 200
    token = resp.json()["token"]

    share = store.get_share(token)
    assert share is not None
    assert share["share_type"] == "file"
    assert len(share["content_snapshot"]) == 1
    snap = share["content_snapshot"][0]
    assert snap["file_name"] == "notes.txt"
    assert "Line one" in snap["content"]
    assert snap["is_binary"] is False


@pytest.mark.asyncio
async def test_file_share_includes_provenance(client, tmp_path):
    """Provenance sidecar is captured in the snapshot."""
    test_file = tmp_path / "output.md"
    test_file.write_text("Agent output here.")

    provenance = {
        "agent_name": "my-test-agent",
        "task_id": "→042",
        "created_at": "2026-04-27T12:00:00+00:00",
        "prompt_summary": "generate a report",
    }

    store = make_store(tmp_path)

    with (
        patch("routers.shares.shares_store", store),
        patch("routers.projects._resolve_readable_path", return_value=test_file),
        patch("services.provenance.read_sidecar", return_value=provenance),
    ):
        resp = await client.post(f"/api/files/share?path={test_file}")

    assert resp.status_code == 200
    token = resp.json()["token"]

    share = store.get_share(token)
    snap = share["content_snapshot"][0]
    assert snap["provenance"] is not None
    assert snap["provenance"]["agent_name"] == "my-test-agent"
    assert snap["provenance"]["task_id"] == "→042"


@pytest.mark.asyncio
async def test_public_token_retrieves_file_share_with_provenance(client, tmp_path):
    """GET /api/shares/{token} returns the file share, including attribution data."""
    store = make_store(tmp_path)
    provenance = {"agent_name": "builder-agent", "task_id": "→099"}
    share = store.create_share(
        share_type="file",
        content_ids=["/some/file.md"],
        title="file.md",
        content_snapshot=[{
            "file_name": "file.md",
            "file_path": "/some/file.md",
            "content": "# Report",
            "is_binary": False,
            "provenance": provenance,
        }],
    )
    token = share["token"]

    with patch("routers.shares.shares_store", store):
        resp = await client.get(f"/api/shares/{token}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["share_type"] == "file"
    snap = data["content_snapshot"][0]
    assert snap["file_name"] == "file.md"
    assert snap["provenance"]["agent_name"] == "builder-agent"


@pytest.mark.asyncio
async def test_file_share_missing_file_returns_404(client, tmp_path):
    """POST /api/files/share with a nonexistent path returns 404."""
    missing = tmp_path / "does_not_exist.md"
    store = make_store(tmp_path)

    with (
        patch("routers.shares.shares_store", store),
        patch("routers.projects._resolve_readable_path", return_value=missing),
    ):
        resp = await client.post(f"/api/files/share?path={missing}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_file_share_no_provenance_still_works(client, tmp_path):
    """File shares work even when no provenance sidecar exists."""
    test_file = tmp_path / "plain.txt"
    test_file.write_text("just a plain file")

    store = make_store(tmp_path)

    with (
        patch("routers.shares.shares_store", store),
        patch("routers.projects._resolve_readable_path", return_value=test_file),
        patch("services.provenance.read_sidecar", return_value=None),
    ):
        resp = await client.post(f"/api/files/share?path={test_file}")

    assert resp.status_code == 200
    token = resp.json()["token"]
    share = store.get_share(token)
    snap = share["content_snapshot"][0]
    assert snap["provenance"] is None

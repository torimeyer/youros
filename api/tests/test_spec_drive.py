"""Tests for the Spec Drive sync service and endpoints.

All Drive API calls and file system side effects are mocked so the tests
run without real credentials or internet access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_drive_service(file_result: dict):
    """Return a minimal mock Drive service that returns file_result on create/update."""
    svc = MagicMock()
    files = svc.files.return_value
    files.create.return_value.execute.return_value = file_result
    files.update.return_value.execute.return_value = file_result
    files.list.return_value.execute.return_value = {"files": [{"id": "folder-1"}]}
    return svc


def _write_spec(tmp_path: Path, filename: str = "my-spec.md") -> Path:
    spec_file = tmp_path / filename
    spec_file.write_text("# My Spec\n\n- [ ] Criterion 1\n")
    return spec_file


# ---------------------------------------------------------------------------
# Service: get_drive_link
# ---------------------------------------------------------------------------


def test_get_drive_link_returns_none_when_no_links_file(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    with patch("services.spec_drive._LINKS_PATH", links_path):
        from services.spec_drive import get_drive_link

        assert get_drive_link("docs/draft/missing.md") is None


def test_get_drive_link_returns_stored_link(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(
        json.dumps({"docs/draft/my.md": {"doc_id": "abc", "web_view_link": "https://docs.google.com/abc"}})
    )
    with patch("services.spec_drive._LINKS_PATH", links_path):
        from services.spec_drive import get_drive_link

        result = get_drive_link("docs/draft/my.md")
        assert result is not None
        assert result["doc_id"] == "abc"


def test_get_drive_link_returns_none_for_unknown_path(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(json.dumps({"docs/draft/other.md": {"doc_id": "xyz"}}))
    with patch("services.spec_drive._LINKS_PATH", links_path):
        from services.spec_drive import get_drive_link

        assert get_drive_link("docs/draft/different.md") is None


# ---------------------------------------------------------------------------
# Service: publish_to_drive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_raises_when_spec_not_found(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
    ):
        from services.spec_drive import publish_to_drive

        with pytest.raises(FileNotFoundError):
            await publish_to_drive("docs/draft/nonexistent.md")


@pytest.mark.asyncio
async def test_publish_creates_doc_and_stores_link(tmp_path):
    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "my-spec.md"
    spec_file.write_text("# My Spec\n")

    links_path = tmp_path / "spec_drive_links.json"
    file_result = {
        "id": "doc-111",
        "name": "My Spec",
        "webViewLink": "https://docs.google.com/doc-111",
    }
    mock_svc = _mock_drive_service(file_result)

    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
    ):
        from services.spec_drive import publish_to_drive

        result = await publish_to_drive("docs/draft/my-spec.md")

    assert result["doc_id"] == "doc-111"
    assert "doc-111" in result["web_view_link"]

    stored = json.loads(links_path.read_text())
    assert stored["docs/draft/my-spec.md"]["doc_id"] == "doc-111"


# ---------------------------------------------------------------------------
# Service: sync_to_drive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_when_no_existing_link(tmp_path):
    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec\n")

    links_path = tmp_path / "spec_drive_links.json"
    file_result = {"id": "new-doc", "name": "Spec", "webViewLink": "https://docs.google.com/new"}
    mock_svc = _mock_drive_service(file_result)

    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
    ):
        from services.spec_drive import sync_to_drive

        result = await sync_to_drive("docs/draft/spec.md")

    assert result["action"] == "created"
    assert result["doc_id"] == "new-doc"


@pytest.mark.asyncio
async def test_sync_updates_existing_doc(tmp_path):
    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Updated\n")

    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(
        json.dumps({"docs/draft/spec.md": {"doc_id": "existing-doc", "web_view_link": "https://docs.google.com/existing"}})
    )
    file_result = {"id": "existing-doc", "name": "Spec", "webViewLink": "https://docs.google.com/existing"}
    mock_svc = _mock_drive_service(file_result)

    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
    ):
        from services.spec_drive import sync_to_drive

        result = await sync_to_drive("docs/draft/spec.md")

    assert result["action"] == "updated"
    assert result["doc_id"] == "existing-doc"
    mock_svc.files.return_value.update.assert_called_once()


@pytest.mark.asyncio
async def test_sync_raises_when_spec_missing_and_link_exists(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(
        json.dumps({"docs/draft/gone.md": {"doc_id": "d1", "web_view_link": "https://x"}})
    )
    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
    ):
        from services.spec_drive import sync_to_drive

        with pytest.raises(FileNotFoundError):
            await sync_to_drive("docs/draft/gone.md")


# ---------------------------------------------------------------------------
# Service: pull_from_drive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_raises_when_no_link(tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
    ):
        from services.spec_drive import pull_from_drive

        with pytest.raises(ValueError, match="No Drive link found"):
            await pull_from_drive("docs/draft/unlinked.md")


@pytest.mark.asyncio
async def test_pull_writes_drive_content_to_local_file(tmp_path):
    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)

    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(
        json.dumps({"docs/draft/spec.md": {"doc_id": "d-abc", "web_view_link": "https://x"}})
    )

    drive_content = "# Updated from Drive\n\n- [ ] New criterion\n"

    mock_svc = MagicMock()
    mock_svc.files.return_value.export_media.return_value = MagicMock()

    import io

    class _FakeDownloader:
        def __init__(self, buf, _req):
            buf.write(drive_content.encode())

        def next_chunk(self):
            return None, True

    with (
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
        patch("googleapiclient.http.MediaIoBaseDownload", _FakeDownloader),
    ):
        from services.spec_drive import pull_from_drive

        result = await pull_from_drive("docs/draft/spec.md")

    assert result["updated"] is True
    written = (spec_dir / "spec.md").read_text()
    assert "Updated from Drive" in written


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_endpoint_returns_401_when_not_authed(client, tmp_path):
    with patch("services.google_auth.TOKEN_PATH", tmp_path / "no_token.json"):
        resp = await client.post("/api/specs/docs%2Fdraft%2Fspec.md/publish")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_link_endpoint_returns_not_linked(client, tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    with patch("services.spec_drive._LINKS_PATH", links_path):
        resp = await client.get("/api/specs/docs%2Fdraft%2Funlinked.md/drive-link")
    assert resp.status_code == 200
    assert resp.json()["linked"] is False


@pytest.mark.asyncio
async def test_drive_link_endpoint_returns_link_when_published(client, tmp_path):
    links_path = tmp_path / "spec_drive_links.json"
    links_path.write_text(
        json.dumps({"docs/draft/spec.md": {"doc_id": "d-xyz", "web_view_link": "https://docs.google.com/d-xyz"}})
    )
    with patch("services.spec_drive._LINKS_PATH", links_path):
        resp = await client.get("/api/specs/docs%2Fdraft%2Fspec.md/drive-link")
    assert resp.status_code == 200
    data = resp.json()
    assert data["linked"] is True
    assert data["doc_id"] == "d-xyz"


@pytest.mark.asyncio
async def test_publish_endpoint_creates_doc(client, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "tok"}))

    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec\n")

    links_path = tmp_path / "spec_drive_links.json"
    file_result = {"id": "new-123", "name": "Spec", "webViewLink": "https://docs.google.com/new-123"}
    mock_svc = _mock_drive_service(file_result)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
    ):
        resp = await client.post("/api/specs/docs%2Fdraft%2Fspec.md/publish")

    assert resp.status_code == 200
    assert resp.json()["doc_id"] == "new-123"


@pytest.mark.asyncio
async def test_sync_endpoint_returns_action(client, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "tok"}))

    spec_dir = tmp_path / "docs" / "draft"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec\n")

    links_path = tmp_path / "spec_drive_links.json"
    file_result = {"id": "s-1", "name": "Spec", "webViewLink": "https://docs.google.com/s-1"}
    mock_svc = _mock_drive_service(file_result)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.spec_drive._LINKS_PATH", links_path),
        patch("services.spec_drive.PROJECT_ROOT", str(tmp_path)),
        patch("services.spec_drive._build_drive_service", return_value=mock_svc),
    ):
        resp = await client.post("/api/specs/docs%2Fdraft%2Fspec.md/sync")

    assert resp.status_code == 200
    assert resp.json()["action"] == "created"


@pytest.mark.asyncio
async def test_pull_endpoint_returns_404_when_no_link(client, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "tok"}))
    links_path = tmp_path / "spec_drive_links.json"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.spec_drive._LINKS_PATH", links_path),
    ):
        resp = await client.post("/api/specs/docs%2Fdraft%2Funlinked.md/pull")

    assert resp.status_code == 404

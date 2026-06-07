"""Tests for POST /api/files/import-from-drive."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def _drive_file_meta(
    file_id: str = "file-abc",
    name: str = "Report.pdf",
    mime: str = "application/pdf",
    size: str = "1024",
) -> dict:
    return {"id": file_id, "name": name, "mimeType": mime, "size": size}


class TestImportFromDrive:
    def test_requires_authentication(self, client: TestClient):
        with patch("services.google_auth.is_authenticated", return_value=False):
            resp = client.post(
                "/api/files/import-from-drive",
                json={"drive_file_id": "file-abc"},
            )
        assert resp.status_code == 401

    def test_rejects_missing_file_id(self, client: TestClient):
        with patch("services.google_auth.is_authenticated", return_value=True):
            resp = client.post(
                "/api/files/import-from-drive",
                json={"drive_file_id": ""},
            )
        assert resp.status_code == 400

    def test_downloads_native_pdf_and_writes_sidecar(
        self, client: TestClient, tmp_path: Path
    ):
        meta = _drive_file_meta(name="Slides.pdf", mime="application/pdf")
        fake_bytes = b"%PDF-1.4 fake content"

        mock_service = MagicMock()
        mock_service.files().get().execute.return_value = meta

        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.google_auth.get_credentials", return_value={"access_token": "tok"}),
            patch("googleapiclient.discovery.build", return_value=mock_service),
            patch("google.oauth2.credentials.Credentials"),
            patch(
                "googleapiclient.http.MediaIoBaseDownload",
                side_effect=lambda buf, req: _FakeDownloader(buf, fake_bytes),
            ),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            resp = client.post(
                "/api/files/import-from-drive",
                json={"drive_file_id": "file-abc"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "Slides.pdf" in data["name"]
        assert data["provenance"]["source"] == "google-drive"
        assert data["provenance"]["drive_file_id"] == "file-abc"

    def test_google_native_doc_exported_as_pdf(
        self, client: TestClient, tmp_path: Path
    ):
        meta = _drive_file_meta(
            name="Q1 Report",
            mime="application/vnd.google-apps.document",
            size="0",
        )
        fake_bytes = b"%PDF-1.4 fake pdf"

        mock_service = MagicMock()
        mock_service.files().get().execute.return_value = meta

        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.google_auth.get_credentials", return_value={"access_token": "tok"}),
            patch("googleapiclient.discovery.build", return_value=mock_service),
            patch("google.oauth2.credentials.Credentials"),
            patch(
                "googleapiclient.http.MediaIoBaseDownload",
                side_effect=lambda buf, req: _FakeDownloader(buf, fake_bytes),
            ),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            resp = client.post(
                "/api/files/import-from-drive",
                json={"drive_file_id": "native-doc"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Google Doc exported as PDF should have .pdf extension.
        assert data["name"].endswith(".pdf")

    def test_file_written_to_imports_dir(
        self, client: TestClient, tmp_path: Path
    ):
        meta = _drive_file_meta(name="Budget.pdf", mime="application/pdf")
        fake_bytes = b"%PDF-budget"

        mock_service = MagicMock()
        mock_service.files().get().execute.return_value = meta

        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.google_auth.get_credentials", return_value={"access_token": "tok"}),
            patch("googleapiclient.discovery.build", return_value=mock_service),
            patch("google.oauth2.credentials.Credentials"),
            patch(
                "googleapiclient.http.MediaIoBaseDownload",
                side_effect=lambda buf, req: _FakeDownloader(buf, fake_bytes),
            ),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            resp = client.post(
                "/api/files/import-from-drive",
                json={"drive_file_id": "budget-id"},
            )

        assert resp.status_code == 200
        imports_dir = tmp_path / ".youros" / "files" / "imports"
        assert imports_dir.exists(), "imports dir should be created"
        files = list(imports_dir.iterdir())
        # Expect the file + sidecar.
        names = [f.name for f in files]
        assert any("Budget.pdf" in n and not n.endswith(".json") for n in names)
        sidecar_files = [n for n in names if n.endswith(".json")]
        assert sidecar_files, "provenance sidecar should exist"
        sidecar_path = next(
            imports_dir / n for n in names if n.endswith(".json")
        )
        provenance = json.loads(sidecar_path.read_text())
        assert provenance["source"] == "google-drive"
        assert provenance["drive_file_id"] == "budget-id"
        assert "imported_at" in provenance


# ---------------------------------------------------------------------------
# Fake downloader helper
# ---------------------------------------------------------------------------

class _FakeDownloader:
    """Minimal MediaIoBaseDownload stand-in that writes bytes and signals done."""

    def __init__(self, buf, content: bytes):
        self._buf = buf
        self._content = content
        self._done = False

    def next_chunk(self):
        if not self._done:
            self._buf.write(self._content)
            self._done = True
        return None, True

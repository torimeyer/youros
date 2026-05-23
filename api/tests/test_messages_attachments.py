"""Tests for HEIC attachment transcoding (→1633).

The /imessage/attachment endpoint must transcode HEIC/HEIF files to JPEG
via sips before serving them, so browsers can display them. Non-HEIC files
are served directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


def _is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# _transcode_heic_to_jpeg unit tests
# ---------------------------------------------------------------------------

class TestTranscodeHeicToJpeg:
    def test_returns_none_when_sips_fails(self, tmp_path):
        from routers.imessage import _transcode_heic_to_jpeg
        heic = tmp_path / "test.heic"
        heic.write_bytes(b"\x00" * 10)
        with patch("routers.imessage.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = _transcode_heic_to_jpeg(heic)
        assert result is None

    def test_returns_cache_path_on_success(self, tmp_path):
        from routers.imessage import _transcode_heic_to_jpeg, _jpeg_cache_path
        heic = tmp_path / "photo.heic"
        heic.write_bytes(b"\x00" * 10)

        expected = _jpeg_cache_path(heic)

        def fake_sips(cmd, capture_output, timeout):
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_bytes(b"\xff\xd8\xff")  # minimal JPEG magic
            return MagicMock(returncode=0)

        with patch("routers.imessage.subprocess.run", side_effect=fake_sips):
            result = _transcode_heic_to_jpeg(heic)

        assert result == expected
        assert result.exists()

    def test_uses_cache_on_second_call(self, tmp_path):
        from routers.imessage import _transcode_heic_to_jpeg, _jpeg_cache_path
        heic = tmp_path / "cached.heic"
        heic.write_bytes(b"\x00" * 10)

        expected = _jpeg_cache_path(heic)
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_bytes(b"\xff\xd8\xff")

        with patch("routers.imessage.subprocess.run") as mock_run:
            result = _transcode_heic_to_jpeg(heic)
            mock_run.assert_not_called()

        assert result == expected

    def test_handles_timeout_gracefully(self, tmp_path):
        import subprocess as _sp
        from routers.imessage import _transcode_heic_to_jpeg
        heic = tmp_path / "slow.heic"
        heic.write_bytes(b"\x00" * 10)

        with patch("routers.imessage.subprocess.run", side_effect=_sp.TimeoutExpired("sips", 15)):
            result = _transcode_heic_to_jpeg(heic)

        assert result is None


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

class TestAttachmentEndpoint:
    @pytest.mark.skipif(not _is_macos(), reason="iMessage only on macOS")
    def test_non_heic_served_directly(self, client, tmp_path):
        allowed = Path.home() / "Library" / "Messages" / "Attachments"
        fake_path = allowed / "00" / "photo.jpg"

        with (
            patch("routers.imessage.Path.resolve", return_value=fake_path),
            patch.object(Path, "exists", return_value=True),
            patch("routers.imessage.FileResponse") as mock_fr,
        ):
            mock_fr.return_value = MagicMock(status_code=200)
            resp = client.get("/api/imessage/attachment", params={"path": str(fake_path)})
        assert resp.status_code != 403

    @pytest.mark.skipif(not _is_macos(), reason="iMessage only on macOS")
    def test_heic_triggers_transcoding(self, client, tmp_path):
        # Real directory tree + real HEIC stub so exists() works without mocking.
        # Patch _allowed_attachments_dir so the security check uses tmp_path.
        fake_attachments = tmp_path.resolve() / "Attachments"
        att_dir = fake_attachments / "00"
        att_dir.mkdir(parents=True)
        fake_heic = att_dir / "IMG_2328.HEIC"
        fake_heic.write_bytes(b"\x00" * 4)

        fake_jpeg = tmp_path / "converted.jpg"
        fake_jpeg.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")

        with (
            patch("routers.imessage._require_macos"),
            patch("routers.imessage._allowed_attachments_dir", return_value=fake_attachments),
            patch("routers.imessage._transcode_heic_to_jpeg", return_value=fake_jpeg),
        ):
            resp = client.get("/api/imessage/attachment", params={"path": str(fake_heic)})
        assert resp.status_code == 200
        assert "image/jpeg" in resp.headers.get("content-type", "")

    @pytest.mark.skipif(not _is_macos(), reason="iMessage only on macOS")
    def test_heic_transcoding_failure_returns_502(self, client, tmp_path):
        fake_attachments = tmp_path.resolve() / "Attachments"
        att_dir = fake_attachments / "00"
        att_dir.mkdir(parents=True)
        fake_heic = att_dir / "broken.heic"
        fake_heic.write_bytes(b"\x00" * 4)

        with (
            patch("routers.imessage._require_macos"),
            patch("routers.imessage._allowed_attachments_dir", return_value=fake_attachments),
            patch("routers.imessage._transcode_heic_to_jpeg", return_value=None),
        ):
            resp = client.get("/api/imessage/attachment", params={"path": str(fake_heic)})
        assert resp.status_code == 502

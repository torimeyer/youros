"""Tests for Google Drive integration endpoints.

All Drive API calls and file-system side effects are mocked so the tests
run without real credentials or internet access.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_drive_files(n: int = 3) -> list[dict]:
    return [
        {
            "id": f"file-{i}",
            "name": f"Document {i}.gdoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-04-01T00:00:00Z",
            "iconLink": "",
            "webViewLink": f"https://docs.google.com/document/d/file-{i}",
            "size": None,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Auth status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_status_not_authenticated(client, tmp_path):
    """When no token exists, authenticated should be False."""
    token_path = tmp_path / "google_token.json"
    creds_path = tmp_path / "google_credentials.json"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["email"] is None


@pytest.mark.asyncio
async def test_drive_auth_status_authenticated(client, tmp_path):
    """When a token exists, authenticated should be True."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text("{}")

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


# ---------------------------------------------------------------------------
# Auth URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_url_no_credentials_file(client, tmp_path):
    """Without a credentials file, the URL endpoint should return 400."""
    creds_path = tmp_path / "google_credentials.json"  # does not exist

    with patch("services.google_auth.CREDENTIALS_PATH", creds_path):
        resp = await client.get("/api/drive/auth/url")

    assert resp.status_code == 400
    assert "credentials" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_drive_auth_url_returns_google_url(client, tmp_path):
    """With credentials present, returns a Google OAuth URL."""
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-id",
                    "client_secret": "test-secret",
                }
            }
        )
    )

    with patch("services.google_auth.CREDENTIALS_PATH", creds_path):
        resp = await client.get("/api/drive/auth/url")

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert "accounts.google.com" in url
    assert "test-id" in url
    assert "drive.readonly" in url


# ---------------------------------------------------------------------------
# Auth URL redirect_uri
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_url_contains_correct_redirect_uri(client, tmp_path):
    """The auth URL must contain the exact redirect_uri the backend expects."""
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "test-id",
                    "client_secret": "test-secret",
                }
            }
        )
    )

    with patch("services.google_auth.CREDENTIALS_PATH", creds_path):
        resp = await client.get("/api/drive/auth/url")

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert "http%3A%2F%2Flocalhost%3A37373%2Fapi%2Fdrive%2Fauth%2Fcallback" in url


# ---------------------------------------------------------------------------
# Auth callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_callback_error_param(client):
    """An error parameter from Google should redirect with ?error= (not 400)."""
    resp = await client.get(
        "/api/drive/auth/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=access_denied" in resp.headers["location"]
    assert "localhost:5173/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_invalid_state(client):
    """An unknown state should redirect with ?error=invalid_state."""
    resp = await client.get(
        "/api/drive/auth/callback?code=abc&state=bogus-state",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid_state" in resp.headers["location"]
    assert "localhost:5173/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_success(client, tmp_path):
    """A valid code+state should exchange tokens and redirect to Drive with ?connected=true."""
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["valid-state"] = True

    token_path = tmp_path / "google_token.json"
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps(
            {"installed": {"client_id": "cid", "client_secret": "csec"}}
        )
    )

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch("services.google_auth.DRIVE_CACHE_DIR", tmp_path / "drive_cache"),
        patch("routers.drive._sync_file_list", new=AsyncMock(return_value=[])),
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"access_token": "ya29.ok", "refresh_token": "1//ok"}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        resp = await client.get(
            "/api/drive/auth/callback?code=good-code&state=valid-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "connected=true" in resp.headers["location"]
    assert "localhost:5173/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_token_exchange_failure(client, tmp_path):
    """When token exchange fails, redirect with ?error= instead of a 500."""
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["fail-state"] = True

    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})
    )

    with (
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch(
            "routers.drive.exchange_code",
            side_effect=RuntimeError("token exchange error"),
        ),
    ):
        resp = await client.get(
            "/api/drive/auth/callback?code=bad-code&state=fail-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "error=token_exchange_failed" in resp.headers["location"]
    assert "localhost:5173/drive" in resp.headers["location"]


# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_files_not_authenticated(client, tmp_path):
    """Without auth, file list should return 401."""
    token_path = tmp_path / "google_token.json"  # does not exist

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/drive/files")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_files_returns_cached(client, tmp_path):
    """When a fresh cache exists, return it without hitting Drive API."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()
    index_path = cache_dir / "index.json"
    fake_files = _make_drive_files(2)
    index_path.write_text(json.dumps(fake_files))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._INDEX_PATH", index_path),
    ):
        resp = await client.get("/api/drive/files")

    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is True
    assert len(data["files"]) == 2


@pytest.mark.asyncio
async def test_drive_files_fetches_when_cache_stale(client, tmp_path):
    """When the cache is old, fetch fresh data from Drive."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()
    index_path = cache_dir / "index.json"
    fake_files = _make_drive_files(5)
    index_path.write_text(json.dumps(fake_files))

    # Make the cache appear 2 hours old.
    old_time = time.time() - 7300
    import os

    os.utime(index_path, (old_time, old_time))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._INDEX_PATH", index_path),
        patch("routers.drive._fetch_drive_files", new=AsyncMock(return_value=fake_files)),
    ):
        resp = await client.get("/api/drive/files")

    assert resp.status_code == 200
    assert resp.json()["cached"] is False


@pytest.mark.asyncio
async def test_drive_files_search_skips_cache(client, tmp_path):
    """A ?q= search should bypass the cache and hit Drive directly."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()
    index_path = cache_dir / "index.json"
    index_path.write_text(json.dumps(_make_drive_files(10)))

    fresh = _make_drive_files(1)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._INDEX_PATH", index_path),
        patch("routers.drive._fetch_drive_files", new=AsyncMock(return_value=fresh)),
    ):
        resp = await client.get("/api/drive/files?q=Document+0")

    assert resp.status_code == 200
    assert len(resp.json()["files"]) == 1


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_preview_not_authenticated(client, tmp_path):
    """Preview should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"  # does not exist

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/drive/files/some-id/preview")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_preview_cache_hit(client, tmp_path):
    """A cached PDF should be returned without calling the Drive API."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()
    pdf_bytes = b"%PDF-1.4 fake-pdf"
    (cache_dir / "file-abc.pdf").write_bytes(pdf_bytes)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
    ):
        resp = await client.get("/api/drive/files/file-abc/preview")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == pdf_bytes


@pytest.mark.asyncio
async def test_drive_preview_cache_miss_exports_pdf(client, tmp_path):
    """On cache miss for a Google Doc, export as PDF and cache it."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()

    fake_meta = {
        "id": "doc-id",
        "name": "My Doc",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/...",
        "size": None,
    }
    fake_pdf = b"%PDF-1.4 exported"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch("routers.drive._export_as_pdf", new=AsyncMock(return_value=fake_pdf)),
    ):
        resp = await client.get("/api/drive/files/doc-id/preview")

    assert resp.status_code == 200
    assert resp.content == fake_pdf
    # Verify it was cached on disk.
    cached = (cache_dir / "doc-id.pdf").read_bytes()
    assert cached == fake_pdf


@pytest.mark.asyncio
async def test_drive_preview_non_exportable_returns_json(client, tmp_path):
    """Non-previewable file types return JSON with previewable=false."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()

    fake_meta = {
        "id": "zip-id",
        "name": "archive.zip",
        "mimeType": "application/zip",
        "webViewLink": "https://drive.google.com/...",
        "size": "0",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/files/zip-id/preview")

    assert resp.status_code == 200
    data = resp.json()
    assert data["previewable"] is False
    assert "webViewLink" in data


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_sync_not_authenticated(client, tmp_path):
    """Sync should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/drive/sync")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_sync_success(client, tmp_path):
    """Sync should refresh the cache and report the file count."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    cache_dir.mkdir()
    index_path = cache_dir / "index.json"
    fresh = _make_drive_files(4)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._INDEX_PATH", index_path),
        patch("routers.drive._fetch_drive_files", new=AsyncMock(return_value=fresh)),
    ):
        resp = await client.post("/api/drive/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["file_count"] == 4

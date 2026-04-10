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
    assert "http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fdrive%2Fauth%2Fcallback" in url


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
    assert "localhost:3010/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_invalid_state(client):
    """An unknown state should redirect with ?error=invalid_state."""
    resp = await client.get(
        "/api/drive/auth/callback?code=abc&state=bogus-state",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid_state" in resp.headers["location"]
    assert "localhost:3010/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_success(client, tmp_path):
    """A valid code+state should exchange tokens and redirect to Drive with ?connected=true."""
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["valid-state"] = {"return_to": "http://localhost:3010/drive", "expires": 9999999999}

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
    assert "localhost:3010/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_token_exchange_failure(client, tmp_path):
    """When token exchange fails, redirect with ?error= instead of a 500."""
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["fail-state"] = {"return_to": "http://localhost:3010/drive", "expires": 9999999999}

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
    assert "localhost:3010/drive" in resp.headers["location"]


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


# ---------------------------------------------------------------------------
# Credentials upload
# ---------------------------------------------------------------------------


def _valid_credentials_json() -> bytes:
    """Return a minimal valid Google credentials JSON (installed app format)."""
    return json.dumps(
        {
            "installed": {
                "client_id": "test-client-id.apps.googleusercontent.com",
                "client_secret": "test-secret",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        }
    ).encode()


@pytest.mark.asyncio
async def test_drive_credentials_upload_valid_saves_file(client, tmp_path):
    """A valid credentials file should be saved to the expected path and return ok=true."""
    creds_path = tmp_path / "google_credentials.json"

    with patch("routers.drive.CREDENTIALS_PATH", creds_path):
        resp = await client.post(
            "/api/drive/credentials",
            files={"file": ("credentials.json", _valid_credentials_json(), "application/json")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert creds_path.exists()
    saved = json.loads(creds_path.read_bytes())
    assert "installed" in saved


@pytest.mark.asyncio
async def test_drive_credentials_upload_web_format(client, tmp_path):
    """Credentials in 'web' app format should also be accepted."""
    creds_path = tmp_path / "google_credentials.json"
    web_creds = json.dumps(
        {
            "web": {
                "client_id": "web-id.apps.googleusercontent.com",
                "client_secret": "web-secret",
                "redirect_uris": ["http://localhost:8000/api/drive/auth/callback"],
            }
        }
    ).encode()

    with patch("routers.drive.CREDENTIALS_PATH", creds_path):
        resp = await client.post(
            "/api/drive/credentials",
            files={"file": ("credentials.json", web_creds, "application/json")},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_drive_credentials_upload_invalid_json_returns_error(client, tmp_path):
    """Non-JSON content should return ok=false with a plain language error."""
    creds_path = tmp_path / "google_credentials.json"

    with patch("routers.drive.CREDENTIALS_PATH", creds_path):
        resp = await client.post(
            "/api/drive/credentials",
            files={"file": ("creds.json", b"this is not json!!!", "application/json")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data
    assert len(data["error"]) > 0
    assert not creds_path.exists()


@pytest.mark.asyncio
async def test_drive_credentials_upload_missing_keys_returns_error(client, tmp_path):
    """A JSON file that lacks required fields should return ok=false."""
    creds_path = tmp_path / "google_credentials.json"
    bad_creds = json.dumps({"some_random_key": "some_value"}).encode()

    with patch("routers.drive.CREDENTIALS_PATH", creds_path):
        resp = await client.post(
            "/api/drive/credentials",
            files={"file": ("creds.json", bad_creds, "application/json")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data
    assert not creds_path.exists()


@pytest.mark.asyncio
async def test_drive_auth_status_includes_credentials_present_field(client, tmp_path):
    """The status endpoint must include credentials_present so the frontend knows which step to show."""
    token_path = tmp_path / "google_token.json"
    creds_path = tmp_path / "google_credentials.json"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "credentials_file_present" in data
    assert data["credentials_file_present"] is False

    # Now write a creds file and check again.
    creds_path.write_text("{}")
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp2 = await client.get("/api/drive/auth/status")

    assert resp2.json()["credentials_file_present"] is True


# ---------------------------------------------------------------------------
# needs_reauth flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_status_needs_reauth_missing_scope(client, tmp_path):
    """When the token lacks the drive.file scope, needs_reauth should be True."""
    token_path = tmp_path / "google_token.json"
    # Token without drive.file scope in the 'scope' field.
    token_path.write_text(
        json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/drive.readonly"})
    )
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text("{}")

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is True


@pytest.mark.asyncio
async def test_drive_auth_status_no_reauth_when_scope_present(client, tmp_path):
    """When the token includes drive.file scope, needs_reauth should be False."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": (
                "https://www.googleapis.com/auth/drive.readonly "
                "https://www.googleapis.com/auth/drive.file"
            ),
        })
    )
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text("{}")

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")

    assert resp.status_code == 200
    assert resp.json()["needs_reauth"] is False


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_upload_not_authenticated(client, tmp_path):
    """Upload should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/drive/files/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_upload_no_write_scope(client, tmp_path):
    """Upload should return 403 when the drive.file scope is missing."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/drive.readonly"})
    )

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/drive/files/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_drive_upload_success(client, tmp_path):
    """A valid upload should call the Drive API and return the new file's metadata."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive.file",
        })
    )

    fake_created = {
        "id": "new-file-id",
        "name": "test.txt",
        "webViewLink": "https://drive.google.com/file/d/new-file-id/view",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_or_create_myos_folder", new=AsyncMock(return_value="folder-id")),
        patch("routers.drive._sync_file_list", new=AsyncMock(return_value=[])),
        patch("routers.drive._build_drive_service") as mock_svc,
    ):
        mock_files = MagicMock()
        mock_files.create.return_value.execute.return_value = fake_created
        mock_svc.return_value.files.return_value = mock_files

        resp = await client.post(
            "/api/drive/files/upload",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "new-file-id"
    assert data["name"] == "test.txt"
    assert "webViewLink" in data


# ---------------------------------------------------------------------------
# Create folder endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_create_folder_not_authenticated(client, tmp_path):
    """Create folder should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/drive/folders", json={"name": "My Folder"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_create_folder_no_write_scope(client, tmp_path):
    """Create folder should return 403 when the drive.file scope is missing."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/drive.readonly"})
    )

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/drive/folders", json={"name": "My Folder"})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_drive_create_folder_empty_name(client, tmp_path):
    """Create folder should return 400 when name is empty."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive.file",
        })
    )

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/drive/folders", json={"name": "   "})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_drive_create_folder_success(client, tmp_path):
    """Create folder should call Drive API and return folder metadata."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive.file",
        })
    )

    fake_folder = {
        "id": "folder-xyz",
        "name": "My Folder",
        "webViewLink": "https://drive.google.com/drive/folders/folder-xyz",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._build_drive_service") as mock_svc,
    ):
        mock_files = MagicMock()
        mock_files.create.return_value.execute.return_value = fake_folder
        mock_svc.return_value.files.return_value = mock_files

        resp = await client.post("/api/drive/folders", json={"name": "My Folder"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "folder-xyz"
    assert data["name"] == "My Folder"


# ---------------------------------------------------------------------------
# Delete (trash) endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_delete_not_authenticated(client, tmp_path):
    """Delete should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.delete("/api/drive/files/some-id")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_delete_no_write_scope(client, tmp_path):
    """Delete should return 403 when the drive.file scope is missing."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/drive.readonly"})
    )

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.delete("/api/drive/files/some-id")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_drive_delete_success(client, tmp_path):
    """Delete should move the file to trash and return ok=true."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive.file",
        })
    )

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._sync_file_list", new=AsyncMock(return_value=[])),
        patch("routers.drive._build_drive_service") as mock_svc,
    ):
        mock_files = MagicMock()
        mock_files.update.return_value.execute.return_value = {}
        mock_svc.return_value.files.return_value = mock_files

        resp = await client.delete("/api/drive/files/file-to-delete")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Thumbnail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_thumbnail_not_authenticated(client, tmp_path):
    """Thumbnail should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/drive/files/some-id/thumbnail")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_thumbnail_returns_link_when_available(client, tmp_path):
    """When a file has a thumbnail, return its URL."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "doc-id",
        "name": "My Doc",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/...",
        "size": None,
        "thumbnailLink": "https://lh3.googleusercontent.com/thumb=s220",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/files/doc-id/thumbnail")

    assert resp.status_code == 200
    data = resp.json()
    assert data["thumbnailLink"] is not None
    assert "s800" in data["thumbnailLink"]
    assert data["name"] == "My Doc"


@pytest.mark.asyncio
async def test_drive_thumbnail_returns_null_when_no_thumbnail(client, tmp_path):
    """When a file has no thumbnail, return thumbnailLink=null."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "zip-id",
        "name": "archive.zip",
        "mimeType": "application/zip",
        "webViewLink": "https://drive.google.com/...",
        "size": "1024",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/files/zip-id/thumbnail")

    assert resp.status_code == 200
    data = resp.json()
    assert data["thumbnailLink"] is None


@pytest.mark.asyncio
async def test_drive_thumbnail_enlarges_size_parameter(client, tmp_path):
    """The thumbnail endpoint should request a larger thumbnail (s800)."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "doc-id",
        "name": "My Doc",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/...",
        "size": None,
        "thumbnailLink": "https://lh3.googleusercontent.com/thumb=s220",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/files/doc-id/thumbnail")

    data = resp.json()
    # Original had =s220, should be changed to =s800
    assert "=s800" in data["thumbnailLink"]
    assert "=s220" not in data["thumbnailLink"]


@pytest.mark.asyncio
async def test_drive_get_file_meta_includes_thumbnail_field(client, tmp_path):
    """The _get_file_meta function should request the thumbnailLink field."""
    import inspect
    from routers.drive import _get_file_meta

    source = inspect.getsource(_get_file_meta)
    assert "thumbnailLink" in source

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


@pytest.mark.asyncio
async def test_drive_auth_status_cached_path_is_fast(client, tmp_path):
    """Warm cache hits for /drive/auth/status must return under 50ms."""
    import time as _time

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/drive",
    }))
    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text("{}")

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        first = await client.get("/api/drive/auth/status")
        assert first.status_code == 200

        start = _time.perf_counter()
        second = await client.get("/api/drive/auth/status")
        elapsed = _time.perf_counter() - start

    assert second.status_code == 200
    assert second.json() == first.json()
    assert elapsed < 0.050, f"cached path took {elapsed*1000:.1f}ms"


@pytest.mark.asyncio
async def test_drive_auth_status_cache_invalidates_on_credentials_upload(client, tmp_path):
    """Uploading credentials via /drive/credentials must invalidate the status cache."""
    from services import connections_cache

    token_path = tmp_path / "google_token.json"
    creds_path = tmp_path / "google_credentials.json"

    # Prime the cache with a no-creds result.
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
    ):
        resp = await client.get("/api/drive/auth/status")
    assert resp.json()["credentials_file_present"] is False
    assert connections_cache.get("drive_auth_status") is not None

    # Upload a valid credentials file via the real endpoint.
    valid = json.dumps({
        "installed": {
            "client_id": "xyz.apps.googleusercontent.com",
            "client_secret": "shh",
            "redirect_uris": ["http://localhost"],
        }
    }).encode()
    with patch("routers.drive.CREDENTIALS_PATH", creds_path):
        upload = await client.post(
            "/api/drive/credentials",
            files={"file": ("creds.json", valid, "application/json")},
        )
    assert upload.status_code == 200
    assert upload.json()["ok"] is True
    # Cache must have been invalidated by the upload handler.
    assert connections_cache.get("drive_auth_status") is None


# ---------------------------------------------------------------------------
# Auth URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_url_no_credentials_file(client, tmp_path):
    """Without a credentials file, the URL endpoint should return 400."""
    creds_path = tmp_path / "google_credentials.json"  # does not exist

    with (
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}, clear=False),
    ):
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
    # redirect_uri is now derived dynamically from the request's base URL
    assert "%2Fapi%2Fauth%2Fgoogle%2Fcallback" in url


# ---------------------------------------------------------------------------
# Auth callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_auth_callback_error_param(client):
    """An error parameter from Google should redirect with ?error= (not 400)."""
    resp = await client.get(
        "/api/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "access_denied" in resp.headers["location"]
    assert resp.headers["location"].startswith("https://localhost:3010/")


@pytest.mark.asyncio
async def test_drive_auth_callback_invalid_state(client):
    """An unknown state should redirect with ?error=invalid_state."""
    resp = await client.get(
        "/api/auth/google/callback?code=abc&state=bogus-state",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "invalid_state" in resp.headers["location"]
    assert resp.headers["location"].startswith("https://localhost:3010/")


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
            "/api/auth/google/callback?code=good-code&state=valid-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "connected=true" in resp.headers["location"]
    assert "localhost:3010/drive" in resp.headers["location"]


@pytest.mark.asyncio
async def test_drive_auth_callback_prewarms_gmail_and_calendar(client, tmp_path):
    """After a successful token exchange the callback should prewarm
    Drive, Gmail, and Calendar caches in parallel so the first page
    load after OAuth serves from cache instead of paying the cold
    fetch cost. Regression guard for the "Connect Gmail feels laggy"
    demo bug.
    """
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["prewarm-state"] = {
        "return_to": "http://localhost:3010/gmail",
        "expires": 9999999999,
    }

    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})
    )
    token_path = tmp_path / "google_token.json"

    mock_drive = AsyncMock(return_value=[])
    mock_gmail = AsyncMock(return_value=[])
    mock_calendar = AsyncMock(return_value=[])

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch("services.google_auth.DRIVE_CACHE_DIR", tmp_path / "drive_cache"),
        patch("services.google_auth.has_gmail_scope", return_value=True),
        patch("services.google_auth.has_calendar_scope", return_value=True),
        patch("routers.drive._sync_file_list", mock_drive),
        patch("services.gmail.get_inbox_messages", mock_gmail),
        patch("services.calendar.get_upcoming_events", mock_calendar),
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
            "/api/auth/google/callback?code=good-code&state=prewarm-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "connected=true" in resp.headers["location"]
    # All three prewarms ran in parallel as part of the callback.
    assert mock_drive.await_count == 1
    assert mock_gmail.await_count == 1
    assert mock_calendar.await_count == 1


@pytest.mark.asyncio
async def test_drive_auth_callback_prewarm_skips_missing_scopes(client, tmp_path):
    """If a Google scope is missing (for example the user connected only
    for Drive), the prewarm must skip the corresponding service so we
    never make a blocking API call that is guaranteed to fail with a
    scope error. Drive still prewarms because the token itself
    unlocked Drive.
    """
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["scope-state"] = {
        "return_to": "http://localhost:3010/drive",
        "expires": 9999999999,
    }

    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})
    )
    token_path = tmp_path / "google_token.json"

    mock_drive = AsyncMock(return_value=[])
    mock_gmail = AsyncMock(return_value=[])
    mock_calendar = AsyncMock(return_value=[])

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch("services.google_auth.DRIVE_CACHE_DIR", tmp_path / "drive_cache"),
        patch("services.google_auth.has_gmail_scope", return_value=False),
        patch("services.google_auth.has_calendar_scope", return_value=False),
        patch("routers.drive._sync_file_list", mock_drive),
        patch("services.gmail.get_inbox_messages", mock_gmail),
        patch("services.calendar.get_upcoming_events", mock_calendar),
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
            "/api/auth/google/callback?code=good-code&state=scope-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert mock_drive.await_count == 1
    assert mock_gmail.await_count == 0
    assert mock_calendar.await_count == 0


@pytest.mark.asyncio
async def test_drive_auth_callback_prewarm_isolates_failures(client, tmp_path):
    """One prewarm throwing must not stop the others or fail the
    redirect. The user must still land back on the page with
    ?connected=true even if (say) Gmail API is not enabled.
    """
    from routers.drive import _drive_oauth_states

    _drive_oauth_states["isolate-state"] = {
        "return_to": "http://localhost:3010/gmail",
        "expires": 9999999999,
    }

    creds_path = tmp_path / "google_credentials.json"
    creds_path.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})
    )
    token_path = tmp_path / "google_token.json"

    mock_drive = AsyncMock(return_value=[])
    # Gmail prewarm throws, Calendar still runs, redirect still works.
    mock_gmail = AsyncMock(side_effect=RuntimeError("Gmail API not enabled"))
    mock_calendar = AsyncMock(return_value=[])

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.google_auth.CREDENTIALS_PATH", creds_path),
        patch("services.google_auth.DRIVE_CACHE_DIR", tmp_path / "drive_cache"),
        patch("services.google_auth.has_gmail_scope", return_value=True),
        patch("services.google_auth.has_calendar_scope", return_value=True),
        patch("routers.drive._sync_file_list", mock_drive),
        patch("services.gmail.get_inbox_messages", mock_gmail),
        patch("services.calendar.get_upcoming_events", mock_calendar),
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
            "/api/auth/google/callback?code=good-code&state=isolate-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "connected=true" in resp.headers["location"]
    assert mock_drive.await_count == 1
    assert mock_gmail.await_count == 1
    assert mock_calendar.await_count == 1


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
            "routers.auth._drive_exchange_code",
            side_effect=RuntimeError("token exchange error"),
        ),
    ):
        resp = await client.get(
            "/api/auth/google/callback?code=bad-code&state=fail-state",
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

    # Make the cache appear 7 hours old so it is past the 6 hour TTL.
    # Needle 285 bumped the TTL from 1h to 6h so stale tests must age
    # beyond the new floor or the fresh-cache path wins.
    old_time = time.time() - (7 * 3600)
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


@pytest.mark.asyncio
async def test_drive_files_cached_response_includes_last_synced_at(client, tmp_path):
    """Cache hit should include last_synced_at as a float (the index file mtime)."""
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
    assert isinstance(data["last_synced_at"], float)


@pytest.mark.asyncio
async def test_drive_files_no_cache_last_synced_at_is_null(client, tmp_path):
    """When no cache exists, a fresh fetch returns last_synced_at as a float (just saved)."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "drive_cache"
    # No index.json — cache is empty, so the fresh fetch path runs.
    fake_files = _make_drive_files(2)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive.DRIVE_CACHE_DIR", cache_dir),
        patch("routers.drive._INDEX_PATH", cache_dir / "index.json"),
        patch("routers.drive._fetch_drive_files", new=AsyncMock(return_value=fake_files)),
    ):
        resp = await client.get("/api/drive/files")

    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is False
    # Fresh fetch always returns a float timestamp (time.time() at save).
    assert isinstance(data["last_synced_at"], float)


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
                "redirect_uris": ["https://localhost:8000/api/drive/auth/callback"],
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


def test_google_auth_redirect_uri_is_dynamic():
    """get_auth_url must accept a redirect_uri parameter rather than using a hardcoded constant.

    Regression guard: redirect_uri is now derived from request.base_url so it
    works on any host/scheme without requiring a hardcoded URL.
    """
    import inspect
    from services import google_auth

    sig = inspect.signature(google_auth.get_auth_url)
    assert "redirect_uri" in sig.parameters, (
        "get_auth_url must accept a redirect_uri parameter. "
        "The old hardcoded REDIRECT_URI constant has been removed."
    )
    assert "redirect_uri" in inspect.signature(google_auth.exchange_code).parameters, (
        "exchange_code must accept redirect_uri param (hardcoded REDIRECT_URI was removed)"
    )
    assert not hasattr(google_auth, "REDIRECT_URI"), (
        "REDIRECT_URI constant must be removed; redirect_uri is now passed as a parameter."
    )


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
    # In production, the creds file is created via /drive/credentials
    # which invalidates the cached status on write. Here the test writes
    # directly to disk, so mirror that invalidation to force a recompute.
    from services import connections_cache
    connections_cache.invalidate_all()
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
async def test_drive_auth_status_no_reauth_when_full_scope_present(client, tmp_path):
    """When the token includes the full drive scope, needs_reauth should be False."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": (
                "https://www.googleapis.com/auth/drive "
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
            "scope": (
                "https://www.googleapis.com/auth/drive "
                "https://www.googleapis.com/auth/drive.file"
            ),
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


@pytest.mark.asyncio
async def test_drive_delete_app_not_authorized_surfaces_reauth(client, tmp_path):
    """When Google returns appNotAuthorizedToFile (drive.file scope, non-app file),
    the endpoint must return 403 with needs_reauth=True and a plain-language message
    instead of the raw API error string (regression guard for Bug B)."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive.file",
        })
    )

    class FakeHttpError(Exception):
        pass

    app_not_auth_error = FakeHttpError(
        "<HttpError 403 when requesting ... returned "
        "\"The user has not granted the app 12345 write access to the file abc.\". "
        "Details: \"[{'reason': 'appNotAuthorizedToFile'}]\""
    )

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._build_drive_service") as mock_svc,
    ):
        mock_files = MagicMock()
        mock_files.update.return_value.execute.side_effect = app_not_auth_error
        mock_svc.return_value.files.return_value = mock_files

        resp = await client.delete("/api/drive/files/shared-file-id")

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["needs_reauth"] is True
    assert "reconnect" in detail["message"].lower()
    # Must not be the raw googleapis URL error string.
    assert "HttpError" not in detail["message"]


@pytest.mark.asyncio
async def test_drive_delete_full_scope_success(client, tmp_path):
    """Delete with the full drive scope should succeed on any file."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/drive",
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

        resp = await client.delete("/api/drive/files/any-file")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_drive_auth_status_needs_reauth_without_full_drive_scope(client, tmp_path):
    """needs_reauth should be True when the token has drive.file but not the full drive scope."""
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
    assert resp.json()["needs_reauth"] is True


@pytest.mark.asyncio
async def test_drive_auth_status_no_reauth_with_full_drive_scope(client, tmp_path):
    """needs_reauth should be False when the token includes the full drive scope."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": (
                "https://www.googleapis.com/auth/drive "
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


# ---------------------------------------------------------------------------
# Structured preview endpoint (kind-specific renderers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_structured_preview_not_authenticated(client, tmp_path):
    """Structured preview returns 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"  # does not exist

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/drive/preview/file-xyz")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_drive_structured_preview_image(client, tmp_path):
    """Image files return kind=image with a thumbnail URL and no sample."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "img-1",
        "name": "photo.png",
        "mimeType": "image/png",
        "webViewLink": "https://drive.google.com/file/d/img-1",
        "size": "12345",
        "thumbnailLink": "https://lh3.googleusercontent.com/img=s220",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/preview/img-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "image"
    assert data["thumbnail_url"] is not None
    assert "=s800" in data["thumbnail_url"]
    assert data["export_url"] == "/api/drive/files/img-1/preview"
    assert data["sample"] is None


@pytest.mark.asyncio
async def test_drive_structured_preview_pdf(client, tmp_path):
    """PDF files return kind=pdf with the export URL and no sample."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "pdf-1",
        "name": "report.pdf",
        "mimeType": "application/pdf",
        "webViewLink": "https://drive.google.com/file/d/pdf-1",
        "size": "50000",
        "thumbnailLink": "https://lh3.googleusercontent.com/pdf=s220",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/preview/pdf-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "pdf"
    assert data["export_url"] == "/api/drive/files/pdf-1/preview"
    assert data["sample"] is None


@pytest.mark.asyncio
async def test_drive_structured_preview_sheet_returns_parsed_table(client, tmp_path):
    """Google Sheets export CSV is parsed into a headers+rows sample."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "sheet-1",
        "name": "Budget",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-1",
        "size": None,
        "thumbnailLink": None,
    }
    fake_csv = "Month,Revenue,Cost\nJan,100,50\nFeb,200,80\n"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch("routers.drive._export_sheet_csv", new=AsyncMock(return_value=fake_csv)),
    ):
        resp = await client.get("/api/drive/preview/sheet-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "sheet"
    assert data["sample"] is not None
    sheet = data["sample"]["sheets"][0]
    assert sheet["headers"] == ["Month", "Revenue", "Cost"]
    assert sheet["rows"] == [["Jan", "100", "50"], ["Feb", "200", "80"]]
    assert sheet["truncated"] is False


@pytest.mark.asyncio
async def test_drive_structured_preview_doc_returns_blocks(client, tmp_path):
    """Google Doc export text is parsed into heading + paragraph blocks."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "doc-1",
        "name": "Plan",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc-1",
        "size": None,
        "thumbnailLink": None,
    }
    fake_text = (
        "Project Plan\n\n"
        "This is the first paragraph of the plan.\n\n"
        "Next Steps\n\n"
        "We will review the draft and finalize it."
    )

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch("routers.drive._export_doc_text", new=AsyncMock(return_value=fake_text)),
    ):
        resp = await client.get("/api/drive/preview/doc-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "doc"
    assert data["sample"] is not None
    blocks = data["sample"]["blocks"]
    # First block is the heading.
    assert blocks[0]["type"] == "heading"
    assert blocks[0]["text"] == "Project Plan"
    # Paragraph ends with a period so it is classified as paragraph.
    assert any(b["type"] == "paragraph" for b in blocks)
    assert any("Next Steps" in b["text"] and b["type"] == "heading" for b in blocks)


@pytest.mark.asyncio
async def test_drive_structured_preview_doc_returns_full_content(client, tmp_path):
    """Google Doc export returns the full text without truncation, even for long docs."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "doc-long",
        "name": "Long Report",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc-long",
        "size": None,
        "thumbnailLink": None,
    }
    last_paragraph = "This is the final paragraph and must appear in the preview."
    fake_text = (
        "Introduction\n\n"
        + ("Body paragraph. " * 300 + "\n\n")
        + f"Conclusion\n\n{last_paragraph}"
    )
    assert len(fake_text) > 4000

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch("routers.drive._export_doc_html", new=AsyncMock(side_effect=Exception("html unavailable"))),
        patch("routers.drive._export_doc_text", new=AsyncMock(return_value=fake_text)),
    ):
        resp = await client.get("/api/drive/preview/doc-long")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "doc"
    assert data["sample"] is not None
    assert data["sample"]["truncated"] is False
    blocks = data["sample"]["blocks"]
    block_texts = [b["text"] for b in blocks]
    assert any(last_paragraph in t for t in block_texts), (
        "Last paragraph missing from preview — doc content was truncated"
    )


@pytest.mark.asyncio
async def test_drive_structured_preview_slides_returns_thumbnail_strip(client, tmp_path):
    """Google Slides returns a list of per-slide thumbnail URLs."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "slides-1",
        "name": "Deck",
        "mimeType": "application/vnd.google-apps.presentation",
        "webViewLink": "https://docs.google.com/presentation/d/slides-1",
        "size": None,
        "thumbnailLink": "https://lh3.googleusercontent.com/deck=s220",
    }
    fake_thumbs = [
        {"slide_id": "p1", "thumbnail_url": "https://img/p1"},
        {"slide_id": "p2", "thumbnail_url": "https://img/p2"},
        {"slide_id": "p3", "thumbnail_url": "https://img/p3"},
    ]

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch(
            "routers.drive._fetch_slides_thumbnails",
            new=AsyncMock(return_value=fake_thumbs),
        ),
    ):
        resp = await client.get("/api/drive/preview/slides-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "slides"
    assert data["sample"] is not None
    assert len(data["sample"]["slides"]) == 3
    assert data["sample"]["slides"][0]["thumbnail_url"] == "https://img/p1"


@pytest.mark.asyncio
async def test_drive_structured_preview_slides_fallback_on_api_error(client, tmp_path):
    """When the Slides API fails, fall back to the Drive thumbnail as a single image."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "slides-2",
        "name": "Deck",
        "mimeType": "application/vnd.google-apps.presentation",
        "webViewLink": "https://docs.google.com/presentation/d/slides-2",
        "size": None,
        "thumbnailLink": "https://lh3.googleusercontent.com/deck=s220",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch(
            "routers.drive._fetch_slides_thumbnails",
            new=AsyncMock(side_effect=RuntimeError("slides api down")),
        ),
    ):
        resp = await client.get("/api/drive/preview/slides-2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "slides"
    # Fallback: single slide entry using the enlarged Drive thumbnail.
    assert len(data["sample"]["slides"]) == 1
    assert "=s800" in data["sample"]["slides"][0]["thumbnail_url"]


@pytest.mark.asyncio
async def test_drive_structured_preview_auth_error_returns_401(client, tmp_path):
    """When _get_file_meta raises invalid_grant, the endpoint returns 401 not 500."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.expired"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "routers.drive._get_file_meta",
            new=AsyncMock(
                side_effect=Exception(
                    "('invalid_grant: Token has been expired or revoked.', "
                    "{'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'})"
                )
            ),
        ),
    ):
        resp = await client.get("/api/drive/preview/any-file-id")

    assert resp.status_code == 401
    assert "reconnect" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_drive_structured_preview_other(client, tmp_path):
    """Unknown mime types return kind=other with no sample."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "zip-1",
        "name": "archive.zip",
        "mimeType": "application/zip",
        "webViewLink": "https://drive.google.com/file/d/zip-1",
        "size": "1000",
        "thumbnailLink": None,
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
    ):
        resp = await client.get("/api/drive/preview/zip-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "other"
    assert data["sample"] is None


@pytest.mark.asyncio
async def test_drive_structured_preview_sheet_truncates_large_data(client, tmp_path):
    """Sheets with more than 20 rows or 10 cols report truncated=True."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_meta = {
        "id": "sheet-big",
        "name": "Big",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "webViewLink": "",
        "size": None,
        "thumbnailLink": None,
    }
    # 25 rows of 3 cols: more than the 20-row cap.
    rows = ["a,b,c"] + [f"{i},{i+1},{i+2}" for i in range(25)]
    fake_csv = "\n".join(rows)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._get_file_meta", new=AsyncMock(return_value=fake_meta)),
        patch("routers.drive._export_sheet_csv", new=AsyncMock(return_value=fake_csv)),
    ):
        resp = await client.get("/api/drive/preview/sheet-big")

    data = resp.json()
    sheet = data["sample"]["sheets"][0]
    assert sheet["truncated"] is True
    assert len(sheet["rows"]) <= 20


# ---------------------------------------------------------------------------
# B8: Drive folder migration — yourOS / myOS fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_myos_folder_youros_found(tmp_path):
    """Finds the yourOS folder first (new brand) and returns its ID."""
    from routers.drive import _get_or_create_myos_folder, _YOUROS_FOLDER_NAME

    token_path = tmp_path / "google_token.json"
    token_path.write_text('{"access_token": "ya29.test"}')

    fake_service = MagicMock()
    fake_service.files().list().execute.return_value = {
        "files": [{"id": "folder-youros-id", "name": _YOUROS_FOLDER_NAME}]
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._build_drive_service", return_value=fake_service),
    ):
        result = await _get_or_create_myos_folder()

    assert result == "folder-youros-id"


@pytest.mark.asyncio
async def test_get_or_create_myos_folder_legacy_myos_found(tmp_path):
    """Falls back to the legacy myOS folder when yourOS folder is absent."""
    from routers.drive import _get_or_create_myos_folder

    token_path = tmp_path / "google_token.json"
    token_path.write_text('{"access_token": "ya29.test"}')

    def _list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        if "yourOS" in q:
            return MagicMock(**{"execute.return_value": {"files": []}})
        return MagicMock(**{"execute.return_value": {"files": [{"id": "folder-myos-id", "name": "myOS"}]}})

    fake_service = MagicMock()
    fake_service.files().list.side_effect = _list_side_effect

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._build_drive_service", return_value=fake_service),
    ):
        result = await _get_or_create_myos_folder()

    assert result == "folder-myos-id"


@pytest.mark.asyncio
async def test_get_or_create_myos_folder_creates_youros(tmp_path):
    """Creates a yourOS folder when neither yourOS nor myOS exists."""
    from routers.drive import _get_or_create_myos_folder, _YOUROS_FOLDER_NAME

    token_path = tmp_path / "google_token.json"
    token_path.write_text('{"access_token": "ya29.test"}')

    fake_service = MagicMock()
    fake_service.files().list().execute.return_value = {"files": []}
    fake_service.files().create().execute.return_value = {"id": "new-youros-folder-id"}

    created_name = []

    def _create_side_effect(body, fields):
        created_name.append(body.get("name"))
        return MagicMock(**{"execute.return_value": {"id": "new-youros-folder-id"}})

    fake_service.files().create.side_effect = _create_side_effect

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("routers.drive._build_drive_service", return_value=fake_service),
    ):
        result = await _get_or_create_myos_folder()

    assert result == "new-youros-folder-id"
    assert _YOUROS_FOLDER_NAME in created_name

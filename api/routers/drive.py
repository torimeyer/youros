"""Google Drive integration endpoints.

Provides file browsing, preview (exported as PDF), and sync for Drive files.
All cached data lives in ~/.myos/drive_cache/ -- never inside the repo.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services.google_auth import (
    DRIVE_CACHE_DIR,
    TOKEN_PATH,
    credentials_file_exists,
    exchange_code,
    get_auth_url,
    get_credentials,
    get_email,
    is_authenticated,
    revoke,
)

router = APIRouter(tags=["drive"])

# In-memory state map for CSRF protection during the Drive OAuth flow.
_drive_oauth_states: dict[str, bool] = {}

# MIME types that Google Drive can export to PDF.
_EXPORTABLE_MIME = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.drawing",
}

# Cache TTL for exported PDFs (1 hour).
_CACHE_TTL_SECONDS = 3600

# Maximum files returned by the list endpoint.
_MAX_FILES = 100


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.get("/drive/auth/status")
async def drive_auth_status():
    """Return whether the user has connected their Google account."""
    authed = is_authenticated()
    email = get_email() if authed else None
    has_creds = credentials_file_exists()
    return {
        "authenticated": authed,
        "email": email,
        "credentials_file_present": has_creds,
    }


@router.get("/drive/auth/url")
async def drive_auth_url():
    """Return the URL the user should visit to connect their Google account."""
    if not credentials_file_exists():
        raise HTTPException(
            status_code=400,
            detail=(
                "No credentials file found at ~/.myos/google_credentials.json. "
                "Download it from Google Cloud Console and save it there."
            ),
        )
    state = secrets.token_urlsafe(32)
    _drive_oauth_states[state] = True
    url = get_auth_url(state)
    return {"url": url}


@router.get("/drive/auth/callback")
async def drive_auth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
):
    """Handle the OAuth callback from Google."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google returned an error: {error}")

    if state not in _drive_oauth_states:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter.")
    del _drive_oauth_states[state]

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received.")

    try:
        exchange_code(code)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not complete sign-in with Google: {exc}",
        ) from exc

    # After saving the token, immediately cache the file list.
    try:
        await _sync_file_list()
    except Exception:
        pass

    email = get_email()
    return {"ok": True, "email": email}


@router.post("/drive/auth/revoke")
async def drive_auth_revoke():
    """Disconnect the Google account."""
    revoke()
    return {"ok": True}


# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------


def _build_drive_service():
    """Build an authenticated Google Drive API service object."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Google API client is not available on this server.",
        ) from exc

    tokens = get_credentials()
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=None,
        client_secret=None,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@router.get("/drive/files")
async def drive_files(
    q: Optional[str] = Query(None, description="Search query"),
    folder_id: Optional[str] = Query(None, description="Folder ID to list"),
):
    """List Drive files sorted by last modified time (most recent first)."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    # Try the cache first if no filters are applied.
    if not q and not folder_id:
        cached = _load_file_list_cache()
        if cached is not None:
            return {"files": cached, "cached": True}

    try:
        files = await _fetch_drive_files(q=q, folder_id=folder_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load files from Google Drive: {exc}",
        ) from exc

    # Cache unfiltered results.
    if not q and not folder_id:
        _save_file_list_cache(files)

    return {"files": files, "cached": False}


async def _fetch_drive_files(
    q: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> list[dict]:
    """Hit the Drive API and return a clean list of file dicts."""
    import asyncio

    def _call():
        service = _build_drive_service()
        query_parts = ["trashed = false"]
        if q:
            query_parts.append(f"name contains '{q.replace(chr(39), '')}'" )
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        query = " and ".join(query_parts)

        results = (
            service.files()
            .list(
                q=query,
                pageSize=_MAX_FILES,
                orderBy="modifiedTime desc",
                fields=(
                    "files(id,name,mimeType,modifiedTime,"
                    "iconLink,webViewLink,size,parents)"
                ),
            )
            .execute()
        )
        return results.get("files", [])

    return await asyncio.get_event_loop().run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# File list cache
# ---------------------------------------------------------------------------

_INDEX_PATH = DRIVE_CACHE_DIR / "index.json"


def _load_file_list_cache() -> list[dict] | None:
    """Return cached file list if it exists and is less than 1 hour old."""
    if not _INDEX_PATH.exists():
        return None
    age = time.time() - _INDEX_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(_INDEX_PATH.read_text())
    except Exception:
        return None


def _save_file_list_cache(files: list[dict]) -> None:
    """Persist the file list to the cache."""
    DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(files))


async def _sync_file_list() -> list[dict]:
    """Fetch from Drive and refresh the cache."""
    files = await _fetch_drive_files()
    _save_file_list_cache(files)
    return files


# ---------------------------------------------------------------------------
# Sync endpoint
# ---------------------------------------------------------------------------


@router.post("/drive/sync")
async def drive_sync():
    """Refresh the cached file list from Google Drive."""
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    try:
        files = await _sync_file_list()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {exc}",
        ) from exc

    return {"ok": True, "file_count": len(files), "synced_at": time.time()}


# ---------------------------------------------------------------------------
# File preview
# ---------------------------------------------------------------------------


@router.get("/drive/files/{file_id}/preview")
async def drive_file_preview(file_id: str):
    """Export a Drive file as PDF and return it.

    For Google Docs / Slides / Sheets, exports via the Drive API.
    For uploaded files (.pptx, .pdf, etc.), downloads the binary directly.
    Non-previewable files return JSON with previewable=false.
    Exported PDFs are cached in ~/.myos/drive_cache/ for 1 hour.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    # Check disk cache first.
    cache_path = DRIVE_CACHE_DIR / f"{file_id}.pdf"
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            pdf_bytes = cache_path.read_bytes()
            return Response(content=pdf_bytes, media_type="application/pdf")

    # Fetch file metadata.
    try:
        meta = await _get_file_meta(file_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not get file info from Drive: {exc}",
        ) from exc

    mime = meta.get("mimeType", "")
    web_view_link = meta.get("webViewLink", "")

    if mime in _EXPORTABLE_MIME:
        # Export as PDF via the Drive API.
        try:
            pdf_bytes = await _export_as_pdf(file_id)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not export file as PDF: {exc}",
            ) from exc

        DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(pdf_bytes)
        return Response(content=pdf_bytes, media_type="application/pdf")

    # Non-Google-native files: try to download the binary.
    # Only download if Drive reports a non-zero size.
    size = int(meta.get("size", 0))
    if size > 0:
        # For known previewable formats, download and serve as-is.
        previewable_uploads = {
            "application/pdf": "application/pdf",
        }
        response_mime = previewable_uploads.get(mime)
        if response_mime:
            try:
                content = await _download_file(file_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not download file from Drive: {exc}",
                ) from exc
            DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
            return Response(content=content, media_type=response_mime)

    # Not previewable inline.
    return {"previewable": False, "webViewLink": web_view_link, "mimeType": mime}


async def _get_file_meta(file_id: str) -> dict:
    """Fetch metadata for a single Drive file."""
    import asyncio

    def _call():
        service = _build_drive_service()
        return (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,webViewLink,size",
            )
            .execute()
        )

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _export_as_pdf(file_id: str) -> bytes:
    """Export a Google-native file as PDF using the Drive API."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _build_drive_service()
        request = service.files().export_media(
            fileId=file_id, mimeType="application/pdf"
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _download_file(file_id: str) -> bytes:
    """Download a non-Google-native file's binary content."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _build_drive_service()
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    return await asyncio.get_event_loop().run_in_executor(None, _call)

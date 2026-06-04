"""Google Drive integration endpoints.

Provides file browsing, preview (exported as PDF), and sync for Drive files.
All cached data lives in ~/.myos/drive_cache/ -- never inside the repo.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from config import FRONTEND_URL_DEFAULT
from services import connections_cache, recent_deletes
from services.google_auth import (
    CREDENTIALS_PATH,
    DRIVE_CACHE_DIR,
    TOKEN_PATH,
    build_redirect_uri,
    can_start_oauth,
    credentials_file_exists,
    exchange_code,
    get_auth_url,
    get_credentials,
    get_email,
    has_full_drive_scope,
    has_write_scope,
    is_authenticated,
    revoke,
)
from services.oauth_state import (
    _STATE_TTL_SECONDS,
    drive_oauth_states as _drive_oauth_states,
    load_drive_oauth_states as _load_oauth_states,
    save_drive_oauth_states as _save_oauth_states,
)

router = APIRouter(tags=["drive"])

# Cache key used by the in-memory TTL cache around /drive/auth/status.
_DRIVE_STATUS_CACHE_KEY = "drive_auth_status"

# Drive OAuth states are now managed by services.oauth_state (imported above).
# _drive_oauth_states, _load_oauth_states, _save_oauth_states, _STATE_TTL_SECONDS
# are all imported from there; no local definitions needed.

# MIME types that Google Drive can export to PDF.
_EXPORTABLE_MIME = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.drawing",
}

# Cache TTL for exported PDFs (1 hour).
# 6 hours. Drive files rarely change in a way the user notices from
# second to second, and a longer TTL means fewer cold hits with slow
# Google API round trips. Sync button still bypasses the cache.
_CACHE_TTL_SECONDS = 6 * 3600

# Maximum files returned by the list endpoint.
_MAX_FILES = 100

# Allowed sort values and their Google Drive orderBy strings.
# Default is "opened" (viewedByMeTime desc) — the gap that prompted →1752.
_SORT_ORDER_BY: dict[str, str] = {
    "edited": "modifiedTime desc",
    "opened": "viewedByMeTime desc",
    "created": "createdTime desc",
}
_SORT_DEFAULT = "opened"


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.post("/drive/credentials")
async def drive_upload_credentials(file: UploadFile = File(...)):
    """Accept a Google credentials JSON file upload and save it to ~/.myos/.

    Validates that the file contains the required fields before saving.
    Returns {ok: true} on success or {ok: false, error: "..."} on failure.
    """
    content = await file.read()
    if not content:
        return {"ok": False, "error": "The file was empty. Please download your credentials file again from Google Cloud Console."}

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"ok": False, "error": "That file could not be read. Make sure you downloaded the correct JSON file from Google Cloud Console."}

    # Support both 'web' and 'installed' app types, as well as a flat format.
    cfg = None
    for key in ("web", "installed"):
        if key in data:
            cfg = data[key]
            break
    if cfg is None:
        cfg = data

    # Validate required fields.
    missing = [f for f in ("client_id", "client_secret") if f not in cfg]
    has_redirect = "redirect_uris" in cfg or "web" in data
    if missing or not has_redirect:
        return {
            "ok": False,
            "error": (
                "That does not look like a Google credentials file. "
                "Download it again from the Google Cloud Console credentials page."
            ),
        }

    MYOS_DIR = CREDENTIALS_PATH.parent
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_bytes(content)
    # Credentials file just appeared. Drop the cached status so the
    # Settings page sees credentials_file_present=True on the next poll.
    connections_cache.invalidate(_DRIVE_STATUS_CACHE_KEY)
    return {"ok": True}


def _compute_drive_status() -> dict:
    """Pure function that resolves the Drive status payload from disk."""
    authed = is_authenticated()
    email = get_email() if authed else None
    has_creds = credentials_file_exists()
    # needs_reauth when write scope is missing entirely, OR when the full
    # drive scope is absent (required to delete files not created by this app).
    needs_reauth = authed and (not has_write_scope() or not has_full_drive_scope())
    return {
        "authenticated": authed,
        "email": email,
        "credentials_file_present": has_creds,
        "needs_reauth": needs_reauth,
    }


@router.get("/drive/auth/status")
async def drive_auth_status():
    """Return whether the user has connected their Google account.

    Also surfaces needs_reauth=True when the account is connected but either
    the drive.file scope (required for uploads) or the full drive scope
    (required to delete any file) is missing from the saved token.
    The user must reconnect their Google account to grant the new permission.

    Payload is served from an in-memory TTL cache so repeat polls within
    the same minute return in sub-millisecond time.
    """
    return connections_cache.get_or_compute(
        _DRIVE_STATUS_CACHE_KEY,
        _compute_drive_status,
    )


@router.get("/drive/auth/url")
async def drive_auth_url(request: Request, return_to: str = ""):
    """Return the URL the user should visit to connect their Google account."""
    if not can_start_oauth():
        raise HTTPException(
            status_code=400,
            detail=(
                "Google credentials are not configured. "
                "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file."
            ),
        )
    frontend = _frontend_url(request)
    effective_return_to = _validate_return_to(return_to, f"{frontend}/drive", request)
    state = secrets.token_urlsafe(32)
    _drive_oauth_states[state] = {
        "return_to": effective_return_to,
        "expires": time.time() + _STATE_TTL_SECONDS,
    }
    _save_oauth_states(_drive_oauth_states)
    redirect_uri = build_redirect_uri(request)
    url = get_auth_url(state, redirect_uri)
    return {"url": url}


@router.get("/drive/auth/url/calendar")
async def drive_auth_url_for_calendar(request: Request, return_to: str = ""):
    """Return an OAuth URL that redirects back to the Calendar page after auth."""
    if not can_start_oauth():
        raise HTTPException(
            status_code=400,
            detail=(
                "Google credentials are not configured. "
                "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file."
            ),
        )
    frontend = _frontend_url(request)
    effective_return_to = _validate_return_to(return_to, f"{frontend}/calendar", request)
    state = secrets.token_urlsafe(32)
    _drive_oauth_states[state] = {
        "return_to": effective_return_to,
        "expires": time.time() + _STATE_TTL_SECONDS,
    }
    _save_oauth_states(_drive_oauth_states)
    redirect_uri = build_redirect_uri(request)
    url = get_auth_url(state, redirect_uri)
    return {"url": url}


@router.get("/drive/auth/url/gmail")
async def drive_auth_url_for_gmail(request: Request, return_to: str = ""):
    """Return an OAuth URL that redirects back to the Gmail page after auth."""
    if not can_start_oauth():
        raise HTTPException(
            status_code=400,
            detail=(
                "Google credentials are not configured. "
                "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file."
            ),
        )
    frontend = _frontend_url(request)
    effective_return_to = _validate_return_to(return_to, f"{frontend}/gmail", request)
    state = secrets.token_urlsafe(32)
    _drive_oauth_states[state] = {
        "return_to": effective_return_to,
        "expires": time.time() + _STATE_TTL_SECONDS,
    }
    _save_oauth_states(_drive_oauth_states)
    redirect_uri = build_redirect_uri(request)
    url = get_auth_url(state, redirect_uri)
    return {"url": url}


def _frontend_url(request: Request) -> str:
    return os.environ.get("FRONTEND_URL", FRONTEND_URL_DEFAULT)


# Module-level anchor for regression tests: the post-auth redirect
# target for the Drive OAuth callback. The short-lived Drive+Files to
# Documents migration was reverted in eaa7ffa, so the redirect goes
# back to the Drive page. The runtime code uses ``_frontend_drive_url()``
# so FRONTEND_URL env changes take effect without a restart.
FRONTEND_DRIVE_URL = "/drive"


def _frontend_drive_url(request: Request) -> str:
    """Build the default post-auth redirect URL at request time."""
    return f"{_frontend_url(request)}{FRONTEND_DRIVE_URL}"


def _append_query(url: str, param: str) -> str:
    """Append a query parameter to a URL, using & if ? already present."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{param}"


def _validate_return_to(return_to: str, default: str, request: Request) -> str:
    """Validate and return a safe post-OAuth redirect target.

    Only accepts same-origin paths (starting with /) or URLs that start with
    the configured FRONTEND_URL. Anything else falls back to ``default``
    to prevent open-redirect attacks.
    """
    if not return_to:
        return default
    frontend = _frontend_url(request)
    if return_to.startswith("/") or return_to.startswith(frontend):
        if return_to.startswith("/"):
            return f"{frontend}{return_to}"
        return return_to
    return default


# Drive OAuth callback is now handled by /api/auth/google/callback in auth.py.
# That URI is already registered in GCP Console and handles both Drive and
# Gemini OAuth flows via the state token prefix.


async def _prewarm_all_google_caches() -> None:
    """Warm Drive, Gmail, and Calendar caches in parallel after OAuth.

    Runs after a successful token exchange so the first page load on
    any of the three Google-backed pages serves from disk instead of
    paying the 5 to 10 second cold fetch cost. Each prewarm catches
    its own exceptions: one service failing (API not enabled, scope
    missing, transient network error) must not block the others.
    """
    import asyncio

    async def _safe_drive() -> None:
        try:
            await _sync_file_list()
        except Exception:
            pass

    async def _safe_gmail() -> None:
        try:
            from services import gmail as gmail_service
            from services.google_auth import has_gmail_scope
            if not has_gmail_scope():
                return
            # get_inbox_messages writes _save_full_inbox_cache internally,
            # so the first GET /gmail/messages after redirect is a cache hit.
            await gmail_service.get_inbox_messages()
        except Exception:
            pass

    async def _safe_calendar() -> None:
        try:
            from services import calendar as calendar_service
            from services.google_auth import has_calendar_scope
            if not has_calendar_scope():
                return
            # get_upcoming_events writes _save_cache internally.
            await calendar_service.get_upcoming_events(days=7)
        except Exception:
            pass

    await asyncio.gather(
        _safe_drive(),
        _safe_gmail(),
        _safe_calendar(),
    )


@router.post("/drive/auth/revoke")
async def drive_auth_revoke():
    """Disconnect the Google account."""
    revoke()
    # revoke() already invalidates google_* status caches, but call here
    # too so the contract is explicit at the router seam.
    connections_cache.invalidate(_DRIVE_STATUS_CACHE_KEY)
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
    client_config = {}
    try:
        from services.google_auth import _load_client_config
        client_config = _load_client_config()
    except Exception:
        pass
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config.get("client_id"),
        client_secret=client_config.get("client_secret"),
    )
    return build("drive", "v3", credentials=creds)


# Module-scope cache for the Drive service object.
# Keyed by (token_path, token_mtime) so it automatically invalidates when
# the token file changes (OAuth refresh) or when tests swap TOKEN_PATH.
_svc_cache: dict = {}
_svc_lock = threading.Lock()


def _get_cached_drive_service():
    """Return a cached Drive service, rebuilding on token change or 30-min TTL."""
    from services.google_auth import TOKEN_PATH as _tp
    try:
        mtime = _tp.stat().st_mtime if _tp.exists() else 0.0
    except OSError:
        mtime = 0.0
    with _svc_lock:
        c = _svc_cache
        if (
            c.get("tp") == _tp
            and c.get("mt") == mtime
            and time.time() < c.get("exp", 0.0)
            and c.get("svc") is not None
        ):
            return c["svc"]
        svc = _build_drive_service()
        c.update({"tp": _tp, "mt": mtime, "exp": time.time() + 1800, "svc": svc})
        return svc


@router.get("/drive/files")
async def drive_files(
    q: Optional[str] = Query(None, description="Search query"),
    folder_id: Optional[str] = Query(None, description="Folder ID to list"),
    sort: Optional[str] = Query(None, description="Sort order: edited, opened, created"),
):
    """List Drive files with configurable sort order.

    sort=edited  → most recently modified first (modifiedTime desc)
    sort=opened  → most recently opened first (viewedByMeTime desc) [default]
    sort=created → newest first by creation date (createdTime desc)

    Cold path wraps the Drive API call in an 8 second timeout and
    falls back to any existing stale cache if the call hangs or
    fails, so the UI never spins for 30 seconds or lands on an empty
    list on a transient error. Regression guard for needle 285.
    Cache is only used for the default sort (opened) to avoid serving
    differently-ordered data to the wrong sort request.
    """
    import asyncio

    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    sort_key = sort if sort in _SORT_ORDER_BY else _SORT_DEFAULT
    order_by = _SORT_ORDER_BY[sort_key]
    use_cache = not q and not folder_id and sort_key == _SORT_DEFAULT

    # Try the cache first if no filters are applied and using default sort.
    if use_cache:
        cached = _load_file_list_cache()
        if cached is not None:
            last_synced_at = _INDEX_PATH.stat().st_mtime if _INDEX_PATH.exists() else None
            return {"files": cached, "cached": True, "last_synced_at": last_synced_at}

    try:
        files = await asyncio.wait_for(
            _fetch_drive_files(q=q, folder_id=folder_id, order_by=order_by),
            timeout=8.0,
        )
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        # Drive API hung. Fall back to stale cache (if any) for
        # unfiltered default-sort requests so the UI still shows something.
        if use_cache:
            stale = _load_file_list_cache(allow_stale=True)
            if stale is not None:
                last_synced_at = _INDEX_PATH.stat().st_mtime if _INDEX_PATH.exists() else None
                return {"files": stale, "cached": True, "stale": True, "last_synced_at": last_synced_at}
        raise HTTPException(
            status_code=504,
            detail="Google Drive is slow to respond right now. Try again in a moment.",
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "accessnotconfigured" in msg or "has not been used" in msg:
            raise HTTPException(
                status_code=403,
                detail={
                    "needs_reauth": False,
                    "api_not_enabled": True,
                    "message": (
                        "Google Drive API is not enabled in your Google Cloud project. "
                        "Enable it in Google Cloud Console, then wait a minute and reload."
                    ),
                },
            ) from exc
        # Any other failure: serve stale cache if we have one so the
        # UI does not lose state on a transient 5xx from Google.
        if use_cache:
            stale = _load_file_list_cache(allow_stale=True)
            if stale is not None:
                last_synced_at = _INDEX_PATH.stat().st_mtime if _INDEX_PATH.exists() else None
                return {"files": stale, "cached": True, "stale": True, "last_synced_at": last_synced_at}
        raise HTTPException(
            status_code=500,
            detail=f"Could not load files from Google Drive: {exc}",
        ) from exc

    # Cache unfiltered default-sort results.
    if use_cache:
        _save_file_list_cache(files)

    return {"files": files, "cached": False, "last_synced_at": time.time()}


async def _fetch_drive_files(
    q: Optional[str] = None,
    folder_id: Optional[str] = None,
    order_by: str = "viewedByMeTime desc",
) -> list[dict]:
    """Hit the Drive API and return a clean list of file dicts."""
    import asyncio

    def _call():
        service = _get_cached_drive_service()
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
                orderBy=order_by,
                fields=(
                    "files(id,name,mimeType,modifiedTime,viewedByMeTime,createdTime,"
                    "iconLink,webViewLink,size,parents,thumbnailLink)"
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


def _load_file_list_cache(allow_stale: bool = False) -> list[dict] | None:
    """Return cached file list.

    When ``allow_stale`` is False (default) the TTL is enforced. When
    True, any cached file is returned regardless of age. The stale
    path is the fallback path for when the Drive API call hangs or
    fails, so the UI never renders an empty file list on a transient
    error. Regression guard for needle 285.
    """
    if not _INDEX_PATH.exists():
        return None
    if not allow_stale:
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
# Write endpoints (requires drive.file scope)
# ---------------------------------------------------------------------------

# Legacy folder name kept for backwards-compatible lookup on existing user Drives.
_MYOS_FOLDER_NAME = "myOS"
# New folder name used for fresh installs and new folder creation.
_YOUROS_FOLDER_NAME = "yourOS"


async def _get_or_create_myos_folder() -> str:
    """Return the Drive ID of the yourOS (or legacy myOS) folder.

    Lookup order:
    1. yourOS folder (new brand, created from v4.0.0 onwards).
    2. myOS folder (legacy; existing users keep their folder without disruption).
    3. Neither found: create a new yourOS folder.

    Existing users' myOS folders are never renamed automatically.
    """
    import asyncio

    def _call():
        service = _get_cached_drive_service()
        for folder_name in (_YOUROS_FOLDER_NAME, _MYOS_FOLDER_NAME):
            results = (
                service.files()
                .list(
                    q=(
                        f"name = '{folder_name}' "
                        "and mimeType = 'application/vnd.google-apps.folder' "
                        "and trashed = false"
                    ),
                    fields="files(id,name)",
                    pageSize=1,
                )
                .execute()
            )
            existing = results.get("files", [])
            if existing:
                return existing[0]["id"]

        meta = {
            "name": _YOUROS_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = (
            service.files()
            .create(body=meta, fields="id")
            .execute()
        )
        return folder["id"]

    return await asyncio.get_event_loop().run_in_executor(None, _call)


@router.post("/drive/files/upload")
async def drive_upload_file(file: UploadFile = File(...)):
    """Upload a file to the yourOS (or legacy myOS) folder in Google Drive.

    Creates the yourOS folder if it doesn't already exist.
    Returns the new file's id, name, and a link to open it in Drive.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")
    if not has_write_scope():
        raise HTTPException(
            status_code=403,
            detail="Upload permission not granted. Please reconnect your Google account.",
        )

    import asyncio
    import io

    content = await file.read()
    filename = file.filename or "uploaded-file"
    mime_type = file.content_type or "application/octet-stream"

    try:
        folder_id = await _get_or_create_myos_folder()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not access the yourOS folder in Drive: {exc}",
        ) from exc

    def _call():
        from googleapiclient.http import MediaIoBaseUpload

        service = _get_cached_drive_service()
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        meta = {"name": filename, "parents": [folder_id]}
        result = (
            service.files()
            .create(
                body=meta,
                media_body=media,
                fields="id,name,webViewLink",
            )
            .execute()
        )
        return result

    try:
        created = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not upload the file to Drive: {exc}",
        ) from exc

    # Bust the file list cache so the new file appears immediately.
    try:
        await _sync_file_list()
    except Exception:
        pass

    return {
        "id": created.get("id"),
        "name": created.get("name"),
        "webViewLink": created.get("webViewLink"),
    }


from pydantic import BaseModel


class FolderCreateBody(BaseModel):
    name: str


@router.post("/drive/folders")
async def drive_create_folder(body: FolderCreateBody):
    """Create a new folder in Google Drive.

    Returns the new folder's id and name.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")
    if not has_write_scope():
        raise HTTPException(
            status_code=403,
            detail="Upload permission not granted. Please reconnect your Google account.",
        )

    import asyncio

    folder_name = body.name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty.")

    def _call():
        service = _get_cached_drive_service()
        meta = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        result = (
            service.files()
            .create(body=meta, fields="id,name,webViewLink")
            .execute()
        )
        return result

    try:
        created = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create folder in Drive: {exc}",
        ) from exc

    return {
        "id": created.get("id"),
        "name": created.get("name"),
        "webViewLink": created.get("webViewLink"),
    }


@router.delete("/drive/files/{file_id}")
async def drive_delete_file(file_id: str):
    """Move a file to the Drive trash.

    Requires drive.file scope at minimum. The full drive scope is needed
    to trash files that were not created by this app. If the token only
    has drive.file and the file is not app-created, surfaces a clear
    re-auth message instead of a generic error.
    Returns {ok: true} on success.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")
    if not has_write_scope():
        raise HTTPException(
            status_code=403,
            detail={
                "needs_reauth": True,
                "message": "Delete permission not granted. Please reconnect your Google account.",
            },
        )

    import asyncio

    def _call():
        service = _get_cached_drive_service()
        service.files().update(fileId=file_id, body={"trashed": True}).execute()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        err_str = str(exc)
        # appNotAuthorizedToFile means the token only has drive.file scope
        # and the file was not created by this app. The fix is to reconnect
        # with the full drive scope.
        if "appNotAuthorizedToFile" in err_str:
            raise HTTPException(
                status_code=403,
                detail={
                    "needs_reauth": True,
                    "message": (
                        "Your Google account connection needs to be updated to allow "
                        "deleting files you did not create in myOS. Please reconnect "
                        "your Google account to continue."
                    ),
                },
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not move the file to trash: {exc}",
        ) from exc

    # Bust the file list cache.
    try:
        await _sync_file_list()
    except Exception:
        pass

    # Tombstone so a fast double-delete retry does not re-surface the row.
    recent_deletes.record_id(f"drive-file:{file_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# File preview
# ---------------------------------------------------------------------------


@router.get("/drive/files/{file_id}/thumbnail")
async def drive_file_thumbnail(file_id: str):
    """Return the Google Drive thumbnail URL for a file.

    This is much faster than the full preview endpoint because it only
    fetches metadata (one lightweight API call) instead of downloading or
    exporting the entire file. The frontend uses this for the initial
    preview and offers a "View full document" button for the heavy export.
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    try:
        meta = await _get_file_meta(file_id)
    except Exception as exc:
        exc_str = str(exc).lower()
        if "invalid_grant" in exc_str or "token has been expired" in exc_str or "revoked" in exc_str:
            raise HTTPException(
                status_code=401,
                detail="Your Google connection expired. Please reconnect from the Drive page.",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not get file info from Drive: {exc}",
        ) from exc

    thumbnail_link = meta.get("thumbnailLink")
    if thumbnail_link:
        # Google returns small thumbnails by default. Request a larger one.
        if "=s" in thumbnail_link:
            thumbnail_link = thumbnail_link.rsplit("=s", 1)[0] + "=s800"
        return {"thumbnailLink": thumbnail_link, "name": meta.get("name", "")}

    return {"thumbnailLink": None, "name": meta.get("name", "")}


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

    # Fetch metadata and, for Google-native files, export as PDF in a single
    # executor call so we reuse the cached service and avoid a second thread-pool
    # dispatch.  pdf_bytes is None for non-exportable mime types.
    try:
        meta, pdf_bytes = await _fetch_meta_and_pdf_if_exportable(file_id)
    except Exception as exc:
        exc_str = str(exc).lower()
        if "invalid_grant" in exc_str or "token has been expired" in exc_str or "revoked" in exc_str:
            raise HTTPException(
                status_code=401,
                detail="Your Google connection expired. Please reconnect from the Drive page.",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not get file info from Drive: {exc}",
        ) from exc

    mime = meta.get("mimeType", "")
    web_view_link = meta.get("webViewLink", "")

    if mime in _EXPORTABLE_MIME:
        DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(pdf_bytes)
        return Response(content=pdf_bytes, media_type="application/pdf")

    # Non-Google-native files: try to download the binary.
    # Only download if Drive reports a non-zero size.
    size = int(meta.get("size", 0))
    if size > 0:
        # Files we can serve directly to the browser.
        direct_serve = {
            "application/pdf": "application/pdf",
            "image/png": "image/png",
            "image/jpeg": "image/jpeg",
            "image/gif": "image/gif",
            "image/webp": "image/webp",
            "image/svg+xml": "image/svg+xml",
            "text/plain": "text/plain; charset=utf-8",
            "text/markdown": "text/markdown; charset=utf-8",
            "text/csv": "text/csv; charset=utf-8",
            "text/html": "text/html; charset=utf-8",
            "application/json": "application/json",
        }
        response_mime = direct_serve.get(mime)
        # Heuristic: anything with a text/* mime can be served as plain text.
        if response_mime is None and mime.startswith("text/"):
            response_mime = "text/plain; charset=utf-8"
        if response_mime:
            collected: list[bytes] = []

            async def _stream_and_cache():
                async for chunk in _stream_download_file(file_id):
                    collected.append(chunk)
                    yield chunk
                DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(b"".join(collected))

            return StreamingResponse(_stream_and_cache(), media_type=response_mime)

        # Office files: download and convert to PDF via Drive's upload+export trick.
        # Drive can convert .docx/.pptx/.xlsx to Google native, then export as PDF.
        office_conversion_targets = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
            "application/msword": "application/vnd.google-apps.document",
            "application/vnd.ms-powerpoint": "application/vnd.google-apps.presentation",
            "application/vnd.ms-excel": "application/vnd.google-apps.spreadsheet",
        }
        if mime in office_conversion_targets:
            try:
                pdf_bytes = await _export_office_file_as_pdf(file_id, office_conversion_targets[mime])
                DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(pdf_bytes)
                return Response(content=pdf_bytes, media_type="application/pdf")
            except Exception:
                # Fall through to the not-previewable response below.
                pass

    # Not previewable inline.
    return {"previewable": False, "webViewLink": web_view_link, "mimeType": mime}


async def _get_file_meta(file_id: str) -> dict:
    """Fetch metadata for a single Drive file."""
    import asyncio

    def _call():
        service = _get_cached_drive_service()
        return (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,webViewLink,size,thumbnailLink",
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

        service = _get_cached_drive_service()
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


async def _fetch_meta_and_pdf_if_exportable(file_id: str) -> tuple[dict, bytes | None]:
    """Fetch metadata and, in the same executor call, export as PDF if the file is a
    Google-native type.  Reuses the cached service for both operations so the cold
    path pays only one service-build + one network round-trip to get metadata, then
    immediately starts the export without a second thread-pool dispatch.

    Returns (meta, pdf_bytes).  pdf_bytes is None for non-exportable mime types.
    """
    import asyncio
    import io

    def _call():
        service = _get_cached_drive_service()
        meta = (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,webViewLink,size,thumbnailLink",
            )
            .execute()
        )
        if meta.get("mimeType", "") not in _EXPORTABLE_MIME:
            return meta, None
        from googleapiclient.http import MediaIoBaseDownload
        request = service.files().export_media(fileId=file_id, mimeType="application/pdf")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return meta, buf.getvalue()

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _download_file(file_id: str) -> bytes:
    """Download a non-Google-native file's binary content."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _get_cached_drive_service()
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _stream_download_file(file_id: str):
    """Yield a non-Google-native file's binary content in 2 MB chunks.

    Runs each chunk download in the default executor so the event loop
    stays responsive between chunks, letting the browser receive data
    progressively instead of waiting for the entire file to buffer first.
    """
    import asyncio
    import io

    loop = asyncio.get_event_loop()

    def _init():
        from googleapiclient.http import MediaIoBaseDownload
        service = _get_cached_drive_service()
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request, chunksize=2 * 1024 * 1024)
        return buf, downloader

    buf, downloader = await loop.run_in_executor(None, _init)

    done = False
    while not done:
        def _get_chunk(buf=buf, downloader=downloader):
            _, is_done = downloader.next_chunk()
            buf.seek(0)
            data = buf.read()
            buf.seek(0)
            buf.truncate()
            return data, is_done

        chunk, done = await loop.run_in_executor(None, _get_chunk)
        if chunk:
            yield chunk


async def _export_office_file_as_pdf(file_id: str, target_google_mime: str) -> bytes:
    """Copy an Office file to a Google-native format, export as PDF, then delete the copy.

    This lets us preview .docx/.pptx/.xlsx files by round-tripping through Google's
    converter. The temporary copy is always deleted, even on failure.
    """
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _get_cached_drive_service()
        # Step 1: copy the file as a Google-native format.
        copy_meta = service.files().copy(
            fileId=file_id,
            body={"mimeType": target_google_mime, "name": f"_myos_preview_{file_id}"},
        ).execute()
        copy_id = copy_meta["id"]

        try:
            # Step 2: export the copy as PDF.
            request = service.files().export_media(
                fileId=copy_id, mimeType="application/pdf"
            )
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buf.getvalue()
        finally:
            # Step 3: always delete the temporary copy.
            try:
                service.files().delete(fileId=copy_id).execute()
            except Exception:
                pass

    return await asyncio.get_event_loop().run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# Structured preview (kind-specific renderers)
# ---------------------------------------------------------------------------


# Cap preview payloads so the response stays small and fast.
_SHEET_MAX_ROWS = 20
_SHEET_MAX_COLS = 10
_DOC_MAX_CHARS = 4000
_SLIDES_MAX_THUMBS = 20


def _classify_mime(mime: str) -> str:
    """Map a mime type to a preview kind."""
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/vnd.google-apps.spreadsheet":
        return "sheet"
    if mime == "application/vnd.google-apps.presentation":
        return "slides"
    if mime == "application/vnd.google-apps.document":
        return "doc"
    return "other"


def _enlarge_thumb(link: str | None) -> str | None:
    """Rewrite a Drive thumbnail URL to request a larger size."""
    if not link:
        return None
    if "=s" in link:
        return link.rsplit("=s", 1)[0] + "=s800"
    return link


async def _export_sheet_csv(file_id: str) -> str:
    """Export a Google Sheet as CSV text."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _get_cached_drive_service()
        request = service.files().export_media(fileId=file_id, mimeType="text/csv")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _export_doc_text(file_id: str) -> str:
    """Export a Google Doc as plain text."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _get_cached_drive_service()
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")

    return await asyncio.get_event_loop().run_in_executor(None, _call)


def _parse_csv_sample(csv_text: str) -> dict:
    """Parse a CSV string into a small sample for table rendering.

    Returns {"headers": [...], "rows": [[...], ...], "truncated": bool}.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(csv_text))
    rows: list[list[str]] = []
    total_cols = 0
    total_rows = 0
    for i, row in enumerate(reader):
        total_rows += 1
        total_cols = max(total_cols, len(row))
        if i >= _SHEET_MAX_ROWS:
            continue
        rows.append([cell for cell in row[:_SHEET_MAX_COLS]])
    if not rows:
        return {"headers": [], "rows": [], "truncated": False}
    headers = rows[0]
    data_rows = rows[1:]
    truncated = total_rows > _SHEET_MAX_ROWS or total_cols > _SHEET_MAX_COLS
    return {"headers": headers, "rows": data_rows, "truncated": truncated}


def _parse_doc_blocks(text: str) -> dict:
    """Turn plain text into a lightweight block list for formatted rendering.

    Each block is {"type": "heading"|"paragraph", "text": "..."}. A line
    that looks like a heading (short, no trailing period, followed by a
    blank line) becomes a heading. Everything else is a paragraph.
    """
    blocks: list[dict] = []
    # Split on blank lines into paragraphs.
    for chunk in text.split("\n\n"):
        line = chunk.strip()
        if not line:
            continue
        # Heading heuristic: short single line, no trailing period.
        is_single_line = "\n" not in line
        is_short = len(line) <= 80
        if is_single_line and is_short and not line.endswith("."):
            blocks.append({"type": "heading", "text": line})
        else:
            blocks.append({"type": "paragraph", "text": line})
    return {"blocks": blocks, "truncated": False}


async def _fetch_slides_thumbnails(file_id: str) -> list[dict]:
    """Return a list of {slide_id, thumbnail_url} for each slide.

    Uses the Slides API. Per-slide thumbnail failures are skipped rather
    than aborting the whole fetch, so a partial list is returned instead
    of falling back to a single Drive thumbnail.
    """
    import asyncio

    def _call():
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        tokens = get_credentials()
        client_config = {}
        try:
            from services.google_auth import _load_client_config
            client_config = _load_client_config()
        except Exception:
            pass
        creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_config.get("client_id"),
            client_secret=client_config.get("client_secret"),
        )
        slides = build("slides", "v1", credentials=creds)
        pres = slides.presentations().get(presentationId=file_id).execute()
        slide_list = pres.get("slides", [])[:_SLIDES_MAX_THUMBS]
        out = []
        for s in slide_list:
            sid = s.get("objectId")
            if not sid:
                continue
            try:
                thumb = (
                    slides.presentations()
                    .pages()
                    .getThumbnail(
                        presentationId=file_id,
                        pageObjectId=sid,
                        thumbnailProperties_thumbnailSize="MEDIUM",
                    )
                    .execute()
                )
                out.append({"slide_id": sid, "thumbnail_url": thumb.get("contentUrl", "")})
            except Exception as _thumb_exc:
                logger.debug("getThumbnail failed for slide %s in %s: %s", sid, file_id, _thumb_exc)
                out.append({"slide_id": sid, "thumbnail_url": ""})
        return out

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _export_all_sheets(file_id: str) -> list[dict]:
    """Return all sheets from a Google Spreadsheet via the Sheets API v4.

    Returns a list of {name, headers, rows, truncated} dicts, one per sheet tab.
    Falls back to empty rows on a per-sheet error rather than aborting.
    """
    import asyncio

    def _call():
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        tokens = get_credentials()
        client_config = {}
        try:
            from services.google_auth import _load_client_config
            client_config = _load_client_config()
        except Exception:
            pass
        creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_config.get("client_id"),
            client_secret=client_config.get("client_secret"),
        )
        sheets_svc = build("sheets", "v4", credentials=creds)

        spreadsheet = sheets_svc.spreadsheets().get(
            spreadsheetId=file_id,
            fields="sheets.properties(sheetId,title)",
        ).execute()
        sheet_props = [s["properties"] for s in spreadsheet.get("sheets", [])]

        result = []
        for prop in sheet_props:
            sheet_name = prop.get("title", "Sheet")
            try:
                values_resp = sheets_svc.spreadsheets().values().get(
                    spreadsheetId=file_id,
                    range=sheet_name,
                ).execute()
                all_rows = values_resp.get("values", [])
            except Exception:
                all_rows = []

            total_rows = len(all_rows)
            total_cols = max((len(r) for r in all_rows), default=0)
            truncated = total_rows > _SHEET_MAX_ROWS or total_cols > _SHEET_MAX_COLS

            sampled = all_rows[:_SHEET_MAX_ROWS]
            max_cols = min(total_cols, _SHEET_MAX_COLS)
            padded = [
                list(row[:max_cols]) + [""] * max(0, max_cols - len(row))
                for row in sampled
            ]

            if padded:
                headers = padded[0]
                data_rows = padded[1:]
            else:
                headers = []
                data_rows = []

            result.append({
                "name": sheet_name,
                "headers": headers,
                "rows": data_rows,
                "truncated": truncated,
            })

        return result

    return await asyncio.get_event_loop().run_in_executor(None, _call)


async def _export_doc_html(file_id: str) -> str:
    """Export a Google Doc as HTML."""
    import asyncio
    import io

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _get_cached_drive_service()
        request = service.files().export_media(fileId=file_id, mimeType="text/html")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")

    return await asyncio.get_event_loop().run_in_executor(None, _call)


@router.get("/drive/preview/{file_id}")
async def drive_file_structured_preview(file_id: str):
    """Return a structured preview for a Drive file.

    Response shape:
      {
        kind: "image"|"pdf"|"sheet"|"slides"|"doc"|"other",
        name: str,
        mime_type: str,
        thumbnail_url: str | None,
        export_url: str,       # always points to /api/drive/files/{id}/preview
        web_view_link: str,
        sample: dict | None,   # type-specific parsed preview (see below)
      }

    Sample payloads by kind:
      - image:  null (thumbnail_url is the inline render)
      - pdf:    null (use export_url in an iframe)
      - sheet:  {headers: [...], rows: [[...]], truncated: bool}
      - slides: {slides: [{slide_id, thumbnail_url}], truncated: bool}
      - doc:    {blocks: [{type, text}], truncated: bool}
      - other:  null
    """
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not connected to Google Drive.")

    try:
        meta = await _get_file_meta(file_id)
    except Exception as exc:
        exc_str = str(exc).lower()
        if "invalid_grant" in exc_str or "token has been expired" in exc_str or "revoked" in exc_str:
            raise HTTPException(
                status_code=401,
                detail="Your Google connection expired. Please reconnect from the Drive page.",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Could not get file info from Drive: {exc}",
        ) from exc

    mime = meta.get("mimeType", "")
    kind = _classify_mime(mime)
    thumbnail_url = _enlarge_thumb(meta.get("thumbnailLink"))
    export_url = f"/api/drive/files/{file_id}/preview"

    sample: dict | None = None

    if kind == "sheet":
        try:
            sheets = await _export_all_sheets(file_id)
            sample = {"sheets": sheets}
        except Exception as _sheets_exc:
            logger.warning("Sheets API failed for %s, falling back to CSV: %s", file_id, _sheets_exc)
            # Fall back to single-sheet CSV export.
            try:
                csv_text = await _export_sheet_csv(file_id)
                single = _parse_csv_sample(csv_text)
                sample = {"sheets": [{"name": "Sheet 1", **single}]}
            except Exception as _csv_exc:
                logger.warning("CSV fallback also failed for %s: %s", file_id, _csv_exc)
                sample = None

    elif kind == "doc":
        try:
            html = await _export_doc_html(file_id)
            sample = {"html": html, "truncated": False}
        except Exception:
            # Fall back to plain-text block rendering.
            try:
                doc_text = await _export_doc_text(file_id)
                sample = _parse_doc_blocks(doc_text)
            except Exception:
                sample = None

    elif kind == "slides":
        try:
            slides = await _fetch_slides_thumbnails(file_id)
            sample = {
                "slides": slides,
                "truncated": len(slides) >= _SLIDES_MAX_THUMBS,
            }
        except Exception as _slides_exc:
            logger.warning("Slides API failed for %s, using iframe fallback: %s", file_id, _slides_exc)
            # Return empty slides so the frontend falls through to the
            # export_url iframe, which shows the full deck interactively.
            sample = {"slides": [], "truncated": False}

    return {
        "kind": kind,
        "name": meta.get("name", ""),
        "mime_type": mime,
        "thumbnail_url": thumbnail_url,
        "export_url": export_url,
        "web_view_link": meta.get("webViewLink", ""),
        "sample": sample,
    }


# ---------------------------------------------------------------------------
# Google Docs proxy endpoints (used by fcp-gdocs plugin in myOS auth mode)
# ---------------------------------------------------------------------------


def _build_docs_service():
    """Build an authenticated Google Docs API service object."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Google API client is not available on this server.",
        ) from exc

    tokens = get_credentials()
    client_config = {}
    try:
        from services.google_auth import _load_client_config
        client_config = _load_client_config()
    except Exception:
        pass
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config.get("client_id"),
        client_secret=client_config.get("client_secret"),
    )
    return build("docs", "v1", credentials=creds)


class CreateDocFromMd(BaseModel):
    path: str
    title: Optional[str] = None


class ReplaceDocFromMd(BaseModel):
    doc_id: str
    path: str


class BatchUpdateDoc(BaseModel):
    doc_id: str
    requests: list


class UpdateDocText(BaseModel):
    text: str


@router.post("/drive/docs/create-from-md")
async def create_doc_from_md(body: CreateDocFromMd):
    """Upload a .md file to Drive and convert it to a Google Doc."""
    import asyncio

    file_path = Path(body.path).expanduser().resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    content = file_path.read_bytes()
    title = body.title or file_path.stem

    try:
        folder_id = await _get_or_create_myos_folder()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not access the yourOS folder in Drive: {exc}",
        ) from exc

    def _call():
        import io
        from googleapiclient.http import MediaIoBaseUpload

        service = _get_cached_drive_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype="text/markdown",
            resumable=False,
        )
        meta = {
            "name": title,
            "parents": [folder_id],
            "mimeType": "application/vnd.google-apps.document",
        }
        result = (
            service.files()
            .create(body=meta, media_body=media, fields="id,name,webViewLink")
            .execute()
        )
        return result

    try:
        created = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create Google Doc from markdown: {exc}",
        ) from exc

    return {
        "doc_id": created.get("id"),
        "title": created.get("name"),
        "url": created.get("webViewLink"),
    }


@router.post("/drive/docs/replace-from-md")
async def replace_doc_from_md(body: ReplaceDocFromMd):
    """Replace an existing Google Doc's content by re-uploading a .md file."""
    import asyncio

    file_path = Path(body.path).expanduser().resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    content = file_path.read_bytes()

    def _call():
        import io
        from googleapiclient.http import MediaIoBaseUpload

        service = _get_cached_drive_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype="text/markdown",
            resumable=False,
        )
        result = (
            service.files()
            .update(
                fileId=body.doc_id,
                media_body=media,
                fields="id,name,webViewLink",
            )
            .execute()
        )
        return result

    try:
        updated = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not replace Google Doc content: {exc}",
        ) from exc

    return {
        "doc_id": updated.get("id"),
        "title": updated.get("name"),
        "url": updated.get("webViewLink"),
    }


@router.post("/drive/docs/batch-update")
async def batch_update_doc(body: BatchUpdateDoc):
    """Forward a Google Docs batchUpdate request."""
    import asyncio

    def _call():
        service = _build_docs_service()
        result = (
            service.documents()
            .batchUpdate(
                documentId=body.doc_id,
                body={"requests": body.requests},
            )
            .execute()
        )
        return result

    try:
        response = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Google Docs batchUpdate failed: {exc}",
        ) from exc

    return {
        "doc_id": response.get("documentId"),
        "replies": response.get("replies", []),
    }


@router.get("/drive/docs/{doc_id}")
async def get_doc_structure(doc_id: str):
    """Return the structural outline of a Google Doc."""
    import asyncio

    def _call():
        service = _build_docs_service()
        doc = service.documents().get(documentId=doc_id).execute()
        headings = []
        for element in doc.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            style = paragraph.get("paragraphStyle", {})
            named_style = style.get("namedStyleType", "")
            if named_style.startswith("HEADING_"):
                level = int(named_style.split("_")[1])
                text = "".join(
                    run.get("textRun", {}).get("content", "")
                    for run in paragraph.get("elements", [])
                ).strip()
                headings.append({
                    "level": level,
                    "text": text,
                    "start_index": element.get("startIndex", 0),
                    "end_index": element.get("endIndex", 0),
                })
        return {
            "doc_id": doc.get("documentId"),
            "title": doc.get("title"),
            "headings": headings,
            "revision_id": doc.get("revisionId"),
        }

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read Google Doc: {exc}",
        ) from exc

    return result


@router.post("/drive/docs/{doc_id}/update-text")
async def update_doc_text(doc_id: str, body: UpdateDocText):
    """Replace a Google Doc's body with plain text via batchUpdate."""
    import asyncio

    def _call():
        service = _build_docs_service()
        doc = service.documents().get(documentId=doc_id).execute()
        content = doc.get("body", {}).get("content", [])
        end_index = content[-1].get("endIndex", 1) if content else 1

        requests: list = []
        if end_index > 1:
            requests.append({
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1}
                }
            })
        if body.text:
            requests.append({
                "insertText": {
                    "location": {"index": 1},
                    "text": body.text,
                }
            })
        if requests:
            service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests},
            ).execute()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not update Google Doc content: {exc}",
        ) from exc

    return {"ok": True, "doc_id": doc_id}


@router.get("/drive/docs/{doc_id}/export-text")
async def export_doc_text_endpoint(doc_id: str):
    """Return a Google Doc's current body as plain text.

    The in-app editor (→1939) calls this to pre-fill the edit box with the
    doc's current text. Without it the box would open empty and saving would
    erase the document, so we surface a clear error rather than empty content.
    """
    try:
        text = await _export_doc_text(doc_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read Google Doc content: {exc}",
        ) from exc

    return Response(content=text, media_type="text/plain")

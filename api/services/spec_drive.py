"""Spec Drive sync service.

Publishes specs as Google Docs and keeps them in sync.
Links are stored in ~/.myos/spec_drive_links.json (user data, not in repo).
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

_LINKS_PATH = Path.home() / ".myos" / "spec_drive_links.json"
_SPECS_FOLDER_NAME = "myOS Specs"


def _load_links() -> dict:
    try:
        if _LINKS_PATH.exists():
            return json.loads(_LINKS_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_links(links: dict) -> None:
    _LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LINKS_PATH.write_text(json.dumps(links, indent=2))


def _build_drive_service():
    """Build authenticated Google Drive API service object."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("Google API client not available.") from exc

    from services.google_auth import get_credentials

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


def _get_or_create_specs_folder_sync() -> str:
    """Find or create the 'myOS Specs' folder in Drive. Returns folder id."""
    service = _build_drive_service()
    escaped = _SPECS_FOLDER_NAME.replace("'", "\\'")
    query = (
        f"name='{escaped}' "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    result = service.files().list(q=query, fields="files(id,name)").execute()
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    folder = (
        service.files()
        .create(
            body={
                "name": _SPECS_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        )
        .execute()
    )
    return folder["id"]


def _spec_full_path(spec_path: str) -> Path:
    return (Path(PROJECT_ROOT) / spec_path).resolve()


async def publish_to_drive(spec_path: str) -> dict:
    """Create a new Google Doc from a spec file. Returns {doc_id, web_view_link}."""
    full_path = _spec_full_path(spec_path)
    if not full_path.exists():
        raise FileNotFoundError(f"Spec not found: {spec_path}")

    content = full_path.read_bytes()
    title = full_path.stem.replace("-", " ").replace("_", " ").title()

    def _call():
        from googleapiclient.http import MediaIoBaseUpload

        service = _build_drive_service()
        folder_id = _get_or_create_specs_folder_sync()
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

    created = await asyncio.get_event_loop().run_in_executor(None, _call)
    doc_id = created.get("id")
    web_view_link = created.get("webViewLink", "")

    links = _load_links()
    links[spec_path] = {"doc_id": doc_id, "web_view_link": web_view_link}
    _save_links(links)

    return {"doc_id": doc_id, "web_view_link": web_view_link}


async def sync_to_drive(spec_path: str) -> dict:
    """Sync local spec to Drive. Creates if not linked, updates if already linked."""
    links = _load_links()
    existing = links.get(spec_path)

    if not existing:
        result = await publish_to_drive(spec_path)
        return {**result, "action": "created"}

    full_path = _spec_full_path(spec_path)
    if not full_path.exists():
        raise FileNotFoundError(f"Spec not found: {spec_path}")

    content = full_path.read_bytes()
    doc_id = existing["doc_id"]

    def _call():
        from googleapiclient.http import MediaIoBaseUpload

        service = _build_drive_service()
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype="text/markdown",
            resumable=False,
        )
        result = (
            service.files()
            .update(
                fileId=doc_id,
                media_body=media,
                fields="id,name,webViewLink",
            )
            .execute()
        )
        return result

    updated = await asyncio.get_event_loop().run_in_executor(None, _call)
    web_view_link = updated.get("webViewLink", existing.get("web_view_link", ""))

    links[spec_path] = {"doc_id": doc_id, "web_view_link": web_view_link}
    _save_links(links)

    return {"doc_id": doc_id, "web_view_link": web_view_link, "action": "updated"}


async def pull_from_drive(spec_path: str) -> dict:
    """Pull Drive content back to the local spec file."""
    links = _load_links()
    existing = links.get(spec_path)
    if not existing:
        raise ValueError(f"No Drive link found for spec: {spec_path}")

    doc_id = existing["doc_id"]

    def _call():
        from googleapiclient.http import MediaIoBaseDownload

        service = _build_drive_service()
        request = service.files().export_media(fileId=doc_id, mimeType="text/plain")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")

    text = await asyncio.get_event_loop().run_in_executor(None, _call)

    full_path = _spec_full_path(spec_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(text)

    return {"updated": True, "content_preview": text[:200]}


def get_drive_link(spec_path: str) -> Optional[dict]:
    """Return stored link info for a spec, or None if not published."""
    links = _load_links()
    return links.get(spec_path)

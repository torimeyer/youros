"""Spec Drive sync endpoints.

Attaches publish/sync/pull/drive-link actions to the spec lifecycle.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.google_auth import is_authenticated

router = APIRouter(tags=["specs"])


def _check_drive_auth() -> None:
    if not is_authenticated():
        raise HTTPException(
            status_code=401,
            detail="Google Drive is not connected. Connect it in Settings first.",
        )


@router.post("/specs/{spec_path:path}/publish")
async def publish_spec_to_drive(spec_path: str):
    """Create a Google Doc from this spec and store the link."""
    _check_drive_auth()
    try:
        from services.spec_drive import publish_to_drive

        return await publish_to_drive(spec_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not publish to Drive: {exc}")


@router.post("/specs/{spec_path:path}/sync")
async def sync_spec_to_drive(spec_path: str):
    """Push local spec changes to Google Drive."""
    _check_drive_auth()
    try:
        from services.spec_drive import sync_to_drive

        return await sync_to_drive(spec_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not sync to Drive: {exc}")


@router.post("/specs/{spec_path:path}/pull")
async def pull_spec_from_drive(spec_path: str):
    """Pull Drive changes back to the local spec file."""
    _check_drive_auth()
    try:
        from services.spec_drive import pull_from_drive

        return await pull_from_drive(spec_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not pull from Drive: {exc}")


@router.get("/specs/{spec_path:path}/drive-link")
async def get_spec_drive_link(spec_path: str):
    """Return the Drive link for this spec, or {linked: false} if not published."""
    from services.spec_drive import get_drive_link

    link = get_drive_link(spec_path)
    if link is None:
        return {"linked": False}
    return {"linked": True, **link}

"""Sync router.

Endpoints for configuring and managing cross-device settings sync
via a private git repo the user owns.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import settings_sync

router = APIRouter(tags=["sync"])


class ConfigureBody(BaseModel):
    repo_url: str


@router.get("/sync/status")
async def get_sync_status():
    """Return whether sync is configured and when it last ran."""
    return settings_sync.status()


@router.post("/sync/configure")
async def configure_sync(body: ConfigureBody):
    """Set up a private git repo as the sync destination."""
    if not body.repo_url.strip():
        raise HTTPException(status_code=400, detail="repo_url is required")
    try:
        settings_sync.configure(body.repo_url.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"result": "configured", "repo_url": body.repo_url.strip()}


@router.post("/sync/push")
async def push_sync():
    """Push local settings to the remote git repo."""
    if not settings_sync.is_configured():
        raise HTTPException(status_code=400, detail="Sync is not set up yet.")
    try:
        result = settings_sync.push()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@router.post("/sync/pull")
async def pull_sync():
    """Pull settings from the remote git repo and apply them locally."""
    if not settings_sync.is_configured():
        raise HTTPException(status_code=400, detail="Sync is not set up yet.")
    try:
        result = settings_sync.pull()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@router.post("/sync/disconnect")
async def disconnect_sync():
    """Remove the sync configuration. Your remote repo is not deleted."""
    settings_sync.disconnect()
    return {"result": "disconnected"}

"""Team resource catalog router.

Endpoints:
  GET  /team/catalog               — list published items for the org
  GET  /team/catalog/{name}/versions — version history for a resource
  POST /team/catalog/agentfiles    — admin publishes a new agentfile version
  POST /team/catalog/{id}/rollback — rollback to a prior version
  POST /team/catalog/{id}/install  — member installs current version locally
"""

import asyncio
import logging

from typing import Optional

from fastapi import APIRouter, HTTPException

from services import team_catalog as svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["team_catalog"])

_DEFAULT_ORG = "default"


def _org_from_request() -> str:
    # TODO: derive from auth session once SSO lands
    return _DEFAULT_ORG


@router.get("/team/catalog")
async def list_catalog():
    org = _org_from_request()
    entries = await asyncio.to_thread(svc.list_catalog, org)
    return {"entries": entries}


@router.get("/team/catalog/{name}/versions")
async def list_versions(name: str):
    org = _org_from_request()
    versions = await asyncio.to_thread(svc.list_all_versions, org, name)
    return {"versions": versions}


@router.post("/team/catalog/agentfiles")
async def publish_agentfile(body: dict):
    name: Optional[str] = body.get("name")
    content: Optional[str] = body.get("content")
    signed_by: str = body.get("signed_by", "admin")

    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    if content is None:
        raise HTTPException(status_code=422, detail="content is required")

    org = _org_from_request()
    entry = await asyncio.to_thread(svc.publish_agentfile, org, name.strip(), content, signed_by)
    return {"entry": entry}


@router.post("/team/catalog/{entry_id}/rollback")
async def rollback_entry(entry_id: str):
    org = _org_from_request()
    entry = await asyncio.to_thread(svc.rollback, org, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"entry": entry}


@router.post("/team/catalog/{entry_id}/install")
async def install_entry(entry_id: str):
    org = _org_from_request()
    result = await asyncio.to_thread(svc.install_agentfile, org, entry_id)
    if not result:
        raise HTTPException(status_code=404, detail="Entry not found or not published")
    return {"ok": True, **result}

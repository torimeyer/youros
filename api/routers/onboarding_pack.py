"""Onboarding pack router (E7).

GET /api/onboarding/effective-pack  -- returns org pack if org exists, else community defaults.
GET /api/onboarding/org-pack        -- admin reads the current org pack.
PUT /api/onboarding/org-pack        -- admin writes the org starter pack.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import enterprise_store
from services.starter_pack import (
    get_effective_pack,
    get_org_starter_pack,
    get_community_pack,
    set_org_starter_pack,
)

router = APIRouter(tags=["onboarding"])


class StarterPackBody(BaseModel):
    agentfiles: list[str] = []
    skills: list[str] = []
    suggested_first_runs: list[dict] = []


@router.get("/onboarding/effective-pack")
async def effective_pack():
    """Return the pack this user should receive during onboarding.

    Returns the org's configured pack when an org exists and has one set,
    otherwise returns community defaults.
    """
    org = enterprise_store.get_org()
    org_id = org.get("id") if org else None
    return get_effective_pack(org_id)


@router.get("/onboarding/org-pack")
async def get_pack():
    """Return the org's current starter pack, or community defaults if none set."""
    org = enterprise_store.get_org()
    if not org:
        raise HTTPException(status_code=404, detail="No org configured.")
    org_id = org.get("id", "")
    pack = get_org_starter_pack(org_id)
    if pack is None:
        return get_community_pack()
    return pack


@router.put("/onboarding/org-pack")
async def save_pack(body: StarterPackBody):
    """Save the org's starter pack. Admin only."""
    org = enterprise_store.get_org()
    if not org:
        raise HTTPException(status_code=404, detail="No org configured.")
    org_id = org.get("id", "")
    return set_org_starter_pack(org_id, body.model_dump())

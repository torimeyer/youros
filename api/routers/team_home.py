"""Member-facing team home router.

GET /api/team/home — returns team picks, org announcements, and adoption summary.
"""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter

from services import enterprise_store
from services import team_catalog as catalog_svc
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

router = APIRouter(tags=["team_home"])

_DEFAULT_ORG = "default"


def _catalog_org() -> str:
    # TODO: derive from auth session once SSO lands
    return _DEFAULT_ORG


def _enterprise_org_id() -> str:
    org = enterprise_store.get_org()
    if org:
        return org.get("id", _DEFAULT_ORG)
    return _DEFAULT_ORG


def _load_announcements(org_id: str) -> list[dict]:
    path = youros_home() / "orgs" / org_id / "announcements.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return data.get("announcements", [])
    except Exception:
        return []


def _compute_adoption(org_id: str) -> dict:
    members = enterprise_store.list_members()
    active_members_this_week = len([m for m in members if m.get("role") != "admin"])
    entries = catalog_svc.list_catalog(org_id)
    top_skill = entries[0]["name"] if entries else None
    return {
        "active_members_this_week": active_members_this_week,
        "top_skill": top_skill,
    }


@router.get("/team/home")
async def team_home():
    catalog_org = _catalog_org()
    enterprise_org_id = _enterprise_org_id()

    entries, announcements, adoption = await asyncio.gather(
        asyncio.to_thread(catalog_svc.list_catalog, catalog_org),
        asyncio.to_thread(_load_announcements, enterprise_org_id),
        asyncio.to_thread(_compute_adoption, catalog_org),
    )

    return {
        "team_picks": entries[:3],
        "announcements": announcements,
        "adoption_summary": adoption,
    }

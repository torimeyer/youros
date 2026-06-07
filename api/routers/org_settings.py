"""Org settings router.

GET /api/org/settings  — return current org settings plus policies summary.
PATCH /api/org/settings — admin-only update of org settings.

Settings persist to ~/.youros/orgs/{org_id}/settings.json so they survive
git pulls and stay outside the repo.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.auth import get_current_user
from services import enterprise_store
from services.atomic_io import atomic_write_json

router = APIRouter(tags=["org_settings"])

MYOS_DIR = Path.home() / ".youros"


def _settings_path(org_id: str) -> Path:
    return MYOS_DIR / "orgs" / org_id / "settings.json"


def _load_settings(org_id: str) -> dict:
    path = _settings_path(org_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(org_id: str, data: dict) -> None:
    path = _settings_path(org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


PROVIDER_POLICY_VALUES = {"auto", "claude_only", "gemini_only", "prefer_gemini"}


def _default_settings() -> dict:
    return {
        "org_name": "",
        "logo_url": "",
        "marketplace_url": "",
        "welcome_message": "",
        "default_skill_pack_id": "",
        "provider_policy": "auto",
    }


class OrgSettingsUpdate(BaseModel):
    org_name: Optional[str] = None
    logo_url: Optional[str] = None
    marketplace_url: Optional[str] = None
    welcome_message: Optional[str] = None
    default_skill_pack_id: Optional[str] = None
    provider_policy: Optional[str] = None


@router.get("/org/settings")
async def get_org_settings(request: Request):
    """Return current org settings and a summary of active policies."""
    org = enterprise_store.get_org()
    if not org:
        raise HTTPException(status_code=404, detail="No org configured")

    settings = {**_default_settings(), **_load_settings(org["id"])}

    policies = enterprise_store.get_policies()
    policies_summary = {
        "max_agent_budget": policies.get("max_agent_budget"),
        "require_approval_above": policies.get("require_approval_above"),
        "allowed_models": policies.get("allowed_models", []),
        "audit_retention_days": policies.get("audit_retention_days"),
        "isolation_level": policies.get("isolation_level", "open"),
    }

    return {"settings": settings, "policies_summary": policies_summary}


@router.patch("/org/settings")
async def update_org_settings(body: OrgSettingsUpdate, request: Request):
    """Update org settings. Requires admin role."""
    user = get_current_user(request)
    if user and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update org settings")

    org = enterprise_store.get_org()
    if not org:
        raise HTTPException(status_code=404, detail="No org configured")

    settings = {**_default_settings(), **_load_settings(org["id"])}
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "provider_policy" in updates and updates["provider_policy"] not in PROVIDER_POLICY_VALUES:
        raise HTTPException(status_code=422, detail=f"provider_policy must be one of: {sorted(PROVIDER_POLICY_VALUES)}")
    settings.update(updates)
    _save_settings(org["id"], settings)

    return {"settings": settings}

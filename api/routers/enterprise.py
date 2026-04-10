"""Enterprise mode router.

Provides endpoints for org setup, team management, policy configuration,
and compliance audit export. All endpoints are prefixed with /enterprise.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import enterprise_store

router = APIRouter(tags=["enterprise"])


class OrgCreate(BaseModel):
    name: str
    admin_email: str


class OrgUpdate(BaseModel):
    name: Optional[str] = None


class MemberAdd(BaseModel):
    email: str
    role: str = "member"


class MemberRoleUpdate(BaseModel):
    role: str


class PolicyUpdate(BaseModel):
    max_agent_budget: Optional[float] = None
    require_approval_above: Optional[float] = None
    allowed_models: Optional[list] = None
    audit_retention_days: Optional[int] = None


@router.get("/enterprise")
async def get_enterprise():
    """Return the full enterprise state: org, members, policies."""
    return enterprise_store.get_enterprise_state()


@router.post("/enterprise/org")
async def create_org(body: OrgCreate):
    """Create an org and activate enterprise mode."""
    if enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="An org already exists")
    org = enterprise_store.create_org(body.name, body.admin_email)
    return {"org": org}


@router.patch("/enterprise/org")
async def update_org(body: OrgUpdate):
    """Update org profile."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=404, detail="No org exists")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    org = enterprise_store.update_org(updates)
    return {"org": org}


@router.delete("/enterprise/org")
async def delete_org():
    """Delete the org and deactivate enterprise mode."""
    enterprise_store.delete_org()
    return {"ok": True}


# --- Team ---

@router.get("/enterprise/members")
async def list_members():
    return {"members": enterprise_store.list_members()}


@router.post("/enterprise/members")
async def add_member(body: MemberAdd):
    try:
        member = enterprise_store.add_member(body.email, body.role)
        return {"member": member}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/enterprise/members/{member_id}")
async def remove_member(member_id: str):
    if enterprise_store.remove_member(member_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Member not found")


@router.patch("/enterprise/members/{member_id}/role")
async def update_role(member_id: str, body: MemberRoleUpdate):
    member = enterprise_store.update_member_role(member_id, body.role)
    if member:
        return {"member": member}
    raise HTTPException(status_code=404, detail="Member not found")


# --- Policies ---

@router.get("/enterprise/policies")
async def get_policies():
    return {"policies": enterprise_store.get_policies()}


@router.patch("/enterprise/policies")
async def update_policies(body: PolicyUpdate):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    policies = enterprise_store.update_policies(updates)
    return {"policies": policies}


# --- Audit export ---

@router.get("/enterprise/audit")
async def export_audit():
    """Export the audit trail for compliance. Returns all audit events."""
    from pathlib import Path
    from config import PROJECT_ROOT

    audit_path = PROJECT_ROOT / ".ostk" / "audit.jsonl"
    events = []
    if audit_path.exists():
        import json
        try:
            with open(audit_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass

    return {
        "events": events[-500:],  # Last 500 events
        "total": len(events),
        "enterprise": enterprise_store.is_enterprise(),
    }

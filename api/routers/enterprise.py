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


# --- SSO ---

class SSOConfig(BaseModel):
    provider: str  # "okta", "azure", "google", "auth0"
    issuer_url: str
    client_id: str
    client_secret: str
    allowed_domains: Optional[list] = None


@router.get("/enterprise/sso")
async def get_sso():
    """Return SSO configuration (secrets redacted)."""
    from services.sso import get_sso_config
    config = get_sso_config()
    if not config:
        return {"configured": False}
    return {
        "configured": True,
        "provider": config.get("provider"),
        "issuer_url": config.get("issuer_url"),
        "allowed_domains": config.get("allowed_domains", []),
        "configured_at": config.get("configured_at"),
    }


@router.post("/enterprise/sso")
async def configure_sso(body: SSOConfig):
    """Configure SSO for the org."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")
    from services.sso import configure_sso as _configure
    config = _configure(
        provider=body.provider,
        issuer_url=body.issuer_url,
        client_id=body.client_id,
        client_secret=body.client_secret,
        allowed_domains=body.allowed_domains,
    )
    return {"configured": True, "provider": config["provider"]}


@router.delete("/enterprise/sso")
async def remove_sso():
    """Remove SSO configuration."""
    from services.sso import remove_sso as _remove
    _remove()
    return {"ok": True}


@router.get("/enterprise/sso/login")
async def sso_login_url():
    """Get the SSO login URL to redirect the user to."""
    import os
    from services.sso import get_auth_url
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3010")
    redirect_uri = "http://localhost:8000/api/enterprise/sso/callback"  # REDIRECT_URI
    url = get_auth_url(redirect_uri)
    if not url:
        raise HTTPException(status_code=400, detail="SSO not configured")
    return {"url": url}


@router.get("/enterprise/sso/callback")
async def sso_callback(code: str = "", state: str = ""):
    """Handle the SSO callback from the IdP."""
    import os
    from fastapi.responses import RedirectResponse
    from services.sso import exchange_code, validate_state

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3010")
    redirect_uri = "http://localhost:8000/api/enterprise/sso/callback"  # REDIRECT_URI

    if not code:
        return RedirectResponse(f"{frontend_url}/settings?sso_error=no_code")

    if state:
        valid = validate_state(state)
        if not valid:
            return RedirectResponse(f"{frontend_url}/settings?sso_error=invalid_state")

    claims = await exchange_code(code, redirect_uri)
    if not claims:
        return RedirectResponse(f"{frontend_url}/settings?sso_error=exchange_failed")

    # Auto-add the user as a team member if not already present
    email = claims.get("email", "")
    if email:
        try:
            enterprise_store.add_member(email, "member")
        except ValueError:
            pass  # Already a member

    return RedirectResponse(f"{frontend_url}/settings?sso_success=true&sso_email={email}")


# --- Autonomy / Isolation ---

class IsolationUpdate(BaseModel):
    level: str  # "open", "governed", "sealed"


ISOLATION_LEVELS = {
    "open": {
        "label": "Open",
        "description": "Full agent access, minimal restrictions. Best for solo use.",
        "agent_permissions": ["shell", "file:read", "file:write", "file:edit", "web:search", "web:fetch"],
        "require_approval": False,
        "allow_destructive": True,
    },
    "governed": {
        "label": "Governed",
        "description": "Agents need approval for destructive operations. Best for teams.",
        "agent_permissions": ["shell:readonly", "file:read", "file:edit", "web:search"],
        "require_approval": True,
        "allow_destructive": False,
    },
    "sealed": {
        "label": "Sealed",
        "description": "Strict enforcement. Agents can only read and suggest. Best for compliance.",
        "agent_permissions": ["file:read", "web:search"],
        "require_approval": True,
        "allow_destructive": False,
    },
}


@router.get("/enterprise/isolation")
async def get_isolation():
    """Return the current isolation level and available levels."""
    data = enterprise_store.get_policies()
    current = data.get("isolation_level", "open")
    return {
        "current": current,
        "levels": ISOLATION_LEVELS,
    }


@router.patch("/enterprise/isolation")
async def set_isolation(body: IsolationUpdate):
    """Set the isolation level for the org."""
    if body.level not in ISOLATION_LEVELS:
        raise HTTPException(status_code=400, detail=f"Unknown isolation level: {body.level}")
    enterprise_store.update_policies({"isolation_level": body.level})
    level_config = ISOLATION_LEVELS[body.level]
    # Also update the related policies
    enterprise_store.update_policies({
        "require_approval_above": 0.0 if level_config["require_approval"] else 5.0,
        "allow_destructive": level_config["allow_destructive"],
    })
    return {"level": body.level, "config": level_config}


# --- Agentfile ---

@router.get("/enterprise/agentfile")
async def get_agentfile():
    """Return the current Agentfile configuration."""
    from config import PROJECT_ROOT
    agentfile_path = PROJECT_ROOT / "Agentfile"
    content = ""
    if agentfile_path.exists():
        try:
            content = agentfile_path.read_text()
        except OSError:
            pass
    policies = enterprise_store.get_policies()
    isolation = policies.get("isolation_level", "open")
    level_config = ISOLATION_LEVELS.get(isolation, ISOLATION_LEVELS["open"])
    return {
        "content": content,
        "isolation_level": isolation,
        "permissions": level_config["agent_permissions"],
        "exists": bool(content),
    }


@router.post("/enterprise/agentfile/generate")
async def generate_agentfile():
    """Generate an Agentfile based on the current isolation level and user profile."""
    from config import PROJECT_ROOT
    from services.settings_store import settings_store

    policies = enterprise_store.get_policies()
    isolation = policies.get("isolation_level", "open")
    level_config = ISOLATION_LEVELS.get(isolation, ISOLATION_LEVELS["open"])

    user_name = settings_store.get("user_name", "")
    user_role = settings_store.get("user_role", "")
    os_name = settings_store.get("os_name", "myOS")

    perm = {
        "open": "autonomous",
        "governed": "governed",
        "sealed": "sealed",
    }.get(isolation, "autonomous")

    tools = "\n".join(f"TOOL {t}" for t in level_config["agent_permissions"])

    agentfile = (
        f"# Agentfile — {os_name} default agent\n"
        f"# Generated by enterprise mode (isolation: {isolation})\n"
        f"# Owner: {user_name or 'not set'}\n"
        f"# Role: {user_role or 'not set'}\n"
        f"\n"
        f"FROM auto\n"
        f"PROMPT \"You are the default agent for {os_name}.\"\n"
        f"{tools}\n"
        f"LIMIT tokens 200000\n"
        f"LIMIT budget {policies.get('max_agent_budget', 10.0)}\n"
        f"LIMIT permissions {perm}\n"
        f"BOOT ostk boot\n"
    )

    agentfile_path = PROJECT_ROOT / "Agentfile"
    agentfile_path.write_text(agentfile)

    return {"content": agentfile, "path": str(agentfile_path)}

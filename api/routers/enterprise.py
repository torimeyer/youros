"""Enterprise mode router.

Provides endpoints for org setup, team management, policy configuration,
and compliance audit export. All endpoints are prefixed with /enterprise.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from services import enterprise_store
from services import recent_deletes
from services.policy_enforcement import ISOLATION_LEVELS
from services.session import create_session, verify_session, SESSION_COOKIE_NAME

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


class ApiKeySet(BaseModel):
    provider: str
    key: str


class OrgTemplateCreate(BaseModel):
    name: str
    description: str = ""
    icon: str = "smart_toy"
    prompt_template: str = ""
    model: str = "sonnet"
    budget: float = 2.0


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
    recent_deletes.record_id("enterprise-org")
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
        recent_deletes.record_id(f"enterprise-member:{member_id}")
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


# --- Org-level API keys ---

@router.post("/enterprise/api-keys")
async def set_api_key(body: ApiKeySet):
    """Admin sets an org-level API key for a provider (e.g. Anthropic, Gemini)."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")
    try:
        enterprise_store.set_org_api_key(body.provider, body.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "provider": body.provider.lower()}


@router.get("/enterprise/api-keys")
async def list_api_keys():
    """List which providers have org keys configured. Never returns key values."""
    providers = enterprise_store.list_org_api_key_providers()
    return {"providers": providers}


@router.delete("/enterprise/api-keys/{provider}")
async def delete_api_key(provider: str):
    """Remove the org-level API key for a provider."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")
    if enterprise_store.delete_org_api_key(provider):
        recent_deletes.record_id(f"enterprise-api-key:{provider}")
        return {"ok": True}
    raise HTTPException(status_code=404, detail="No key found for that provider")


# --- Org-level shared templates ---

@router.get("/enterprise/templates")
async def list_org_templates():
    """List shared org-level agent templates."""
    return {"templates": enterprise_store.list_org_templates()}


@router.post("/enterprise/templates")
async def create_org_template(body: OrgTemplateCreate):
    """Admin creates a shared org template."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")
    try:
        tpl = enterprise_store.add_org_template(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"template": tpl}


@router.delete("/enterprise/templates/{template_id}")
async def delete_org_template(template_id: str):
    """Admin removes a shared org template."""
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")
    if enterprise_store.delete_org_template(template_id):
        recent_deletes.record_id(f"enterprise-template:{template_id}")
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Template not found")


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


# --- User session ---


class InviteCreate(BaseModel):
    email: str
    role: str = "member"


@router.post("/enterprise/invite")
async def create_invite(body: InviteCreate, request: Request):
    """Admin creates an invite link for a new team member."""
    from services.auth import get_current_user

    user = get_current_user(request)
    if user and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invite members")

    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")

    import os

    token = secrets.token_urlsafe(32)
    try:
        enterprise_store.add_invite(body.email, body.role, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3010")
    invite_url = f"{frontend_url}/invite/{token}"
    return {"invite_url": invite_url, "email": body.email}


@router.get("/enterprise/invite/{token}")
async def accept_invite(token: str):
    """Accept an invite link, add user as member, redirect to login."""
    import os

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3010")

    invite = enterprise_store.consume_invite(token)
    if not invite:
        raise HTTPException(
            status_code=404, detail="Invalid or expired invite link"
        )

    session_token = create_session(
        email=invite["email"],
        role=invite.get("role", "member"),
        member_id=invite["id"],
        org_id=invite.get("org_id", ""),
    )

    response = RedirectResponse(
        url=f"{frontend_url}/?invite_accepted=true"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=86400,
        path="/",
    )
    return response


@router.get("/enterprise/invites")
async def list_invites(request: Request):
    """List pending invites. Admin only."""
    from services.auth import get_current_user

    user = get_current_user(request)
    if user and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view invites")

    return {"invites": enterprise_store.list_pending_invites()}


class MagicLinkRequest(BaseModel):
    email: str


@router.post("/enterprise/magic-link")
async def send_magic_link(body: MagicLinkRequest):
    """Generate a magic link login token for the given email.

    The email must belong to an existing team member. In production
    the link would be emailed. For now the URL is returned directly
    so an admin can share it.
    """
    if not enterprise_store.is_enterprise():
        raise HTTPException(status_code=400, detail="Enterprise mode must be active first")

    # Check the email is a registered member
    members = enterprise_store.list_members()
    member = next((m for m in members if m["email"].lower() == body.email.lower()), None)
    if not member:
        raise HTTPException(status_code=404, detail="No team member with that email")

    token = secrets.token_urlsafe(32)
    enterprise_store.add_login_token(body.email, token)

    import os
    base_url = os.environ.get("BACKEND_URL", "http://localhost:8000")
    login_url = f"{base_url}/api/enterprise/login/{token}"

    return {
        "result": "Magic link created",
        "login_url": login_url,
        "email": body.email,
    }


@router.get("/enterprise/login/{token}")
async def magic_link_login(token: str):
    """Consume a magic link token, create a session, set a cookie, and redirect."""
    import os

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3010")

    member = enterprise_store.consume_login_token(token)
    if not member:
        return RedirectResponse(f"{frontend_url}/settings?login_error=invalid_or_expired")

    org = enterprise_store.get_org()
    org_id = org.get("id", "") if org else ""

    session_token = create_session(
        email=member["email"],
        role=member.get("role", "member"),
        member_id=member["id"],
        org_id=org_id,
    )

    response = RedirectResponse(f"{frontend_url}/settings?login_success=true")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=24 * 3600,
        path="/",
    )
    return response


@router.get("/enterprise/me")
async def get_me(request: Request):
    """Return the current user's identity and enterprise status."""
    if not enterprise_store.is_enterprise():
        return {"authenticated": False, "enterprise": False}

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    claims = verify_session(token) if token else None

    if not claims:
        return {"authenticated": False, "enterprise": True}

    return {
        "authenticated": True,
        "enterprise": True,
        "email": claims["sub"],
        "role": claims.get("role", "member"),
        "member_id": claims.get("member_id", ""),
    }


@router.post("/enterprise/logout")
async def logout():
    """Clear the session cookie and log out."""
    response = JSONResponse({"result": "logged out"})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


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
    recent_deletes.record_id("enterprise-sso")
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

    # Find the member record and create a session
    member = None
    for m in enterprise_store.list_members():
        if m["email"].lower() == email.lower():
            member = m
            break

    if not member:
        return RedirectResponse(f"{frontend_url}/settings?sso_error=member_not_found")

    org = enterprise_store.get_org()
    org_id = org.get("id", "") if org else ""

    session_token = create_session(
        email=email,
        role=member.get("role", "member"),
        member_id=member["id"],
        org_id=org_id,
    )

    response = RedirectResponse(f"{frontend_url}/settings?sso_success=true")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=24 * 3600,
        path="/",
    )
    return response


# --- Autonomy / Isolation ---

class IsolationUpdate(BaseModel):
    level: str  # "open", "governed", "sealed"



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


# --- Org signing key ---

@router.get("/org/signing-key")
async def get_signing_key():
    """Return signing key status. Never returns private key."""
    from services import signing
    org = enterprise_store.get_org()
    org_id = org.get("id", "default") if org else "default"
    return signing.get_status(org_id)


@router.post("/org/signing-key/generate")
async def generate_signing_key(request: Request):
    """Admin generates an Ed25519 signing key for catalog item signing."""
    from services.auth import get_current_user
    from services import signing
    user = get_current_user(request)
    if user and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can manage the org signing key")
    org = enterprise_store.get_org()
    org_id = org.get("id", "default") if org else "default"
    return signing.generate(org_id)


@router.post("/org/signing-key/revoke")
async def revoke_signing_key(request: Request):
    """Admin removes the org signing key. Signed catalog items can no longer be verified."""
    from services.auth import get_current_user
    from services import signing
    user = get_current_user(request)
    if user and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can manage the org signing key")
    org = enterprise_store.get_org()
    org_id = org.get("id", "default") if org else "default"
    signing.revoke(org_id)
    return {
        "ok": True,
        "warning": "The org signing key has been removed. Catalog items signed with this key can no longer be verified.",
    }


# --- Agentfile ---

class AgentfileRawUpdate(BaseModel):
    content: str


class AgentfileFormUpdate(BaseModel):
    name: str = ""
    description: str = ""
    model: str = "auto"
    tools: list = []
    instructions: str = ""
    tags: list = []


@router.get("/enterprise/agentfile/form")
async def get_agentfile_form():
    """Return the org Agentfile parsed into structured form fields."""
    from config import PROJECT_ROOT
    from services.agentfile_parser import agentfile_to_form

    agentfile_path = PROJECT_ROOT / "Agentfile"
    content = ""
    if agentfile_path.exists():
        try:
            content = agentfile_path.read_text()
        except OSError:
            pass
    form = agentfile_to_form(content) if content else {
        "name": "org-default",
        "description": "",
        "model": "auto",
        "tools": [],
        "instructions": "",
        "tags": [],
    }
    form["name"] = form.get("name") or "org-default"
    return form


@router.put("/enterprise/agentfile/form")
async def update_agentfile_form(body: AgentfileFormUpdate):
    """Save form fields back to the org Agentfile."""
    from config import PROJECT_ROOT
    from services.agentfile_parser import form_to_agentfile

    content = form_to_agentfile(body.model_dump())
    agentfile_path = PROJECT_ROOT / "Agentfile"
    try:
        agentfile_path.write_text(content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save Agentfile: {e}")
    return {"content": content, "path": str(agentfile_path)}


@router.patch("/enterprise/agentfile")
async def update_agentfile_raw(body: AgentfileRawUpdate):
    """Save raw agentfile text for the org."""
    from config import PROJECT_ROOT

    agentfile_path = PROJECT_ROOT / "Agentfile"
    try:
        agentfile_path.write_text(body.content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save Agentfile: {e}")
    return {"content": body.content, "path": str(agentfile_path)}


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
        f"# Agentfile - {os_name} default agent\n"
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

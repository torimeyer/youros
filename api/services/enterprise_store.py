"""Enterprise settings store.

Manages the ENTITYFILE: org identity, team members, policies, and
compliance settings. All data lives in ~/.youros/enterprise.json so
it survives git pulls and stays outside the repo.

Enterprise mode is opt-in. When no enterprise.json exists, the app
runs in single-user mode. Creating an org activates enterprise mode.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
ENTERPRISE_PATH = MYOS_DIR / "enterprise.json"


def _load() -> dict:
    if not ENTERPRISE_PATH.exists():
        return {}
    try:
        return json.loads(ENTERPRISE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    atomic_write_json(ENTERPRISE_PATH, data)


def is_enterprise() -> bool:
    """Return True if enterprise mode is active (an org exists)."""
    data = _load()
    return bool(data.get("org"))


def get_org() -> Optional[dict]:
    """Return the org profile or None if not in enterprise mode."""
    return _load().get("org")


def create_org(name: str, admin_email: str) -> dict:
    """Create an org and activate enterprise mode."""
    data = _load()
    org = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "admin_email": admin_email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data["org"] = org
    if "members" not in data:
        data["members"] = [
            {
                "id": str(uuid.uuid4())[:8],
                "email": admin_email,
                "role": "admin",
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    if "policies" not in data:
        data["policies"] = {
            "max_agent_budget": 10.0,
            "require_approval_above": 5.0,
            "allowed_models": ["sonnet", "haiku", "opus"],
            "audit_retention_days": 90,
        }
    _save(data)
    return org


def update_org(updates: dict) -> dict:
    """Update org profile fields."""
    data = _load()
    if not data.get("org"):
        raise ValueError("No org exists")
    data["org"].update(updates)
    _save(data)
    return data["org"]


def delete_org() -> None:
    """Delete the org and deactivate enterprise mode."""
    if ENTERPRISE_PATH.exists():
        ENTERPRISE_PATH.unlink()


# --- Team members ---

def list_members() -> list[dict]:
    data = _load()
    return data.get("members", [])


def add_member(email: str, role: str = "member") -> dict:
    data = _load()
    if not data.get("org"):
        raise ValueError("No org exists")
    # Check for duplicate
    for m in data.get("members", []):
        if m["email"].lower() == email.lower():
            raise ValueError(f"{email} is already a member")
    member = {
        "id": str(uuid.uuid4())[:8],
        "email": email,
        "role": role,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    data.setdefault("members", []).append(member)
    _save(data)
    return member


def remove_member(member_id: str) -> bool:
    data = _load()
    before = len(data.get("members", []))
    data["members"] = [m for m in data.get("members", []) if m["id"] != member_id]
    if len(data["members"]) < before:
        _save(data)
        return True
    return False


def update_member_role(member_id: str, role: str) -> Optional[dict]:
    data = _load()
    for m in data.get("members", []):
        if m["id"] == member_id:
            m["role"] = role
            _save(data)
            return m
    return None


# --- Policies ---

def get_policies() -> dict:
    data = _load()
    return data.get("policies", {})


def update_policies(updates: dict) -> dict:
    data = _load()
    data.setdefault("policies", {}).update(updates)
    _save(data)
    return data["policies"]


# --- Full state ---

def get_enterprise_state() -> dict:
    """Return the full enterprise state for the UI."""
    data = _load()
    return {
        "enabled": bool(data.get("org")),
        "org": data.get("org"),
        "members": data.get("members", []),
        "policies": data.get("policies", {}),
    }


# --- Magic link authentication ---

LOGIN_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


def add_login_token(email: str, token: str) -> None:
    """Store a magic link token with 1-hour expiry."""
    data = _load()
    tokens = data.get("login_tokens", {})
    tokens[token] = {
        "email": email.lower(),
        "created_at": time.time(),
        "expires_at": time.time() + LOGIN_TOKEN_EXPIRY_SECONDS,
    }
    data["login_tokens"] = tokens
    _save(data)


def consume_login_token(token: str) -> Optional[dict]:
    """Validate and consume a login token. Returns member info or None.

    The token is removed after use (single-use). Expired tokens are
    also cleaned up.
    """
    data = _load()
    tokens = data.get("login_tokens", {})
    entry = tokens.pop(token, None)

    # Clean up any other expired tokens while we are here
    now = time.time()
    tokens = {k: v for k, v in tokens.items() if v.get("expires_at", 0) > now}
    data["login_tokens"] = tokens
    _save(data)

    if not entry:
        return None

    if entry.get("expires_at", 0) < now:
        return None

    email = entry["email"]
    # Find the matching member
    for m in data.get("members", []):
        if m["email"].lower() == email:
            return m

    return None


# --- Invite tokens ---

INVITE_TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days


def add_invite(email: str, role: str, token: str) -> None:
    """Store a pending invite with 7-day expiry.

    Raises ValueError if an invite for this email already exists and
    has not expired yet.
    """
    data = _load()
    invites = data.get("invites", {})

    # Check for existing non-expired invite for this email
    now = time.time()
    for existing in invites.values():
        if (
            existing["email"].lower() == email.lower()
            and existing.get("expires_at", 0) > now
        ):
            raise ValueError(f"An invite for {email} already exists")

    invites[token] = {
        "email": email.lower(),
        "role": role,
        "created_at": time.time(),
        "expires_at": time.time() + INVITE_TOKEN_EXPIRY_SECONDS,
    }
    data["invites"] = invites
    _save(data)


def consume_invite(token: str) -> Optional[dict]:
    """Validate and consume an invite token.

    If valid, adds the user as a member and returns their member info.
    The token is removed after use.
    Returns None if the token is missing or expired.
    """
    data = _load()
    invites = data.get("invites", {})
    entry = invites.pop(token, None)

    # Clean up expired invites
    now = time.time()
    invites = {k: v for k, v in invites.items() if v.get("expires_at", 0) > now}
    data["invites"] = invites
    _save(data)

    if not entry:
        return None

    if entry.get("expires_at", 0) < now:
        return None

    email = entry["email"]
    role = entry.get("role", "member")

    # Add as a member (skip if already exists)
    org = data.get("org")
    org_id = org.get("id", "") if org else ""
    member_id = str(uuid.uuid4())[:8]

    for m in data.get("members", []):
        if m["email"].lower() == email:
            # Already a member, just return them
            return {**m, "org_id": org_id}

    member = {
        "id": member_id,
        "email": email,
        "role": role,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    data = _load()
    data.setdefault("members", []).append(member)
    # Remove the consumed invite
    invites = data.get("invites", {})
    invites.pop(token, None)
    data["invites"] = invites
    _save(data)

    return {**member, "org_id": org_id}


def list_pending_invites() -> list[dict]:
    """Return all pending (non-expired) invites."""
    data = _load()
    invites = data.get("invites", {})
    now = time.time()
    result = []
    for token, inv in invites.items():
        if inv.get("expires_at", 0) > now:
            result.append({
                "token": token,
                "email": inv["email"],
                "role": inv.get("role", "member"),
                "created_at": inv.get("created_at"),
                "expires_at": inv.get("expires_at"),
            })
    return result


# --- Org-level API keys ---

def set_org_api_key(provider: str, key: str) -> None:
    """Store an org-level API key for a provider (e.g. 'anthropic', 'gemini').

    Keys are stored under the ``api_keys`` section of enterprise.json.
    Only admins should call this (enforced at the router level).
    """
    data = _load()
    if not data.get("org"):
        raise ValueError("No org exists")
    data.setdefault("api_keys", {})[provider.lower()] = key
    _save(data)


def get_org_api_key(provider: str) -> Optional[str]:
    """Return the org API key for a provider, or None if not set."""
    data = _load()
    return data.get("api_keys", {}).get(provider.lower())


def delete_org_api_key(provider: str) -> bool:
    """Remove the org API key for a provider. Returns True if it existed."""
    data = _load()
    keys = data.get("api_keys", {})
    if provider.lower() in keys:
        del keys[provider.lower()]
        data["api_keys"] = keys
        _save(data)
        return True
    return False


def list_org_api_key_providers() -> list[str]:
    """Return the list of providers that have org keys configured.

    Never returns the key values themselves.
    """
    data = _load()
    return list(data.get("api_keys", {}).keys())


# --- Org-level shared agent templates ---

def add_org_template(template: dict) -> dict:
    """Store a shared org-level agent template with an auto-generated ID.

    Template shape: {name, description, icon, prompt_template, model, budget}
    Returns the template with its generated ``id`` field.
    """
    data = _load()
    if not data.get("org"):
        raise ValueError("No org exists")
    tpl = dict(template)
    tpl["id"] = str(uuid.uuid4())[:8]
    data.setdefault("org_templates", []).append(tpl)
    _save(data)
    return tpl


def list_org_templates() -> list[dict]:
    """Return all shared org-level agent templates."""
    data = _load()
    return data.get("org_templates", [])


def delete_org_template(template_id: str) -> bool:
    """Remove an org template by ID. Returns True if it existed."""
    data = _load()
    templates = data.get("org_templates", [])
    before = len(templates)
    data["org_templates"] = [t for t in templates if t.get("id") != template_id]
    if len(data["org_templates"]) < before:
        _save(data)
        return True
    return False

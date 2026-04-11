"""Enterprise settings store.

Manages the ENTITYFILE: org identity, team members, policies, and
compliance settings. All data lives in ~/.myos/enterprise.json so
it survives git pulls and stays outside the repo.

Enterprise mode is opt-in. When no enterprise.json exists, the app
runs in single-user mode. Creating an org activates enterprise mode.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json

MYOS_DIR = Path.home() / ".myos"
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

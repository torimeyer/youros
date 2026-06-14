"""Org starter pack service.

Admins configure a default set of agents and skills that new org members
receive automatically during onboarding.

Disk store: ~/.youros/orgs/{org_id}/starter_pack.json
Pack shape: {agentfiles: [...], skills: [...], suggested_first_runs: [...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

_ORGS_DIR = youros_home() / "orgs"

_COMMUNITY_PACK: dict = {
    "agentfiles": [
        "builtin-builder",
        "builtin-diagnose",
        "builtin-research",
        "builtin-explain-plain",
    ],
    "skills": [],
    "suggested_first_runs": [
        {
            "id": "fr-default-1",
            "title": "Start a conversation",
            "description": "Open chat and ask anything.",
            "icon": "chat",
        },
        {
            "id": "fr-default-2",
            "title": "Try a quick research task",
            "description": "Ask your AI to research a topic and summarize it.",
            "icon": "search",
        },
        {
            "id": "fr-default-3",
            "title": "Brainstorm an idea",
            "description": "Describe a problem or goal and get a list of options.",
            "icon": "lightbulb",
        },
    ],
}


def _pack_path(org_id: str) -> Path:
    return _ORGS_DIR / org_id / "starter_pack.json"


def get_org_starter_pack(org_id: str) -> Optional[dict]:
    """Return the org's custom starter pack, or None if not configured."""
    path = _pack_path(org_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def set_org_starter_pack(org_id: str, pack: dict) -> dict:
    """Save the starter pack for an org. Returns the saved pack."""
    path = _pack_path(org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, pack)
    return pack


def get_community_pack() -> dict:
    """Return the community default pack."""
    import copy
    return copy.deepcopy(_COMMUNITY_PACK)


def get_effective_pack(org_id: Optional[str]) -> dict:
    """Return org pack when set, else community defaults."""
    if org_id:
        org_pack = get_org_starter_pack(org_id)
        if org_pack is not None:
            return org_pack
    return get_community_pack()

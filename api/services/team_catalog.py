"""Team resource catalog service.

Stores shared agentfiles for an org. Each publish creates a new immutable
version row. Rollback flips the current_version pointer without deleting
history. Members install by copying the current version to their local
~/.youros/agentfiles/ dir.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

CATALOG_BASE = youros_home() / "team_catalog"

EntryStatus = Literal["draft", "published", "rolled_back"]
EntryKind = Literal["agentfile"]


def _org_path(org_id: str) -> Path:
    p = CATALOG_BASE / org_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _entries_path(org_id: str) -> Path:
    return _org_path(org_id) / "entries.json"


def _load(org_id: str) -> dict:
    p = _entries_path(org_id)
    if not p.exists():
        return {"entries": {}, "current_versions": {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        logger.warning("Corrupt catalog for org %s, starting fresh", org_id)
        return {"entries": {}, "current_versions": {}}


def _save(org_id: str, data: dict) -> None:
    _entries_path(org_id).write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _verify_entry(entry: dict, org_id: str):
    """Return True/False/None for the entry's signature validity."""
    sig = entry.get("signature")
    if sig is None:
        return None
    try:
        from services import signing as signing_svc
        return signing_svc.verify_content(entry["content"], sig, org_id)
    except Exception:
        return None


def list_catalog(org_id: str) -> list[dict]:
    """Return all published entries (latest version per name)."""
    data = _load(org_id)
    result = []
    for entry_id, entry in data["entries"].items():
        if entry["status"] == "published":
            e = dict(entry)
            e["signature_valid"] = _verify_entry(e, org_id)
            result.append(e)
    # Sort newest first
    result.sort(key=lambda e: e["published_at"] or "", reverse=True)
    return result


def list_all_versions(org_id: str, name: str) -> list[dict]:
    """Return all versions for a given resource name, newest first."""
    data = _load(org_id)
    rows = [e for e in data["entries"].values() if e["name"] == name]
    rows.sort(key=lambda e: e["version"], reverse=True)
    return rows


def publish_agentfile(
    org_id: str,
    name: str,
    content: str,
    signed_by: str,
) -> dict:
    """Publish a new version. Returns the new entry."""
    data = _load(org_id)

    # Determine next version number for this name
    existing = [e for e in data["entries"].values() if e["name"] == name]
    next_version = max((e["version"] for e in existing), default=0) + 1

    entry_id = str(uuid.uuid4())
    entry = {
        "id": entry_id,
        "kind": "agentfile",
        "name": name,
        "version": next_version,
        "content": content,
        "signed_by_org": signed_by,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "status": "published",
        "signature": None,
    }

    # Sign content if org signing key exists
    try:
        from services import signing as signing_svc
        sig = signing_svc.sign_content(content, org_id)
        if sig:
            entry["signature"] = sig
    except Exception:
        pass

    # Mark any previously published version for this name as rolled_back
    # so the list only shows the current published one.
    for existing_entry in data["entries"].values():
        if existing_entry["name"] == name and existing_entry["status"] == "published":
            existing_entry["status"] = "rolled_back"

    data["entries"][entry_id] = entry
    data["current_versions"][name] = entry_id
    _save(org_id, data)

    # TODO: replace mock with real ostk validate call once callable from Python
    # asyncio.to_thread(subprocess.run, ["ostk", "validate", ...])
    logger.info("Published agentfile '%s' v%d for org %s", name, next_version, org_id)
    return entry


def rollback(org_id: str, entry_id: str) -> Optional[dict]:
    """Roll back to a prior version by ID. Returns the reinstated entry."""
    data = _load(org_id)
    target = data["entries"].get(entry_id)
    if not target:
        return None

    name = target["name"]

    # Demote current published entry for this name
    for e in data["entries"].values():
        if e["name"] == name and e["status"] == "published":
            e["status"] = "rolled_back"

    # Restore target as a new published version (new row = immutable history)
    new_id = str(uuid.uuid4())
    existing = [e for e in data["entries"].values() if e["name"] == name]
    next_version = max(e["version"] for e in existing) + 1

    new_entry = {
        "id": new_id,
        "kind": "agentfile",
        "name": name,
        "version": next_version,
        "content": target["content"],
        "signed_by_org": target["signed_by_org"],
        "published_at": datetime.now(timezone.utc).isoformat(),
        "status": "published",
    }
    data["entries"][new_id] = new_entry
    data["current_versions"][name] = new_id
    _save(org_id, data)

    logger.info(
        "Rolled back agentfile '%s' to content from v%d (now v%d)",
        name, target["version"], next_version,
    )
    return new_entry


def install_agentfile(org_id: str, entry_id: str) -> Optional[dict]:
    """Copy an entry's content to ~/.youros/agentfiles/. Returns install path info."""
    data = _load(org_id)
    entry = data["entries"].get(entry_id)
    if not entry or entry["status"] != "published":
        return None

    dest_dir = youros_home() / "agentfiles"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{entry['name']}.agent"
    dest.write_text(entry["content"])

    logger.info("Installed agentfile '%s' to %s", entry["name"], dest)
    return {"installed_path": str(dest), "name": entry["name"], "version": entry["version"]}


def get_entry(org_id: str, entry_id: str) -> Optional[dict]:
    data = _load(org_id)
    return data["entries"].get(entry_id)

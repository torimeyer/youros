"""Data-only seed loader (spec S009 Track 0.3).

On backend startup this reads every JSON file in ~/.myos/seeds/ and
applies it through existing CRUD code paths. This is the supported way
a downstream distribution preloads starter content (labels, shared org
templates, agent templates) without the main codebase knowing that
distribution exists.

Seed file shape:

  {
    "target": "labels" | "org_templates" | "agent_templates",
    "version": "1",
    "payload": [
      {"op": "create", "data": {...}},
      ...
    ]
  }

Design constraints (from the spec, do not violate):
- Pure data. Only "create" operations, no code self-registration, no DSL.
- Fail-open. A bad or unparseable seed is logged and skipped. Startup
  never errors because of a seed.
- Reversible. Applied seeds are tracked in <seeds dir>/.applied keyed on
  (target, version) together with the IDs of every item created, so:
    * re-running with the same version does not duplicate,
    * a changed version re-applies as an update (the old version's items
      are removed, then the new payload is applied),
    * unapply(target, version) deletes exactly the items that were
      created for that (target, version) pair.

One seed file per target is the supported shape; if two files share a
target, the later one (alphabetical order) wins.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Seeds live under ~/.myos (the customization layer), not ~/.youros (the
# backend data root), per spec S009 Track 0.3.
SEEDS_DIR = Path.home() / ".myos" / "seeds"
APPLIED_FILENAME = ".applied"


# --- Target handlers -------------------------------------------------------
# Each target maps to an existing CRUD code path. create(data) returns the
# new item's ID; delete(item_id) removes it. Imports are lazy so a broken
# optional store can never break loader import (fail-open).

def _create_label(data: dict) -> Optional[str]:
    from services.labels_store import labels_store
    label = labels_store.create_label(
        str(data.get("name") or ""), str(data.get("color") or "#3b82f6")
    )
    return label.get("id")


def _delete_label(item_id: str) -> None:
    from services.labels_store import labels_store
    labels_store.delete_label(item_id)


def _create_org_template(data: dict) -> Optional[str]:
    from services import enterprise_store
    return enterprise_store.add_org_template(dict(data)).get("id")


def _delete_org_template(item_id: str) -> None:
    from services import enterprise_store
    enterprise_store.delete_org_template(item_id)


def _create_agent_template(data: dict) -> Optional[str]:
    from services.agent_templates_store import agent_templates_store
    return (agent_templates_store.create(dict(data)) or {}).get("id")


def _delete_agent_template(item_id: str) -> None:
    from services.agent_templates_store import agent_templates_store
    agent_templates_store.delete(item_id)


TARGETS: dict[str, dict[str, Callable]] = {
    "labels": {"create": _create_label, "delete": _delete_label},
    "org_templates": {"create": _create_org_template, "delete": _delete_org_template},
    "agent_templates": {"create": _create_agent_template, "delete": _delete_agent_template},
}


# --- Applied-seed tracking --------------------------------------------------

def _applied_path(seeds_dir: Path) -> Path:
    return seeds_dir / APPLIED_FILENAME


def _load_applied(seeds_dir: Path) -> dict:
    try:
        data = json.loads(_applied_path(seeds_dir).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_applied(seeds_dir: Path, data: dict) -> None:
    try:
        seeds_dir.mkdir(parents=True, exist_ok=True)
        _applied_path(seeds_dir).write_text(json.dumps(data, indent=2))
    except OSError:
        logger.warning("seed_loader: could not write the applied-seeds record")


def _validate_seed(raw) -> Optional[tuple[str, str, list]]:
    """Return (target, version, payload) or None when the seed is invalid."""
    if not isinstance(raw, dict):
        return None
    target = raw.get("target")
    version = raw.get("version")
    payload = raw.get("payload")
    if not isinstance(target, str) or target not in TARGETS:
        return None
    if isinstance(version, bool) or not isinstance(version, (str, int)):
        return None
    if not isinstance(payload, list):
        return None
    return target, str(version), payload


def _delete_created(target: str, created_ids: list) -> int:
    """Best-effort delete of previously created items. Returns count removed."""
    removed = 0
    for item_id in created_ids:
        try:
            TARGETS[target]["delete"](str(item_id))
            removed += 1
        except Exception as exc:
            logger.warning(
                "seed_loader: could not remove seeded item %s from %s: %s",
                item_id, target, exc,
            )
    return removed


# --- Public API --------------------------------------------------------------

def apply_seeds(seeds_dir: Optional[Path] = None) -> dict:
    """Read and apply every seed file. Never raises (fail-open).

    Returns a summary: {"applied": [...], "skipped": [...], "unchanged": [...]}
    with seed file names in each bucket.
    """
    seeds_dir = seeds_dir or SEEDS_DIR
    summary: dict = {"applied": [], "skipped": [], "unchanged": []}
    try:
        if not seeds_dir.is_dir():
            return summary
        seed_files = sorted(seeds_dir.glob("*.json"))
    except OSError:
        return summary

    applied = _load_applied(seeds_dir)

    for seed_file in seed_files:
        try:
            raw = json.loads(seed_file.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("seed_loader: %s is not valid JSON, skipping it", seed_file.name)
            summary["skipped"].append(seed_file.name)
            continue

        validated = _validate_seed(raw)
        if validated is None:
            logger.warning(
                "seed_loader: %s does not have a valid target, version, and payload, skipping it",
                seed_file.name,
            )
            summary["skipped"].append(seed_file.name)
            continue
        target, version, payload = validated

        entry = applied.get(target)
        if entry and str(entry.get("version")) == version:
            # Already applied at this version. Re-running must not duplicate.
            summary["unchanged"].append(seed_file.name)
            continue
        if entry:
            # Version bump: remove what the old version created, then
            # apply the new payload as the update.
            _delete_created(target, entry.get("created_ids") or [])
            applied.pop(target, None)

        created_ids: list[str] = []
        for op in payload:
            if not isinstance(op, dict) or op.get("op") != "create":
                logger.warning(
                    "seed_loader: %s contains an unsupported operation, skipping that entry",
                    seed_file.name,
                )
                continue
            try:
                item_id = TARGETS[target]["create"](op.get("data") or {})
            except Exception as exc:
                logger.warning(
                    "seed_loader: could not apply an entry from %s: %s", seed_file.name, exc
                )
                continue
            if item_id:
                created_ids.append(str(item_id))

        applied[target] = {
            "version": version,
            "created_ids": created_ids,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "seed_file": seed_file.name,
        }
        _save_applied(seeds_dir, applied)
        summary["applied"].append(seed_file.name)

    return summary


def unapply(target: str, version: str, seeds_dir: Optional[Path] = None) -> dict:
    """Delete exactly the items created for (target, version).

    Returns {"removed": N}. A missing record or a version mismatch is a
    no-op, so callers can never delete items another version created.
    """
    seeds_dir = seeds_dir or SEEDS_DIR
    applied = _load_applied(seeds_dir)
    entry = applied.get(target)
    if not entry or str(entry.get("version")) != str(version):
        return {"removed": 0}

    removed = _delete_created(target, entry.get("created_ids") or [])
    applied.pop(target, None)
    _save_applied(seeds_dir, applied)
    return {"removed": removed}

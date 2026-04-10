"""Career growth tracking router.

Tracks skills with proficiency levels (1-5) and optional notes,
stored in ~/.myos/growth.json.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["growth"])

MYOS_DIR = Path.home() / ".myos"
GROWTH_PATH = MYOS_DIR / "growth.json"


class SkillCreate(BaseModel):
    skill: str
    level: int = Field(..., ge=1, le=5)
    note: Optional[str] = None
    category: Optional[str] = None


def _load_skills() -> list:
    if GROWTH_PATH.exists():
        try:
            return json.loads(GROWTH_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_skills(skills: list) -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    GROWTH_PATH.write_text(json.dumps(skills, indent=2))


@router.get("/growth")
async def list_skills():
    """List all tracked skills, most recent first."""
    skills = _load_skills()
    skills.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return {"skills": skills, "count": len(skills)}


@router.post("/growth")
async def add_skill(entry: SkillCreate):
    """Add a skill entry with a proficiency level (1-5) and optional note."""
    skills = _load_skills()
    record = {
        "id": str(uuid.uuid4()),
        "skill": entry.skill,
        "level": entry.level,
        "note": entry.note or "",
        "category": entry.category or "general",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    skills.append(record)
    _save_skills(skills)
    return record


@router.get("/growth/summary")
async def skill_summary():
    """Summary of skills grouped by category.

    For each category, shows the skills and their highest recorded level.
    """
    skills = _load_skills()

    # Group by category, track highest level per skill
    categories: dict[str, dict[str, dict]] = defaultdict(dict)
    for entry in skills:
        cat = entry.get("category", "general")
        skill_name = entry.get("skill", "")
        level = entry.get("level", 1)
        if skill_name not in categories[cat] or level > categories[cat][skill_name].get("level", 0):
            categories[cat][skill_name] = {
                "skill": skill_name,
                "level": level,
                "note": entry.get("note", ""),
            }

    summary = {}
    for cat, skill_map in sorted(categories.items()):
        summary[cat] = {
            "skills": sorted(skill_map.values(), key=lambda s: s["skill"]),
            "count": len(skill_map),
        }

    return {"categories": summary, "total_skills": len({e.get("skill") for e in skills})}

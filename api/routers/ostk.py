from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from services import recent_deletes

router = APIRouter(tags=["ostk"])

_VERB_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")

_VERBS_FILE = Path(__file__).resolve().parents[2] / "config" / "custom_verbs.jsonl"


def _read_verbs() -> list[dict]:
    if not _VERBS_FILE.exists():
        return []
    verbs = []
    for line in _VERBS_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            verbs.append(json.loads(line))
    return verbs


def _write_verbs(verbs: list[dict]) -> None:
    _VERBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VERBS_FILE.write_text(
        "\n".join(json.dumps(v) for v in verbs) + ("\n" if verbs else "")
    )


class VerbCreate(BaseModel):
    name: str
    description: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _VERB_NAME_RE.match(v):
            raise ValueError("name must match ^[a-zA-Z][a-zA-Z0-9_-]*$")
        return v


@router.post("/ostk/language/verb")
def register_verb(body: VerbCreate):
    verbs = _read_verbs()
    verbs.append({"name": body.name, "description": body.description})
    _write_verbs(verbs)
    return {"verb": body.name, "status": "registered"}


@router.get("/ostk/language/verbs")
def list_verbs():
    return {"verbs": _read_verbs()}


@router.delete("/ostk/language/verb/{name}")
def delete_verb(name: str):
    verbs = _read_verbs()
    filtered = [v for v in verbs if v["name"] != name]
    if len(filtered) == len(verbs):
        raise HTTPException(status_code=404, detail=f"verb '{name}' not found")
    _write_verbs(filtered)
    recent_deletes.record_id(f"verb:{name}")
    return {"verb": name, "status": "removed"}

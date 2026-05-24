"""HTTP surface for the myOS primitives registry."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.primitives_registry import (
    PRIMITIVES,
    all_active,
    contract as load_contract,
    get as get_primitive,
)

router = APIRouter(tags=["primitives"])


@router.get("/primitives")
async def list_primitives() -> dict:
    """List every registered primitive."""
    return {"primitives": [p.asdict() for p in all_active()]}


@router.get("/primitives/{name}")
async def get_one(name: str) -> dict:
    """Return one primitive's registry entry."""
    p = get_primitive(name)
    if p is None:
        raise HTTPException(status_code=404, detail=f"primitive '{name}' not found")
    return p.asdict()


@router.get("/primitives/{name}/contract")
async def get_contract(name: str) -> dict:
    """Return the parsed contract doc for one primitive."""
    if get_primitive(name) is None:
        raise HTTPException(status_code=404, detail=f"primitive '{name}' not found")
    c = load_contract(name)
    if c is None:
        raise HTTPException(status_code=404, detail=f"contract doc missing for '{name}'")
    return {"name": name, "contract": c}

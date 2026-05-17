"""
api/routers/primitives.py

HTTP routes for the myOS primitives registry.

Endpoints:
  GET /api/primitives              — list all active primitives
  GET /api/primitives/{name}       — metadata for one primitive
  GET /api/primitives/{name}/contract — full contract JSON for one primitive
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.primitives_registry import all_active, contract, get

router = APIRouter(tags=["primitives"])


@router.get("/primitives")
def list_primitives() -> list[dict]:
    """Return all active primitives with their metadata."""
    from dataclasses import asdict

    return [asdict(p) for p in all_active()]


@router.get("/primitives/{name}")
def get_primitive(name: str) -> dict:
    """Return metadata for a single primitive by name."""
    from dataclasses import asdict

    p = get(name)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{name}' not found")
    return asdict(p)


@router.get("/primitives/{name}/contract")
def get_primitive_contract(name: str) -> dict:
    """
    Return the full contract for a primitive.

    The contract includes the primitive's public symbols, HTTP routes, version,
    and doc path — everything needed for a caller to understand and use it
    without reading source code.
    """
    c = contract(name)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Primitive '{name}' not found")
    return c

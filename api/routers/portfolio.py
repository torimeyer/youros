"""Executive Summary API (internal namespace: portfolio).

Vendor-neutral. Returns configured:false and writes nothing when the
~/.myos/portfolio.json mapping is absent.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/portfolio/health")
async def get_portfolio_health() -> dict:
    raise NotImplementedError

"""Team mode router.

GET  /team               — team config + member list (or {configured: false})
POST /team/configure     — save ~/.myos/team.json
POST /team/sync          — outbound + inbound sync
GET  /team/search        — search teammates' items
GET  /team/feed          — recent items from all teammates
GET  /team/context       — top context items for a task
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from models.team_schemas import TeamConfig
from services import team_sync

router = APIRouter(tags=["team"])


@router.get("/team")
async def get_team():
    cfg = team_sync.load_config()
    if cfg is None:
        return {"configured": False}
    cache = team_sync._load_cache()
    members = [
        {"member_id": mid, "display_name": data.get("display_name", mid)}
        for mid, data in cache.items()
    ]
    return {
        "configured": True,
        "config": cfg.model_dump(),
        "members": members,
    }


@router.post("/team/configure")
async def configure_team(body: TeamConfig):
    team_sync.save_config(body)
    return {"ok": True, "config": body.model_dump()}


@router.post("/team/sync")
async def sync_team():
    cfg = team_sync.load_config()
    if cfg is None:
        return {"ok": False, "error": "Team not configured"}
    out = team_sync.sync_outbound(cfg)
    inb = team_sync.sync_inbound(cfg)
    return {"ok": True, "pushed": out["pushed"], "pulled": inb["pulled"]}


@router.get("/team/search")
async def search_team(
    q: str = Query(..., description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
):
    cfg = team_sync.load_config()
    if cfg is None:
        return {"results": [], "count": 0}
    results = team_sync.search_team(q, category, cfg)
    return {"results": results, "count": len(results)}


@router.get("/team/feed")
async def team_feed(limit: int = Query(20, ge=1, le=100)):
    cfg = team_sync.load_config()
    if cfg is None:
        return {"items": [], "count": 0}
    items = team_sync.get_feed(cfg, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/team/context")
async def team_context(task: str = Query(..., description="Task description")):
    cfg = team_sync.load_config()
    if cfg is None:
        return {"context": [], "count": 0}
    context = team_sync.get_context_for_task(task, cfg)
    return {"context": context, "count": len(context)}

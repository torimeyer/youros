"""patterns.py — REST endpoints for the pattern watcher v2 review panel.

Exposes:
  GET  /api/patterns/clusters       list observation clusters for the panel
  POST /api/patterns/clusters/{id}/tier   promote or dismiss a cluster tier

Clusters are derived from ~/myos/observations/ markdown files. Each distinct
observation kind+sub-kind group becomes a cluster row. The cluster ID is a
hex hash of the kind+representative text (NC-3 fallback, since ostk-recall
cluster IDs are only available when the daemon is running).
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.pattern_watcher import promote_tier

_log = logging.getLogger(__name__)
router = APIRouter(tags=["patterns"])

_OBS_DIR = Path.home() / "myos" / "observations"

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(
    r"^- (?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
    r"(?P<kind>\w+:\w+)(?P<rest>.*)"
)


def _parse_bullets(path: Path) -> list[dict]:
    if not path.exists():
        return []
    bullets = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _BULLET_RE.match(line.strip())
        if m:
            bullets.append({
                "ts": m.group("ts"),
                "kind": m.group("kind"),
                "rest": m.group("rest").strip(),
            })
    return bullets


def _cluster_id(kind: str, rep: str) -> str:
    key = f"{kind}:{rep[:80]}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _human_label(kind: str, sample_rest: str) -> str:
    if kind == "task:defer":
        return "You frequently defer tasks"
    if kind == "vocab:new":
        tok = re.search(r'token="([^"]+)"', sample_rest)
        if tok:
            return f'You use the term "{tok.group(1)}"'
        return "New vocabulary term used"
    return f"Pattern: {kind}"


def _time_ago(iso: str) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        secs = (datetime.now(tz=timezone.utc) - dt).total_seconds()
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs / 60)}m ago"
        if secs < 86400:
            return f"{int(secs / 3600)}h ago"
        return f"{int(secs / 86400)}d ago"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/patterns/clusters")
async def get_clusters():
    """Return observation clusters for the review panel."""
    all_bullets: list[dict] = []
    for md in _OBS_DIR.glob("*.md"):
        all_bullets.extend(_parse_bullets(md))

    if not all_bullets:
        return {"clusters": []}

    groups: dict[str, list[dict]] = defaultdict(list)
    for b in all_bullets:
        groups[b["kind"]].append(b)

    clusters = []
    for kind, bullets in groups.items():
        bullets_sorted = sorted(bullets, key=lambda x: x["ts"])
        rep = bullets_sorted[-1]["rest"]
        cid = _cluster_id(kind, rep)
        clusters.append({
            "id": cid,
            "kind": kind,
            "label": _human_label(kind, bullets_sorted[-1]["rest"]),
            "count": len(bullets),
            "last_seen": _time_ago(bullets_sorted[-1]["ts"]),
            "tier": 1,
        })

    clusters.sort(key=lambda c: -c["count"])
    return {"clusters": clusters}


class TierRequest(BaseModel):
    tier: int
    action: Optional[str] = None


@router.post("/patterns/clusters/{cluster_id}/tier")
async def set_cluster_tier(cluster_id: str, body: TierRequest):
    """Promote or dismiss a cluster tier (Confirm=2, Approve silent=3, Dismiss=0)."""
    tier = body.tier
    if tier not in (0, 2, 3):
        raise HTTPException(status_code=400, detail="tier must be 0 (dismiss), 2 (confirm), or 3 (approve silent)")

    if tier == 0:
        success = promote_tier(cluster_id, 0)
    else:
        success = promote_tier(cluster_id, tier)

    if not success:
        _log.warning("patterns: tier promotion failed cluster=%s tier=%d", cluster_id, tier)

    return {"ok": True, "cluster_id": cluster_id, "tier": tier}

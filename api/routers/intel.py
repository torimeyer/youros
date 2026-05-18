"""FastAPI router for competitive intel captures (Theme C MVP — →1440).

Endpoints:
  POST /api/intel/capture        — store a competitor signal
  GET  /api/intel/feed?since=ISO — captures newest first
  GET  /api/intel/competitors    — distinct competitor names (for autocomplete)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# Storage root — redirected in tests via monkeypatch.setattr(intel_router, "CAPTURES_DIR", ...)
CAPTURES_DIR: Path = Path.home() / ".myos" / "intel" / "captures"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CaptureRequest(BaseModel):
    competitor: str
    tags: List[str] = []
    url: Optional[str] = None
    text: Optional[str] = None


class CaptureResponse(BaseModel):
    id: str
    competitor: str
    tags: List[str]
    url: Optional[str]
    text: Optional[str]
    captured_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dir() -> Path:
    """Create CAPTURES_DIR if it does not exist and return it."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTURES_DIR


def _load_all_captures() -> list[dict]:
    """Read every capture JSON from CAPTURES_DIR, sorted newest first."""
    d = CAPTURES_DIR
    if not d.exists():
        return []
    captures = []
    for f in d.glob("*.json"):
        try:
            captures.append(json.loads(f.read_text()))
        except Exception:
            pass  # skip malformed files
    captures.sort(key=lambda c: c.get("captured_at", ""), reverse=True)
    return captures


# ---------------------------------------------------------------------------
# POST /api/intel/capture
# ---------------------------------------------------------------------------


@router.post("/intel/capture")
async def post_capture(body: CaptureRequest) -> dict:
    """Store a competitor signal as a JSON file under CAPTURES_DIR."""
    capture_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": capture_id,
        "competitor": body.competitor,
        "tags": body.tags,
        "url": body.url,
        "text": body.text,
        "captured_at": now,
    }
    d = _ensure_dir()
    (d / f"{capture_id}.json").write_text(json.dumps(record))
    return {"id": capture_id, "captured_at": now}


# ---------------------------------------------------------------------------
# GET /api/intel/feed
# ---------------------------------------------------------------------------


@router.get("/intel/feed")
async def get_feed(since: Optional[str] = None) -> dict:
    """Return captures newest first, optionally filtered by ISO since timestamp."""
    captures = _load_all_captures()
    if since:
        captures = [c for c in captures if c.get("captured_at", "") >= since]
    return {"captures": captures}


# ---------------------------------------------------------------------------
# GET /api/intel/competitors
# ---------------------------------------------------------------------------


@router.get("/intel/competitors")
async def get_competitors() -> dict:
    """Return sorted distinct competitor names derived from existing captures."""
    captures = _load_all_captures()
    seen: set[str] = set()
    for c in captures:
        name = c.get("competitor", "").strip()
        if name:
            seen.add(name)
    return {"competitors": sorted(seen)}

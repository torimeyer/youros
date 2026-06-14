"""FastAPI router for competitive intel captures (Theme C MVP — →1440).

Endpoints:
  POST /api/intel/capture            — store a competitor signal
  GET  /api/intel/feed?since=ISO     — captures newest first
  GET  /api/intel/competitors        — distinct competitor names (for autocomplete)
  GET  /api/intel/digest?window_days — synthesize captures into a markdown digest (SC-016, SC-017)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from services.youros_paths import youros_home

router = APIRouter()

# Storage root — redirected in tests via monkeypatch.setattr(intel_router, "CAPTURES_DIR", ...)
CAPTURES_DIR: Path = youros_home() / "intel" / "captures"

# Spec promotion target — redirected in tests via monkeypatch.setattr(intel_router, "DIGEST_SPECS_DIR", ...)
DIGEST_SPECS_DIR: Path = youros_home() / "specs"

# Synthesis callable — lazily resolved to routers.chat.synthesize_intel_digest.
# Tests replace this with a stub via monkeypatch.setattr(intel_router, "_synthesize", stub).
_synthesize: Callable[[list[dict]], Awaitable[str]] | None = None


def _get_synthesizer() -> Callable[[list[dict]], Awaitable[str]]:
    """Return the synthesis callable, loading it lazily on first use."""
    global _synthesize
    if _synthesize is None:
        from routers.chat import synthesize_intel_digest  # lazy to avoid circular import
        _synthesize = synthesize_intel_digest
    return _synthesize


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


# ---------------------------------------------------------------------------
# GET /api/intel/digest  (SC-016, SC-017, FR-005)
# ---------------------------------------------------------------------------


@router.get("/intel/digest")
async def get_digest(window_days: int = 7) -> dict:
    """Synthesize captures from the last window_days days into a markdown digest.

    SC-016: returns {digest, capture_count, spec_path}.
    SC-017: promotes the digest to DIGEST_SPECS_DIR/intel-digest-{date}.md.
    FR-005: synthesis is delegated to routers.chat.synthesize_intel_digest which
            carries the TODO marker for the future intel-synthesize skill.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    all_captures = _load_all_captures()
    in_window = [
        c for c in all_captures
        if c.get("captured_at", "") >= cutoff.isoformat()
    ]

    synthesizer = _get_synthesizer()
    digest_text = await synthesizer(in_window)

    # SC-017 — promote digest to a dated spec file
    DIGEST_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spec_filename = f"intel-digest-{date_str}.md"
    spec_path = DIGEST_SPECS_DIR / spec_filename
    spec_path.write_text(digest_text)

    return {
        "digest": digest_text,
        "capture_count": len(in_window),
        "spec_path": str(spec_path),
        "window_days": window_days,
    }

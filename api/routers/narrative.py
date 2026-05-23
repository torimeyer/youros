"""Narrative router — exec-update draft generation (→1437 MVP, →1451 v2).

Endpoints:
  GET  /api/narrative/sources           — aggregate sources (Jira, Confluence, specs, calendar)
  POST /api/narrative/draft             — generate + persist a draft
  GET  /api/narrative/drafts            — list all drafts, newest first (summary fields)
  GET  /api/narrative/draft/{id}        — single full draft JSON
  POST /api/narrative/draft/{id}/promote — emit spec + create exec-update tracking task

Storage: ~/.myos/narratives/{draft_id}.json
Specs:   ~/.myos/specs/narrative-{draft_id}.md
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import services.atlassian as atlassian_service
from services.ai_backend import get_ai_client

# ---------------------------------------------------------------------------
# Storage paths (patchable in tests via MYOS_DIR env var)
# ---------------------------------------------------------------------------

def _myos_dir() -> Path:
    return Path(os.environ.get("MYOS_DIR", os.path.expanduser("~/.myos")))

def _narratives_dir() -> Path:
    return _myos_dir() / "narratives"

def _specs_dir() -> Path:
    return _myos_dir() / "specs"

# Module-level alias for backward-compat with tests that patch NARRATIVES_DIR directly
NARRATIVES_DIR: Path = Path(os.path.expanduser("~/.myos/narratives"))

router = APIRouter(tags=["narrative"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _gather_sources(window_days: int = 7) -> list[dict]:
    """Aggregate available sources from connected integrations.

    Each integration that is not connected returns [] rather than raising.
    """
    sources: list[dict] = []

    # --- Atlassian (Jira + Confluence) ---
    if atlassian_service.is_connected():
        try:
            issues = await atlassian_service.list_assigned_issues()
            for issue in issues or []:
                sources.append({
                    "kind": "jira_issue",
                    "id": issue.get("key", ""),
                    "title": issue.get("summary", ""),
                    "meta": {
                        "type": issue.get("type", ""),
                        "status": issue.get("status", ""),
                    },
                })
        except Exception:
            pass

        try:
            pages = await atlassian_service.list_recent_pages()
            for page in pages or []:
                sources.append({
                    "kind": "confluence_page",
                    "id": str(page.get("id", "")),
                    "title": page.get("title", ""),
                    "meta": {
                        "space": page.get("space", ""),
                        "url": page.get("url", ""),
                    },
                })
        except Exception:
            pass

    # --- Calendar ---
    try:
        from routers.calendar import _build_calendar_data
        cal_data = await _build_calendar_data(days=window_days)
        for event in (cal_data.get("events") or [])[:10]:
            sources.append({
                "kind": "calendar_event",
                "id": event.get("id", ""),
                "title": event.get("summary", event.get("title", "")),
                "meta": {"start": event.get("start", "")},
            })
    except Exception:
        pass

    # --- Specs (local ~/.myos/specs) ---
    try:
        from services.ostk import USER_SPECS_DIR
        if USER_SPECS_DIR.is_dir():
            for md in sorted(USER_SPECS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                sources.append({
                    "kind": "spec",
                    "id": md.name,
                    "title": md.stem.replace("-", " ").replace("_", " ").title(),
                    "meta": {"path": str(md)},
                })
    except Exception:
        pass

    return sources


async def _build_markdown_async(audience: str, window_days: int, sources: list[dict]) -> str:
    """Async version of markdown builder — uses LLM if API key available."""
    if not sources:
        return "<no sources in window>"

    header = [
        f"# Progress Update — {audience.title()} ({window_days}-day window)",
        f"_Generated {_now_iso()}_",
        "",
        "## Sources used",
    ]
    for s in sources[:10]:
        header.append(f"- **{s['kind']}**: {s['title']}")
    header += ["", "## Summary", ""]

    summary = ""
    try:
        client = await get_ai_client()
        if client is not None:
            source_list = "\n".join(
                f"- [{s['kind']}] {s['title']}" for s in sources[:10]
            )
            prompt = (
                f"You are drafting a {window_days}-day executive update for audience: {audience}.\n"
                f"Available sources:\n{source_list}\n\n"
                "Write a concise 3-5 sentence update covering key progress, blockers, and next steps. "
                "Plain language, no jargon."
            )
            resp = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = resp.content[0].text.strip()
    except Exception:
        pass

    if not summary:
        summary = "_(Draft generated from available sources. Review and edit before sending.)_"

    return "\n".join(header) + summary + "\n"


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class DraftRequest(BaseModel):
    audience: str = "exec"
    window_days: int = 7
    source_ids: list[str] = []


class DraftResponse(BaseModel):
    draft_id: str
    markdown: str
    source_refs: list[dict]


class DraftSummary(BaseModel):
    draft_id: str
    created_at: str
    audience: str
    source_count: int


class PromoteResponse(BaseModel):
    spec_path: str
    task_id: Optional[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/narrative/sources")
async def get_narrative_sources(window_days: int = 7):
    """Return available sources for narrative drafting from all connected integrations."""
    sources = await _gather_sources(window_days=window_days)
    return {"sources": sources}


@router.post("/narrative/draft", response_model=DraftResponse)
async def create_narrative_draft(body: DraftRequest):
    """Generate and persist an exec-update draft.

    Returns {draft_id, markdown, source_refs}. Never raises for missing integrations.
    """
    all_sources = await _gather_sources(window_days=body.window_days)

    if body.source_ids:
        id_set = set(body.source_ids)
        filtered = [s for s in all_sources if s["id"] in id_set]
    else:
        filtered = all_sources

    markdown = await _build_markdown_async(body.audience, body.window_days, filtered)

    draft_id = str(uuid.uuid4())
    narratives_dir = NARRATIVES_DIR
    narratives_dir.mkdir(parents=True, exist_ok=True)

    draft_data = {
        "draft_id": draft_id,
        "audience": body.audience,
        "window_days": body.window_days,
        "markdown": markdown,
        "source_refs": filtered,
        "created_at": _now_iso(),
    }
    (narratives_dir / f"{draft_id}.json").write_text(json.dumps(draft_data, indent=2))

    return DraftResponse(
        draft_id=draft_id,
        markdown=markdown,
        source_refs=filtered,
    )


@router.get("/narrative/drafts")
async def list_narrative_drafts(limit: int = 20):
    """List all narrative drafts, newest first.

    Returns summary fields only: {draft_id, created_at, audience, source_count}.
    """
    try:
        NARRATIVES_DIR.mkdir(parents=True, exist_ok=True)
        summaries = []
        for f in sorted(NARRATIVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(f.read_text())
                summaries.append({
                    "draft_id": data.get("draft_id", f.stem),
                    "created_at": data.get("created_at", ""),
                    "audience": data.get("audience", ""),
                    "source_count": len(data.get("source_refs", [])),
                })
            except Exception:
                pass
        return {"drafts": summaries}
    except Exception:
        return {"drafts": []}


@router.get("/narrative/draft/{draft_id}")
async def get_narrative_draft(draft_id: str):
    """Fetch a single narrative draft by ID. Returns full JSON. 404 if not found."""
    draft_file = NARRATIVES_DIR / f"{draft_id}.json"
    if not draft_file.exists():
        raise HTTPException(status_code=404, detail="Draft not found")
    return json.loads(draft_file.read_text())


@router.post("/narrative/draft/{draft_id}/promote", response_model=PromoteResponse)
async def promote_narrative_draft(draft_id: str):
    """Promote a draft to a spec file and create an exec-update tracking task.

    Writes spec to ~/.myos/specs/narrative-{draft_id}.md with frontmatter.
    Creates a tracking task labeled exec-update via tasks.create_task.
    Returns {spec_path, task_id}.
    404 if draft not found.
    """
    draft_file = NARRATIVES_DIR / f"{draft_id}.json"
    if not draft_file.exists():
        raise HTTPException(status_code=404, detail="Draft not found")

    draft_data = json.loads(draft_file.read_text())

    # Ensure specs dir exists
    specs_dir = NARRATIVES_DIR.parent / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec_filename = f"narrative-{draft_id}.md"
    spec_path = specs_dir / spec_filename
    now = _now_iso()
    audience = draft_data.get("audience", "exec")
    title = f"Progress Update — {audience.title()}"

    frontmatter = (
        f"---\n"
        f"title: {title}\n"
        f"created_at: {now}\n"
        f"status: spec\n"
        f"source: narrative-{draft_id}\n"
        f"---\n\n"
    )
    spec_path.write_text(frontmatter + draft_data.get("markdown", ""))

    # Create tracking task labeled exec-update
    task_id: Optional[str] = None
    try:
        from models.schemas import TaskCreate
        from routers.tasks import create_task

        task_title = f"[Progress Update] {title}"
        if len(task_title) > 60:
            task_title = task_title[:57] + "..."

        result = await create_task(
            TaskCreate(
                title=task_title,
                priority="P1",
                description=(
                    f"Exec update draft promoted to spec.\n\n"
                    f"Spec: {spec_path}\n"
                    f"Audience: {audience}\n"
                    f"Draft ID: {draft_id}"
                ),
                source="narrative",
                source_ref=draft_id,
            )
        )
        task_id = result.get("task_id") if isinstance(result, dict) else None
    except Exception:
        pass

    return PromoteResponse(spec_path=str(spec_path), task_id=task_id)

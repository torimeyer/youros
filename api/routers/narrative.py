"""Narrative router — exec-update draft generation (→1437, v3.19.0).

Endpoints:
  GET  /api/narrative/sources   — aggregate sources from Jira, Confluence, specs, calendar
  POST /api/narrative/draft     — generate a draft exec update and persist it

Storage: ~/.myos/narratives/{draft_id}.json
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import services.atlassian as atlassian_service

# ---------------------------------------------------------------------------
# Storage path (patchable in tests)
# ---------------------------------------------------------------------------

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


def _build_markdown(audience: str, window_days: int, sources: list[dict]) -> str:
    """Generate a simple narrative markdown from the gathered sources.

    # TODO: replace with vision skill once published to marketplace.
    """
    if not sources:
        return "<no sources in window>"

    lines = [
        f"# Exec Update — {audience.title()} ({window_days}-day window)",
        f"_Generated {_now_iso()}_",
        "",
        "## Sources used",
    ]
    for s in sources[:10]:
        lines.append(f"- **{s['kind']}**: {s['title']}")

    lines += [
        "",
        "## Summary",
        "",
        "_(Draft generated from available sources. Review and edit before sending.)_",
        "",
    ]

    # Try LLM generation if an API key is available
    try:
        import anthropic
        from services.chat_providers import _resolve_api_key
        import asyncio

        async def _llm_summary() -> str:
            api_key = await _resolve_api_key("anthropic_api_key")
            if not api_key:
                return ""
            client = anthropic.AsyncAnthropic(api_key=api_key)
            source_list = "\n".join(
                f"- [{s['kind']}] {s['title']}" for s in sources[:10]
            )
            prompt = (
                f"You are drafting a {window_days}-day executive update for audience: {audience}.\n"
                f"Available sources:\n{source_list}\n\n"
                "Write a concise 3-5 sentence update covering key progress, blockers, and next steps. "
                "Plain language, no jargon."
            )
            response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()

        # Run the async call synchronously from sync context when possible
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context (normal FastAPI path).
                # Schedule as a task and return placeholder — the caller is async.
                # Handled by the async endpoint below.
                return "\n".join(lines)
        except RuntimeError:
            pass
    except Exception:
        pass

    return "\n".join(lines)


async def _build_markdown_async(audience: str, window_days: int, sources: list[dict]) -> str:
    """Async version of markdown builder — uses LLM if API key available."""
    if not sources:
        return "<no sources in window>"

    header = [
        f"# Exec Update — {audience.title()} ({window_days}-day window)",
        f"_Generated {_now_iso()}_",
        "",
        "## Sources used",
    ]
    for s in sources[:10]:
        header.append(f"- **{s['kind']}**: {s['title']}")
    header += ["", "## Summary", ""]

    summary = ""
    try:
        import anthropic
        from services.chat_providers import _resolve_api_key

        api_key = await _resolve_api_key("anthropic_api_key")
        if api_key:
            ac = anthropic.AsyncAnthropic(api_key=api_key)
            source_list = "\n".join(
                f"- [{s['kind']}] {s['title']}" for s in sources[:10]
            )
            prompt = (
                f"You are drafting a {window_days}-day executive update for audience: {audience}.\n"
                f"Available sources:\n{source_list}\n\n"
                "Write a concise 3-5 sentence update covering key progress, blockers, and next steps. "
                "Plain language, no jargon."
            )
            resp = await ac.messages.create(
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
    # Gather sources
    all_sources = await _gather_sources(window_days=body.window_days)

    # Filter to requested source_ids if specified
    if body.source_ids:
        id_set = set(body.source_ids)
        filtered = [s for s in all_sources if s["id"] in id_set]
    else:
        filtered = all_sources

    # Generate markdown
    markdown = await _build_markdown_async(body.audience, body.window_days, filtered)

    # Persist
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
async def list_narrative_drafts(limit: int = 10):
    """List recent narrative drafts, newest first."""
    try:
        NARRATIVES_DIR.mkdir(parents=True, exist_ok=True)
        drafts = []
        for f in sorted(NARRATIVES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                drafts.append(json.loads(f.read_text()))
            except Exception:
                pass
        return {"drafts": drafts}
    except Exception:
        return {"drafts": []}


@router.get("/narrative/draft/{draft_id}")
async def get_narrative_draft(draft_id: str):
    """Fetch a single narrative draft by ID."""
    from fastapi import HTTPException
    draft_file = NARRATIVES_DIR / f"{draft_id}.json"
    if not draft_file.exists():
        raise HTTPException(status_code=404, detail="Draft not found")
    return json.loads(draft_file.read_text())

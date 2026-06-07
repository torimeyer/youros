"""Narrative router — exec-update draft generation (→1437 MVP, →1451 v2).

Endpoints:
  GET  /api/narrative/sources           — aggregate sources (Jira, Confluence, specs, calendar)
  POST /api/narrative/draft             — generate + persist a draft
  GET  /api/narrative/drafts            — list all drafts, newest first (summary fields)
  GET  /api/narrative/draft/{id}        — single full draft JSON
  POST /api/narrative/draft/{id}/promote — emit spec + create exec-update tracking task

Storage: ~/.youros/narratives/{draft_id}.json
Specs:   ~/.youros/specs/narrative-{draft_id}.md
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncio
import re
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import services.atlassian as atlassian_service
from services.ai_backend import get_ai_client

# Personal calendar event patterns to exclude from work updates
_PERSONAL_EVENT_RE = re.compile(
    r"(gymnastics|soccer|baseball|ballet|trash\s*night|recycle|birthday|anniversary"
    r"|dentist|doctor|haircut|pickup|drop.?off|school|daycare|swim\s*lesson"
    r"|piano|karate|yoga|church|mass|synagogue|mosque|pediatric|vet\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Storage paths (patchable in tests via MYOS_DIR env var)
# ---------------------------------------------------------------------------

def _myos_dir() -> Path:
    return Path(os.environ.get("MYOS_DIR", os.path.expanduser("~/.youros")))

def _narratives_dir() -> Path:
    return _myos_dir() / "narratives"

def _specs_dir() -> Path:
    return _myos_dir() / "specs"

# Module-level alias for backward-compat with tests that patch NARRATIVES_DIR directly
NARRATIVES_DIR: Path = Path(os.path.expanduser("~/.youros/narratives"))

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

    # --- Calendar (skip personal events) ---
    try:
        from routers.calendar import _build_calendar_data
        cal_data = await _build_calendar_data(days=window_days)
        for event in (cal_data.get("events") or [])[:20]:
            title = event.get("summary", event.get("title", ""))
            if _PERSONAL_EVENT_RE.search(title):
                continue
            sources.append({
                "kind": "calendar_event",
                "id": event.get("id", ""),
                "title": title,
                "meta": {"start": event.get("start", "")},
            })
    except Exception:
        pass

    # --- Specs (local ~/.youros/specs) with body excerpts ---
    try:
        from services.ostk import USER_SPECS_DIR
        if USER_SPECS_DIR.is_dir():
            for md in sorted(USER_SPECS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                body_excerpt = ""
                try:
                    raw = md.read_text(encoding="utf-8", errors="replace")
                    body_excerpt = raw[:600].strip()
                except Exception:
                    pass
                sources.append({
                    "kind": "spec",
                    "id": md.name,
                    "title": md.stem.replace("-", " ").replace("_", " ").title(),
                    "meta": {"path": str(md), "body_excerpt": body_excerpt},
                })
    except Exception:
        pass

    # --- Recently closed needles ---
    try:
        from services.ostk import ostk
        closed_tasks = await ostk.list_tasks(status="closed")
        # Filter to window: tasks closed within window_days
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        for task in (closed_tasks or [])[:20]:
            closed_at_str = task.get("closed_at") or task.get("updated_at") or ""
            in_window = True
            if closed_at_str:
                try:
                    closed_dt = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
                    in_window = closed_dt >= cutoff
                except Exception:
                    pass
            if in_window:
                sources.append({
                    "kind": "closed_needle",
                    "id": str(task.get("id", "")),
                    "title": task.get("title", ""),
                    "meta": {
                        "closed_at": closed_at_str,
                        "priority": task.get("priority", ""),
                    },
                })
    except Exception:
        pass

    # --- Recent git commits ---
    try:
        from config import PROJECT_ROOT
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["git", "log", f"--since={window_days} days ago", "--oneline", "--no-merges", "-20"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=5,
            ),
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(" ", 1)
                commit_hash = parts[0]
                commit_msg = parts[1] if len(parts) > 1 else line
                sources.append({
                    "kind": "git_commit",
                    "id": commit_hash,
                    "title": commit_msg,
                    "meta": {"hash": commit_hash},
                })
    except Exception:
        pass

    # --- Git Commits (recent ships) ---
    try:
        import subprocess
        from config import PROJECT_ROOT
        # git log --since="N days ago" --oneline
        cmd = ["git", "log", f"--since={window_days} days ago", "--oneline", "-n", "50"]
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue
                sha, title = parts
                sources.append({
                    "kind": "git_commit",
                    "id": sha,
                    "title": title,
                    "meta": {"sha": sha},
                })
    except Exception:
        pass

    return sources


def _build_context_block(sources: list[dict]) -> str:
    """Build a rich context string from enriched sources for the model prompt."""
    lines: list[str] = []

    # Closed needles (shipped work)
    needles = [s for s in sources if s["kind"] == "closed_needle"]
    if needles:
        lines.append("RECENTLY SHIPPED (closed needles):")
        for s in needles[:10]:
            lines.append(f"  - {s['title']}")
        lines.append("")

    # Git commits
    commits = [s for s in sources if s["kind"] == "git_commit"]
    if commits:
        lines.append("RECENT COMMITS:")
        for s in commits[:10]:
            lines.append(f"  - {s['title']}")
        lines.append("")

    # Spec excerpts
    specs = [s for s in sources if s["kind"] == "spec"]
    if specs:
        lines.append("ACTIVE SPECS (context):")
        for s in specs[:5]:
            excerpt = (s.get("meta") or {}).get("body_excerpt", "")
            if excerpt:
                lines.append(f"  [{s['title']}]")
                lines.append(f"  {excerpt[:300]}")
            else:
                lines.append(f"  [{s['title']}]")
        lines.append("")

    # Jira issues
    jira = [s for s in sources if s["kind"] == "jira_issue"]
    if jira:
        lines.append("JIRA (in progress):")
        for s in jira[:5]:
            status = (s.get("meta") or {}).get("status", "")
            lines.append(f"  - {s['title']}" + (f" ({status})" if status else ""))
        lines.append("")

    # Calendar (work events only — personal filtered earlier)
    cal = [s for s in sources if s["kind"] == "calendar_event"]
    if cal:
        lines.append("WORK CALENDAR:")
        for s in cal[:5]:
            lines.append(f"  - {s['title']}")
        lines.append("")

    return "\n".join(lines).strip()


_SYSTEM_PROMPT = """\
You write executive progress updates. Your output is sent directly to stakeholders — \
no preamble, no explanation of how you built the update, no trailing questions.

Rules (non-negotiable):
- Output ONLY the finished update text. Start with the first sentence of content.
- Never use [bracket] placeholders. If a detail is unknown, omit it or write around it.
- No meta-commentary ("I notice...", "Based on...", "Here's a draft...", "Want me to read...").
- No trailing questions ("Would you like me to...", "Should I also...").
- 3-5 sentences covering: what shipped, one real blocker if any, and the next focus.
- Plain language. No finance or engineering jargon.
"""


async def _build_markdown_async(audience: str, window_days: int, sources: list[dict]) -> str:
    """Async version of markdown builder — uses LLM if API key available."""
    if not sources:
        return "<no sources in window>"

    summary = ""
    try:
        client = await get_ai_client()
        if client is not None:
            context_block = _build_context_block(sources)
            user_prompt = (
                f"Write a {window_days}-day progress update for audience: {audience}.\n\n"
                f"Here is what happened:\n\n{context_block}\n\n"
                "Output only the finished update. "
                "Do not use [bracket] placeholders. "
                "Do not ask questions. "
                "Do not explain what you are doing."
            )
            resp = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            summary = resp.content[0].text.strip()
    except Exception:
        pass

    header = "\n".join([
        f"# Progress Update — {audience.title()} ({window_days}-day window)",
        f"_Generated {_now_iso()}_",
        "",
    ])

    if not summary:
        summary = "_(No AI backend available. Add sources and regenerate when configured.)_"

    return header + summary + "\n"


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

    Writes spec to ~/.youros/specs/narrative-{draft_id}.md with frontmatter.
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

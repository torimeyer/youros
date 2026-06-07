"""My AI Setup page endpoints.

Three endpoints:
- GET /api/my-setup/summary       — structured JSON snapshot of the user's setup
- GET /api/my-setup/export?format=markdown — shareable plain-text summary
- GET /api/my-setup/export?format=json    — config pack (no secrets)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from config import AGENTS_DIR
from services.ostk import ostk, OstkError, read_audit_entries
from services.settings_store import settings_store

router = APIRouter(tags=["my-setup"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_AGENTFILES_DIR = Path.home() / ".youros" / "agentfiles"

_TEMPLATE_NAMES: dict[str, str] = {
    "builtin-builder":              "Writing assistant",
    "builtin-diagnose":             "Bug diagnosis",
    "builtin-research":             "Research",
    "builtin-review":               "Code reviewer",
    "builtin-brainstorm":           "Brainstorm",
    "builtin-eng-debug-helper":     "Debug helper",
    "builtin-eng-bug-finder":       "Bug finder",
    "builtin-eng-refactor-plan":    "Refactor plan",
    "builtin-eng-write-tests":      "Write tests",
    "builtin-test":                 "Test runner",
    "builtin-pm-prd":               "Product spec",
    "builtin-pm-roadmap":           "Roadmap",
    "builtin-pm-competitive-scan":  "Competitive scan",
    "builtin-pm-stakeholder-update":"Stakeholder update",
    "builtin-pm-launch-checklist":  "Launch checklist",
    "builtin-pm-customer-interviews":"Customer interviews",
    "builtin-writer-blog-post":     "Blog post",
    "builtin-writer-social-post":   "Social post",
    "builtin-writer-headlines":     "Headlines",
    "builtin-writer-proofreader":   "Proofreader",
    "builtin-writer-name-generator":"Name generator",
    "builtin-sales-prospect-research":"Prospect research",
    "builtin-sales-cold-outreach":  "Cold outreach",
    "builtin-sales-call-prep":      "Call prep",
    "builtin-sales-follow-up":      "Follow-up",
    "builtin-sales-objection-handling":"Objection handling",
    "builtin-home-meal-planner":    "Meal planner",
    "builtin-home-grocery-list":    "Grocery list",
    "builtin-home-trip-planner":    "Trip planner",
    "builtin-home-gift-finder":     "Gift finder",
    "builtin-home-homework-helper": "Homework helper",
    "builtin-student-study-guide":  "Study guide",
    "builtin-student-essay-outline":"Essay outline",
    "builtin-student-flash-cards":  "Flash cards",
    "builtin-student-citation-helper":"Citation helper",
    "builtin-student-concept-explainer":"Concept explainer",
    "builtin-explain-plain":        "Plain-language explainer",
}

_NAME_PATTERNS: list[tuple[str, str]] = [
    ("diagnose",    "builtin-diagnose"),
    ("debug",       "builtin-eng-debug-helper"),
    ("bug-find",    "builtin-eng-bug-finder"),
    ("bug_find",    "builtin-eng-bug-finder"),
    ("refactor",    "builtin-eng-refactor-plan"),
    ("write-test",  "builtin-eng-write-tests"),
    ("write_test",  "builtin-eng-write-tests"),
    ("test",        "builtin-test"),
    ("brainstorm",  "builtin-brainstorm"),
    ("ideate",      "builtin-brainstorm"),
    ("research",    "builtin-research"),
    ("review",      "builtin-review"),
    ("prd",         "builtin-pm-prd"),
    ("roadmap",     "builtin-pm-roadmap"),
    ("competitive", "builtin-pm-competitive-scan"),
    ("stakeholder", "builtin-pm-stakeholder-update"),
    ("launch",      "builtin-pm-launch-checklist"),
    ("interview",   "builtin-pm-customer-interviews"),
    ("blog",        "builtin-writer-blog-post"),
    ("social-post", "builtin-writer-social-post"),
    ("social_post", "builtin-writer-social-post"),
    ("headline",    "builtin-writer-headlines"),
    ("proofread",   "builtin-writer-proofreader"),
    ("name-gen",    "builtin-writer-name-generator"),
    ("prospect",    "builtin-sales-prospect-research"),
    ("outreach",    "builtin-sales-cold-outreach"),
    ("call-prep",   "builtin-sales-call-prep"),
    ("call_prep",   "builtin-sales-call-prep"),
    ("follow-up",   "builtin-sales-follow-up"),
    ("follow_up",   "builtin-sales-follow-up"),
    ("objection",   "builtin-sales-objection-handling"),
    ("meal",        "builtin-home-meal-planner"),
    ("grocery",     "builtin-home-grocery-list"),
    ("trip",        "builtin-home-trip-planner"),
    ("gift",        "builtin-home-gift-finder"),
    ("homework",    "builtin-home-homework-helper"),
    ("study",       "builtin-student-study-guide"),
    ("essay",       "builtin-student-essay-outline"),
    ("flash-card",  "builtin-student-flash-cards"),
    ("flash_card",  "builtin-student-flash-cards"),
    ("citation",    "builtin-student-citation-helper"),
    ("concept",     "builtin-student-concept-explainer"),
    ("explain",     "builtin-explain-plain"),
    ("builder",     "builtin-builder"),
    ("build",       "builtin-builder"),
]

_SECRET_KEYS = re.compile(
    r"(key|secret|token|password|credential|api_key|apikey|client_secret)",
    re.IGNORECASE,
)


def _provider_label(default_model: str) -> str:
    m = (default_model or "").lower()
    if "gemini" in m or "google" in m:
        return "Google (Gemini)"
    if "gpt" in m or "openai" in m:
        return "OpenAI (GPT)"
    if "bedrock" in m:
        return "AWS Bedrock"
    if "vertex" in m:
        return "Google Vertex"
    return "Claude"


def _model_label(default_model: str) -> str:
    m = (default_model or "").lower()
    if "opus" in m:
        return "Opus"
    if "haiku" in m:
        return "Haiku"
    if "sonnet" in m or "4-6" in m or "4.6" in m:
        return "Sonnet"
    if "4-7" in m or "4.7" in m:
        return "Opus 4.7"
    return "Sonnet"


def _count_agentfiles() -> int:
    count = 0
    if AGENTS_DIR.is_dir():
        count += sum(1 for _ in AGENTS_DIR.glob("*.agent"))
    if USER_AGENTFILES_DIR.is_dir():
        count += sum(1 for _ in USER_AGENTFILES_DIR.glob("*.agent"))
    return count


def _top_skills_this_week(audit_entries: list[dict]) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    counts: dict[str, int] = {}
    for entry in audit_entries:
        if entry.get("event") != "agent.spawned":
            continue
        ts = entry.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
        if dt < cutoff:
            continue
        name = (entry.get("name") or "").lower()
        for keyword, tid in _NAME_PATTERNS:
            if keyword in name:
                counts[tid] = counts.get(tid, 0) + 1
                break
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
    return [_TEMPLATE_NAMES.get(tid, tid) for tid, _ in top]


def _agents_created_count(audit_entries: list[dict]) -> int:
    return sum(1 for e in audit_entries if e.get("event") == "agent.spawned")


def _strip_secrets(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: "[redacted]" if _SECRET_KEYS.search(k) else _strip_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_secrets(item) for item in obj]
    return obj


def _mcp_names(mcp_servers: list[dict]) -> list[str]:
    names = []
    for s in mcp_servers:
        name = s.get("name") or s.get("url") or ""
        if name:
            names.append(name)
    return names


def _today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/my-setup/summary")
async def my_setup_summary():
    """Structured snapshot of the user's AI setup."""
    settings = settings_store.load()
    mcp_servers: list[dict] = settings.get("mcp_servers") or []
    default_model: str = settings.get("default_model") or "@claude"

    try:
        audit_entries = read_audit_entries()
    except Exception:
        audit_entries = []

    top_skills = _top_skills_this_week(audit_entries)
    agents_created = _agents_created_count(audit_entries)
    agentfiles_count = _count_agentfiles()

    provider = _provider_label(default_model)
    model = _model_label(default_model)

    return {
        "provider": provider,
        "model": model,
        "connected_tools": _mcp_names(mcp_servers),
        "connected_tools_count": len(mcp_servers),
        "top_skills": top_skills,
        "agents_created": agents_created,
        "agentfiles_count": agentfiles_count,
    }


@router.get("/my-setup/export")
async def my_setup_export(format: str = Query(default="markdown")):
    """Export the setup summary as markdown or JSON config pack."""
    if format not in ("markdown", "json"):
        raise HTTPException(
            status_code=400,
            detail="format must be 'markdown' or 'json'",
        )

    settings = settings_store.load()
    mcp_servers: list[dict] = settings.get("mcp_servers") or []
    default_model: str = settings.get("default_model") or "@claude"
    instance_name: str = settings.get("instance_name") or "myOS"

    try:
        audit_entries = read_audit_entries()
    except Exception:
        audit_entries = []

    top_skills = _top_skills_this_week(audit_entries)
    agents_created = _agents_created_count(audit_entries)
    agentfiles_count = _count_agentfiles()
    provider = _provider_label(default_model)
    model = _model_label(default_model)
    tool_names = _mcp_names(mcp_servers)

    stamp = _today_stamp()

    if format == "markdown":
        lines: list[str] = []
        lines.append(f"# My AI Setup")
        lines.append("")
        lines.append(f"**Your AI assistant:** {provider} ({model})")
        if tool_names:
            lines.append(f"**Connected tools:** {len(tool_names)} ({', '.join(tool_names)})")
        else:
            lines.append("**Connected tools:** None")
        if top_skills:
            lines.append(f"**Top skills this week:** {', '.join(top_skills)}")
        else:
            lines.append("**Top skills this week:** None yet")
        lines.append(f"**Agents created:** {agents_created}")
        lines.append(f"**Skills installed:** {agentfiles_count}")
        lines.append("")
        lines.append(f"_Exported {stamp} from {instance_name}_")
        lines.append("")
        body = "\n".join(lines)
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="my-setup-{stamp}.md"'},
        )

    # JSON config pack: strip all secrets
    config = {
        "exported_at": stamp,
        "instance_name": instance_name,
        "provider": provider,
        "model": model,
        "mcp_servers": _strip_secrets(mcp_servers),
        "agentfiles_count": agentfiles_count,
        "default_model": default_model,
    }
    body = json.dumps(config, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="my-setup-{stamp}.json"'},
    )

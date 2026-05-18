import logging
import subprocess
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["onboarding"])

logger = logging.getLogger(__name__)


# --- Request / Response models ---

class StarterPackItem(BaseModel):
    kind: Literal["skill", "agent"]
    id: str
    name: str
    description: str
    default_selected: bool


class IntentRequest(BaseModel):
    intent: Literal["writing", "personal", "coding", "research", "work_role", "sales", "general", "marketing", "founder", "support", "designer"]
    role: Optional[str] = None


class IntentResponse(BaseModel):
    starter_pack: list[StarterPackItem]


# --- Intent → starter pack mapping ---

_INTENT_PACKS: dict[str, list[dict]] = {
    "writing": [
        {"id": "builtin-writer-blog-post", "default_selected": True},
        {"id": "builtin-writer-social-post", "default_selected": True},
        {"id": "builtin-writer-proofreader", "default_selected": True},
        {"id": "builtin-writer-headlines", "default_selected": False},
        {"id": "builtin-research", "default_selected": False},
    ],
    "personal": [
        {"id": "builtin-home-meal-planner", "default_selected": True},
        {"id": "builtin-home-trip-planner", "default_selected": True},
        {"id": "builtin-home-gift-finder", "default_selected": True},
        {"id": "builtin-home-homework-helper", "default_selected": False},
    ],
    "coding": [
        {"id": "builtin-builder", "default_selected": True},
        {"id": "builtin-diagnose", "default_selected": True},
        {"id": "builtin-eng-write-tests", "default_selected": True},
        {"id": "builtin-eng-debug-helper", "default_selected": False},
        {"id": "builtin-eng-refactor-plan", "default_selected": False},
    ],
    "research": [
        {"id": "builtin-research", "default_selected": True},
        {"id": "builtin-explain-plain", "default_selected": True},
        {"id": "builtin-student-study-guide", "default_selected": True},
        {"id": "builtin-student-essay-outline", "default_selected": False},
    ],
    "work_role": [
        {"id": "builtin-pm-prd", "default_selected": True},
        {"id": "builtin-pm-competitive-scan", "default_selected": True},
        {"id": "builtin-pm-roadmap", "default_selected": True},
        {"id": "builtin-pm-stakeholder-update", "default_selected": False},
    ],
    "sales": [
        {"id": "builtin-sales-prospect-research", "default_selected": True},
        {"id": "builtin-sales-cold-outreach", "default_selected": True},
        {"id": "builtin-sales-call-prep", "default_selected": True},
        {"id": "builtin-sales-follow-up", "default_selected": False},
        {"id": "builtin-sales-objection-handling", "default_selected": False},
    ],
    "general": [
        {"id": "builtin-builder", "default_selected": True},
        {"id": "builtin-research", "default_selected": True},
        {"id": "builtin-brainstorm", "default_selected": True},
        {"id": "builtin-explain-plain", "default_selected": True},
        {"id": "builtin-diagnose", "default_selected": False},
    ],
    "marketing": [
        {"id": "builtin-marketing-campaign-brief", "default_selected": True},
        {"id": "builtin-research", "default_selected": True},
        {"id": "builtin-brainstorm", "default_selected": True},
        {"id": "builtin-writer-social-post", "default_selected": False},
    ],
    "founder": [
        {"id": "builtin-founder-investor-update", "default_selected": True},
        {"id": "builtin-pm-prd", "default_selected": True},
        {"id": "builtin-research", "default_selected": True},
        {"id": "builtin-builder", "default_selected": False},
    ],
    "support": [
        {"id": "builtin-support-customer-reply", "default_selected": True},
        {"id": "builtin-explain-plain", "default_selected": True},
        {"id": "builtin-research", "default_selected": False},
        {"id": "builtin-pm-stakeholder-update", "default_selected": False},
    ],
    "designer": [
        {"id": "builtin-designer-design-critique", "default_selected": True},
        {"id": "builtin-brainstorm", "default_selected": True},
        {"id": "builtin-research", "default_selected": False},
        {"id": "builtin-explain-plain", "default_selected": False},
    ],
}


# --- First-runs hints (static, keyed by intent) ---

_FIRST_RUNS_HINTS: dict[str, list[dict]] = {
    "writing": [
        {"label": "Draft a blog post", "seed": "Help me draft a blog post about ", "kind": "chat"},
        {"label": "Polish a draft", "seed": "Here's a draft I'd like help polishing: ", "kind": "chat"},
        {"label": "Write three headlines", "seed": "Write me three headline options for: ", "kind": "chat"},
    ],
    "personal": [
        {"label": "Plan today", "seed": "Help me plan my day. Here's what I need to get done: ", "kind": "chat"},
        {"label": "Summarize an email", "seed": "Here's an email I need to summarize: ", "kind": "chat"},
        {"label": "Set a reminder", "seed": "Remind me to ", "kind": "task"},
    ],
    "coding": [
        {"label": "Refactor a function", "seed": "Help me refactor this function: ", "kind": "chat"},
        {"label": "Diagnose a failing test", "seed": "Help me figure out why this test is failing: ", "kind": "chat"},
        {"label": "Write a README section", "seed": "Write a README section for ", "kind": "chat"},
    ],
    "research": [
        {"label": "Summarize a paper or article", "seed": "Summarize this for me: ", "kind": "chat"},
        {"label": "Compare two things", "seed": "Compare these two for me: ", "kind": "chat"},
        {"label": "Build a reading list", "seed": "Build a reading list on the topic of ", "kind": "chat"},
    ],
    "work_role": [
        {"label": "Draft a status update", "seed": "Draft a status update for my team about ", "kind": "chat"},
        {"label": "Prep for a meeting", "seed": "Help me prepare for a meeting about ", "kind": "chat"},
        {"label": "Write a brief", "seed": "Write a brief for ", "kind": "chat"},
    ],
    "sales": [
        {"label": "Research a prospect", "seed": "Research this company for me: ", "kind": "chat"},
        {"label": "Draft an outreach email", "seed": "Help me write an outreach email to ", "kind": "chat"},
        {"label": "Prep for a call", "seed": "Help me prepare for a sales call with ", "kind": "chat"},
    ],
    "general": [
        {"label": "Build something", "seed": "Build this for me: ", "kind": "chat"},
        {"label": "Research a topic", "seed": "Research this for me: ", "kind": "chat"},
        {"label": "Brainstorm ideas", "seed": "Give me ideas for ", "kind": "chat"},
    ],
}


class FirstRunsItem(BaseModel):
    label: str
    seed: str
    kind: Literal["chat", "task"]


class FirstRunsResponse(BaseModel):
    hints: list[FirstRunsItem]


@router.get("/onboarding/first-runs", response_model=FirstRunsResponse)
async def first_runs(intent: str = "writing"):
    """Return 3 concrete starter actions tailored to the user's intent."""
    raw = _FIRST_RUNS_HINTS.get(intent, _FIRST_RUNS_HINTS["writing"])
    return FirstRunsResponse(hints=[FirstRunsItem(**h) for h in raw])


class EnableHooksRequest(BaseModel):
    scope: Optional[Literal['everywhere', 'repo', 'myos-only']] = None
    path: Optional[str] = None


@router.post("/onboarding/enable-myos-hooks")
async def enable_myos_hooks(body: EnableHooksRequest = EnableHooksRequest()):
    """Run myos-track.sh to wire myOS hooks into Claude Code.

    scope='everywhere': install machine-wide hook at ~/.claude/settings.json
    scope='repo': run myos-track.sh against the provided path
    scope='myos-only': no-op, return success without changes
    No scope: keep existing behavior (run myos-track.sh in repo root).
    """
    if body.scope == 'myos-only':
        return {"enabled": True, "method": "myos-only"}

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "myos-track.sh"

    cmd: list[str] = [str(script)]
    cwd = str(repo_root)

    if body.scope == 'everywhere':
        cmd.append('--global')
    elif body.scope == 'repo':
        if body.path:
            cmd.append(body.path)
        else:
            return {"enabled": False, "error": "path is required when scope is 'repo'"}

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return {"enabled": False, "error": str(exc)}
    if result.returncode != 0:
        return {"enabled": False, "error": result.stderr.strip() or f"exit {result.returncode}"}
    return {"enabled": True, "method": body.scope or "track"}


@router.post("/onboarding/intent", response_model=IntentResponse)
async def intent(body: IntentRequest):
    """Return a tailored starter pack of agents based on the user's intended use case."""
    from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES

    by_id = {t["id"]: t for t in BUILTIN_AGENT_TEMPLATES}
    pack_spec = _INTENT_PACKS.get(body.intent, [])

    items = []
    for spec in pack_spec:
        tpl = by_id.get(spec["id"])
        if tpl is None:
            continue
        items.append(StarterPackItem(
            kind="agent",
            id=tpl["id"],
            name=tpl["name"],
            description=tpl["description"],
            default_selected=spec["default_selected"],
        ))

    return IntentResponse(starter_pack=items)

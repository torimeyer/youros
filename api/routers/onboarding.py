import json
import logging
import subprocess
from pathlib import Path
from typing import Literal, Optional

import anthropic

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.ai_backend import get_ai_client
from services.ostk import ostk, OstkError
from services.task_labeling import extract_task_id, schedule_auto_labels

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


class TaskItem(BaseModel):
    title: str
    priority: str


class GoalItem(BaseModel):
    title: str
    description: str


class DreamResponse(BaseModel):
    goal: GoalItem
    tasks: list[TaskItem]


class DreamRequest(BaseModel):
    dreading: str
    done_looks_like: Optional[str] = None


# --- LLM prompt ---

_SYSTEM_PROMPT = (
    "You are a personal planning assistant. The user will describe something "
    "they have been putting off or dreading. Your job is to turn it into a "
    "clear goal and a short list of concrete, actionable tasks.\n\n"
    "Rules:\n"
    "- Return ONLY valid JSON, no markdown, no code fences, no extra text.\n"
    "- The goal title should be short (under 10 words).\n"
    "- The goal description should be one sentence in plain language.\n"
    "- Return 3 to 5 tasks.\n"
    "- Each task title should be a concrete next step someone can act on today.\n"
    "- Use plain language. No jargon, no technical terms, no abbreviations.\n"
    "- Assign priority P1 to the most important tasks, P2 to nice-to-haves.\n"
    "- Never use em-dashes.\n\n"
    "Respond with this exact JSON structure:\n"
    '{"goal": {"title": "...", "description": "..."}, '
    '"tasks": [{"title": "...", "priority": "P1"}, ...]}'
)


def _build_user_message(dreading: str, done_looks_like: Optional[str]) -> str:
    parts = [f"I have been putting off this: {dreading}"]
    if done_looks_like:
        parts.append(f"Done looks like: {done_looks_like}")
    return "\n".join(parts)


# --- Fallback when the LLM is unavailable ---

def _fallback_response(dreading: str, done_looks_like: Optional[str]) -> DreamResponse:
    """Generate a sensible generic plan when the LLM call fails."""
    short = dreading[:60].rstrip(".").strip()
    description = done_looks_like or f"Get '{short}' off your plate"
    return DreamResponse(
        goal=GoalItem(title=f"Handle: {short}", description=description),
        tasks=[
            TaskItem(title="Write down everything you know about this so far", priority="P1"),
            TaskItem(title="Figure out the very first small step you can take", priority="P1"),
            TaskItem(title="Set aside 30 minutes to work on just that first step", priority="P1"),
            TaskItem(title="Ask for help if you get stuck", priority="P2"),
        ],
    )


# --- Core logic ---

async def _call_llm(dreading: str, done_looks_like: Optional[str]) -> DreamResponse:
    """Ask the LLM to turn a dreaded task into a structured plan."""
    client = await get_ai_client()
    if client is None:
        logger.warning("No AI backend available, using fallback plan")
        return _fallback_response(dreading, done_looks_like)
    user_msg = _build_user_message(dreading, done_looks_like)

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.APIError as exc:
        logger.error("LLM API error: %s", exc)
        return _fallback_response(dreading, done_looks_like)

    # Extract text from the response
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    # Parse JSON from the LLM response
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.error("LLM returned invalid JSON: %s", text[:200])
        return _fallback_response(dreading, done_looks_like)

    # Validate structure
    try:
        goal = GoalItem(
            title=data["goal"]["title"],
            description=data["goal"]["description"],
        )
        tasks = [
            TaskItem(title=t["title"], priority=t.get("priority", "P1"))
            for t in data["tasks"]
        ]
    except (KeyError, TypeError) as exc:
        logger.error("LLM response missing expected fields: %s", exc)
        return _fallback_response(dreading, done_looks_like)

    if not tasks:
        return _fallback_response(dreading, done_looks_like)

    return DreamResponse(goal=goal, tasks=tasks)


async def _persist_tasks(plan: DreamResponse) -> None:
    """Save the generated tasks using the ostk task system."""
    for task in plan.tasks:
        try:
            result = await ostk.add_task(task.title, task.priority)
        except OstkError as exc:
            logger.warning("Failed to persist task '%s': %s", task.title, exc)
            continue
        # Fire-and-forget auto label suggestion. Never blocks task creation.
        new_id = extract_task_id(result)
        schedule_auto_labels(new_id, task.title, "")


# --- Endpoint ---

@router.post("/onboarding/dream", response_model=DreamResponse)
async def dream(body: DreamRequest):
    # Deprecated: use /onboarding/intent instead. Kept so existing flows don't break.
    if not body.dreading.strip():
        raise HTTPException(status_code=422, detail="Please describe what you have been putting off.")

    plan = await _call_llm(body.dreading, body.done_looks_like)
    return plan


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

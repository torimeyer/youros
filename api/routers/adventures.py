"""Onboarding adventures: choose-your-own-adventure templates that turn a
user's goal into a concrete plan with tasks.

Each adventure has its own LLM system prompt tuned for the kind of plan that
makes sense (a website needs different tasks than a learning goal). The
generated tasks are persisted via ostk so they show up on the dashboard
right after onboarding.
"""

import json
import logging
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.chat_providers import _resolve_api_key
from services.ostk import ostk, OstkError
from services.task_labeling import extract_task_id, schedule_auto_labels

router = APIRouter(tags=["adventures"])

logger = logging.getLogger(__name__)


# --- Adventure templates ---

class AdventureTemplate(BaseModel):
    id: str
    title: str
    tagline: str
    icon: str
    placeholder: str
    system_prompt: str


WEBSITE_PROMPT = (
    "You are a calm planning assistant helping someone build a website who "
    "may not be technical. The user will tell you what kind of site they "
    "want. Turn it into a clear goal and 4 to 8 concrete tasks they can "
    "actually act on.\n\n"
    "Pick a sensible default stack the user does NOT have to choose:\n"
    "- For static or content sites, use Vercel + a simple framework.\n"
    "- For sites that need to store data (recipes, listings, posts, users), "
    "include a Postgres database task. Suggest Vercel Postgres or Supabase.\n"
    "- For sites that need accounts, include an auth task.\n"
    "- For sites that need a custom domain, include a domain task.\n\n"
    "Rules:\n"
    "- Return ONLY valid JSON. No markdown, no code fences, no extra text.\n"
    "- Goal title: short, under 10 words, names the kind of site.\n"
    "- Goal description: one sentence in plain language.\n"
    "- Each task title is something the user can sit down and do.\n"
    "- Use plain language. Avoid jargon. If you must name a tool, name it "
    "in parentheses (e.g., 'Set up a free Vercel account').\n"
    "- Priority P1 for must-haves, P2 for polish.\n"
    "- Never use em-dashes.\n\n"
    "Respond with this exact JSON structure:\n"
    '{"goal": {"title": "...", "description": "..."}, '
    '"tasks": [{"title": "...", "priority": "P1"}, ...]}'
)


PROJECT_PROMPT = (
    "You are a calm planning assistant helping someone scope a project. "
    "The user will describe the project in their own words. Turn it into a "
    "clear goal and 4 to 7 concrete first tasks they can act on this week.\n\n"
    "Rules:\n"
    "- Return ONLY valid JSON. No markdown, no code fences, no extra text.\n"
    "- Goal title: short, under 10 words.\n"
    "- Goal description: one sentence in plain language.\n"
    "- Each task is a concrete action, not a phase or theme.\n"
    "- Front-load the tasks that unblock everything else.\n"
    "- Priority P1 for the critical path, P2 for nice-to-haves.\n"
    "- Plain language. No jargon. Never use em-dashes.\n\n"
    "Respond with this exact JSON structure:\n"
    '{"goal": {"title": "...", "description": "..."}, '
    '"tasks": [{"title": "...", "priority": "P1"}, ...]}'
)


LEARN_PROMPT = (
    "You are a calm tutor helping someone learn a new skill. The user will "
    "tell you what they want to learn. Turn it into a clear goal and 4 to 6 "
    "concrete starter tasks that build on each other.\n\n"
    "Rules:\n"
    "- Return ONLY valid JSON. No markdown, no code fences, no extra text.\n"
    "- Goal title: short, under 10 words, names the skill.\n"
    "- Goal description: one sentence in plain language about why this matters.\n"
    "- Each task is a small, doable step (not 'master X').\n"
    "- Start with picking a single resource (book, course, video) so the user "
    "is not paralyzed by choice.\n"
    "- Priority P1 for the first few steps, P2 for the later ones.\n"
    "- Plain language. No jargon. Never use em-dashes.\n\n"
    "Respond with this exact JSON structure:\n"
    '{"goal": {"title": "...", "description": "..."}, '
    '"tasks": [{"title": "...", "priority": "P1"}, ...]}'
)


OFF_PLATE_PROMPT = (
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


ADVENTURES: list[AdventureTemplate] = [
    AdventureTemplate(
        id="build_website",
        title="Build a website",
        tagline="From idea to live site, with the right pieces planned out for you.",
        icon="language",
        placeholder="e.g. A recipe site where I can post my own recipes and let friends save favorites",
        system_prompt=WEBSITE_PROMPT,
    ),
    AdventureTemplate(
        id="plan_project",
        title="Plan a project",
        tagline="Turn a fuzzy idea into a real plan you can start this week.",
        icon="bolt",
        placeholder="e.g. Launch a small newsletter about climate tech for non-engineers",
        system_prompt=PROJECT_PROMPT,
    ),
    AdventureTemplate(
        id="learn_skill",
        title="Learn something new",
        tagline="A starter path so you stop bookmarking courses and actually begin.",
        icon="school",
        placeholder="e.g. Learn enough Spanish to hold a basic conversation",
        system_prompt=LEARN_PROMPT,
    ),
    AdventureTemplate(
        id="off_plate",
        title="Get something off your plate",
        tagline="The thing you have been avoiding. Let's break it into doable steps.",
        icon="task_alt",
        placeholder="e.g. I need to do my taxes but I have no idea where to start",
        system_prompt=OFF_PLATE_PROMPT,
    ),
]

ADVENTURES_BY_ID = {a.id: a for a in ADVENTURES}


# --- Request / Response models ---

class StartAdventureRequest(BaseModel):
    adventure_id: str
    description: str


class TaskItem(BaseModel):
    title: str
    priority: str


class GoalItem(BaseModel):
    title: str
    description: str


class AdventurePlan(BaseModel):
    adventure_id: str
    goal: GoalItem
    tasks: list[TaskItem]


class TemplatesResponse(BaseModel):
    adventures: list[AdventureTemplate]


# --- Fallback when the LLM is unavailable ---

def _fallback_plan(adventure: AdventureTemplate, description: str) -> AdventurePlan:
    """Return a sensible generic plan when the LLM call fails."""
    short = description[:60].rstrip(".").strip() or adventure.title

    if adventure.id == "build_website":
        tasks = [
            TaskItem(title="Write down what your site is for in one sentence", priority="P1"),
            TaskItem(title="Pick a name for your site and check if the domain is available", priority="P1"),
            TaskItem(title="Sign up for a free Vercel account", priority="P1"),
            TaskItem(title="Sketch the homepage on paper or in any drawing tool", priority="P1"),
            TaskItem(title="Decide if your site needs to store data (a database)", priority="P2"),
        ]
        goal_title = f"Build: {short}"
        goal_desc = "A live website you can share, planned step by step."

    elif adventure.id == "plan_project":
        tasks = [
            TaskItem(title="Write down what done looks like in one sentence", priority="P1"),
            TaskItem(title="List the three biggest unknowns and what would resolve each", priority="P1"),
            TaskItem(title="Pick the very first thing you can finish in under an hour", priority="P1"),
            TaskItem(title="Block 30 minutes on your calendar to start it", priority="P2"),
        ]
        goal_title = f"Project: {short}"
        goal_desc = "A clear first week of work on the thing you want to build."

    elif adventure.id == "learn_skill":
        tasks = [
            TaskItem(title="Pick one resource to start with (book, course, or video) and bookmark it", priority="P1"),
            TaskItem(title="Block 20 minutes on your calendar for tomorrow to start", priority="P1"),
            TaskItem(title="Write down what you want to be able to do in 30 days", priority="P1"),
            TaskItem(title="Find one person who already knows this and ask them what to skip", priority="P2"),
        ]
        goal_title = f"Learn: {short}"
        goal_desc = "A starter path so you actually begin instead of bookmarking forever."

    else:  # off_plate
        tasks = [
            TaskItem(title="Write down everything you know about this so far", priority="P1"),
            TaskItem(title="Figure out the very first small step you can take", priority="P1"),
            TaskItem(title="Set aside 30 minutes to work on just that first step", priority="P1"),
            TaskItem(title="Ask for help if you get stuck", priority="P2"),
        ]
        goal_title = f"Handle: {short}"
        goal_desc = "Get this off your plate, one small step at a time."

    return AdventurePlan(
        adventure_id=adventure.id,
        goal=GoalItem(title=goal_title, description=goal_desc),
        tasks=tasks,
    )


# --- Core logic ---

async def _call_llm(adventure: AdventureTemplate, description: str) -> AdventurePlan:
    """Ask the LLM to turn a user's goal into a structured plan."""
    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        logger.warning("No Anthropic API key available, using fallback plan for %s", adventure.id)
        return _fallback_plan(adventure, description)

    client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=adventure.system_prompt,
            messages=[{"role": "user", "content": description}],
        )
    except anthropic.APIError as exc:
        logger.error("LLM API error for %s: %s", adventure.id, exc)
        return _fallback_plan(adventure, description)

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.error("LLM returned invalid JSON for %s: %s", adventure.id, text[:200])
        return _fallback_plan(adventure, description)

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
        logger.error("LLM response missing expected fields for %s: %s", adventure.id, exc)
        return _fallback_plan(adventure, description)

    if not tasks:
        return _fallback_plan(adventure, description)

    return AdventurePlan(adventure_id=adventure.id, goal=goal, tasks=tasks)


async def _persist_tasks(plan: AdventurePlan) -> None:
    """Save the generated tasks via ostk so they show up on the dashboard."""
    for task in plan.tasks:
        safe_title = task.title if len(task.title) <= 80 else task.title[:77].rstrip() + "..."
        try:
            result = await ostk.add_task(safe_title, task.priority)
        except OstkError as exc:
            logger.warning("Failed to persist task '%s': %s", safe_title, exc)
            continue
        # Fire-and-forget auto label suggestion. Never blocks task creation.
        new_id = extract_task_id(result)
        schedule_auto_labels(new_id, safe_title, "")


# --- Endpoints ---

@router.get("/adventures/templates", response_model=TemplatesResponse)
async def list_templates() -> TemplatesResponse:
    """List the available onboarding adventures."""
    return TemplatesResponse(adventures=ADVENTURES)


@router.post("/adventures/start", response_model=AdventurePlan)
async def start_adventure(body: StartAdventureRequest) -> AdventurePlan:
    """Generate a plan for the chosen adventure and persist its tasks."""
    if not body.description.strip():
        raise HTTPException(status_code=422, detail="Please describe what you want to do.")

    adventure = ADVENTURES_BY_ID.get(body.adventure_id)
    if adventure is None:
        raise HTTPException(status_code=404, detail=f"Unknown adventure: {body.adventure_id}")

    plan = await _call_llm(adventure, body.description)
    await _persist_tasks(plan)
    return plan

import json
import logging
from typing import Optional

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.chat_providers import _resolve_api_key
from services.ostk import ostk, OstkError

router = APIRouter(tags=["onboarding"])

logger = logging.getLogger(__name__)


# --- Request / Response models ---

class DreamRequest(BaseModel):
    dreading: str
    done_looks_like: Optional[str] = None


class TaskItem(BaseModel):
    title: str
    priority: str


class GoalItem(BaseModel):
    title: str
    description: str


class DreamResponse(BaseModel):
    goal: GoalItem
    tasks: list[TaskItem]


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
    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        logger.warning("No Anthropic API key available, using fallback plan")
        return _fallback_response(dreading, done_looks_like)

    client = anthropic.AsyncAnthropic(api_key=api_key)
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
            await ostk.add_task(task.title, task.priority)
        except OstkError as exc:
            logger.warning("Failed to persist task '%s': %s", task.title, exc)


# --- Endpoint ---

@router.post("/onboarding/dream", response_model=DreamResponse)
async def dream(body: DreamRequest):
    """Turn something you have been dreading into a goal with tasks."""
    if not body.dreading.strip():
        raise HTTPException(status_code=422, detail="Please describe what you have been putting off.")

    plan = await _call_llm(body.dreading, body.done_looks_like)
    await _persist_tasks(plan)
    return plan

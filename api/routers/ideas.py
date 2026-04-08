from datetime import datetime, timezone
from pathlib import Path
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from models.schemas import HayCreate, HayConvert
from services.idea_breakdown import BreakdownTask, break_down_idea
from services.ostk import ostk, OstkError
from services.task_labeling import extract_task_id, schedule_auto_labels
from services.templates_store import templates_store, BUILTIN_TEMPLATES

router = APIRouter(tags=["ideas"])

# Paths for staleness calculation
_AUDIT_PATH = Path(__file__).resolve().parent.parent.parent / ".ostk" / "audit.jsonl"

_STALE_DAYS = 14
_VERY_STALE_DAYS = 30


class IdeaAnswer(BaseModel):
    """Follow up answer to a clarifying question from idea breakdown."""
    straw: str
    answer: str
    priority: str = "P1"
    delete_hay: Optional[bool] = False


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    icon: str = "lightbulb"
    prompt_template: str
    breakdown_hint: str = ""


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    prompt_template: Optional[str] = None
    breakdown_hint: Optional[str] = None


def _compute_staleness(created_at_str: Optional[str], is_converted: bool) -> Optional[str]:
    """Return None, 'stale', or 'very_stale' based on age."""
    if is_converted or not created_at_str:
        return None
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = (now - created).days
        if age_days >= _VERY_STALE_DAYS:
            return "very_stale"
        if age_days >= _STALE_DAYS:
            return "stale"
        return None
    except Exception:
        return None


def _load_hay_metadata() -> dict[str, dict]:
    """Read audit.jsonl and return a dict of straw -> {timestamp, source}."""
    result: dict[str, dict] = {}
    if not _AUDIT_PATH.exists():
        return result
    try:
        text = _AUDIT_PATH.read_text()
    except OSError:
        return result
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "hay.filed":
            straw = entry.get("straw", "")
            ts = entry.get("timestamp", "")
            source = entry.get("source", None)
            if straw and straw not in result:
                result[straw] = {"timestamp": ts, "source": source}
    return result


def _annotate_with_staleness(hay_data: dict, converted_straws: set) -> dict:
    """Add staleness and source fields to each idea in the hay data."""
    hay_meta = _load_hay_metadata()

    def annotate_list(items: list) -> list:
        result = []
        for item in items:
            straw = item if isinstance(item, str) else item
            meta = hay_meta.get(straw, {})
            ts = meta.get("timestamp")
            source = meta.get("source")
            is_converted = straw in converted_straws
            staleness = _compute_staleness(ts, is_converted)
            result.append({"straw": straw, "staleness": staleness, "source": source})
        return result

    annotated: dict = {
        "clusters": [],
        "unclustered": [],
    }

    for cluster in hay_data.get("clusters", []):
        annotated_cluster = {
            "name": cluster["name"],
            "count": cluster["count"],
            "items": annotate_list(cluster.get("items", [])),
        }
        annotated["clusters"].append(annotated_cluster)

    annotated["unclustered"] = annotate_list(hay_data.get("unclustered", []))
    return annotated


@router.get("/ideas/templates")
async def list_templates():
    """List all available idea templates (built-ins + custom)."""
    return {"templates": templates_store.list_all()}


@router.get("/ideas")
async def list_ideas(status: str = "active"):
    """List ideas.

    Query params:
        status: "active" (default) returns ideas not yet turned into tasks.
                "converted" returns ideas that have been turned into tasks.
    """
    try:
        if status == "converted":
            converted = await ostk.list_converted_hay()
            return {"converted": converted}
        hay = await ostk.list_hay(exclude_converted=True)
        converted_list = await ostk.list_converted_hay()
        converted_straws = {c["straw"] for c in converted_list}
        annotated = _annotate_with_staleness(hay, converted_straws)
        return annotated
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ideas")
async def add_idea(body: HayCreate):
    try:
        result = await ostk.add_hay(body.thought)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ideas/compile")
async def compile_ideas(dry_run: bool = False):
    try:
        result = await ostk.compile_hay(dry_run=dry_run)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ideas/{straw:path}")
async def delete_idea(straw: str):
    try:
        result = await ostk.delete_hay(straw)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _create_tasks_from_breakdown(
    tasks: list[BreakdownTask],
    straw: str,
    fallback_priority: str,
    delete_hay: bool,
) -> list[dict]:
    """Create each task from a breakdown and return a list of created task dicts.

    Every task goes through the same path as POST /api/tasks so it picks up
    auto labels. The original straw is marked converted (once, to the first
    task) so the idea moves out of the active list.
    """
    created: list[dict] = []
    first_task_result: Optional[str] = None
    for task in tasks:
        title = task.title.strip() or straw
        description = task.description.strip()
        priority = task.priority if task.priority else fallback_priority
        try:
            if first_task_result is None:
                # Convert on the first task so the idea is marked converted.
                add_result = await ostk.convert_hay_to_task(
                    straw=straw,
                    priority=priority,
                    delete_hay=delete_hay or False,
                )
                first_task_result = add_result
            else:
                add_result = await ostk.add_task(title, priority, description=description)
        except OstkError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        task_id = extract_task_id(add_result)
        schedule_auto_labels(task_id, title, description)
        created.append(
            {
                "id": task_id,
                "title": title,
                "description": description,
                "priority": priority,
                "order": task.order,
            }
        )
    return created


@router.post("/ideas/convert")
async def convert_idea_to_task(body: HayConvert):
    """Break the idea into the full list of tasks needed to ship it.

    If the idea is too vague, returns a clarifying question for the user
    to answer via POST /api/ideas/answer.
    """
    breakdown = await break_down_idea(title=body.straw, description="")

    if breakdown.needs_clarification:
        return {
            "status": "needs_clarification",
            "question": breakdown.question or "Tell me a little more about this idea.",
            "straw": body.straw,
        }

    created = await _create_tasks_from_breakdown(
        tasks=breakdown.tasks,
        straw=body.straw,
        fallback_priority=body.priority,
        delete_hay=body.delete_hay or False,
    )

    # Backwards compat shim: older tests and UI code look for ``task_id`` and
    # ``result`` at the top level. Keep the first task exposed there so the
    # existing assertions continue to work.
    first_id = created[0]["id"] if created else None
    return {
        "status": "created",
        "result": f"created {len(created)} task(s)",
        "task_id": first_id,
        "tasks": created,
    }


@router.post("/ideas/answer")
async def answer_clarification(body: IdeaAnswer):
    """Retry the breakdown with the user's answer as extra context.

    Returns the same shape as /ideas/convert: either another clarifying
    question or the created task list.
    """
    breakdown = await break_down_idea(
        title=body.straw,
        description="",
        extra_context=body.answer,
    )

    if breakdown.needs_clarification:
        return {
            "status": "needs_clarification",
            "question": breakdown.question or "Could you share a little more?",
            "straw": body.straw,
        }

    created = await _create_tasks_from_breakdown(
        tasks=breakdown.tasks,
        straw=body.straw,
        fallback_priority=body.priority,
        delete_hay=body.delete_hay or False,
    )

    first_id = created[0]["id"] if created else None
    return {
        "status": "created",
        "result": f"created {len(created)} task(s)",
        "task_id": first_id,
        "tasks": created,
    }


# --- Admin template CRUD (Feature 4) ---

@router.get("/admin/templates")
async def list_custom_templates():
    """List user-defined custom templates."""
    return {"templates": templates_store.list_custom()}


@router.post("/admin/templates")
async def create_custom_template(body: TemplateCreate):
    """Create a new custom template."""
    created = templates_store.create(body.model_dump())
    return {"template": created}


@router.put("/admin/templates/{template_id}")
async def update_custom_template(template_id: str, body: TemplateUpdate):
    """Update a custom template by id."""
    updated = templates_store.update(template_id, body.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": updated}


@router.delete("/admin/templates/{template_id}")
async def delete_custom_template(template_id: str):
    """Delete a custom template by id."""
    ok = templates_store.delete(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"result": "deleted"}

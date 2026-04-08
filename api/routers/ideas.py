from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from models.schemas import HayCreate, HayConvert
from services.idea_breakdown import BreakdownTask, break_down_idea
from services.ostk import ostk, OstkError
from services.task_labeling import extract_task_id, schedule_auto_labels

router = APIRouter(tags=["ideas"])


class IdeaAnswer(BaseModel):
    """Follow up answer to a clarifying question from idea breakdown."""
    straw: str
    answer: str
    priority: str = "P1"
    delete_hay: Optional[bool] = False


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
        return hay
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
                add_result = await ostk.add_task(title, priority)
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

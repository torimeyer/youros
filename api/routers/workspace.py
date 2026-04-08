"""Workspace router: shared message board for running agents."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.agent_workspace import agent_workspace_service, VALID_TYPES

router = APIRouter(tags=["workspace"])


class PostMessageBody(BaseModel):
    agent_name: str
    content: str
    message_type: str = "finding"


@router.get("/workspace/messages")
async def list_messages(
    since_id: Optional[str] = None,
    agent: Optional[str] = None,
):
    """Return workspace messages, optionally filtered."""
    messages = agent_workspace_service.get_messages(
        since_id=since_id,
        agent_name=agent,
    )
    return {
        "messages": [m.to_dict() for m in messages],
        "count": len(messages),
    }


@router.post("/workspace/messages")
async def post_message(body: PostMessageBody):
    """Post a message to the shared workspace."""
    if not body.agent_name.strip():
        raise HTTPException(status_code=400, detail="agent_name is required")
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    if body.message_type not in VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"message_type must be one of: {', '.join(sorted(VALID_TYPES))}",
        )
    msg = agent_workspace_service.post_message(
        agent_name=body.agent_name.strip(),
        content=body.content.strip(),
        message_type=body.message_type,
    )
    return {"message": msg.to_dict()}


@router.delete("/workspace/messages")
async def clear_messages():
    """Clear all workspace messages (admin action)."""
    count = agent_workspace_service.clear_workspace()
    return {"result": "workspace cleared", "cleared_count": count}


@router.get("/workspace/summary")
async def get_summary():
    """Return a compact text summary of the workspace, for injecting into agent prompts."""
    summary = agent_workspace_service.get_summary()
    messages = agent_workspace_service.get_messages()
    return {
        "summary": summary,
        "message_count": len(messages),
        "has_content": bool(summary),
    }

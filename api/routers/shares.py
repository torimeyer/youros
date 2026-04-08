"""Sharing endpoints.

POST /api/shares        -- create a share, returns {token, url}
GET  /api/shares        -- list my shares
GET  /api/shares/{token}-- public: return shared content (no auth required)
DELETE /api/shares/{token} -- revoke a share
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.shares import shares_store
from services.ostk import ostk, OstkError
from services.labels_store import labels_store
from services.task_labels_store import task_labels_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shares"])


class ShareCreate(BaseModel):
    share_type: str          # "task_list" | "agent_output" | "label_view"
    content_ids: list[str]   # task IDs, agent name, or label ID
    title: str
    expires_in_days: int = 7


async def _snapshot_tasks(task_ids: list[str]) -> list[dict]:
    """Load tasks from ostk and return only the requested ones."""
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        return []

    id_set = set(task_ids)
    results = []
    for t in all_tasks:
        if t.get("id") in id_set:
            results.append({
                "id": t.get("id"),
                "title": t.get("title"),
                "priority": t.get("priority"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "description": t.get("description"),
                "label_ids": task_labels_store.get_labels_for_task(t.get("id", "")),
            })
    return results


async def _snapshot_all_tasks_with_label(label_id: str) -> list[dict]:
    """Return all tasks that carry the given label."""
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        return []

    results = []
    all_assignments = task_labels_store.get_all_assignments()
    for t in all_tasks:
        task_id = t.get("id", "")
        if label_id in all_assignments.get(task_id, []):
            results.append({
                "id": task_id,
                "title": t.get("title"),
                "priority": t.get("priority"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "description": t.get("description"),
                "label_ids": all_assignments.get(task_id, []),
            })
    return results


async def _snapshot_agent_output(agent_name: str) -> list[dict]:
    """Return a snippet of an agent's transcript as the snapshot."""
    from config import PROJECT_ROOT
    transcript = PROJECT_ROOT / "transcripts" / f"{agent_name}.md"
    if not transcript.exists():
        return []
    try:
        text = transcript.read_text(encoding="utf-8", errors="replace")
        # Return up to 8000 characters to keep shares reasonable
        snippet = text[:8000]
        return [{"agent": agent_name, "output": snippet}]
    except OSError:
        return []


@router.post("/shares")
async def create_share(body: ShareCreate, request: Request):
    """Create a shareable link for a task list, label view, or agent output."""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    if body.share_type not in ("task_list", "agent_output", "label_view"):
        raise HTTPException(
            status_code=400,
            detail="share_type must be 'task_list', 'agent_output', or 'label_view'",
        )

    if not body.content_ids:
        raise HTTPException(status_code=400, detail="content_ids cannot be empty")

    if body.expires_in_days < 1 or body.expires_in_days > 365:
        raise HTTPException(status_code=400, detail="expires_in_days must be between 1 and 365")

    # Build the content snapshot at creation time so the link is a stable copy
    snapshot: list[dict] = []
    if body.share_type == "task_list":
        snapshot = await _snapshot_tasks(body.content_ids)
    elif body.share_type == "label_view":
        # content_ids[0] is the label ID
        snapshot = await _snapshot_all_tasks_with_label(body.content_ids[0])
        # Also attach label metadata
        label = labels_store.get_label(body.content_ids[0])
        if label:
            for item in snapshot:
                item["_label"] = label
    elif body.share_type == "agent_output":
        # content_ids[0] is the agent name
        snapshot = await _snapshot_agent_output(body.content_ids[0])

    try:
        share = shares_store.create_share(
            share_type=body.share_type,
            content_ids=body.content_ids,
            title=title,
            content_snapshot=snapshot,
            expires_in_days=body.expires_in_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = share["token"]
    # Build the public URL: same host, path /share/<token>
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/share/{token}"

    return {"token": token, "url": url, "share": share}


@router.get("/shares")
async def list_shares():
    """List all shares (including expired ones)."""
    return {"shares": shares_store.list_shares()}


@router.get("/shares/{token}")
async def get_share(token: str):
    """Public endpoint. Returns shared content if the link is valid.

    Returns 410 Gone if the share exists but has expired.
    Returns 404 if the token is not recognised at all.
    """
    # Check if it exists but is expired
    if shares_store.is_expired(token):
        raise HTTPException(status_code=410, detail="This link has expired.")

    share = shares_store.get_share(token)
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")

    return share


@router.delete("/shares/{token}")
async def delete_share(token: str):
    """Revoke a share link."""
    deleted = shares_store.delete_share(token)
    if not deleted:
        raise HTTPException(status_code=404, detail="Share not found")
    return {"result": "revoked"}

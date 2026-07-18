"""Cross-team coordination endpoints (Theme B, →1438 + →1452).

GET /api/coordination/dependencies/{epic_key}
POST /api/coordination/nudge

The blockers endpoint (GET /api/coordination/blockers) was removed (→2924):
it ran an unscoped Jira query and auto-created a task per result on every
Dashboard mount, flooding the task list with duplicate [Blocker] tasks and
starving the backend until Tasks polling timed out.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import atlassian as atlassian_service
from services import slack as slack_service

router = APIRouter(tags=["coordination"])


@router.get("/coordination/dependencies/{epic_key}")
async def get_dependencies(epic_key: str):
    """Return a node/edge dependency graph for the given epic.

    Nodes: [{key, summary, status, owner}]
    Edges: [{from, to, type}]
    Returns empty graph when Atlassian is not configured — never 500.
    Returns 404 when the epic key does not exist in Jira.
    """
    if not atlassian_service.is_connected():
        return {"nodes": [], "edges": []}

    try:
        issue = await atlassian_service.get_issue_links(epic_key)
    except RuntimeError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        # Any other error → empty graph (never 500)
        return {"nodes": [], "edges": []}
    except Exception:
        return {"nodes": [], "edges": []}

    # Build node list starting with the root epic.
    nodes: list[dict] = [
        {
            "key": issue["key"],
            "summary": issue.get("summary", ""),
            "status": issue.get("status", ""),
            "owner": issue.get("assignee", ""),
        }
    ]
    edges: list[dict] = []
    seen_keys: set[str] = {issue["key"]}

    for link in issue.get("issuelinks", []):
        link_type = (link.get("type") or {}).get("name", "relates to")

        for direction, field in (("outward", "outwardIssue"), ("inward", "inwardIssue")):
            linked = link.get(field)
            if not linked:
                continue
            linked_key = linked.get("key", "")
            if not linked_key:
                continue
            lf = linked.get("fields") or {}
            status_obj = lf.get("status") or {}
            assignee_obj = lf.get("assignee") or {}

            if linked_key not in seen_keys:
                seen_keys.add(linked_key)
                nodes.append({
                    "key": linked_key,
                    "summary": lf.get("summary", ""),
                    "status": status_obj.get("name", ""),
                    "owner": assignee_obj.get("displayName", ""),
                })

            # Edge direction: root → outward, inward → root
            if direction == "outward":
                edges.append({"from": issue["key"], "to": linked_key, "type": link_type})
            else:
                edges.append({"from": linked_key, "to": issue["key"], "type": link_type})

    return {"nodes": nodes, "edges": edges}


class NudgeRequest(BaseModel):
    jira_key: str
    channel: str  # "slack" or "jira"
    slack_channel_id: str | None = None  # optional; if omitted and channel=slack, picks first


@router.post("/coordination/nudge")
async def nudge(req: NudgeRequest):
    """Post a nudge via Slack or a Jira comment.

    Body: {jira_key, channel, slack_channel_id?}
    channel must be "slack" or "jira" — returns 400 for anything else.
    Returns {success, message_id} on success.
    Returns {success: false, error: "..."} when the helper isn't configured.
    Never returns 500.
    """
    if req.channel not in ("slack", "jira"):
        raise HTTPException(status_code=400, detail=f"Invalid channel '{req.channel}'. Use 'slack' or 'jira'.")

    nudge_text = f"Nudge: {req.jira_key} needs attention. Please provide an update on current status and blockers."

    if req.channel == "slack":
        if not slack_service.is_connected():
            return {"success": False, "error": "Slack is not connected. Connect Slack in Settings to send nudges."}
        try:
            # Pick the target channel: explicit id or first available.
            channel_id = req.slack_channel_id
            if not channel_id:
                channels = await slack_service.list_channels()
                if not channels:
                    return {"success": False, "error": "No Slack channels available. Join a channel and try again."}
                channel_id = channels[0]["id"]
            result = await slack_service.post_message(channel_id, nudge_text)
            return {"success": True, "message_id": result.get("ts", "")}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # channel == "jira"
    if not atlassian_service.is_connected():
        return {"success": False, "error": "Jira is not connected. Connect Jira in Settings to send nudges."}
    try:
        result = await atlassian_service.add_comment(req.jira_key, nudge_text)
        return {"success": True, "message_id": str(result.get("id", ""))}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

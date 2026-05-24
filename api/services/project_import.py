"""Project import service for Linear and Jira.

Imports issues/tickets from external project trackers into myOS tasks.
Maps statuses, priorities, and labels to myOS equivalents.
"""

from __future__ import annotations

from typing import Optional

import httpx


# --- Status mapping ---

LINEAR_STATUS_MAP = {
    "backlog": "open",
    "todo": "open",
    "in progress": "open",
    "in review": "open",
    "done": "closed",
    "canceled": "closed",
    "cancelled": "closed",
}

JIRA_STATUS_MAP = {
    "to do": "open",
    "in progress": "open",
    "in review": "open",
    "done": "closed",
    "closed": "closed",
    "resolved": "closed",
    "open": "open",
    "reopened": "open",
    "backlog": "open",
}

# --- Priority mapping ---

LINEAR_PRIORITY_MAP = {
    0: "P3",   # No priority
    1: "P0",   # Urgent
    2: "P1",   # High
    3: "P2",   # Medium
    4: "P3",   # Low
}

JIRA_PRIORITY_MAP = {
    "highest": "P0",
    "high": "P1",
    "medium": "P2",
    "low": "P3",
    "lowest": "P3",
    "blocker": "P0",
    "critical": "P0",
    "major": "P1",
    "minor": "P3",
    "trivial": "P3",
}


async def import_from_linear(
    api_key: str,
    team_id: str,
    include_closed: bool = False,
) -> dict:
    """Import issues from Linear.

    Returns {"tasks": [...], "skipped": int, "errors": [str]}
    Each task dict has: title, description, priority, status, labels, source_id, source_url
    """
    tasks: list[dict] = []
    errors: list[str] = []
    skipped = 0

    # GraphQL query to fetch issues from a team
    query = """
    query($teamId: String!, $after: String) {
        team(id: $teamId) {
            issues(first: 100, after: $after) {
                nodes {
                    id
                    identifier
                    title
                    description
                    priority
                    url
                    state { name }
                    labels { nodes { name } }
                    assignee { name }
                    createdAt
                    updatedAt
                }
                pageInfo { hasNextPage endCursor }
            }
        }
    }
    """

    try:
        cursor: Optional[str] = None
        has_more = True

        async with httpx.AsyncClient(timeout=30.0) as client:
            while has_more:
                variables = {"teamId": team_id}
                if cursor:
                    variables["after"] = cursor

                resp = await client.post(
                    "https://api.linear.app/graphql",
                    headers={
                        "Authorization": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "variables": variables},
                )

                if resp.status_code != 200:
                    errors.append(f"Linear API returned {resp.status_code}")
                    break

                data = resp.json()
                if "errors" in data:
                    for err in data["errors"]:
                        errors.append(f"Linear: {err.get('message', str(err))}")
                    break

                team_data = data.get("data", {}).get("team")
                if not team_data:
                    errors.append("Team not found. Check the team ID.")
                    break

                issues_data = team_data.get("issues", {})
                nodes = issues_data.get("nodes", [])

                for issue in nodes:
                    state_name = (issue.get("state") or {}).get("name", "").lower()
                    status = LINEAR_STATUS_MAP.get(state_name, "open")

                    if status == "closed" and not include_closed:
                        skipped += 1
                        continue

                    priority_num = issue.get("priority", 0)
                    priority = LINEAR_PRIORITY_MAP.get(priority_num, "P2")

                    label_nodes = (issue.get("labels") or {}).get("nodes", [])
                    labels = [l.get("name", "") for l in label_nodes if l.get("name")]

                    tasks.append({
                        "title": issue.get("title", ""),
                        "description": issue.get("description", "") or "",
                        "priority": priority,
                        "status": status,
                        "labels": labels,
                        "source_id": issue.get("identifier", ""),
                        "source_url": issue.get("url", ""),
                    })

                page_info = issues_data.get("pageInfo", {})
                has_more = page_info.get("hasNextPage", False)
                cursor = page_info.get("endCursor")

    except httpx.HTTPError as exc:
        errors.append(f"Could not reach Linear: {exc}")

    return {"tasks": tasks, "skipped": skipped, "errors": errors}


async def import_from_jira(
    domain: str,
    email: str,
    api_token: str,
    project_key: str,
    include_closed: bool = False,
) -> dict:
    """Import issues from Jira.

    Returns {"tasks": [...], "skipped": int, "errors": [str]}
    Each task dict has: title, description, priority, status, labels, source_id, source_url
    """
    tasks: list[dict] = []
    errors: list[str] = []
    skipped = 0

    # Clean domain
    domain = domain.strip().rstrip("/")
    if not domain.startswith("https://"):
        domain = f"https://{domain}"

    jql = f"project = {project_key} ORDER BY updated DESC"
    if not include_closed:
        jql = f"project = {project_key} AND status != Done AND status != Closed AND status != Resolved ORDER BY updated DESC"

    try:
        max_results = 100
        next_page_token: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                body: dict = {
                    "jql": jql,
                    "maxResults": max_results,
                    "fields": ["summary", "description", "priority", "status", "labels", "assignee", "created", "updated"],
                }
                if next_page_token:
                    body["nextPageToken"] = next_page_token

                resp = await client.post(
                    f"{domain}/rest/api/3/search/jql",
                    auth=(email, api_token),
                    json=body,
                )

                if resp.status_code == 401:
                    errors.append("Jira authentication failed. Check your email and API token.")
                    break
                if resp.status_code == 403:
                    errors.append("Jira access denied. Check your permissions for this project.")
                    break
                if resp.status_code >= 400:
                    errors.append(f"Jira API returned {resp.status_code}")
                    break

                data = resp.json()
                issues = data.get("issues", [])

                for issue in issues:
                    fields = issue.get("fields", {})
                    status_name = (fields.get("status") or {}).get("name", "").lower()
                    status = JIRA_STATUS_MAP.get(status_name, "open")

                    if status == "closed" and not include_closed:
                        skipped += 1
                        continue

                    priority_name = (fields.get("priority") or {}).get("name", "").lower()
                    priority = JIRA_PRIORITY_MAP.get(priority_name, "P2")

                    labels = fields.get("labels", []) or []

                    desc_raw = fields.get("description")
                    description = ""
                    if isinstance(desc_raw, str):
                        description = desc_raw
                    elif isinstance(desc_raw, dict):
                        description = _extract_adf_text(desc_raw)

                    issue_key = issue.get("key", "")

                    tasks.append({
                        "title": fields.get("summary", ""),
                        "description": description,
                        "priority": priority,
                        "status": status,
                        "labels": labels,
                        "source_id": issue_key,
                        "source_url": f"{domain}/browse/{issue_key}",
                    })

                next_page_token = data.get("nextPageToken")
                if not issues or not next_page_token:
                    break

    except httpx.HTTPError as exc:
        errors.append(f"Could not reach Jira: {exc}")

    return {"tasks": tasks, "skipped": skipped, "errors": errors}


def _extract_adf_text(adf: dict) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    parts: list[str] = []
    content = adf.get("content", [])
    for node in content:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        elif "content" in node:
            parts.append(_extract_adf_text(node))
    return " ".join(parts).strip()

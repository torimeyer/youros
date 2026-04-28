"""GET /api/since-you-last-looked — activity rollup since a given timestamp.

Returns three buckets:
  completed_agents  — agents that reached a terminal state after `since`
  artifacts_created — files written to ~/.myos/files/ after `since`
  awaiting_input    — in-progress tasks that may need the user's attention
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["since_you_last_looked"])

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "stopped"}


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


@router.get("/since-you-last-looked")
async def since_you_last_looked(since: Optional[str] = Query(None)):
    """Return agent activity, new files, and pending tasks since `since`.

    `since` is an ISO 8601 timestamp (e.g. 2026-04-27T10:00:00Z).
    If omitted, all known records are returned.
    """
    since_dt: Optional[datetime] = _parse_iso(since) if since else None

    completed_agents = _get_completed_agents(since_dt)
    artifacts_created = _get_artifacts(since_dt)
    awaiting_input = await _get_awaiting_input()

    return {
        "completed_agents": completed_agents,
        "artifacts_created": artifacts_created,
        "awaiting_input": awaiting_input,
        "since": since,
    }


def _get_completed_agents(since_dt: Optional[datetime]) -> list[dict]:
    from routers.agents import agent_metadata  # lazy to avoid circular import

    results = []
    for name, meta in agent_metadata.items():
        status = meta.get("status", "")
        if status not in _TERMINAL_STATUSES:
            continue
        completed_at_str = meta.get("completed_at")
        if not completed_at_str:
            continue
        completed_at = _parse_iso(completed_at_str)
        if since_dt and (completed_at is None or completed_at <= since_dt):
            continue
        results.append(
            {
                "name": name,
                "status": status,
                "summary": meta.get("summary") or meta.get("task") or "",
                "completed_at": completed_at_str,
                "output_path": meta.get("output_path"),
            }
        )
    results.sort(key=lambda a: a["completed_at"], reverse=True)
    return results


def _get_artifacts(since_dt: Optional[datetime]) -> list[dict]:
    try:
        from services.files_dir import get_files_dir
        files_dir = get_files_dir()
        if not files_dir.exists():
            return []
    except Exception:
        return []

    results = []
    try:
        for path in files_dir.iterdir():
            if not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if since_dt and mtime <= since_dt:
                continue
            results.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "created_at": mtime.isoformat(),
                }
            )
    except OSError:
        return []

    results.sort(key=lambda f: f["created_at"], reverse=True)
    return results


async def _get_awaiting_input() -> list[dict]:
    try:
        from services.ostk import ostk
        tasks = await ostk.list_tasks(status="in_progress")
        return [
            {
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "status": t.get("status", ""),
            }
            for t in (tasks or [])
        ]
    except Exception:
        return []

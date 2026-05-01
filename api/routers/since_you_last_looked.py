"""GET /api/since-you-last-looked — activity rollup since a given timestamp.

Returns buckets covering agents, files, tasks, and Google Workspace
signals (new emails, calendar events that happened, Drive files changed
by collaborators).
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["since_you_last_looked"])

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "stopped"}


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        # URL query params decode '+' as space; restore it for tz offsets like +00:00
        dt = datetime.fromisoformat(ts.replace(" ", "+"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


@router.get("/since-you-last-looked")
async def since_you_last_looked(since: Optional[str] = Query(None)):
    """Return activity rollup since `since`.

    `since` is an ISO 8601 timestamp (e.g. 2026-04-27T10:00:00Z).
    If omitted, all known records are returned.
    """
    since_dt: Optional[datetime] = _parse_iso(since) if since else None

    completed_agents = _get_completed_agents(since_dt)
    artifacts_created = _get_artifacts(since_dt)

    awaiting_input, new_emails, calendar_events, drive_changes = await asyncio.gather(
        _get_awaiting_input(),
        _get_new_emails(since_dt),
        _get_calendar_events(since_dt),
        _get_drive_changes(since_dt),
    )

    return {
        "completed_agents": completed_agents,
        "artifacts_created": artifacts_created,
        "awaiting_input": awaiting_input,
        "new_emails": new_emails,
        "calendar_events_happened": calendar_events,
        "drive_files_changed": drive_changes,
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


async def _get_new_emails(since_dt: Optional[datetime]) -> list[dict]:
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services.gmail import get_inbox_messages
        messages = await get_inbox_messages()
        if not since_dt:
            return [
                {
                    "subject": m.get("subject", ""),
                    "from_name": m.get("from_name", ""),
                    "snippet": m.get("snippet", ""),
                    "date": m.get("date", ""),
                    "thread_id": m.get("thread_id", ""),
                    "is_unread": m.get("is_unread", False),
                }
                for m in messages
                if m.get("is_unread")
            ]
        results = []
        for m in messages:
            msg_date = _parse_iso(m.get("date", ""))
            if msg_date and msg_date > since_dt and m.get("is_unread"):
                results.append({
                    "subject": m.get("subject", ""),
                    "from_name": m.get("from_name", ""),
                    "snippet": m.get("snippet", ""),
                    "date": m.get("date", ""),
                    "thread_id": m.get("thread_id", ""),
                    "is_unread": True,
                })
        return results
    except Exception:
        return []


async def _get_calendar_events(since_dt: Optional[datetime]) -> list[dict]:
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services import calendar as cal_service
        events = await cal_service.get_today_events()
        now = datetime.now(timezone.utc)
        results = []
        for ev in events:
            start_raw = (ev.get("start") or {}).get("dateTime")
            if not start_raw:
                continue
            start_dt = _parse_iso(start_raw)
            if start_dt is None:
                continue
            if start_dt > now:
                continue
            if since_dt and start_dt <= since_dt:
                continue
            results.append({
                "summary": ev.get("summary", ""),
                "start": ev.get("start"),
                "end": ev.get("end"),
                "htmlLink": ev.get("htmlLink", ""),
                "hangoutLink": ev.get("hangoutLink", ""),
            })
        return results
    except Exception:
        return []


async def _get_drive_changes(since_dt: Optional[datetime]) -> list[dict]:
    try:
        from services.google_auth import is_authenticated, has_full_drive_scope
        if not is_authenticated() or not has_full_drive_scope():
            return []
        import asyncio
        from services.google_auth import get_credentials, _load_client_config
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        tokens = get_credentials()
        client_config = {}
        try:
            client_config = _load_client_config()
        except Exception:
            pass
        creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_config.get("client_id"),
            client_secret=client_config.get("client_secret"),
        )

        def _fetch():
            service = build("drive", "v3", credentials=creds)
            query_parts = ["mimeType != 'application/vnd.google-apps.folder'"]
            if since_dt:
                query_parts.append(f"modifiedTime > '{since_dt.isoformat()}'")
            q = " and ".join(query_parts)
            resp = service.files().list(
                q=q,
                pageSize=20,
                fields="files(id,name,mimeType,modifiedTime,webViewLink,lastModifyingUser)",
                orderBy="modifiedTime desc",
            ).execute()
            return resp.get("files", [])

        files = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        results = []
        for f in files:
            modifier = (f.get("lastModifyingUser") or {}).get("me", True)
            if modifier:
                continue
            results.append({
                "name": f.get("name", ""),
                "mimeType": f.get("mimeType", ""),
                "modifiedTime": f.get("modifiedTime", ""),
                "webViewLink": f.get("webViewLink", ""),
            })
        return results
    except Exception:
        return []

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from services.ostk import ostk, OstkError


def _tokenize(text: str) -> set[str]:
    STOP = {'the', 'and', 'for', 'with', 'this', 'that', 'from', 'have', 'are', 'was', 'will'}
    return {w for w in text.lower().split() if len(w) >= 3 and w not in STOP}


def _score_task(task: dict, meeting_tokens: set[str], attendee_emails: set[str]) -> float:
    task_text = ' '.join(filter(None, [
        task.get('title', ''),
        task.get('description', ''),
        task.get('notes', ''),
        task.get('source_ref', ''),
    ]))
    task_tokens = _tokenize(task_text)
    score = 0.0
    if meeting_tokens and task_tokens:
        union = meeting_tokens | task_tokens
        score += len(meeting_tokens & task_tokens) / len(union) * 10
    for email in attendee_emails:
        if email and email.lower() in task_text.lower():
            score += 5.0
    labels = task.get('labels') or []
    if isinstance(labels, list):
        label_tokens: set[str] = set()
        for label in labels:
            label_tokens |= _tokenize(str(label))
        if label_tokens & meeting_tokens:
            score += 2.0
    return score


async def tasks_for_meeting(event_id: str) -> list[dict]:
    try:
        from services import calendar as cal_service
        events = await cal_service.get_upcoming_events(days=7)
        event = next((e for e in events if e.get('id') == event_id), None)
        if event is None:
            return []
    except Exception:
        return []
    try:
        all_tasks = await ostk.list_tasks('open')
    except (OstkError, Exception):
        return []
    meeting_text = ' '.join(filter(None, [
        event.get('summary', ''),
        event.get('description', ''),
        event.get('location', ''),
    ]))
    meeting_tokens = _tokenize(meeting_text)
    attendee_emails = {
        a.get('email', '').lower()
        for a in (event.get('attendees') or [])
        if a.get('email')
    }
    scored = []
    for task in all_tasks:
        s = _score_task(task, meeting_tokens, attendee_emails)
        if s > 0:
            scored.append({**task, 'relevance_score': round(s, 3)})
    scored.sort(key=lambda t: t['relevance_score'], reverse=True)
    return scored


async def post_meeting_nudge(event_id: str) -> dict:
    try:
        from services import calendar as cal_service
        events = await cal_service.get_upcoming_events(days=7)
        event = next((e for e in events if e.get('id') == event_id), None)
    except Exception:
        event = None
    if event is None:
        return {'event_id': event_id, 'event_summary': None,
                'ended_minutes_ago': None, 'related_tasks': [], 'nudge_message': None}
    summary = event.get('summary') or 'Untitled meeting'
    ended_minutes_ago: Optional[int] = None
    nudge_message: Optional[str] = None
    end_raw = (event.get('end') or {}).get('dateTime')
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if end_dt < now:
                delta = now - end_dt
                ended_minutes_ago = int(delta.total_seconds() / 60)
                nudge_message = ("Meeting '" + summary + "' ended "
                                 + str(ended_minutes_ago) + " min ago. Any follow-up tasks?")
        except ValueError:
            pass
    related_tasks = await tasks_for_meeting(event_id)
    return {'event_id': event_id, 'event_summary': summary,
            'ended_minutes_ago': ended_minutes_ago,
            'related_tasks': related_tasks, 'nudge_message': nudge_message}


async def get_meeting_context_for_dashboard() -> list[dict]:
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services import calendar as cal_service
        events = await cal_service.get_today_events()
    except Exception:
        return []
    result = []
    for event in events:
        event_id = event.get('id', '')
        related_tasks = await tasks_for_meeting(event_id)
        result.append({'event': event, 'related_tasks': related_tasks})
    return result

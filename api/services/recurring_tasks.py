"""Recurring tasks service.

Stores "task routines" that regenerate themselves on a schedule. A routine
is a task template plus a human-readable schedule. When a routine is due,
the service creates a fresh task via ostk and stamps the routine with the
time it last spawned so it does not fire twice in the same window.

Public surface:
- ``RECURRING_TASKS_PATH`` -- module-level Path constant (data-safety check)
- ``add_rule(rule)``, ``list_rules()``, ``remove_rule(rule_id)``,
  ``toggle_rule(rule_id)`` -- CRUD
- ``due_rules(now=None)`` -- list of rules that should spawn now
- ``spawn_due_tasks(now=None)`` -- create tasks for due rules
- ``recurring_tasks_store`` -- singleton instance

Schedule shapes (all plain language, no cron):

  daily:   {"kind": "daily", "days": [0,1,2,3,4]}
            days is a list of weekdays (Monday=0 .. Sunday=6). Omit or
            pass [0..6] for "every day".

  weekly:  {"kind": "weekly", "days": [0]}
            days is a list of weekdays the rule fires on. Same numbering
            as daily. This exists as a separate kind so the UI can show
            a "pick which days of the week" picker.

  monthly: {"kind": "monthly", "day_of_month": 1}
            day_of_month is 1..31. If a month does not have that day
            (e.g. Feb 30), the rule fires on the last day of the month.

The store is a list of rule objects at ~/.youros/recurring_tasks.json. We
keep the file outside the repo so git pull never clobbers user data.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text
from services.youros_paths import youros_home

RECURRING_TASKS_PATH = youros_home() / "recurring_tasks.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _normalise_template(template: dict) -> dict:
    """Make sure a template has the fields ostk.add_task expects."""
    return {
        "title": str(template.get("title", "")).strip(),
        "description": str(template.get("description", "")).strip(),
        "priority": str(template.get("priority", "P1")).strip() or "P1",
        "ac": str(template.get("ac", "")).strip(),
    }


def _normalise_schedule(schedule: dict) -> dict:
    """Coerce a schedule dict into a canonical shape we can reason about."""
    kind = str(schedule.get("kind", "daily")).strip().lower()
    if kind not in ("daily", "weekly", "monthly"):
        kind = "daily"

    if kind in ("daily", "weekly"):
        raw_days = schedule.get("days")
        if raw_days is None:
            days = list(range(7)) if kind == "daily" else []
        else:
            days = sorted({int(d) for d in raw_days if 0 <= int(d) <= 6})
            if not days and kind == "daily":
                days = list(range(7))
        return {"kind": kind, "days": days}

    # monthly
    try:
        day_of_month = int(schedule.get("day_of_month", 1))
    except (TypeError, ValueError):
        day_of_month = 1
    day_of_month = max(1, min(31, day_of_month))
    return {"kind": "monthly", "day_of_month": day_of_month}


def _effective_day_of_month(day_of_month: int, year: int, month: int) -> int:
    """Clamp ``day_of_month`` to the actual last day of ``year``/``month``."""
    last_day = calendar.monthrange(year, month)[1]
    return min(day_of_month, last_day)


def _is_rule_due(rule: dict, now: datetime) -> bool:
    """Decide whether ``rule`` should spawn a task right now.

    A rule is due when:
    1. It is active.
    2. The current weekday (daily/weekly) or day-of-month (monthly) matches.
    3. It has not already spawned a task on the current calendar day (UTC).
    """
    if not rule.get("active", True):
        return False

    schedule = rule.get("schedule") or {}
    kind = schedule.get("kind")

    now_utc = now.astimezone(timezone.utc)
    today = now_utc.date()

    last_spawned = _parse_iso(rule.get("last_spawned_at"))
    if last_spawned is not None and last_spawned.astimezone(timezone.utc).date() == today:
        # Already fired today, don't spawn again.
        return False

    if kind in ("daily", "weekly"):
        days = schedule.get("days") or []
        return now_utc.weekday() in days

    if kind == "monthly":
        dom = int(schedule.get("day_of_month", 1))
        effective = _effective_day_of_month(dom, now_utc.year, now_utc.month)
        return now_utc.day == effective

    return False


class RecurringTasksStore:
    """CRUD plus scheduling logic for recurring tasks."""

    def _ensure_exists(self) -> None:
        RECURRING_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not RECURRING_TASKS_PATH.exists():
            atomic_write_text(RECURRING_TASKS_PATH, "[]")

    def list_rules(self) -> list[dict]:
        self._ensure_exists()
        try:
            data = json.loads(RECURRING_TASKS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        return data

    def _save(self, rules: list[dict]) -> None:
        self._ensure_exists()
        atomic_write_json(RECURRING_TASKS_PATH, rules)

    def add_rule(self, rule: dict) -> dict:
        """Create a new rule. Returns the stored rule with its generated id."""
        rules = self.list_rules()
        new_rule = {
            "id": f"rec-{uuid.uuid4().hex[:8]}",
            "task_template": _normalise_template(rule.get("task_template") or {}),
            "schedule": _normalise_schedule(rule.get("schedule") or {}),
            "last_spawned_at": None,
            "active": bool(rule.get("active", True)),
            "created_at": _iso(_utcnow()),
        }
        rules.append(new_rule)
        self._save(rules)
        return new_rule

    def remove_rule(self, rule_id: str) -> bool:
        rules = self.list_rules()
        updated = [r for r in rules if r.get("id") != rule_id]
        if len(updated) == len(rules):
            return False
        self._save(updated)
        return True

    def toggle_rule(self, rule_id: str) -> Optional[dict]:
        rules = self.list_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule["active"] = not rule.get("active", True)
                self._save(rules)
                return rule
        return None

    def mark_spawned(self, rule_id: str, when: datetime) -> None:
        rules = self.list_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule["last_spawned_at"] = _iso(when)
                self._save(rules)
                return

    def due_rules(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or _utcnow()
        return [r for r in self.list_rules() if _is_rule_due(r, now)]


recurring_tasks_store = RecurringTasksStore()


# ---------------------------------------------------------------------------
# Public helpers (module-level wrappers around the singleton)
# ---------------------------------------------------------------------------


def list_rules() -> list[dict]:
    return recurring_tasks_store.list_rules()


def add_rule(rule: dict) -> dict:
    return recurring_tasks_store.add_rule(rule)


def remove_rule(rule_id: str) -> bool:
    return recurring_tasks_store.remove_rule(rule_id)


def toggle_rule(rule_id: str) -> Optional[dict]:
    return recurring_tasks_store.toggle_rule(rule_id)


def due_rules(now: Optional[datetime] = None) -> list[dict]:
    return recurring_tasks_store.due_rules(now)


async def spawn_due_tasks(now: Optional[datetime] = None) -> list[dict]:
    """Create a fresh ostk task for every rule that is due.

    Returns a list of {"rule_id", "title", "ok", "error"} dicts so callers
    (tests, the manual trigger endpoint) can see what happened.
    """
    # Import here so tests can patch services.ostk.ostk without importing
    # the real singleton up front.
    from services.ostk import ostk, OstkError

    now = now or _utcnow()
    results: list[dict] = []
    for rule in recurring_tasks_store.due_rules(now):
        template = rule.get("task_template") or {}
        title = template.get("title", "").strip()
        if not title:
            continue
        try:
            await ostk.add_task(
                title=title,
                priority=template.get("priority", "P1"),
                description=template.get("description", ""),
                ac=template.get("ac", ""),
            )
            recurring_tasks_store.mark_spawned(rule["id"], now)
            results.append({"rule_id": rule["id"], "title": title, "ok": True})
        except OstkError as exc:
            results.append({
                "rule_id": rule["id"],
                "title": title,
                "ok": False,
                "error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "rule_id": rule["id"],
                "title": title,
                "ok": False,
                "error": str(exc),
            })
    return results


__all__ = [
    "RECURRING_TASKS_PATH",
    "RecurringTasksStore",
    "recurring_tasks_store",
    "add_rule",
    "list_rules",
    "remove_rule",
    "toggle_rule",
    "due_rules",
    "spawn_due_tasks",
]

"""Tests for the recurring tasks service and router.

Covers:
- add_rule / list_rules / remove_rule / toggle_rule CRUD
- due_rules() identifies daily / weekly / monthly rules correctly
- spawn_due_tasks() calls ostk.add_task for due rules
- Rules are not spawned twice on the same day
- Inactive rules are skipped
- The /api/recurring router round-trips a rule end to end
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Point the recurring tasks store at a tmp file and return the module."""
    import services.recurring_tasks as rt

    store_path = tmp_path / "recurring_tasks.json"
    monkeypatch.setattr(rt, "RECURRING_TASKS_PATH", store_path)
    # Also patch the instance's path indirectly: the store reads the module
    # constant through the module global, so patching the module-level name
    # is enough. Nothing else to do here.
    yield rt


def _daily_rule(title: str = "Daily standup") -> dict:
    return {
        "task_template": {
            "title": title,
            "description": "What did I do? What's next? Blockers?",
            "priority": "P1",
            "ac": "Update posted.",
        },
        "schedule": {"kind": "daily", "days": [0, 1, 2, 3, 4]},
    }


def _weekly_rule(title: str = "Weekly review", days=None) -> dict:
    return {
        "task_template": {
            "title": title,
            "description": "Reflect on the week.",
            "priority": "P2",
            "ac": "Notes written.",
        },
        "schedule": {"kind": "weekly", "days": days if days is not None else [4]},
    }


def _monthly_rule(title: str = "Invoice clients", day: int = 1) -> dict:
    return {
        "task_template": {
            "title": title,
            "description": "Send invoices.",
            "priority": "P0",
            "ac": "All invoices sent.",
        },
        "schedule": {"kind": "monthly", "day_of_month": day},
    }


# ---------------------------------------------------------------------------
# 1. CRUD: add / list / remove / toggle
# ---------------------------------------------------------------------------


def test_add_rule_persists_and_returns_id(temp_store):
    rt = temp_store
    rule = rt.add_rule(_daily_rule())

    assert rule["id"].startswith("rec-")
    assert rule["active"] is True
    assert rule["last_spawned_at"] is None
    assert rule["schedule"]["kind"] == "daily"

    listed = rt.list_rules()
    assert len(listed) == 1
    assert listed[0]["id"] == rule["id"]


def test_list_rules_is_empty_when_store_missing(temp_store, tmp_path):
    rt = temp_store
    # Freshly patched path, file does not yet exist.
    assert not rt.RECURRING_TASKS_PATH.exists()
    assert rt.list_rules() == []


def test_remove_rule_deletes_and_returns_false_for_missing(temp_store):
    rt = temp_store
    r1 = rt.add_rule(_daily_rule("one"))
    r2 = rt.add_rule(_daily_rule("two"))

    assert rt.remove_rule(r1["id"]) is True
    remaining = rt.list_rules()
    assert len(remaining) == 1
    assert remaining[0]["id"] == r2["id"]

    assert rt.remove_rule("rec-does-not-exist") is False


def test_toggle_rule_flips_active(temp_store):
    rt = temp_store
    r = rt.add_rule(_daily_rule())
    assert r["active"] is True

    toggled = rt.toggle_rule(r["id"])
    assert toggled is not None
    assert toggled["active"] is False

    toggled_again = rt.toggle_rule(r["id"])
    assert toggled_again["active"] is True

    # Unknown id returns None, no exception.
    assert rt.toggle_rule("rec-nope") is None


# ---------------------------------------------------------------------------
# 2. due_rules() for daily / weekly / monthly
# ---------------------------------------------------------------------------


def test_due_rules_daily_fires_on_listed_weekday(temp_store):
    rt = temp_store
    # 2026-04-08 is a Wednesday (weekday 2).
    rt.add_rule(_daily_rule())
    wednesday = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)
    due = rt.due_rules(now=wednesday)
    assert len(due) == 1


def test_due_rules_daily_skips_unlisted_weekday(temp_store):
    rt = temp_store
    # Weekend-only rule, asked on a Wednesday.
    rt.add_rule({
        "task_template": {"title": "Weekend chore", "description": "", "priority": "P2", "ac": ""},
        "schedule": {"kind": "daily", "days": [5, 6]},  # Sat, Sun
    })
    wednesday = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)
    assert rt.due_rules(now=wednesday) == []


def test_due_rules_weekly_matches_only_target_day(temp_store):
    rt = temp_store
    # Weekly rule every Friday (weekday 4).
    rt.add_rule(_weekly_rule(days=[4]))
    friday = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    thursday = datetime(2026, 4, 9, 9, 0, tzinfo=timezone.utc)

    assert len(rt.due_rules(now=friday)) == 1
    assert rt.due_rules(now=thursday) == []


def test_due_rules_monthly_matches_target_day_of_month(temp_store):
    rt = temp_store
    rt.add_rule(_monthly_rule(day=15))
    mid_month = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    other_day = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)

    assert len(rt.due_rules(now=mid_month)) == 1
    assert rt.due_rules(now=other_day) == []


def test_due_rules_monthly_clamps_to_last_day_of_short_month(temp_store):
    rt = temp_store
    # "31st of every month" on Feb 28 (Feb 2026 has 28 days).
    rt.add_rule(_monthly_rule(day=31))
    feb_last = datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
    feb_middle = datetime(2026, 2, 15, 9, 0, tzinfo=timezone.utc)

    assert len(rt.due_rules(now=feb_last)) == 1
    assert rt.due_rules(now=feb_middle) == []


# ---------------------------------------------------------------------------
# 3. spawn_due_tasks() calls ostk.add_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_due_tasks_creates_ostk_task(temp_store):
    rt = temp_store
    rule = rt.add_rule(_daily_rule("Hydrate"))
    wednesday = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)

    fake_ostk = AsyncMock()
    fake_ostk.add_task = AsyncMock(return_value="ok")

    with patch("services.ostk.ostk", fake_ostk):
        results = await rt.spawn_due_tasks(now=wednesday)

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["title"] == "Hydrate"

    fake_ostk.add_task.assert_awaited_once()
    kwargs = fake_ostk.add_task.await_args.kwargs
    assert kwargs["title"] == "Hydrate"
    assert kwargs["priority"] == "P1"

    # The rule is now stamped with a last_spawned_at so it does not refire.
    stored = rt.list_rules()[0]
    assert stored["last_spawned_at"] is not None
    assert stored["id"] == rule["id"]


@pytest.mark.asyncio
async def test_rules_are_not_spawned_twice_on_same_day(temp_store):
    rt = temp_store
    rt.add_rule(_daily_rule())
    wednesday_morning = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)
    wednesday_afternoon = datetime(2026, 4, 8, 15, 0, tzinfo=timezone.utc)

    fake_ostk = AsyncMock()
    fake_ostk.add_task = AsyncMock(return_value="ok")

    with patch("services.ostk.ostk", fake_ostk):
        results_am = await rt.spawn_due_tasks(now=wednesday_morning)
        results_pm = await rt.spawn_due_tasks(now=wednesday_afternoon)

    assert len(results_am) == 1
    assert results_pm == []  # Already fired today.
    assert fake_ostk.add_task.await_count == 1


@pytest.mark.asyncio
async def test_inactive_rules_are_skipped(temp_store):
    rt = temp_store
    rule = rt.add_rule(_daily_rule())
    rt.toggle_rule(rule["id"])  # Now inactive.
    wednesday = datetime(2026, 4, 8, 9, 0, tzinfo=timezone.utc)

    fake_ostk = AsyncMock()
    fake_ostk.add_task = AsyncMock(return_value="ok")

    with patch("services.ostk.ostk", fake_ostk):
        results = await rt.spawn_due_tasks(now=wednesday)

    assert results == []
    fake_ostk.add_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Router round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_create_list_delete_round_trip(temp_store, client):
    # Create via POST.
    body = {
        "task_template": {
            "title": "Weekly status update",
            "description": "Write the update and post it.",
            "priority": "P1",
            "ac": "Update posted in #general.",
        },
        "schedule": {"kind": "weekly", "days": [4]},
        "active": True,
    }
    r = await client.post("/api/recurring", json=body)
    assert r.status_code == 200
    created = r.json()["rule"]
    assert created["id"].startswith("rec-")

    # List.
    r = await client.get("/api/recurring")
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert len(rules) == 1
    assert rules[0]["task_template"]["title"] == "Weekly status update"

    # Toggle to inactive.
    r = await client.patch(f"/api/recurring/{created['id']}", json={})
    assert r.status_code == 200
    assert r.json()["rule"]["active"] is False

    # Delete.
    r = await client.delete(f"/api/recurring/{created['id']}")
    assert r.status_code == 200

    r = await client.get("/api/recurring")
    assert r.json()["rules"] == []


@pytest.mark.asyncio
async def test_router_rejects_empty_title(temp_store, client):
    body = {
        "task_template": {"title": "   ", "description": "", "priority": "P1", "ac": ""},
        "schedule": {"kind": "daily", "days": [0, 1, 2, 3, 4]},
    }
    r = await client.post("/api/recurring", json=body)
    assert r.status_code == 400

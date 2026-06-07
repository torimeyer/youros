"""Tests for the smart task suggestions service.

Each rule is exercised both positively (the rule fires when its
condition is met) and negatively (the rule does NOT fire when its
condition is not met). The audit log and the dismissed-suggestion file
are both swapped out for tmp paths so the tests never touch real data.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from services import task_suggestions


# --- Helpers -----------------------------------------------------------


def _ts(days_ago: int) -> str:
    """Build an ISO timestamp ``days_ago`` days before now."""
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task(
    task_id: str,
    *,
    title: str = "Sample task",
    priority: str = "P1",
    status: str = "open",
    created_days_ago: int = 1,
    description: str = "",
    tags: Optional[list[str]] = None,
    blocks: Optional[list[str]] = None,
    depends_on: Optional[list[str]] = None,
) -> dict:
    out: dict = {
        "id": task_id,
        "title": title,
        "priority": priority,
        "status": status,
        "created_at": _ts(created_days_ago),
        "description": description,
        "tags": tags or [],
    }
    if blocks:
        out["blocks"] = blocks
    if depends_on:
        out["depends_on"] = depends_on
    return out


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    """Redirect every on-disk path the service touches to a tmp dir.

    This means the test never reads the real audit.jsonl and never writes
    to ~/.youros/dismissed_suggestions.json.
    """
    fake_audit = tmp_path / "audit.jsonl"
    fake_dismissed = tmp_path / "dismissed.json"
    monkeypatch.setattr(task_suggestions, "AUDIT_PATH", fake_audit)
    monkeypatch.setattr(task_suggestions, "DISMISSED_PATH", fake_dismissed)
    task_suggestions.invalidate_cache()
    yield
    task_suggestions.invalidate_cache()


def _patch_tasks(tasks: list[dict]):
    """Return an AsyncMock that returns the given task list."""
    return patch(
        "services.task_suggestions.ostk.list_tasks",
        new=AsyncMock(return_value=tasks),
    )


def _write_audit(audit_path: Path, events: list[dict]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


# --- analyze_tasks -----------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_tasks_computes_age_and_activity(tmp_path):
    tasks = [_task("→001", created_days_ago=10)]
    audit_path = task_suggestions.AUDIT_PATH
    _write_audit(audit_path, [
        {"event": "task.added", "id": "→001", "timestamp": _ts(10)},
        {"event": "needle.activated", "needle": "→001", "timestamp": _ts(2)},
    ])

    with _patch_tasks(tasks):
        features = await task_suggestions.analyze_tasks()

    assert len(features) == 1
    f = features[0]
    assert f["id"] == "→001"
    assert f["age_days"] == 10
    assert f["num_audit_events"] == 2
    assert f["days_since_activity"] == 2
    assert f["has_recent_activity"] is True


@pytest.mark.asyncio
async def test_analyze_tasks_empty_when_ostk_has_nothing():
    with _patch_tasks([]):
        features = await task_suggestions.analyze_tasks()
    assert features == []


# --- Stale P0 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_p0_rule_fires_after_14_days():
    tasks = [_task("→010", priority="P0", created_days_ago=20)]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→010", "timestamp": _ts(20)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    types = {s["type"] for s in items}
    assert "stale_p0" in types


@pytest.mark.asyncio
async def test_stale_p0_rule_does_not_fire_with_recent_activity():
    tasks = [_task("→010", priority="P0", created_days_ago=20)]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→010", "timestamp": _ts(20)},
        {"event": "needle.edited", "id": "→010", "timestamp": _ts(1)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "stale_p0" for s in items)


@pytest.mark.asyncio
async def test_stale_p0_long_rule_fires_after_30_days():
    tasks = [_task("→011", priority="P0", created_days_ago=40)]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→011", "timestamp": _ts(40)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    long_p0 = [s for s in items if s["type"] == "stale_p0_long"]
    assert len(long_p0) == 1
    assert long_p0[0]["suggested_action"] == "close_task"


# --- Hidden bottleneck -------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_bottleneck_rule_fires_for_p2_blocking_three():
    tasks = [
        _task("→020", priority="P2", blocks=["→021", "→022", "→023"]),
        _task("→021", priority="P1"),
        _task("→022", priority="P1"),
        _task("→023", priority="P1"),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    bottlenecks = [s for s in items if s["type"] == "hidden_bottleneck"]
    assert len(bottlenecks) == 1
    assert bottlenecks[0]["task_id"] == "→020"
    assert bottlenecks[0]["suggested_action"] == "change_priority"
    assert bottlenecks[0]["suggested_action_payload"]["new_priority"] == "P0"


@pytest.mark.asyncio
async def test_hidden_bottleneck_rule_does_not_fire_for_p2_blocking_two():
    tasks = [
        _task("→020", priority="P2", blocks=["→021", "→022"]),
        _task("→021"),
        _task("→022"),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "hidden_bottleneck" for s in items)


# --- Forgotten ---------------------------------------------------------


@pytest.mark.asyncio
async def test_forgotten_rule_fires_for_old_zero_activity_no_description():
    tasks = [_task("→030", priority="P2", created_days_ago=45, description="")]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→030", "timestamp": _ts(45)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    forgotten = [s for s in items if s["type"] == "forgotten"]
    assert len(forgotten) == 1
    assert forgotten[0]["suggested_action"] == "close_task"


@pytest.mark.asyncio
async def test_forgotten_rule_does_not_fire_when_description_present():
    tasks = [_task("→030", created_days_ago=45, description="Real context")]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→030", "timestamp": _ts(45)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "forgotten" for s in items)


# --- Missing description -----------------------------------------------


@pytest.mark.asyncio
async def test_missing_description_rule_fires_for_p1_with_no_description():
    tasks = [_task("→040", priority="P1", description="")]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    missing = [s for s in items if s["type"] == "missing_description"]
    assert len(missing) == 1


@pytest.mark.asyncio
async def test_missing_description_rule_does_not_fire_for_p2():
    tasks = [_task("→040", priority="P2", description="")]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "missing_description" for s in items)


# --- Too many P0s ------------------------------------------------------


@pytest.mark.asyncio
async def test_too_many_p0s_rule_demotes_oldest_until_five_remain():
    tasks = [
        _task(f"→05{i}", priority="P0", created_days_ago=10 - i, description="x")
        for i in range(7)
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": t["id"], "timestamp": t["created_at"]}
        for t in tasks
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    too_many = [s for s in items if s["type"] == "too_many_p0s"]
    # 7 P0s - 5 allowed = 2 demote suggestions.
    assert len(too_many) == 2
    # The two oldest tasks are →050 (10 days) and →051 (9 days).
    demoted_ids = {s["task_id"] for s in too_many}
    assert demoted_ids == {"→050", "→051"}


@pytest.mark.asyncio
async def test_too_many_p0s_rule_does_not_fire_at_five():
    tasks = [
        _task(f"→05{i}", priority="P0", description="x")
        for i in range(5)
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "too_many_p0s" for s in items)


# --- Orphaned cluster --------------------------------------------------


@pytest.mark.asyncio
async def test_orphaned_cluster_rule_fires_for_three_unlinked_with_same_label():
    tasks = [
        _task("→060", description="x", tags=["lego-app"]),
        _task("→061", description="x", tags=["lego-app"]),
        _task("→062", description="x", tags=["lego-app"]),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    orphan = [s for s in items if s["type"] == "orphaned_cluster"]
    assert len(orphan) == 1
    assert orphan[0]["suggested_action"] == "link_tasks"


@pytest.mark.asyncio
async def test_orphaned_cluster_rule_does_not_fire_when_already_linked():
    tasks = [
        _task("→060", description="x", tags=["lego-app"], blocks=["→061"]),
        _task("→061", description="x", tags=["lego-app"]),
        _task("→062", description="x", tags=["lego-app"]),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "orphaned_cluster" for s in items)


# --- Long blocked ------------------------------------------------------


@pytest.mark.asyncio
async def test_long_blocked_rule_fires_when_blocker_is_old_and_open():
    tasks = [
        _task("→070", priority="P1", description="x", depends_on=["→071"]),
        _task("→071", priority="P1", description="x", created_days_ago=20),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    long_blocked = [s for s in items if s["type"] == "long_blocked"]
    assert len(long_blocked) == 1
    assert long_blocked[0]["task_id"] == "→070"


@pytest.mark.asyncio
async def test_long_blocked_rule_does_not_fire_for_fresh_blocker():
    tasks = [
        _task("→070", priority="P1", description="x", depends_on=["→071"]),
        _task("→071", priority="P1", description="x", created_days_ago=2),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["type"] == "long_blocked" for s in items)


# --- Empty / dismissals / apply ----------------------------------------


@pytest.mark.asyncio
async def test_empty_task_list_returns_empty_suggestions():
    with _patch_tasks([]):
        items = await task_suggestions.suggestions(force_refresh=True)
    assert items == []


@pytest.mark.asyncio
async def test_dismissed_suggestion_does_not_come_back_after_refresh():
    tasks = [_task("→010", priority="P0", created_days_ago=20)]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→010", "timestamp": _ts(20)},
    ])

    with _patch_tasks(tasks):
        items = await task_suggestions.suggestions(force_refresh=True)
        assert any(s["type"] == "stale_p0" for s in items)
        target = next(s for s in items if s["type"] == "stale_p0")
        task_suggestions.dismiss_suggestion(target["id"])
        again = await task_suggestions.suggestions(force_refresh=True)

    assert not any(s["id"] == target["id"] for s in again)


@pytest.mark.asyncio
async def test_apply_change_priority_calls_ostk_update():
    tasks = [
        _task("→020", priority="P2", description="x", blocks=["→021", "→022", "→023"]),
        _task("→021"),
        _task("→022"),
        _task("→023"),
    ]
    _write_audit(task_suggestions.AUDIT_PATH, [])

    fake_update = AsyncMock(return_value="updated →020 priority to P0")
    with _patch_tasks(tasks), patch(
        "services.task_suggestions.ostk.update_task_priority",
        new=fake_update,
    ):
        items = await task_suggestions.suggestions(force_refresh=True)
        bottleneck = next(s for s in items if s["type"] == "hidden_bottleneck")
        result = await task_suggestions.apply_suggestion(bottleneck["id"])

    assert result["ok"] is True
    fake_update.assert_called_once_with("→020", "P0")


@pytest.mark.asyncio
async def test_apply_close_task_calls_ostk_close():
    tasks = [_task("→011", priority="P0", created_days_ago=40)]
    _write_audit(task_suggestions.AUDIT_PATH, [
        {"event": "task.added", "id": "→011", "timestamp": _ts(40)},
    ])

    fake_close = AsyncMock(return_value="closed →011")
    with _patch_tasks(tasks), patch(
        "services.task_suggestions.ostk.close_task",
        new=fake_close,
    ):
        items = await task_suggestions.suggestions(force_refresh=True)
        long_p0 = next(s for s in items if s["type"] == "stale_p0_long")
        result = await task_suggestions.apply_suggestion(long_p0["id"])

    assert result["ok"] is True
    fake_close.assert_called_once_with("→011")

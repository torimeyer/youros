"""Tests for routers/theme_rollup.py (GET /api/themes/rollup, spec S009 Track C, →2618).

Covers:
  - risk helpers: overdue / blocked / quiet / none, and their precedence
  - build_rollup math: tasks and projects grouped into themes, catch-all bucket,
    unknown tags kept visible, bucket risk is the worst row
  - GET /api/themes/rollup empty state: no pillars, no tags, no Jira ->
    single catch-all bucket, no errors
  - risk flags surface per row over HTTP
  - Jira not connected -> local rollup still returned with an empty,
    disconnected Jira section (degrade, never fail)
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _task(id="→1", title="A task", status="open", **extra):
    base = {
        "id": id,
        "title": title,
        "status": status,
        "created_at": _iso(NOW - timedelta(days=1)),
    }
    base.update(extra)
    return base


def _project(name="proj", **extra):
    base = {
        "name": name,
        "pillar": None,
        "last_modified": _iso(NOW - timedelta(days=1)),
    }
    base.update(extra)
    return base


# ── risk helpers ─────────────────────────────────────────────────────────────

def test_task_risk_overdue():
    from routers.theme_rollup import task_risk

    t = _task(due=_iso(NOW - timedelta(days=2)))
    assert task_risk(t, open_ids=set(), now=NOW) == "overdue"


def test_task_risk_blocked_by_open_task():
    from routers.theme_rollup import task_risk

    t = _task(id="→2", depends_on=["→1"])
    assert task_risk(t, open_ids={"→1", "→2"}, now=NOW) == "blocked"


def test_task_risk_not_blocked_when_blocker_closed():
    from routers.theme_rollup import task_risk

    # →1 is not in the open set (already closed), so it is not an open blocker.
    t = _task(id="→2", depends_on=["→1"])
    assert task_risk(t, open_ids={"→2"}, now=NOW) == "none"


def test_task_risk_quiet_after_seven_days():
    from routers.theme_rollup import task_risk

    t = _task(created_at=_iso(NOW - timedelta(days=8)))
    assert task_risk(t, open_ids=set(), now=NOW) == "quiet"


def test_task_risk_none_when_fresh():
    from routers.theme_rollup import task_risk

    assert task_risk(_task(), open_ids=set(), now=NOW) == "none"


def test_task_risk_overdue_beats_blocked():
    from routers.theme_rollup import task_risk

    t = _task(id="→2", due=_iso(NOW - timedelta(days=1)), depends_on=["→1"])
    assert task_risk(t, open_ids={"→1"}, now=NOW) == "overdue"


def test_task_risk_survives_garbage_dates():
    from routers.theme_rollup import task_risk

    t = _task(due="not-a-date", created_at="also-not-a-date")
    assert task_risk(t, open_ids=set(), now=NOW) == "none"


def test_project_risk_quiet_after_seven_days():
    from routers.theme_rollup import project_risk

    p = _project(last_modified=_iso(NOW - timedelta(days=9)))
    assert project_risk(p, now=NOW) == "quiet"


def test_project_risk_none_when_fresh():
    from routers.theme_rollup import project_risk

    assert project_risk(_project(), now=NOW) == "none"


# ── rollup math ──────────────────────────────────────────────────────────────

def test_build_rollup_groups_by_theme_with_catch_all():
    from routers.theme_rollup import build_rollup

    tasks = [_task(id="→1"), _task(id="→2"), _task(id="→3")]
    projects = [_project(name="alpha", pillar="Growth"), _project(name="beta")]
    task_pillars = {"→1": "Growth", "→2": "Trust"}

    out = build_rollup(tasks, projects, ["Growth", "Trust"], task_pillars, now=NOW)

    themes = out["themes"]
    assert [t["name"] for t in themes] == ["Growth", "Trust", None]

    growth = themes[0]
    assert [p["name"] for p in growth["projects"]] == ["alpha"]
    assert [t["id"] for t in growth["tasks"]] == ["→1"]
    assert growth["project_count"] == 1
    assert growth["task_count"] == 1

    trust = themes[1]
    assert trust["task_count"] == 1
    assert trust["project_count"] == 0

    catch_all = themes[-1]
    assert catch_all["name"] is None
    assert [t["id"] for t in catch_all["tasks"]] == ["→3"]
    assert [p["name"] for p in catch_all["projects"]] == ["beta"]


def test_build_rollup_keeps_unknown_tags_visible():
    # A tag that is no longer in the org list still gets its own bucket,
    # so renaming the org list never hides tagged work.
    from routers.theme_rollup import build_rollup

    out = build_rollup([_task(id="→9")], [], ["Growth"], {"→9": "Sunset"}, now=NOW)
    assert [t["name"] for t in out["themes"]] == ["Growth", "Sunset", None]


def test_build_rollup_empty_inputs_single_bucket():
    from routers.theme_rollup import build_rollup

    out = build_rollup([], [], [], {}, now=NOW)
    themes = out["themes"]
    assert len(themes) == 1
    assert themes[0]["name"] is None
    assert themes[0]["projects"] == []
    assert themes[0]["tasks"] == []
    assert themes[0]["risk"] == "none"


def test_build_rollup_bucket_risk_is_worst_row():
    from routers.theme_rollup import build_rollup

    tasks = [
        _task(id="→1", due=_iso(NOW - timedelta(days=1))),          # overdue
        _task(id="→2", created_at=_iso(NOW - timedelta(days=10))),  # quiet
    ]
    out = build_rollup(tasks, [], [], {"→1": "Growth", "→2": "Growth"}, now=NOW)

    growth = out["themes"][0]
    assert growth["name"] == "Growth"
    assert growth["risk"] == "overdue"
    risk_by_id = {t["id"]: t["risk"] for t in growth["tasks"]}
    assert risk_by_id == {"→1": "overdue", "→2": "quiet"}


# ── HTTP endpoint ────────────────────────────────────────────────────────────

def _endpoint_patches(tasks=None, projects=None, pillars=None, task_pillars=None,
                      jira_connected=False):
    return [
        patch("routers.theme_rollup._load_tasks",
              AsyncMock(return_value=list(tasks or []))),
        patch("routers.theme_rollup._load_projects",
              AsyncMock(return_value=list(projects or []))),
        patch("routers.theme_rollup._load_pillars",
              return_value=list(pillars or [])),
        patch("routers.theme_rollup._load_task_pillars",
              return_value=dict(task_pillars or {})),
        patch("routers.theme_rollup.atlassian.is_connected",
              return_value=jira_connected),
    ]


@pytest.mark.asyncio
async def test_rollup_empty_state_single_bucket_no_errors(client):
    """No pillars, no tags, no Jira: one catch-all bucket, jira disconnected."""
    with ExitStack() as stack:
        for p in _endpoint_patches():
            stack.enter_context(p)
        resp = await client.get("/api/themes/rollup")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["themes"]) == 1
    assert data["themes"][0]["name"] is None
    assert data["themes"][0]["projects"] == []
    assert data["themes"][0]["tasks"] == []
    assert data["jira"] == {"connected": False, "tickets": []}


@pytest.mark.asyncio
async def test_rollup_http_rows_carry_risk_flags(client):
    tasks = [
        _task(id="→1", title="Late one", due=_iso(NOW - timedelta(days=3))),
        _task(id="→2", title="Waiting one", depends_on=["→1"]),
    ]
    projects = [_project(name="dusty", pillar="Growth",
                         last_modified=_iso(NOW - timedelta(days=30)))]
    with ExitStack() as stack:
        for p in _endpoint_patches(tasks=tasks, projects=projects,
                                   pillars=["Growth"],
                                   task_pillars={"→1": "Growth", "→2": "Growth"}):
            stack.enter_context(p)
        resp = await client.get("/api/themes/rollup")

    assert resp.status_code == 200
    growth = resp.json()["themes"][0]
    assert growth["name"] == "Growth"
    risk_by_id = {t["id"]: t["risk"] for t in growth["tasks"]}
    assert risk_by_id["→1"] == "overdue"
    assert risk_by_id["→2"] == "blocked"
    assert growth["projects"][0]["risk"] == "quiet"
    assert growth["risk"] == "overdue"


@pytest.mark.asyncio
async def test_rollup_jira_not_connected_still_returns_local_rollup(client):
    """Zero Atlassian configuration: the local rollup works untouched."""
    tasks = [_task(id="→1")]
    with ExitStack() as stack:
        for p in _endpoint_patches(tasks=tasks, pillars=["Growth"],
                                   task_pillars={"→1": "Growth"},
                                   jira_connected=False):
            stack.enter_context(p)
        resp = await client.get("/api/themes/rollup")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jira"]["connected"] is False
    assert data["jira"]["tickets"] == []
    assert data["themes"][0]["task_count"] == 1

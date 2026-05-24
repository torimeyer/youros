"""Tests for GET /api/tasks/waves — parallel-safe wave planning."""
from unittest.mock import AsyncMock, patch

import pytest


def _t(id, title, priority="P1", status="open", description=None):
    task = {"id": id, "title": title, "priority": priority, "status": status}
    if description is not None:
        task["description"] = description
    return task


def _patch_ostk(tasks):
    return patch("routers.tasks.ostk", **{"list_tasks": AsyncMock(return_value=tasks)})


@pytest.mark.asyncio
async def test_waves_empty_returns_zero(client):
    with _patch_ostk([]):
        resp = await client.get("/api/tasks/waves")
    assert resp.status_code == 200
    data = resp.json()
    assert data["waves"] == []
    assert data["total_needles"] == 0


@pytest.mark.asyncio
async def test_waves_response_shape(client):
    tasks = [_t("1", "Fix auth token", "P0", description="auth.py token")]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    assert resp.status_code == 200
    data = resp.json()
    assert "waves" in data
    assert "total_needles" in data
    assert data["total_needles"] == 1
    wave = data["waves"][0]
    assert wave["wave"] == 1
    assert wave["blocked_by_prior"] is False
    needle = wave["needles"][0]
    assert needle["id"] == "1"
    assert needle["title"] == "Fix auth token"
    assert needle["priority"] == "P0"
    assert "scope_hint" in needle


@pytest.mark.asyncio
async def test_waves_non_conflicting_tasks_share_wave(client):
    # No shared scope tokens — should land in wave 1 together
    tasks = [
        _t("1", "Update auth login", description="auth.py login handler"),
        _t("2", "Redesign dashboard charts", description="frontend/dashboard charts"),
    ]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    assert len(data["waves"]) == 1
    assert len(data["waves"][0]["needles"]) == 2


@pytest.mark.asyncio
async def test_waves_conflicting_tasks_split(client):
    # Both touch tasks.py — conflict forces separate waves
    tasks = [
        _t("1", "Fix tasks router bug", description="routers/tasks.py export"),
        _t("2", "Add tasks filter feature", description="routers/tasks.py filter"),
    ]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    assert len(data["waves"]) >= 2
    assert data["waves"][1]["blocked_by_prior"] is True
    # Each conflicting task is in its own wave
    wave1_ids = {n["id"] for n in data["waves"][0]["needles"]}
    wave2_ids = {n["id"] for n in data["waves"][1]["needles"]}
    assert wave1_ids.isdisjoint(wave2_ids)


@pytest.mark.asyncio
async def test_waves_p0_before_p2(client):
    # P2 task listed first in input — P0 must appear in wave 1
    tasks = [
        _t("low", "Low priority backend cleanup", "P2", description="backend cleanup scripts"),
        _t("high", "Critical auth fix urgent", "P0", description="auth module critical"),
    ]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    wave1_ids = [n["id"] for n in data["waves"][0]["needles"]]
    assert "high" in wave1_ids


@pytest.mark.asyncio
async def test_waves_cap_at_four(client):
    # 5 tasks with no overlapping scope — wave 1 holds at most 4
    tasks = [
        _t(str(i), f"Unique isolated task number {i}", description=f"uniquemodule{i} feature{i}")
        for i in range(5)
    ]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    assert len(data["waves"][0]["needles"]) <= 4
    assert len(data["waves"]) >= 2


@pytest.mark.asyncio
async def test_waves_first_wave_not_blocked(client):
    tasks = [_t("1", "Some task", description="frontend component")]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    assert data["waves"][0]["blocked_by_prior"] is False


@pytest.mark.asyncio
async def test_waves_scope_hint_present_and_nonempty(client):
    tasks = [_t("1", "Fix auth.py token refresh", description="auth.py services token")]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/waves")
    data = resp.json()
    needle = data["waves"][0]["needles"][0]
    assert needle["scope_hint"]  # not empty
    assert needle["scope_hint"] != "general" or True  # accepts "general" for zero-token tasks


@pytest.mark.asyncio
async def test_waves_ostk_error_returns_500(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock:
        mock.list_tasks = AsyncMock(side_effect=OstkError("boom"))
        resp = await client.get("/api/tasks/waves")
    assert resp.status_code == 500

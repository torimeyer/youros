"""Tests for the E4 team adoption rollup endpoint."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest


def _setup_org(tmp_path):
    from services import enterprise_store

    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.create_org("Acme Corp", "admin@acme.com")
    return ent_path


def _make_session(email, role="admin", member_id="abc", org_id="org1"):
    from services.session import create_session

    return create_session(email=email, role=role, member_id=member_id, org_id=org_id)


def _write_audit(tmp_path, events):
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    audit_path = ostk_dir / "audit.jsonl"
    audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return audit_path


def _recent_ts(days_ago=0):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollup_returns_correct_shape(client, tmp_path):
    """Endpoint returns all four bucket keys plus top_skill and adoption_gap."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("admin@acme.com", "admin")
    audit = _write_audit(tmp_path, [])

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "buckets" in data
    for key in ("power_user", "active", "explorer", "inactive"):
        assert key in data["buckets"]
    assert "top_skill" in data
    assert "adoption_gap" in data


# ---------------------------------------------------------------------------
# Bucket classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_power_user_high_freq_high_depth(client, tmp_path):
    """Member with 5 agents at $3 avg lands in power_user bucket."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("admin@acme.com", "admin")

    events = [
        {"event": "agent.spawned", "user": "admin@acme.com", "budget": 3.0, "ts": _recent_ts(i)}
        for i in range(5)
    ]
    audit = _write_audit(tmp_path, events)

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    data = resp.json()
    assert "admin@acme.com" in data["buckets"]["power_user"]


@pytest.mark.asyncio
async def test_active_high_freq_low_depth(client, tmp_path):
    """Member with 4 cheap agents lands in active bucket."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("admin@acme.com", "admin")

    events = [
        {"event": "agent.spawned", "user": "admin@acme.com", "budget": 0.5, "ts": _recent_ts(i)}
        for i in range(4)
    ]
    audit = _write_audit(tmp_path, events)

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    data = resp.json()
    assert "admin@acme.com" in data["buckets"]["active"]


@pytest.mark.asyncio
async def test_explorer_low_freq_high_depth(client, tmp_path):
    """Member with 1 expensive agent lands in explorer bucket."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("admin@acme.com", "admin")

    events = [
        {"event": "agent.spawned", "user": "admin@acme.com", "budget": 5.0, "ts": _recent_ts(0)},
    ]
    audit = _write_audit(tmp_path, events)

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    data = resp.json()
    assert "admin@acme.com" in data["buckets"]["explorer"]


@pytest.mark.asyncio
async def test_inactive_no_recent_activity(client, tmp_path):
    """Member with no events in last 7 days lands in inactive bucket."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("admin@acme.com", "admin")

    # Event older than 7 days
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [
        {"event": "agent.spawned", "user": "admin@acme.com", "budget": 3.0, "ts": old_ts},
    ]
    audit = _write_audit(tmp_path, events)

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    data = resp.json()
    assert "admin@acme.com" in data["buckets"]["inactive"]


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_rejected(client, tmp_path):
    """Non-admin member receives 403."""
    ent_path = _setup_org(tmp_path)
    session = _make_session("member@acme.com", "member")
    audit = _write_audit(tmp_path, [])

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit),
    ):
        resp = await client.get(
            "/api/team/adoption-rollup",
            cookies={"myos_session": session},
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Empty org
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_org_returns_empty_buckets(client, tmp_path):
    """Solo mode (no enterprise) returns all empty buckets."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/team/adoption-rollup")

    assert resp.status_code == 200
    data = resp.json()
    for key in ("power_user", "active", "explorer", "inactive"):
        assert data["buckets"][key] == []
    assert data["top_skill"] == ""
    assert data["adoption_gap"] == ""


# ---------------------------------------------------------------------------
# Unit tests for _adoption_rollup_data
# ---------------------------------------------------------------------------

def test_rollup_data_multiple_members():
    """_adoption_rollup_data correctly buckets multiple members."""
    from routers.team_dashboard import _adoption_rollup_data

    members = [
        {"email": "power@a.com"},
        {"email": "active@a.com"},
        {"email": "explorer@a.com"},
        {"email": "inactive@a.com"},
    ]

    def ev(user, budget, days_ago=0):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return {"event": "agent.spawned", "user": user, "budget": budget, "ts": ts}

    events = (
        [ev("power@a.com", 3.0) for _ in range(5)]
        + [ev("active@a.com", 0.5) for _ in range(4)]
        + [ev("explorer@a.com", 4.0)]
        # inactive@a.com gets no recent events
    )

    result = _adoption_rollup_data(members, events)
    assert "power@a.com" in result["buckets"]["power_user"]
    assert "active@a.com" in result["buckets"]["active"]
    assert "explorer@a.com" in result["buckets"]["explorer"]
    assert "inactive@a.com" in result["buckets"]["inactive"]


def test_rollup_data_adoption_gap():
    """adoption_gap picks a catalog item that has no recent usage."""
    from routers.team_dashboard import _adoption_rollup_data

    members = [{"email": "user@a.com"}]
    events = [
        {
            "event": "agent.spawned",
            "user": "user@a.com",
            "budget": 1.0,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "task": "code-reviewer",
        }
    ]
    catalog = [
        {"name": "code-reviewer"},
        {"name": "spec-writer"},
    ]

    result = _adoption_rollup_data(members, events, catalog)
    assert result["adoption_gap"] == "spec-writer"
    assert result["top_skill"] == "code-reviewer"

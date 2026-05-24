"""Tests for team dashboard and invite flow.

Covers the team dashboard endpoint, invite creation, invite acceptance,
expired invites, duplicate invites, and listing pending invites.
"""

import json
import time
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_org(tmp_path):
    """Create an enterprise org and return the enterprise.json path."""
    from services import enterprise_store

    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.create_org("Acme Corp", "admin@acme.com")
    return ent_path


def _make_session_cookie(email, role="admin", member_id="abc", org_id="org1"):
    """Create a session token for use in test requests."""
    from services.session import create_session

    return create_session(email=email, role=role, member_id=member_id, org_id=org_id)


# ---------------------------------------------------------------------------
# Team dashboard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_returns_members_and_spend(client, tmp_path):
    """Admin gets members and spend data from the team dashboard."""
    ent_path = _setup_org(tmp_path)

    # Write a small audit file with one agent.spawned event
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    audit_path = ostk_dir / "audit.jsonl"
    audit_path.write_text(
        json.dumps({
            "event": "agent.spawned",
            "user": "admin@acme.com",
            "budget": 2.5,
            "ts": "2025-01-01T00:00:00Z",
        }) + "\n"
    )

    session = _make_session_cookie("admin@acme.com", "admin")

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit_path),
    ):
        resp = await client.get(
            "/api/team/dashboard",
            cookies={"myos_session": session},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_members"] == 1
    assert len(data["members"]) == 1
    assert data["members"][0]["email"] == "admin@acme.com"
    assert len(data["spend_by_user"]) >= 1

    admin_spend = next(
        (s for s in data["spend_by_user"] if s["email"] == "admin@acme.com"),
        None,
    )
    assert admin_spend is not None
    assert admin_spend["agent_count"] == 1
    assert admin_spend["total_budget"] == 2.5


@pytest.mark.asyncio
async def test_unknown_user_events_excluded_from_spend(client, tmp_path):
    """Audit events with no user field are not surfaced as 'unknown' rows."""
    ent_path = _setup_org(tmp_path)

    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    audit_path = ostk_dir / "audit.jsonl"
    audit_path.write_text(
        # One event with no user field (pre-onboarding / local session)
        json.dumps({
            "event": "agent.spawned",
            "budget": 99.0,
            "ts": "2025-01-01T00:00:00Z",
        }) + "\n" +
        # One event with a real user
        json.dumps({
            "event": "agent.spawned",
            "user": "admin@acme.com",
            "budget": 1.0,
            "ts": "2025-01-01T01:00:00Z",
        }) + "\n"
    )

    session = _make_session_cookie("admin@acme.com", "admin")

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
        patch("routers.costs.AUDIT_PATH", audit_path),
    ):
        resp = await client.get(
            "/api/team/dashboard",
            cookies={"myos_session": session},
        )

    assert resp.status_code == 200
    data = resp.json()
    emails = [s["email"] for s in data["spend_by_user"]]
    assert "unknown" not in emails
    assert len(data["spend_by_user"]) == 1
    assert data["spend_by_user"][0]["email"] == "admin@acme.com"


@pytest.mark.asyncio
async def test_dashboard_solo_mode_works(client, tmp_path):
    """Solo mode (no enterprise) returns empty data, not an error."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/team/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["members"] == []
    assert data["spend_by_user"] == []
    assert data["total_members"] == 0


# ---------------------------------------------------------------------------
# Invite creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_creates_pending_invite(client, tmp_path):
    """Admin can create an invite and it appears in pending invites."""
    ent_path = _setup_org(tmp_path)
    session = _make_session_cookie("admin@acme.com", "admin")

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp = await client.post(
            "/api/enterprise/invite",
            json={"email": "newuser@acme.com", "role": "member"},
            cookies={"myos_session": session},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "invite_url" in data
    assert data["email"] == "newuser@acme.com"
    assert "/invite/" in data["invite_url"]

    # Verify the invite is listed as pending
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp2 = await client.get(
            "/api/enterprise/invites",
            cookies={"myos_session": session},
        )

    assert resp2.status_code == 200
    invites = resp2.json()["invites"]
    assert len(invites) == 1
    assert invites[0]["email"] == "newuser@acme.com"


# ---------------------------------------------------------------------------
# Invite acceptance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invite_accept_adds_member_and_sets_cookie(client, tmp_path):
    """Accepting an invite adds the user as a member and sets a session cookie."""
    ent_path = _setup_org(tmp_path)

    # Create an invite directly in the store
    from services import enterprise_store

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.add_invite("newuser@acme.com", "member", "test-token-123")

    # Accept the invite (follow_redirects=False to inspect redirect response)
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp = await client.get(
            "/api/enterprise/invite/test-token-123",
            follow_redirects=False,
        )

    assert resp.status_code == 307
    assert "invite_accepted=true" in resp.headers.get("location", "")
    assert "myos_session" in resp.headers.get("set-cookie", "")

    # Verify the user was added as a member
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        members = enterprise_store.list_members()

    emails = [m["email"] for m in members]
    assert "newuser@acme.com" in emails


# ---------------------------------------------------------------------------
# Expired invite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_invite_rejected(client, tmp_path):
    """An expired invite returns 404."""
    ent_path = _setup_org(tmp_path)

    # Create an invite and manually expire it
    from services import enterprise_store

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.add_invite("old@acme.com", "member", "expired-token")

        # Manually set expiry to the past
        data = enterprise_store._load()
        data["invites"]["expired-token"]["expires_at"] = time.time() - 1
        enterprise_store._save(data)

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp = await client.get(
            "/api/enterprise/invite/expired-token",
            follow_redirects=False,
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Duplicate invite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_invite_rejected(client, tmp_path):
    """Cannot create a second invite for the same email while one is pending."""
    ent_path = _setup_org(tmp_path)
    session = _make_session_cookie("admin@acme.com", "admin")

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        # First invite
        resp1 = await client.post(
            "/api/enterprise/invite",
            json={"email": "dup@acme.com", "role": "member"},
            cookies={"myos_session": session},
        )
        assert resp1.status_code == 200

        # Second invite for same email
        resp2 = await client.post(
            "/api/enterprise/invite",
            json={"email": "dup@acme.com", "role": "member"},
            cookies={"myos_session": session},
        )
        assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# List pending invites
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pending_invites(client, tmp_path):
    """Pending invites endpoint returns all active invites."""
    ent_path = _setup_org(tmp_path)
    session = _make_session_cookie("admin@acme.com", "admin")

    from services import enterprise_store

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.add_invite("a@acme.com", "member", "tok-a")
        enterprise_store.add_invite("b@acme.com", "admin", "tok-b")

        resp = await client.get(
            "/api/enterprise/invites",
            cookies={"myos_session": session},
        )

    assert resp.status_code == 200
    invites = resp.json()["invites"]
    assert len(invites) == 2
    emails = {inv["email"] for inv in invites}
    assert "a@acme.com" in emails
    assert "b@acme.com" in emails

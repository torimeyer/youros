"""Tests for /api/prototypes endpoints.

One test per endpoint, using an in-memory store patched so no disk I/O occurs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Patch store before importing app so the module-level _load() is a no-op.
import services.prototypes_store as proto_store

# Reset in-memory state before each test.
@pytest.fixture(autouse=True)
def clean_store(tmp_path, monkeypatch):
    proto_store._store.clear()
    proto_store._tokens.clear()
    # Redirect disk writes to a temp path so tests never touch ~/.myos
    monkeypatch.setattr(proto_store, "PROTOTYPES_PATH", tmp_path / "prototypes.json")
    yield
    proto_store._store.clear()
    proto_store._tokens.clear()


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def sample_prototype():
    """Insert a prototype directly into the store."""
    p = {
        "id": str(uuid.uuid4()),
        "title": "Dashboard Redesign v2",
        "url": "https://figma.com/file/abc123",
        "description": "Updated nav and metrics layout.",
        "owner_email": "pm@example.com",
        "status": "draft",
        "reviewer_emails": ["reviewer@example.com"],
        "deadline": None,
        "comments": [],
        "votes": {},
        "versions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    proto_store._store[p["id"]] = p
    return p


# ---------------------------------------------------------------------------
# GET /api/prototypes
# ---------------------------------------------------------------------------

def test_list_prototypes_empty(client):
    resp = client.get("/api/prototypes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_prototypes_returns_summary(client, sample_prototype):
    resp = client.get("/api/prototypes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == sample_prototype["id"]
    assert item["title"] == "Dashboard Redesign v2"
    assert item["comment_count"] == 0
    assert "vote_summary" in item


# ---------------------------------------------------------------------------
# POST /api/prototypes
# ---------------------------------------------------------------------------

def test_create_prototype(client):
    payload = {
        "title": "Checkout Flow",
        "url": "https://staging.example.com/checkout",
        "owner_email": "pm@example.com",
        "reviewer_emails": ["eng@example.com"],
    }
    resp = client.post("/api/prototypes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Checkout Flow"
    assert data["status"] == "draft"
    assert data["owner_email"] == "pm@example.com"
    assert "id" in data


def test_create_prototype_invalid_email(client):
    payload = {
        "title": "Bad Email Test",
        "url": "https://example.com",
        "owner_email": "not-an-email",
    }
    resp = client.post("/api/prototypes", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/prototypes/{id}
# ---------------------------------------------------------------------------

def test_get_prototype_found(client, sample_prototype):
    resp = client.get(f"/api/prototypes/{sample_prototype['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sample_prototype["id"]
    assert "vote_summary" in data
    assert "comments" in data


def test_get_prototype_not_found(client):
    resp = client.get("/api/prototypes/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/prototypes/{id}/comments
# ---------------------------------------------------------------------------

def test_add_comment_as_owner(client, sample_prototype):
    payload = {
        "author_email": "pm@example.com",  # owner
        "body": "The nav spacing feels off.",
        "reaction": "fix-this",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/comments", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["body"] == "The nav spacing feels off."
    assert data["reaction"] == "fix-this"
    assert "id" in data


def test_add_comment_unauthorized(client, sample_prototype):
    payload = {
        "author_email": "random@example.com",
        "body": "Should not be allowed.",
        "reaction": "question",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/comments", json=payload)
    assert resp.status_code == 403


def test_add_comment_via_token(client, sample_prototype):
    token = proto_store._make_token if hasattr(proto_store, "_make_token") else None
    # Import the helper from the router module
    from routers.prototypes import _make_token
    tok = _make_token(sample_prototype["id"], "guest@example.com")
    payload = {
        "author_email": "guest@example.com",
        "body": "Love the color palette.",
        "reaction": "love-it",
        "token": tok,
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/comments", json=payload)
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# POST /api/prototypes/{id}/vote
# ---------------------------------------------------------------------------

def test_cast_vote_as_reviewer(client, sample_prototype):
    payload = {
        "voter_email": "reviewer@example.com",  # already in reviewer_emails
        "choice": "approve",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/vote", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["choice"] == "approve"
    assert data["vote_summary"]["counts"]["approve"] == 1


def test_cast_vote_unauthorized(client, sample_prototype):
    payload = {
        "voter_email": "stranger@example.com",
        "choice": "blocked",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/vote", json=payload)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/prototypes/{id}/invite
# ---------------------------------------------------------------------------

def test_invite_reviewer(client, sample_prototype):
    payload = {
        "owner_email": "pm@example.com",
        "reviewer_email": "new-reviewer@example.com",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/invite", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["reviewer_email"] == "new-reviewer@example.com"
    assert "token" in data
    assert "review_url" in data
    assert data["expires_in_days"] == 7


def test_invite_non_owner_rejected(client, sample_prototype):
    payload = {
        "owner_email": "nottheowner@example.com",
        "reviewer_email": "someone@example.com",
    }
    resp = client.post(f"/api/prototypes/{sample_prototype['id']}/invite", json=payload)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/prototypes/{id}
# ---------------------------------------------------------------------------

def test_advance_status(client, sample_prototype):
    payload = {"owner_email": "pm@example.com", "status": "in-review"}
    resp = client.put(f"/api/prototypes/{sample_prototype['id']}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in-review"


def test_update_title(client, sample_prototype):
    payload = {"owner_email": "pm@example.com", "title": "Updated Title"}
    resp = client.put(f"/api/prototypes/{sample_prototype['id']}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Title"


def test_update_non_owner_rejected(client, sample_prototype):
    payload = {"owner_email": "hacker@example.com", "status": "shipped"}
    resp = client.put(f"/api/prototypes/{sample_prototype['id']}", json=payload)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/prototypes/{id}
# ---------------------------------------------------------------------------

def test_delete_prototype(client, sample_prototype):
    resp = client.delete(
        f"/api/prototypes/{sample_prototype['id']}",
        params={"owner_email": "pm@example.com"},
    )
    assert resp.status_code == 204
    assert client.get(f"/api/prototypes/{sample_prototype['id']}").status_code == 404


def test_delete_prototype_non_owner_rejected(client, sample_prototype):
    resp = client.delete(
        f"/api/prototypes/{sample_prototype['id']}",
        params={"owner_email": "notowner@example.com"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/prototypes/{id}/comments/{cid}
# ---------------------------------------------------------------------------

def test_delete_comment(client, sample_prototype):
    # Add a comment first
    add_resp = client.post(
        f"/api/prototypes/{sample_prototype['id']}/comments",
        json={"author_email": "pm@example.com", "body": "To be deleted.", "reaction": "fix-this"},
    )
    assert add_resp.status_code == 201
    cid = add_resp.json()["id"]

    del_resp = client.delete(
        f"/api/prototypes/{sample_prototype['id']}/comments/{cid}",
        params={"owner_email": "pm@example.com"},
    )
    assert del_resp.status_code == 204

    detail = client.get(f"/api/prototypes/{sample_prototype['id']}").json()
    assert all(c["id"] != cid for c in detail["comments"])


def test_delete_comment_non_owner_rejected(client, sample_prototype):
    add_resp = client.post(
        f"/api/prototypes/{sample_prototype['id']}/comments",
        json={"author_email": "pm@example.com", "body": "Keep this.", "reaction": "love-it"},
    )
    cid = add_resp.json()["id"]
    resp = client.delete(
        f"/api/prototypes/{sample_prototype['id']}/comments/{cid}",
        params={"owner_email": "hacker@example.com"},
    )
    assert resp.status_code == 403

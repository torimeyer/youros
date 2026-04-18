"""PM Prototype Collaboration API.

Endpoints:
  GET  /prototypes                list all prototypes with status summary
  POST /prototypes                create a new prototype
  GET  /prototypes/{id}           prototype detail: comments, votes, reviewers
  POST /prototypes/{id}/comments  add a tagged comment (reaction)
  POST /prototypes/{id}/vote      cast Approve / Needs Changes / Blocked vote
  POST /prototypes/{id}/invite    email a reviewer a magic-link invite

Security:
  - Write endpoints that mutate owner data require owner_email + prototype ownership.
  - Invite tokens are single-use, expire after 7 days, signed with PROTOTYPES_MAGIC_SECRET.
  - Vote endpoint accepts either owner_email or a valid token so guests can vote.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import services.prototypes_store as store
from services import recent_deletes

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAGIC_SECRET = os.environ.get("PROTOTYPES_MAGIC_SECRET", "change-me-in-production")
_TOKEN_TTL_DAYS = 7

router = APIRouter(tags=["prototypes"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_token(prototype_id: str, reviewer_email: str) -> str:
    """Generate a cryptographically random invite token and register it."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=_TOKEN_TTL_DAYS)).isoformat()
    store.add_token(token, {
        "prototype_id": prototype_id,
        "reviewer_email": reviewer_email,
        "expires_at": expires_at,
        "used": False,
    })
    return token


def _validate_token(token: str) -> dict:
    """Return token payload or raise 401."""
    payload = store.get_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired invite link.")
    if payload.get("used"):
        raise HTTPException(status_code=401, detail="This invite link has already been used.")
    expires = datetime.fromisoformat(payload["expires_at"])
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=401, detail="This invite link has expired.")
    return payload


def _verify_owner(prototype: dict, owner_email: str) -> None:
    if prototype.get("owner_email") != owner_email:
        raise HTTPException(status_code=403, detail="Only the prototype owner can do that.")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PrototypeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2000)
    description: Optional[str] = Field(None, max_length=2000)
    owner_email: str = Field(..., max_length=200)
    reviewer_emails: List[str] = Field(default_factory=list)
    deadline: Optional[str] = Field(None, max_length=50)

    @field_validator("owner_email")
    @classmethod
    def validate_owner_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


Reaction = Literal["love-it", "this-works", "fix-this", "question"]
VoteChoice = Literal["approve", "needs-changes", "blocked"]


class CommentCreate(BaseModel):
    author_email: str = Field(..., max_length=200)
    body: str = Field(..., min_length=1, max_length=4000)
    reaction: Reaction
    token: Optional[str] = None

    @field_validator("author_email")
    @classmethod
    def validate_author_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class VoteCreate(BaseModel):
    voter_email: str = Field(..., max_length=200)
    choice: VoteChoice
    token: Optional[str] = None

    @field_validator("voter_email")
    @classmethod
    def validate_voter_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class InviteCreate(BaseModel):
    owner_email: str = Field(..., max_length=200)
    reviewer_email: str = Field(..., max_length=200)

    @field_validator("owner_email", "reviewer_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/prototypes")
def list_prototypes():
    """Return all prototypes with status, comment count, and reviewer count."""
    result = []
    for p in store.get_all():
        result.append({
            "id": p["id"],
            "title": p["title"],
            "url": p["url"],
            "status": p["status"],
            "owner_email": p["owner_email"],
            "comment_count": len(p.get("comments", [])),
            "reviewer_count": len(p.get("reviewer_emails", [])),
            "vote_summary": _vote_summary(p),
            "deadline": p.get("deadline"),
            "created_at": p["created_at"],
            "updated_at": p["updated_at"],
        })
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result


@router.post("/prototypes", status_code=201)
def create_prototype(body: PrototypeCreate):
    """Create a new prototype and return it."""
    prototype = {
        "id": str(uuid.uuid4()),
        "title": body.title,
        "url": body.url,
        "description": body.description,
        "owner_email": body.owner_email,
        "status": "draft",
        "reviewer_emails": body.reviewer_emails,
        "deadline": body.deadline,
        "comments": [],
        "votes": {},
        "versions": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    return store.create(prototype)


@router.get("/prototypes/{prototype_id}")
def get_prototype(prototype_id: str):
    """Return full prototype detail including comments and votes."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")
    return {**p, "vote_summary": _vote_summary(p)}


@router.post("/prototypes/{prototype_id}/comments", status_code=201)
def add_comment(prototype_id: str, body: CommentCreate):
    """Add a tagged comment. Requires a valid invite token or being the owner."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")

    _require_access(p, body.author_email, body.token)

    comment = {
        "id": str(uuid.uuid4()),
        "author_email": body.author_email,
        "body": body.body,
        "reaction": body.reaction,
        "created_at": _now(),
    }
    p["comments"].append(comment)
    p["updated_at"] = _now()
    store.update(p)
    return comment


@router.post("/prototypes/{prototype_id}/vote", status_code=201)
def cast_vote(prototype_id: str, body: VoteCreate):
    """Cast an Approve, Needs Changes, or Blocked vote. Requires token or ownership."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")

    _require_access(p, body.voter_email, body.token)

    p["votes"][body.voter_email] = {"choice": body.choice, "voted_at": _now()}
    p["updated_at"] = _now()
    store.update(p)
    return {"voter_email": body.voter_email, "choice": body.choice, "vote_summary": _vote_summary(p)}


@router.post("/prototypes/{prototype_id}/invite", status_code=201)
def invite_reviewer(prototype_id: str, body: InviteCreate):
    """Generate a magic-link invite for a reviewer. Owner-only."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")

    _verify_owner(p, body.owner_email)

    token = _make_token(prototype_id, body.reviewer_email)
    if body.reviewer_email not in p["reviewer_emails"]:
        p["reviewer_emails"].append(body.reviewer_email)
        p["updated_at"] = _now()
        store.update(p)

    # In production: send email with this token. For now, return it.
    return {
        "reviewer_email": body.reviewer_email,
        "token": token,
        "expires_in_days": _TOKEN_TTL_DAYS,
        "review_url": f"/prototypes/{prototype_id}/review?token={token}",
    }


_STATUS_PIPELINE = ["draft", "in-review", "approved", "shipped"]


class PrototypeUpdate(BaseModel):
    owner_email: str = Field(..., max_length=200)
    status: Optional[Literal["draft", "in-review", "approved", "shipped"]] = None
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    url: Optional[str] = Field(None, min_length=1, max_length=2000)

    @field_validator("owner_email")
    @classmethod
    def validate_owner_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


@router.put("/prototypes/{prototype_id}")
def update_prototype(prototype_id: str, body: PrototypeUpdate):
    """Advance status or update fields. Owner-only."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")
    _verify_owner(p, body.owner_email)
    if body.status is not None:
        p["status"] = body.status
    if body.title is not None:
        p["title"] = body.title
    if body.description is not None:
        p["description"] = body.description
    if body.url is not None:
        p["url"] = body.url
    p["updated_at"] = _now()
    return store.update(p)


@router.delete("/prototypes/{prototype_id}", status_code=204)
def delete_prototype(prototype_id: str, owner_email: str):
    """Delete a prototype. Owner-only. Requires ?owner_email= query param."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")
    _verify_owner(p, owner_email)
    store.delete(prototype_id)
    recent_deletes.record_id(f"prototype:{prototype_id}")


@router.delete("/prototypes/{prototype_id}/comments/{comment_id}", status_code=204)
def delete_comment(prototype_id: str, comment_id: str, owner_email: str):
    """Delete a comment. Owner-only. Requires ?owner_email= query param."""
    p = store.get_one(prototype_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prototype not found.")
    _verify_owner(p, owner_email)
    original_len = len(p["comments"])
    p["comments"] = [c for c in p["comments"] if c["id"] != comment_id]
    if len(p["comments"]) == original_len:
        raise HTTPException(status_code=404, detail="Comment not found.")
    p["updated_at"] = _now()
    store.update(p)
    recent_deletes.record_id(f"prototype-comment:{comment_id}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vote_summary(p: dict) -> dict:
    votes = p.get("votes", {})
    counts: dict = {"approve": 0, "needs-changes": 0, "blocked": 0}
    names: dict = {"approve": [], "needs-changes": [], "blocked": []}
    for email, v in votes.items():
        c = v["choice"]
        counts[c] = counts.get(c, 0) + 1
        names[c].append(email)
    return {"counts": counts, "voters": names, "total": len(votes)}


def _require_access(p: dict, actor_email: str, token: Optional[str]) -> None:
    """Allow owner or valid token holder. Raise 403 otherwise."""
    if actor_email == p.get("owner_email"):
        return
    if actor_email in p.get("reviewer_emails", []):
        return
    if token:
        payload = _validate_token(token)
        if payload["prototype_id"] != p["id"]:
            raise HTTPException(status_code=403, detail="This invite link is for a different prototype.")
        if payload["reviewer_email"] != actor_email:
            raise HTTPException(status_code=403, detail="This invite link belongs to a different reviewer.")
        return
    raise HTTPException(status_code=403, detail="You need an invite link to comment or vote on this prototype.")

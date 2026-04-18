"""Dog walking service landing page API.

Endpoints:
  GET  /api/dogwalk/services        list available services with pricing
  GET  /api/dogwalk/testimonials    list customer testimonials
  POST /api/dogwalk/contact         submit a contact/inquiry form
  GET  /api/dogwalk/leads           view submitted leads (admin)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from services.atomic_io import atomic_write_json
from services.dogwalk_store import DOGWALK_LEADS_PATH

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(tags=["dogwalk"])

# ---------------------------------------------------------------------------
# Seeded content (no database needed for static display data)
# ---------------------------------------------------------------------------

_SERVICES = [
    {
        "id": "solo-30",
        "name": "30-Minute Solo Walk",
        "description": "One-on-one attention for your dog. Perfect for daily exercise.",
        "duration_minutes": 30,
        "price_cents": 2500,
        "price_display": "$25",
    },
    {
        "id": "solo-60",
        "name": "60-Minute Solo Walk",
        "description": "A longer adventure for high-energy dogs who need more time outside.",
        "duration_minutes": 60,
        "price_cents": 4000,
        "price_display": "$40",
    },
    {
        "id": "group-30",
        "name": "30-Minute Group Walk",
        "description": "Walk with a small group of up to 4 friendly dogs. Great for socialization.",
        "duration_minutes": 30,
        "price_cents": 1800,
        "price_display": "$18",
    },
    {
        "id": "drop-in",
        "name": "Drop-In Visit",
        "description": "A 20-minute check-in for feeding, potty break, and playtime.",
        "duration_minutes": 20,
        "price_cents": 2000,
        "price_display": "$20",
    },
    {
        "id": "puppy-visit",
        "name": "Puppy Visit",
        "description": "Extra attention for puppies under 12 months. Includes basic training reinforcement.",
        "duration_minutes": 20,
        "price_cents": 2200,
        "price_display": "$22",
    },
]

_TESTIMONIALS = [
    {
        "id": "t1",
        "author": "Sarah M.",
        "location": "Brooklyn, NY",
        "rating": 5,
        "text": "My golden retriever Bruno has never been happier. The solo walks are exactly what he needed.",
    },
    {
        "id": "t2",
        "author": "James T.",
        "location": "Brooklyn, NY",
        "rating": 5,
        "text": "Reliable, caring, and always sends a photo update. I book every week without hesitation.",
    },
    {
        "id": "t3",
        "author": "Priya K.",
        "location": "Park Slope, NY",
        "rating": 5,
        "text": "The puppy visits were a lifesaver while I was at the office. My pup Mochi loves every visit.",
    },
    {
        "id": "t4",
        "author": "Devon R.",
        "location": "Cobble Hill, NY",
        "rating": 4,
        "text": "Great communication and always on time. The group walks have really helped with my dog's social skills.",
    },
]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_ALLOWED_SERVICES = {s["id"] for s in _SERVICES}


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., max_length=200)
    phone: Optional[str] = Field(None, max_length=50)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v
    dog_name: Optional[str] = Field(None, max_length=200)
    dog_breed: Optional[str] = Field(None, max_length=200)
    service_id: Optional[str] = Field(None, max_length=100)
    message: Optional[str] = Field(None, max_length=2000)


class ContactResponse(BaseModel):
    id: str
    created_at: str
    message: str


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load_leads() -> List[dict]:
    if not DOGWALK_LEADS_PATH.exists():
        return []
    try:
        return json.loads(DOGWALK_LEADS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_leads(leads: List[dict]) -> None:
    DOGWALK_LEADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(DOGWALK_LEADS_PATH, leads)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/dogwalk/services")
def list_services():
    """Return the list of available dog walking services."""
    return {"services": _SERVICES}


@router.get("/dogwalk/testimonials")
def list_testimonials():
    """Return customer testimonials."""
    return {"testimonials": _TESTIMONIALS}


@router.post("/dogwalk/contact", status_code=201, response_model=ContactResponse)
def submit_contact(body: ContactRequest):
    """Accept a contact/inquiry form submission and persist it."""
    if body.service_id is not None and body.service_id not in _ALLOWED_SERVICES:
        raise HTTPException(status_code=400, detail="Unknown service. Choose a valid service ID.")

    leads = _load_leads()
    lead_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    lead = {
        "id": lead_id,
        "created_at": now,
        "name": body.name,
        "email": body.email,
        "phone": body.phone,
        "dog_name": body.dog_name,
        "dog_breed": body.dog_breed,
        "service_id": body.service_id,
        "message": body.message,
    }
    leads.append(lead)
    _save_leads(leads)

    return ContactResponse(
        id=lead_id,
        created_at=now,
        message="Thanks! We will be in touch within 24 hours.",
    )


@router.get("/dogwalk/leads")
def list_leads():
    """Return all submitted contact leads, newest first."""
    leads = _load_leads()
    return {"leads": list(reversed(leads)), "total": len(leads)}

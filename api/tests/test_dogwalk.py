"""Tests for the dog walking service landing page API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with leads storage isolated to a temp dir."""
    import routers.dogwalk as dw_module
    monkeypatch.setattr(dw_module, "DOGWALK_LEADS_PATH", tmp_path / "dogwalk_leads.json")
    from main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

def test_list_services_returns_200(client):
    res = client.get("/api/dogwalk/services")
    assert res.status_code == 200


def test_list_services_has_required_fields(client):
    data = client.get("/api/dogwalk/services").json()
    assert "services" in data
    services = data["services"]
    assert len(services) >= 1
    for svc in services:
        assert "id" in svc
        assert "name" in svc
        assert "price_display" in svc
        assert "duration_minutes" in svc


def test_services_include_solo_walk(client):
    services = client.get("/api/dogwalk/services").json()["services"]
    ids = [s["id"] for s in services]
    assert "solo-30" in ids
    assert "solo-60" in ids


# ---------------------------------------------------------------------------
# Testimonials
# ---------------------------------------------------------------------------

def test_list_testimonials_returns_200(client):
    res = client.get("/api/dogwalk/testimonials")
    assert res.status_code == 200


def test_list_testimonials_has_required_fields(client):
    data = client.get("/api/dogwalk/testimonials").json()
    assert "testimonials" in data
    for t in data["testimonials"]:
        assert "author" in t
        assert "text" in t
        assert "rating" in t


def test_testimonials_ratings_are_valid(client):
    testimonials = client.get("/api/dogwalk/testimonials").json()["testimonials"]
    for t in testimonials:
        assert 1 <= t["rating"] <= 5


# ---------------------------------------------------------------------------
# Contact form - happy path
# ---------------------------------------------------------------------------

def test_submit_contact_minimal(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "Jane Smith",
        "email": "jane@example.com",
    })
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert "created_at" in data
    assert "message" in data


def test_submit_contact_full(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "Alex Rivera",
        "email": "alex@example.com",
        "phone": "555-123-4567",
        "dog_name": "Biscuit",
        "dog_breed": "Beagle",
        "service_id": "solo-30",
        "message": "Looking for daily walks starting next Monday.",
    })
    assert res.status_code == 201
    data = res.json()
    assert "id" in data


def test_submit_contact_persists_lead(client):
    client.post("/api/dogwalk/contact", json={"name": "Jo", "email": "jo@example.com"})
    leads = client.get("/api/dogwalk/leads").json()["leads"]
    assert len(leads) == 1
    assert leads[0]["email"] == "jo@example.com"


def test_multiple_contacts_all_saved(client):
    client.post("/api/dogwalk/contact", json={"name": "A", "email": "a@example.com"})
    client.post("/api/dogwalk/contact", json={"name": "B", "email": "b@example.com"})
    data = client.get("/api/dogwalk/leads").json()
    assert data["total"] == 2


def test_leads_returned_newest_first(client):
    client.post("/api/dogwalk/contact", json={"name": "First", "email": "first@example.com"})
    client.post("/api/dogwalk/contact", json={"name": "Second", "email": "second@example.com"})
    leads = client.get("/api/dogwalk/leads").json()["leads"]
    assert leads[0]["name"] == "Second"
    assert leads[1]["name"] == "First"


# ---------------------------------------------------------------------------
# Contact form - validation errors
# ---------------------------------------------------------------------------

def test_contact_missing_name_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={"email": "x@example.com"})
    assert res.status_code == 422


def test_contact_missing_email_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={"name": "Jane"})
    assert res.status_code == 422


def test_contact_invalid_email_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={"name": "Jane", "email": "not-an-email"})
    assert res.status_code == 422


def test_contact_empty_name_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={"name": "", "email": "x@example.com"})
    assert res.status_code == 422


def test_contact_name_too_long_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "x" * 201,
        "email": "x@example.com",
    })
    assert res.status_code == 422


def test_contact_message_too_long_returns_422(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "Jane",
        "email": "jane@example.com",
        "message": "x" * 2001,
    })
    assert res.status_code == 422


def test_contact_unknown_service_id_returns_400(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "Jane",
        "email": "jane@example.com",
        "service_id": "not-a-real-service",
    })
    assert res.status_code == 400
    assert "service" in res.json()["detail"].lower()


def test_contact_valid_service_id_accepted(client):
    res = client.post("/api/dogwalk/contact", json={
        "name": "Jane",
        "email": "jane@example.com",
        "service_id": "group-30",
    })
    assert res.status_code == 201


# ---------------------------------------------------------------------------
# Leads - no data state
# ---------------------------------------------------------------------------

def test_leads_empty_when_no_contacts(client):
    data = client.get("/api/dogwalk/leads").json()
    assert data["leads"] == []
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# Lead fields stored correctly
# ---------------------------------------------------------------------------

def test_lead_stores_all_fields(client):
    client.post("/api/dogwalk/contact", json={
        "name": "Dana Lee",
        "email": "dana@example.com",
        "phone": "555-999-0000",
        "dog_name": "Mochi",
        "dog_breed": "Shiba Inu",
        "service_id": "puppy-visit",
        "message": "Needs extra attention.",
    })
    lead = client.get("/api/dogwalk/leads").json()["leads"][0]
    assert lead["name"] == "Dana Lee"
    assert lead["email"] == "dana@example.com"
    assert lead["phone"] == "555-999-0000"
    assert lead["dog_name"] == "Mochi"
    assert lead["dog_breed"] == "Shiba Inu"
    assert lead["service_id"] == "puppy-visit"
    assert lead["message"] == "Needs extra attention."
    assert "id" in lead
    assert "created_at" in lead


def test_lead_optional_fields_none_when_omitted(client):
    client.post("/api/dogwalk/contact", json={"name": "Sam", "email": "sam@example.com"})
    lead = client.get("/api/dogwalk/leads").json()["leads"][0]
    assert lead["phone"] is None
    assert lead["dog_name"] is None
    assert lead["dog_breed"] is None
    assert lead["service_id"] is None
    assert lead["message"] is None


# ---------------------------------------------------------------------------
# Each lead gets a unique ID
# ---------------------------------------------------------------------------

def test_each_lead_has_unique_id(client):
    client.post("/api/dogwalk/contact", json={"name": "A", "email": "a@example.com"})
    client.post("/api/dogwalk/contact", json={"name": "B", "email": "b@example.com"})
    leads = client.get("/api/dogwalk/leads").json()["leads"]
    ids = [l["id"] for l in leads]
    assert len(ids) == len(set(ids))

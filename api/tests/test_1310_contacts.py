"""Tests for →1310: iMessage contacts service and router.

All Swift subprocess calls are mocked so tests run without macOS Contacts.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


_SAMPLE_CONTACTS = [
    {"name": "Alice Smith", "phone_numbers": ["+15550001234"], "emails": ["alice@example.com"]},
    {"name": "Bob Jones", "phone_numbers": ["5550005678", "5559999"], "emails": []},
    {"name": "Carol", "phone_numbers": [], "emails": ["carol@work.com", "carol@home.com"]},
]


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


def _make_completed_process(stdout: str, returncode: int = 0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def test_list_contacts_returns_parsed_json():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=True), \
         patch("subprocess.run", return_value=_make_completed_process(json.dumps(_SAMPLE_CONTACTS))):
        contacts = svc.list_contacts()

    assert len(contacts) == 3
    assert contacts[0]["name"] == "Alice Smith"
    assert contacts[0]["phone_numbers"] == ["+15550001234"]
    assert contacts[0]["emails"] == ["alice@example.com"]


def test_list_contacts_caches_result():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=True), \
         patch("subprocess.run", return_value=_make_completed_process(json.dumps(_SAMPLE_CONTACTS))) as mock_run:
        svc.list_contacts()
        svc.list_contacts()

    # subprocess.run should only have been called once
    assert mock_run.call_count == 1


def test_list_contacts_non_macos_returns_empty():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=False), \
         patch("subprocess.run") as mock_run:
        contacts = svc.list_contacts()

    assert contacts == []
    mock_run.assert_not_called()


def test_list_contacts_swift_failure_returns_empty():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=True), \
         patch("subprocess.run", return_value=_make_completed_process("error output", returncode=1)):
        contacts = svc.list_contacts()

    assert contacts == []


def test_list_contacts_invalid_json_returns_empty():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=True), \
         patch("subprocess.run", return_value=_make_completed_process("not valid json")):
        contacts = svc.list_contacts()

    assert contacts == []


def test_invalidate_cache_forces_refetch():
    from services import imessage_contacts as svc
    svc.invalidate_cache()
    with patch("services.imessage_contacts.is_macos", return_value=True), \
         patch("subprocess.run", return_value=_make_completed_process(json.dumps(_SAMPLE_CONTACTS))) as mock_run:
        svc.list_contacts()
        svc.invalidate_cache()
        svc.list_contacts()

    assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# Router / HTTP tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


def _mock_contacts(contacts=None):
    return patch(
        "routers.contacts.contacts_service.list_contacts",
        return_value=contacts if contacts is not None else _SAMPLE_CONTACTS,
    )


def test_get_contacts_returns_list(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert len(data["contacts"]) == 3


def test_get_contacts_shape(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts")
    contact = resp.json()["contacts"][0]
    assert "name" in contact
    assert "phone_numbers" in contact
    assert "emails" in contact


def test_get_contacts_filter_by_name(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts?q=alice")
    data = resp.json()
    assert data["count"] == 1
    assert data["contacts"][0]["name"] == "Alice Smith"


def test_get_contacts_filter_by_phone(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts?q=5559999")
    data = resp.json()
    assert data["count"] == 1
    assert data["contacts"][0]["name"] == "Bob Jones"


def test_get_contacts_filter_by_email(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts?q=work.com")
    data = resp.json()
    assert data["count"] == 1
    assert data["contacts"][0]["name"] == "Carol"


def test_get_contacts_non_macos_returns_503(client):
    with patch("routers.contacts.platform.system", return_value="Linux"):
        resp = client.get("/api/contacts")
    assert resp.status_code == 503


def test_get_contacts_empty_filter_returns_all(client):
    with patch("routers.contacts.platform.system", return_value="Darwin"), _mock_contacts():
        resp = client.get("/api/contacts?q=")
    assert resp.json()["count"] == 3

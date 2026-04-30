"""Tests for the ostk language verb API (→901)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import routers.ostk as ostk_router


@pytest.fixture(autouse=True)
def patch_verbs_file(tmp_path, monkeypatch):
    verbs_file = tmp_path / "custom_verbs.jsonl"
    monkeypatch.setattr(ostk_router, "_VERBS_FILE", verbs_file)


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.anyio
async def test_list_empty(client):
    resp = await client.get("/api/ostk/language/verbs")
    assert resp.status_code == 200
    assert resp.json() == {"verbs": []}


@pytest.mark.anyio
async def test_register_verb_appears_in_list(client):
    resp = await client.post(
        "/api/ostk/language/verb",
        json={"name": "deploy", "description": "deploy a service"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"verb": "deploy", "status": "registered"}

    list_resp = await client.get("/api/ostk/language/verbs")
    verbs = list_resp.json()["verbs"]
    assert any(v["name"] == "deploy" for v in verbs)


@pytest.mark.anyio
async def test_register_invalid_name_returns_422(client):
    resp = await client.post(
        "/api/ostk/language/verb",
        json={"name": "123invalid", "description": "bad name"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_delete_verb_no_longer_in_list(client):
    await client.post(
        "/api/ostk/language/verb",
        json={"name": "rollback", "description": "roll back a deployment"},
    )

    del_resp = await client.delete("/api/ostk/language/verb/rollback")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"verb": "rollback", "status": "removed"}

    list_resp = await client.get("/api/ostk/language/verbs")
    assert all(v["name"] != "rollback" for v in list_resp.json()["verbs"])


@pytest.mark.anyio
async def test_delete_nonexistent_verb_returns_404(client):
    resp = await client.delete("/api/ostk/language/verb/doesnotexist")
    assert resp.status_code == 404

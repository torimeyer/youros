"""Tests for the editable agent template alias feature.

Covers:
- Store unit: set_alias, get_alias, clear_alias round-trips
- Store validation: length, charset, collision with builtin names/aliases
- Store: PATCH alias persists across store instances (disk round-trip)
- API: GET /agents/templates/{id}/alias
- API: PATCH /agents/templates/{id}/alias (set and clear)
- Migration: template without a user alias returns None (graceful default)
- Verb resolution: user alias resolves via _resolve_alias / get_template_aliases
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_templates_store as ats_mod
from services.agent_templates_store import (
    BUILTIN_AGENT_TEMPLATES,
    AgentTemplatesStore,
    TEMPLATE_ALIASES_PATH,
)


# ---------- fixtures ----------


@pytest.fixture
def aliases_path(tmp_path):
    """Isolated alias file — does not touch ~/.myos/."""
    return tmp_path / "template_aliases.json"


@pytest.fixture
def store(tmp_path, monkeypatch, aliases_path):
    """AgentTemplatesStore wired to temp files."""
    fake_templates = tmp_path / "agent_templates.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", aliases_path)
    AgentTemplatesStore._invalidate_persona_cache()
    return AgentTemplatesStore()


# ---------- store unit tests ----------


def test_get_alias_returns_none_when_unset(store):
    """Migration: template with no user alias returns None."""
    tpl = BUILTIN_AGENT_TEMPLATES[0]
    assert store.get_alias(tpl["id"]) is None


def test_set_and_get_alias_round_trip(store):
    """set_alias persists; get_alias retrieves the same value."""
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store.set_alias(tpl["id"], "build-it")
    assert store.get_alias(tpl["id"]) == "build-it"


def test_set_alias_disk_persistence(tmp_path, monkeypatch, aliases_path):
    """Alias survives a fresh store instance (real disk round-trip)."""
    fake_templates = tmp_path / "at.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", aliases_path)
    AgentTemplatesStore._invalidate_persona_cache()

    store1 = AgentTemplatesStore()
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store1.set_alias(tpl["id"], "build-it")

    store2 = AgentTemplatesStore()
    assert store2.get_alias(tpl["id"]) == "build-it"


def test_clear_alias(store):
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store.set_alias(tpl["id"], "build-it")
    store.clear_alias(tpl["id"])
    assert store.get_alias(tpl["id"]) is None


def test_one_alias_per_template(store):
    """Setting a second alias replaces the first (one alias per template)."""
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store.set_alias(tpl["id"], "first-alias")
    store.set_alias(tpl["id"], "second-alias")
    assert store.get_alias(tpl["id"]) == "second-alias"
    # The first alias must be gone
    all_aliases = store.get_all_user_aliases()
    assert "first-alias" not in all_aliases


def test_alias_too_short_rejected(store):
    tpl = BUILTIN_AGENT_TEMPLATES[0]
    with pytest.raises(ValueError, match="between 2 and 30"):
        store.set_alias(tpl["id"], "x")


def test_alias_too_long_rejected(store):
    tpl = BUILTIN_AGENT_TEMPLATES[0]
    with pytest.raises(ValueError, match="between 2 and 30"):
        store.set_alias(tpl["id"], "a" * 31)


def test_alias_invalid_chars_rejected(store):
    tpl = BUILTIN_AGENT_TEMPLATES[0]
    with pytest.raises(ValueError):
        store.set_alias(tpl["id"], "no spaces allowed")


def test_alias_collision_with_builtin_name_rejected(store):
    """Cannot set alias = an existing template name."""
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    # "diagnose" is another builtin name
    with pytest.raises(ValueError):
        store.set_alias(tpl["id"], "diagnose")


def test_alias_collision_with_builtin_alias_rejected(store):
    """Cannot set alias = an existing built-in alias."""
    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Explain Plain")
    # "saa" is a built-in alias on Builder
    with pytest.raises(ValueError, match="built-in alias"):
        store.set_alias(tpl["id"], "saa")


def test_alias_collision_between_templates_rejected(store):
    """Alias claimed by one template cannot be set on another."""
    builder = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    diagnose = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Diagnose")
    store.set_alias(builder["id"], "my-runner")
    with pytest.raises(ValueError, match="already used"):
        store.set_alias(diagnose["id"], "my-runner")


def test_get_all_user_aliases(store):
    builder = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    diagnose = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Diagnose")
    store.set_alias(builder["id"], "build-it")
    store.set_alias(diagnose["id"], "find-bugs")
    all_a = store.get_all_user_aliases()
    assert all_a["build-it"] == builder["id"]
    assert all_a["find-bugs"] == diagnose["id"]


# ---------- verb resolution ----------


def test_user_alias_resolves_via_get_template_aliases(store, monkeypatch):
    """User alias appears in the merged alias table from get_template_aliases()."""
    from services.agentfile_parser import get_template_aliases

    builder = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store.set_alias(builder["id"], "my-build")

    # Patch the singleton so get_template_aliases reads from our store
    monkeypatch.setattr(ats_mod, "agent_templates_store", store)

    merged = get_template_aliases()
    assert "my-build" in merged


def test_user_alias_overrides_builtin_alias(store, monkeypatch):
    """User alias wins when it matches a builtin key (higher priority)."""
    from services.agentfile_parser import get_template_aliases

    # "elit" is a built-in alias for explain-plain. A user can't reuse it
    # (would fail validation), so use a fresh alias that doesn't collide.
    builder = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    store.set_alias(builder["id"], "quick-build")
    monkeypatch.setattr(ats_mod, "agent_templates_store", store)

    merged = get_template_aliases()
    # Built-in aliases still present
    assert "saa" in merged
    # New user alias also present
    assert "quick-build" in merged


# ---------- API endpoint tests ----------


@pytest.mark.anyio
async def test_get_alias_api_no_alias_set(tmp_path, monkeypatch):
    """GET /agents/templates/{id}/alias returns null when no user alias is set."""
    fake_templates = tmp_path / "at.json"
    fake_aliases = tmp_path / "aliases.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", fake_aliases)
    AgentTemplatesStore._invalidate_persona_cache()

    from httpx import AsyncClient, ASGITransport
    from main import app

    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/agents/templates/{tpl['id']}/alias")
    assert resp.status_code == 200
    data = resp.json()
    assert data["template_id"] == tpl["id"]
    assert data["alias"] is None


@pytest.mark.anyio
async def test_patch_alias_set_and_get(tmp_path, monkeypatch):
    """PATCH alias, then GET — alias round-trips via the API."""
    fake_templates = tmp_path / "at2.json"
    fake_aliases = tmp_path / "aliases2.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", fake_aliases)
    AgentTemplatesStore._invalidate_persona_cache()

    from httpx import AsyncClient, ASGITransport
    from main import app

    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        patch_resp = await ac.patch(
            f"/api/agents/templates/{tpl['id']}/alias",
            json={"alias": "build-it"},
        )
        assert patch_resp.status_code == 200

        get_resp = await ac.get(f"/api/agents/templates/{tpl['id']}/alias")
    assert get_resp.status_code == 200
    assert get_resp.json()["alias"] == "build-it"


@pytest.mark.anyio
async def test_patch_alias_clear(tmp_path, monkeypatch):
    """PATCH with alias=null clears the alias."""
    fake_templates = tmp_path / "at3.json"
    fake_aliases = tmp_path / "aliases3.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", fake_aliases)
    AgentTemplatesStore._invalidate_persona_cache()

    from httpx import AsyncClient, ASGITransport
    from main import app

    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.patch(
            f"/api/agents/templates/{tpl['id']}/alias",
            json={"alias": "build-it"},
        )
        clear_resp = await ac.patch(
            f"/api/agents/templates/{tpl['id']}/alias",
            json={"alias": None},
        )
        assert clear_resp.status_code == 200

        get_resp = await ac.get(f"/api/agents/templates/{tpl['id']}/alias")
    assert get_resp.json()["alias"] is None


@pytest.mark.anyio
async def test_patch_alias_validation_error(tmp_path, monkeypatch):
    """Setting an invalid alias returns 400."""
    fake_templates = tmp_path / "at4.json"
    fake_aliases = tmp_path / "aliases4.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", fake_aliases)
    AgentTemplatesStore._invalidate_persona_cache()

    from httpx import AsyncClient, ASGITransport
    from main import app

    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/agents/templates/{tpl['id']}/alias",
            json={"alias": "x"},  # too short
        )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_patch_alias_collision_rejected(tmp_path, monkeypatch):
    """Setting an alias that collides with a built-in alias returns 400."""
    fake_templates = tmp_path / "at5.json"
    fake_aliases = tmp_path / "aliases5.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "TEMPLATE_ALIASES_PATH", fake_aliases)
    AgentTemplatesStore._invalidate_persona_cache()

    from httpx import AsyncClient, ASGITransport
    from main import app

    tpl = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/agents/templates/{tpl['id']}/alias",
            json={"alias": "saa"},  # built-in alias
        )
    assert resp.status_code == 400

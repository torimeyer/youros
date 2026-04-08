"""Tests for Ideas v2 features: templates, staleness, chat-to-idea, and admin templates.

needle: →149
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.templates_store import TemplatesStore, BUILTIN_TEMPLATES, TEMPLATES_PATH
from services.ostk import OstkService


# --- Template store unit tests -----------------------------------------------

class TestTemplatesStore:
    """Unit tests for TemplatesStore."""

    def setup_method(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmpfile.close()
        self.store = TemplatesStore()
        # Redirect TEMPLATES_PATH for tests
        self._orig_path = TEMPLATES_PATH

    def _make_store_with_file(self, path: Path) -> TemplatesStore:
        store = TemplatesStore()
        store._ensure_exists = lambda: None  # skip real fs
        store.list_custom = lambda: json.loads(path.read_text()) if path.exists() else []
        store._save = lambda templates: path.write_text(json.dumps(templates))
        return store

    def test_builtin_templates_present(self):
        assert len(BUILTIN_TEMPLATES) == 5
        ids = [t["id"] for t in BUILTIN_TEMPLATES]
        assert "builtin-feature" in ids
        assert "builtin-problem" in ids
        assert "builtin-research" in ids
        assert "builtin-meeting" in ids
        assert "builtin-integration" in ids

    def test_builtin_templates_have_required_fields(self):
        for tpl in BUILTIN_TEMPLATES:
            assert "id" in tpl
            assert "name" in tpl
            assert "prompt_template" in tpl
            assert "breakdown_hint" in tpl
            assert tpl.get("builtin") is True

    def test_create_custom_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            created = store.create({
                "name": "My template",
                "description": "A test",
                "icon": "star",
                "prompt_template": "I want to [do something]",
                "breakdown_hint": "This is a test template.",
            })
            assert created["name"] == "My template"
            assert created["id"].startswith("custom-")
            assert created["builtin"] is False

    def test_update_custom_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            created = store.create({
                "name": "Before",
                "description": "",
                "icon": "star",
                "prompt_template": "[thing]",
                "breakdown_hint": "",
            })
            updated = store.update(created["id"], {"name": "After"})
            assert updated is not None
            assert updated["name"] == "After"

    def test_update_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            result = store.update("no-such-id", {"name": "X"})
            assert result is None

    def test_delete_custom_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            created = store.create({
                "name": "To delete",
                "description": "",
                "icon": "delete",
                "prompt_template": "[x]",
                "breakdown_hint": "",
            })
            ok = store.delete(created["id"])
            assert ok is True
            assert store.list_custom() == []

    def test_delete_nonexistent_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            ok = store.delete("no-such-id")
            assert ok is False

    def test_list_all_includes_builtins_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            store = self._make_store_with_file(path)
            store.create({
                "name": "Custom",
                "description": "",
                "icon": "star",
                "prompt_template": "[x]",
                "breakdown_hint": "",
            })
            all_templates = store.list_all()
            builtin_ids = {t["id"] for t in BUILTIN_TEMPLATES}
            first_5_ids = {t["id"] for t in all_templates[:5]}
            assert first_5_ids == builtin_ids


# --- GET /api/ideas/templates ------------------------------------------------

@pytest.mark.asyncio
async def test_list_templates_endpoint(client):
    """GET /api/ideas/templates returns built-ins and custom templates."""
    resp = await client.get("/api/ideas/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    # Should include at least the 5 built-ins
    assert len(data["templates"]) >= 5
    names = [t["name"] for t in data["templates"]]
    assert "Feature idea" in names
    assert "Problem to solve" in names


# --- Staleness computation ----------------------------------------------------

from routers.ideas import _compute_staleness


def test_staleness_none_for_fresh_idea():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, False) is None


def test_staleness_stale_at_14_days():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, False) == "stale"


def test_staleness_stale_at_20_days():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, False) == "stale"


def test_staleness_very_stale_at_30_days():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, False) == "very_stale"


def test_staleness_very_stale_at_45_days():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, False) == "very_stale"


def test_staleness_converted_is_never_stale():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _compute_staleness(ts, True) is None


def test_staleness_no_timestamp_returns_none():
    assert _compute_staleness(None, False) is None
    assert _compute_staleness("", False) is None


# --- Staleness in GET /api/ideas response ------------------------------------

@pytest.mark.asyncio
async def test_list_ideas_includes_staleness(client):
    """GET /api/ideas should include staleness on unclustered items."""
    mock_hay = {"clusters": [], "unclustered": ["my old idea"]}
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas._load_hay_metadata", return_value={"my old idea": {"timestamp": old_ts, "source": None}}):
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        mock_ostk.list_converted_hay = AsyncMock(return_value=[])
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["unclustered"]) == 1
    item = data["unclustered"][0]
    assert item["straw"] == "my old idea"
    assert item["staleness"] == "stale"


@pytest.mark.asyncio
async def test_list_ideas_fresh_idea_no_staleness(client):
    """Fresh ideas should have staleness=null."""
    mock_hay = {"clusters": [], "unclustered": ["brand new idea"]}
    now = datetime.now(timezone.utc)
    new_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas._load_hay_metadata", return_value={"brand new idea": {"timestamp": new_ts, "source": None}}):
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        mock_ostk.list_converted_hay = AsyncMock(return_value=[])
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    item = resp.json()["unclustered"][0]
    assert item["staleness"] is None


# --- Chat-to-idea intent detection -------------------------------------------

from routers.chat import _detect_save_as_idea, _extract_idea_content


def test_detect_save_as_idea_variants():
    assert _detect_save_as_idea("save this as an idea")
    assert _detect_save_as_idea("Save this as an idea please")
    assert _detect_save_as_idea("park this as an idea")
    assert _detect_save_as_idea("add this to ideas")
    assert _detect_save_as_idea("file this as idea")
    assert _detect_save_as_idea("save as an idea")
    assert _detect_save_as_idea("file as an idea")


def test_detect_save_as_idea_no_false_positives():
    assert not _detect_save_as_idea("what is the status of my tasks?")
    assert not _detect_save_as_idea("I have a great idea")
    assert not _detect_save_as_idea("save my progress")


def test_extract_idea_content_from_prior_message():
    messages = [
        {"role": "user", "content": "Let's talk about a dark mode feature"},
        {"role": "assistant", "content": "Dark mode would invert the color palette."},
        {"role": "user", "content": "save this as an idea"},
    ]
    content = _extract_idea_content(messages)
    # Should pull from the prior conversation, not the command itself
    assert len(content) > 5
    assert "save this as an idea" not in content.lower()


# --- Admin template API endpoints -------------------------------------------

@pytest.mark.asyncio
async def test_admin_create_template(client):
    """POST /api/admin/templates creates a new custom template."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.create = MagicMock(return_value={
            "id": "custom-abc123",
            "name": "Bug report",
            "description": "For bugs",
            "icon": "bug_report",
            "prompt_template": "The bug is [what]",
            "breakdown_hint": "This is a bug.",
            "builtin": False,
        })
        resp = await client.post("/api/admin/templates", json={
            "name": "Bug report",
            "description": "For bugs",
            "icon": "bug_report",
            "prompt_template": "The bug is [what]",
            "breakdown_hint": "This is a bug.",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["template"]["name"] == "Bug report"
    assert data["template"]["id"] == "custom-abc123"


@pytest.mark.asyncio
async def test_admin_update_template(client):
    """PUT /api/admin/templates/{id} updates a custom template."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.update = MagicMock(return_value={
            "id": "custom-abc123",
            "name": "Updated name",
            "description": "",
            "icon": "star",
            "prompt_template": "[x]",
            "breakdown_hint": "",
            "builtin": False,
        })
        resp = await client.put("/api/admin/templates/custom-abc123", json={
            "name": "Updated name",
        })

    assert resp.status_code == 200
    assert resp.json()["template"]["name"] == "Updated name"


@pytest.mark.asyncio
async def test_admin_update_template_not_found(client):
    """PUT /api/admin/templates/{id} returns 404 if template does not exist."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.update = MagicMock(return_value=None)
        resp = await client.put("/api/admin/templates/no-such-id", json={"name": "X"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_delete_template(client):
    """DELETE /api/admin/templates/{id} deletes a custom template."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.delete = MagicMock(return_value=True)
        resp = await client.delete("/api/admin/templates/custom-abc123")

    assert resp.status_code == 200
    assert resp.json()["result"] == "deleted"


@pytest.mark.asyncio
async def test_admin_delete_template_not_found(client):
    """DELETE /api/admin/templates/{id} returns 404 if not found."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.delete = MagicMock(return_value=False)
        resp = await client.delete("/api/admin/templates/no-such-id")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_list_custom_templates(client):
    """GET /api/admin/templates lists only custom templates."""
    with patch("routers.ideas.templates_store") as mock_store:
        mock_store.list_custom = MagicMock(return_value=[
            {"id": "custom-1", "name": "My tpl", "builtin": False},
        ])
        resp = await client.get("/api/admin/templates")

    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    assert len(data["templates"]) == 1
    assert data["templates"][0]["name"] == "My tpl"


# --- Chat-to-idea: add_hay_from_chat service ---------------------------------

class TestAddHayFromChat:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ostk_dir = Path(self.tmpdir) / ".ostk"
        self.ostk_dir.mkdir()
        self.audit_path = self.ostk_dir / "audit.jsonl"
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_add_hay_from_chat_sets_source(self):
        """add_hay_from_chat should mark the hay entry with source=chat."""
        # Seed audit.jsonl with a hay.filed entry as the CLI would
        entry = {
            "event": "hay.filed",
            "straw": "dark mode feature",
            "source": "user",
            "timestamp": "2026-04-08T10:00:00Z",
        }
        self.audit_path.write_text(json.dumps(entry) + "\n")

        with patch.object(self.svc, "_run", new=AsyncMock(return_value="added hay")):
            await self.svc.add_hay_from_chat("dark mode feature")

        lines = [json.loads(l) for l in self.audit_path.read_text().strip().splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0]["source"] == "chat"
        assert lines[0]["straw"] == "dark mode feature"

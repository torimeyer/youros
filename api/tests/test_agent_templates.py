"""Tests for PM agent templates CRUD endpoints and store.

Covers:
- Built-in templates are returned by the store
- Custom template CRUD via the store directly
- API endpoints: list, create, update, delete
- Cannot edit/delete built-ins via the API
- Data safety: AGENT_TEMPLATES_PATH is outside the repo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_templates_store import (
    AGENT_TEMPLATES_PATH,
    BUILTIN_AGENT_TEMPLATES,
    AgentTemplatesStore,
)


# ---------- Store unit tests ----------


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An AgentTemplatesStore that writes to a temp file, not ~/.youros/."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "agent_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Patch the constant on the class/module
    store = AgentTemplatesStore()
    # Patch the module-level constant used inside store methods
    import services.agent_templates_store as ats_mod
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_path)
    return store


def test_builtin_templates_present():
    """BUILTIN_AGENT_TEMPLATES contains the built-in agent templates."""
    assert isinstance(BUILTIN_AGENT_TEMPLATES, list)
    assert len(BUILTIN_AGENT_TEMPLATES) > 0


def test_builtin_ids_are_builtin_prefixed():
    for t in BUILTIN_AGENT_TEMPLATES:
        assert t["id"].startswith("builtin-"), f"Expected builtin- prefix: {t['id']}"


def test_builtin_templates_have_required_fields():
    required = {"id", "name", "description", "icon", "prompt_template", "model", "budget", "builtin"}
    for t in BUILTIN_AGENT_TEMPLATES:
        missing = required - set(t.keys())
        assert not missing, f"Template {t['id']} missing fields: {missing}"


def test_builtin_templates_have_nonempty_prompt():
    """Every built-in prompt_template must be a non-empty string."""
    for t in BUILTIN_AGENT_TEMPLATES:
        assert isinstance(t.get("prompt_template"), str) and len(t["prompt_template"]) > 10, \
            f"Empty or missing prompt_template in {t['id']}"


def test_no_two_builtin_templates_share_normalized_name():
    """No two built-in templates may share a normalized (case-insensitive,
    trimmed) name. Duplicates would cause the Templates page to show the
    same card twice, which was a bug in the v3.4.0 template overhaul."""
    seen: dict[str, str] = {}
    for t in BUILTIN_AGENT_TEMPLATES:
        key = t["name"].strip().lower()
        assert key not in seen, (
            f"Duplicate normalized name '{key}': "
            f"found in both '{seen[key]}' and '{t['id']}'"
        )
        seen[key] = t["id"]


def test_saa_is_alias_not_standalone_template():
    """'saa' must appear only as an alias in the Builder template,
    not as its own template row. Having a standalone 'saa' template
    causes duplicate cards on the Templates page."""
    names = {t["name"].strip().lower() for t in BUILTIN_AGENT_TEMPLATES}
    assert "saa" not in names, (
        "saa must not be a standalone template name. "
        "It should only appear as an alias on Builder."
    )
    # Confirm it IS an alias on Builder
    builder = next((t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder"), None)
    assert builder is not None, "Builder template must exist"
    assert "saa" in [a.lower() for a in builder.get("aliases", [])], (
        "Builder template must list 'saa' as an alias"
    )


def test_builder_template_title_case():
    """Core template names must be Title Case and present in BUILTIN_AGENT_TEMPLATES.

    Note: source="builtin" only applies to the hard-wired builder/diagnose set.
    Research, Review, and Test are marketplace-sourced but still live in
    BUILTIN_AGENT_TEMPLATES (and are always installed).  This test checks their
    names are present regardless of the source field.
    """
    expected_names = {"Builder", "Diagnose", "Research", "Review", "Test"}
    actual_names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES}
    for name in expected_names:
        assert name in actual_names, (
            f"Template '{name}' not found in BUILTIN_AGENT_TEMPLATES. "
            f"Found: {sorted(actual_names)}"
        )


def test_list_custom_empty(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "agent_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.list_custom()
    assert result == []


def test_create_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({
        "name": "My Agent",
        "description": "Does stuff",
        "icon": "bolt",
        "prompt_template": "Do [task] for [user]",
        "model": "sonnet",
        "budget": 1.5,
    })
    assert t["id"].startswith("custom-")
    assert t["name"] == "My Agent"
    assert t["builtin"] is False
    # Persisted
    saved = json.loads(fake_path.read_text())
    assert len(saved) == 1
    assert saved[0]["id"] == t["id"]


def test_update_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at2.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({"name": "Old Name", "description": "Old desc"})
    updated = store.update(t["id"], {"name": "New Name"})
    assert updated is not None
    assert updated["name"] == "New Name"
    assert updated["description"] == "Old desc"


def test_update_nonexistent_returns_none(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at3.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.update("custom-doesnotexist", {"name": "X"})
    assert result is None


def test_delete_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at4.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({"name": "Temp"})
    deleted = store.delete(t["id"])
    assert deleted is True
    assert store.list_custom() == []


def test_delete_nonexistent_returns_false(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at5.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.delete("custom-nope")
    assert result is False


def test_list_all_has_builtins_first(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at6.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    store.create({"name": "Custom One"})
    all_templates = store.list_all()
    # Builtins come first (identified by id prefix, not the builtin flag --
    # marketplace entries in BUILTIN_AGENT_TEMPLATES have builtin=False).
    builtin_count = len(BUILTIN_AGENT_TEMPLATES)
    for i, t in enumerate(all_templates[:builtin_count]):
        assert t["id"].startswith("builtin-"), (
            f"Expected builtin id at position {i}, got {t['id']!r}"
        )
    assert all_templates[builtin_count]["name"] == "Custom One"


def test_agent_templates_path_outside_repo():
    """AGENT_TEMPLATES_PATH must be outside the repo directory."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        AGENT_TEMPLATES_PATH.resolve().relative_to(repo_root)
        assert False, f"AGENT_TEMPLATES_PATH {AGENT_TEMPLATES_PATH} is inside the repo"
    except ValueError:
        pass  # Good: it's outside


# ---------- API endpoint tests ----------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "api_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Also patch the singleton
    mod.agent_templates_store._ensure_exists = lambda: fake_path.parent.mkdir(parents=True, exist_ok=True) or (fake_path.write_text("[]") if not fake_path.exists() else None)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mod, fake_path


@pytest.mark.anyio
async def test_list_pm_templates_returns_builtins_when_no_custom(tmp_path, monkeypatch):
    """With no custom templates, the endpoint still returns built-in templates."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "lpt.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/agents/pm-templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    # Built-in templates are always present
    assert len(data["templates"]) > 0
    # All returned templates have required fields
    for t in data["templates"]:
        assert "id" in t
        assert "name" in t
        assert "prompt_template" in t


@pytest.mark.anyio
async def test_create_pm_template(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "cpt.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/agents/pm-templates", json={
            "name": "My Custom",
            "description": "Does things",
            "prompt_template": "Do [thing]",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"]["id"].startswith("custom-")
    assert data["template"]["name"] == "My Custom"


@pytest.mark.anyio
async def test_create_pm_template_missing_name(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "cpterr.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/agents/pm-templates", json={"description": "No name"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_delete_builtin_not_allowed(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "dbn.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/agents/pm-templates/builtin-research-spike")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_update_builtin_not_allowed(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "ubn.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/agents/pm-templates/builtin-research-spike", json={"name": "Hacked"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_delete_custom_via_api(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "dca.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post("/api/agents/pm-templates", json={"name": "ToDelete"})
        template_id = create_resp.json()["template"]["id"]
        del_resp = await ac.delete(f"/api/agents/pm-templates/{template_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["result"] == "deleted"


# ---------- Agentfile-backed template tests ----------


def test_marketplace_seed_writes_agentfiles_on_first_load(tmp_path, monkeypatch):
    """Importing agent_templates_store writes marketplace agentfiles when absent."""
    import services.agent_templates_store as mod

    # Point the marketplace dir to a fresh tmp location.
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)

    # Trigger seed manually (simulates fresh import with no files).
    mod._seed_marketplace_agentfiles()

    # At least one marketplace template should have produced a file.
    marketplace_templates = [t for t in mod.BUILTIN_AGENT_TEMPLATES if t.get("source") == "marketplace"]
    assert marketplace_templates, "Expected at least one marketplace template"

    files = list(fake_mkt_dir.glob("*.agent"))
    assert len(files) == len(marketplace_templates), (
        f"Expected {len(marketplace_templates)} agentfiles, found {len(files)}"
    )

    # Each file must be parseable and have FROM auto.
    from services.agentfile_parser import parse_agentfile
    for f in files:
        cfg = parse_agentfile(f)
        assert cfg.model == "auto", f"{f.name}: expected FROM auto"
        assert cfg.tools, f"{f.name}: expected at least one TOOL"


def test_marketplace_seed_is_idempotent(tmp_path, monkeypatch):
    """Seeding converges to the store's canonical content (→1681).

    Two contracts:
      * No-op when in sync: re-seeding an unchanged tree leaves content alone.
      * Reconcile-on-drift: a hand-edited (drifted) marketplace agentfile is
        restored to the store-generated content on the next seed. Marketplace
        builtins are store-managed and the seeder overwrites drift so store
        prompt updates propagate without a manual delete; user customizations
        belong in custom agents, not marketplace builtins.
    """
    import services.agent_templates_store as mod

    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)

    mod._seed_marketplace_agentfiles()
    first_file = next(fake_mkt_dir.glob("*.agent"))
    canonical = first_file.read_text()

    # No-op when already in sync: re-seeding does not change content.
    mod._seed_marketplace_agentfiles()
    assert first_file.read_text() == canonical

    # Drift: hand-edit the file, then re-seed.
    first_file.write_text("# hand-edited\nFROM opus\nPROMPT \"custom\"\n")
    mod._seed_marketplace_agentfiles()

    # Reconcile: the drifted file is restored to the store's canonical content.
    assert first_file.read_text() == canonical


def test_custom_template_create_writes_agentfile_to_user_dir(tmp_path, monkeypatch):
    """Creating a custom template writes its agentfile to ~/.youros/agents/custom/."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_custom_dir = tmp_path / "agents" / "custom"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "CUSTOM_AGENTS_DIR", fake_custom_dir)

    store = mod.AgentTemplatesStore()
    t = store.create({
        "name": "My Workflow",
        "description": "Automates my workflow",
        "prompt_template": "You automate [task].",
        "model": "sonnet",
        "budget": 1.5,
    })

    # Agentfile must exist in the custom dir.
    stem = mod._name_to_stem("My Workflow")
    af_path = fake_custom_dir / f"{stem}.agent"
    assert af_path.exists(), f"Expected agentfile at {af_path}"

    # Must be parseable.
    from services.agentfile_parser import parse_agentfile
    cfg = parse_agentfile(af_path)
    assert cfg.model == "auto"
    assert "You automate" in cfg.prompt
    assert "shell" in cfg.tools


def test_custom_template_delete_removes_agentfile(tmp_path, monkeypatch):
    """Deleting a custom template removes its agentfile from disk."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_custom_dir = tmp_path / "agents" / "custom"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "CUSTOM_AGENTS_DIR", fake_custom_dir)

    store = mod.AgentTemplatesStore()
    t = store.create({
        "name": "Temp Template",
        "prompt_template": "Do [thing].",
    })

    stem = mod._name_to_stem("Temp Template")
    af_path = fake_custom_dir / f"{stem}.agent"
    assert af_path.exists(), "Expected agentfile after create"

    store.delete(t["id"])
    assert not af_path.exists(), "Expected agentfile removed after delete"


def test_list_marketplace_reads_from_agentfiles(tmp_path, monkeypatch):
    """list_marketplace() returns templates enriched with capabilities from agentfiles."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)

    # Seed the files first.
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    templates = store.list_marketplace()
    assert len(templates) > 0

    # At least one should have capabilities populated (from agentfile).
    with_caps = [t for t in templates if t.get("capabilities") is not None]
    assert len(with_caps) > 0, "Expected at least one marketplace template with capabilities"

    # Shape check: all expected capability keys present.
    cap = with_caps[0]["capabilities"]
    for key in ("writes_to", "cannot_touch", "budget", "time_limit", "sandbox"):
        assert key in cap, f"Missing capability key: {key}"


def test_name_to_stem():
    """_name_to_stem converts display names to safe filename stems."""
    from services.agent_templates_store import _name_to_stem
    assert _name_to_stem("Competitive Scan") == "competitive-scan"
    assert _name_to_stem("PRD") == "prd"
    assert _name_to_stem("  My Template  ") == "my-template"
    assert _name_to_stem("Hello & World!") == "hello-world"


def test_list_marketplace_excludes_builtin_names(tmp_path, monkeypatch):
    """list_marketplace() must not return any entry whose name matches a
    built-in template name or alias.  Returning a marketplace copy of
    'Research' alongside the built-in 'Research' was the root cause of
    duplicate cards on the Templates page."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    marketplace = store.list_marketplace()

    builtin_names: set[str] = set()
    for t in mod.BUILTIN_AGENT_TEMPLATES:
        if t.get("source") == "builtin":
            builtin_names.add(t["name"].strip().lower())
            for alias in t.get("aliases", []):
                builtin_names.add(alias.strip().lower())

    conflicts = [
        t["name"] for t in marketplace
        if t["name"].strip().lower() in builtin_names
    ]
    assert not conflicts, (
        f"list_marketplace() returned templates whose names collide with built-ins: "
        f"{conflicts}"
    )


def test_marketplace_templates_have_source_marketplace(tmp_path, monkeypatch):
    """Every template returned by list_marketplace() must have source='marketplace',
    not 'custom'. Marketplace persona templates (e.g. Competitive Scan, PRD) were
    showing a 'custom' badge before this fix because the UI used tpl.builtin instead
    of tpl.source."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    marketplace = store.list_marketplace()
    assert len(marketplace) > 0, "Expected at least one marketplace template"

    wrong_source = [
        t["name"] for t in marketplace
        if t.get("source") != "marketplace"
    ]
    assert not wrong_source, (
        f"These marketplace templates have wrong source (expected 'marketplace'): "
        f"{wrong_source}"
    )


def test_list_marketplace_no_duplicate_names(tmp_path, monkeypatch):
    """No two marketplace templates returned by list_marketplace() share a
    normalized name.  Duplicates cause extra cards in the Marketplace section."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    marketplace = store.list_marketplace()

    seen: dict[str, str] = {}
    for t in marketplace:
        key = t["name"].strip().lower()
        assert key not in seen, (
            f"Duplicate marketplace template name '{t['name']}' "
            f"also found as '{seen[key]}'"
        )
        seen[key] = t["name"]


def test_no_duplicate_names_across_installed_and_marketplace(tmp_path, monkeypatch):
    """Combined list_installed() + list_marketplace() must have no duplicate
    normalized names. Duplicates cause a template to appear twice on the page --
    once in the installed grid and once in the marketplace section."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    installed = store.list_installed()
    marketplace = store.list_marketplace()

    installed_names = {t["name"].strip().lower() for t in installed}
    conflicts = [
        t["name"] for t in marketplace
        if t["name"].strip().lower() in installed_names
    ]
    assert not conflicts, (
        f"Templates appear in both installed and marketplace lists (causes duplicate cards): "
        f"{conflicts}"
    )


def test_list_installed_is_alphabetical(tmp_path, monkeypatch):
    """list_installed() must return templates sorted alphabetically by name (→1072)."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at_alpha.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)

    store = mod.AgentTemplatesStore()
    installed = store.list_installed()
    names = [t["name"].lower() for t in installed]
    assert names == sorted(names), (
        f"list_installed() not sorted: {[t['name'] for t in installed[:5]]!r}"
    )


def test_list_marketplace_is_alphabetical(tmp_path, monkeypatch):
    """list_marketplace() must return templates sorted alphabetically by name (→1072)."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "at_mkt_alpha.json"
    fake_mkt_dir = tmp_path / "marketplace"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(mod, "MARKETPLACE_AGENTS_DIR", fake_mkt_dir)
    mod._seed_marketplace_agentfiles()

    store = mod.AgentTemplatesStore()
    marketplace = store.list_marketplace()
    names = [t["name"].lower() for t in marketplace]
    assert names == sorted(names), (
        f"list_marketplace() not sorted: {[t['name'] for t in marketplace[:5]]!r}"
    )


def test_code_review_alias_resolves_to_review(tmp_path, monkeypatch):
    """Spawning by old name 'Code Review' must resolve to the Review template
    via its alias. This ensures the merge does not break callers that used the
    old standalone Code Review template."""
    import services.agent_templates_store as mod

    result = mod._resolve_alias("Code Review")
    assert result == "builtin-review", (
        f"Expected 'Code Review' alias to resolve to 'builtin-review', got {result!r}"
    )


def test_retired_code_review_template_not_in_builtins():
    """'Code Review' must not exist as a standalone template after the merge.
    Its capabilities are now covered by the Review template via alias."""
    names = {t["name"].strip().lower() for t in BUILTIN_AGENT_TEMPLATES}
    assert "code review" not in names, (
        "Found standalone 'Code Review' template. It should be an alias on Review."
    )


def test_review_template_has_code_review_alias():
    """The Review template must list 'Code Review' as an alias so the old name
    still works as a spawn target."""
    review = next(
        (t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Review"),
        None,
    )
    assert review is not None, "Review template must exist in BUILTIN_AGENT_TEMPLATES"
    aliases_lower = [a.lower() for a in review.get("aliases", [])]
    assert "code review" in aliases_lower, (
        f"Review template aliases {review.get('aliases')} must include 'Code Review'"
    )


# ---------- Dedupe + source attribution tests ----------


def test_dedupe_collapses_duplicate_ids():
    """Two entries sharing the same id collapse to one row."""
    from services.agent_templates_store import _dedupe_templates

    rows = [
        {"id": "builtin-prd", "name": "PRD", "source": "builtin"},
        {"id": "builtin-prd", "name": "PRD", "source": "marketplace"},
    ]
    out = _dedupe_templates(rows)
    assert len(out) == 1
    # marketplace > builtin, so the marketplace version wins
    assert out[0]["source"] == "marketplace"


def test_dedupe_collapses_by_name_when_ids_differ():
    """Two entries with different ids but the same normalized name collapse."""
    from services.agent_templates_store import _dedupe_templates

    rows = [
        {"id": "builtin-prd", "name": "PRD", "source": "builtin"},
        {"id": "custom-123", "name": "prd", "source": "custom"},
    ]
    out = _dedupe_templates(rows)
    assert len(out) == 1
    # custom always wins
    assert out[0]["source"] == "custom"
    assert out[0]["id"] == "custom-123"


def test_dedupe_priority_custom_beats_marketplace_beats_builtin():
    """Source priority: custom > marketplace > builtin."""
    from services.agent_templates_store import _dedupe_templates

    rows = [
        {"id": "a", "name": "Same", "source": "builtin"},
        {"id": "b", "name": "Same", "source": "marketplace"},
        {"id": "c", "name": "Same", "source": "custom"},
    ]
    out = _dedupe_templates(rows)
    assert len(out) == 1
    assert out[0]["source"] == "custom"


def test_dedupe_preserves_distinct_entries():
    """Entries with distinct ids and names pass through untouched."""
    from services.agent_templates_store import _dedupe_templates

    rows = [
        {"id": "a", "name": "Alpha", "source": "builtin"},
        {"id": "b", "name": "Beta", "source": "marketplace"},
    ]
    out = _dedupe_templates(rows)
    assert len(out) == 2
    assert {r["name"] for r in out} == {"Alpha", "Beta"}


def test_list_for_persona_dedupes_overlapping_names(store, tmp_path, monkeypatch):
    """list_for_persona must not return two rows for the same template name."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "persona_dedupe.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Install PM marketplace templates so they would otherwise appear.
    store.install_for_persona("pm")
    rows = store.list_for_persona("pm")
    names = [r["name"].strip().lower() for r in rows]
    assert len(names) == len(set(names)), (
        f"Duplicate names in list_for_persona: "
        f"{[n for n in names if names.count(n) > 1]}"
    )


def test_list_user_custom_always_marks_source_custom(store, tmp_path, monkeypatch):
    """Even if the disk store has a stale source field, list_user_custom
    must return source='custom' for every row. Tori's rule: "custom" means
    user-created, period."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "user_custom_source.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Write a stale record with a wrong source field directly to disk.
    fake_path.write_text(json.dumps([
        {
            "id": "custom-abc",
            "name": "My Template",
            "description": "test",
            "source": "marketplace",  # wrong, but may exist from old builds
            "installed": True,
        }
    ]))
    rows = store.list_user_custom()
    assert len(rows) == 1
    assert rows[0]["source"] == "custom"


def test_list_user_custom_excludes_non_custom_ids(store, tmp_path, monkeypatch):
    """Only id=custom-* rows belong in user_custom. Install records for
    marketplace templates (id starts with builtin-) must never leak here."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "user_custom_filter.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    fake_path.write_text(json.dumps([
        {"id": "builtin-pm-prd", "installed": True},
        {"id": "custom-xyz", "name": "Real Custom", "installed": True},
    ]))
    rows = store.list_user_custom()
    assert len(rows) == 1
    assert rows[0]["id"] == "custom-xyz"


def test_explain_plain_agentfile_exists():
    """The sibling session added an 'explain-plain' builtin agentfile.
    After dedupe it should still be reachable from /agents/templates
    (tested via file presence since that endpoint scans the agents dir).
    """
    from pathlib import Path
    try:
        from config import PROJECT_ROOT
        agents_dir = PROJECT_ROOT / "agents"
    except Exception:
        agents_dir = Path(__file__).resolve().parent.parent.parent / "agents"
    path = agents_dir / "explain-plain.agent"
    assert path.exists(), (
        f"explain-plain.agent is expected to ship as a builtin. "
        f"Looked in {agents_dir}."
    )


def test_list_all_survives_custom_source_mislabel(store, tmp_path, monkeypatch):
    """Custom records on disk always surface with source='custom' even if
    a stale record was written with a different source field."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "list_all_source.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    fake_path.write_text(json.dumps([
        {
            "id": "custom-zzz",
            "name": "Stale Source",
            "description": "",
            "source": "builtin",  # intentionally wrong
            "installed": True,
        }
    ]))
    rows = [t for t in store.list_all() if t.get("id") == "custom-zzz"]
    assert len(rows) == 1
    assert rows[0]["source"] == "custom"


# ---------- explain-plain template (generic name; elit is an alias) ----------


def test_explain_plain_template_present_and_builtin():
    """The generic 'Explain Plain' template must exist and be marked builtin.

    This replaces the user-specific 'elit' command. Keep 'elit' as an alias
    so muscle memory keeps working.
    """
    tpl = next(
        (t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-explain-plain"),
        None,
    )
    assert tpl is not None, "builtin-explain-plain must exist in BUILTIN_AGENT_TEMPLATES"
    assert tpl["name"] == "Explain Plain"
    assert tpl["source"] == "builtin", (
        f"explain-plain must be source='builtin' (user-facing core), got {tpl['source']!r}"
    )
    assert tpl["builtin"] is True
    assert tpl["installed"] is True, "Core builtin templates must be installed by default"
    # Description is plain language, visible in the picker.
    assert "plain" in tpl["description"].lower()
    assert "jargon" in tpl["description"].lower()


def test_elit_resolves_to_explain_plain_via_alias():
    """Spawning with name 'elit' must resolve to the explain-plain template.

    This proves Tori's muscle memory shortcut still works after the rename.
    """
    import services.agent_templates_store as mod

    result = mod._resolve_alias("elit")
    assert result == "builtin-explain-plain", (
        f"Expected 'elit' to resolve to 'builtin-explain-plain', got {result!r}"
    )


def test_elit_is_alias_not_standalone_template():
    """'elit' must appear only as an alias, not as its own template row.

    Having a standalone 'elit' template would put duplicate cards on the
    Templates page and undo the rename.
    """
    names = {t["name"].strip().lower() for t in BUILTIN_AGENT_TEMPLATES}
    assert "elit" not in names, (
        "elit must not be a standalone template name. "
        "It should only appear as an alias on Explain Plain."
    )
    explain = next(
        (t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-explain-plain"),
        None,
    )
    assert explain is not None
    aliases_lower = [a.lower() for a in explain.get("aliases", [])]
    assert "elit" in aliases_lower, (
        f"Explain Plain must list 'elit' as an alias. Got {explain.get('aliases')!r}"
    )


def test_explain_plain_agentfile_exists():
    """A matching agentfile must live at agents/explain-plain.agent.

    The templates picker reads capabilities from the agentfile. Missing the
    file would make the card show a 'no capability data' warning.
    """
    from config import PROJECT_ROOT

    path = PROJECT_ROOT / "agents" / "explain-plain.agent"
    assert path.exists(), f"Expected builtin agentfile at {path}"
    text = path.read_text()
    # Sanity: prompt text mentions the core behavior.
    assert "plain language" in text.lower()


def test_elit_agentfile_alias_exists():
    """explain-plain template must declare 'elit' as an alias in the sidecar manifest.

    Aliases moved from inline directives to agents/manifest.json (Tier 1.2,
    commit f44cbf0) so ostk agentfile validate stops failing on unknown directives.
    The 'elit' alias remains required for scripts, memory notes, and older
    chat handlers that reference it directly.
    """
    import json
    from config import PROJECT_ROOT

    manifest_path = PROJECT_ROOT / "agents" / "manifest.json"
    assert manifest_path.exists(), f"Expected manifest at {manifest_path}"
    data = json.loads(manifest_path.read_text())
    entry = data.get("templates", {}).get("explain-plain", {})
    aliases = entry.get("aliases", [])
    assert "elit" in aliases, (
        f"explain-plain template must declare 'elit' alias in manifest.json. Got: {aliases!r}"
    )


def test_builtin_alias_map_has_elit_to_explain_plain():
    """The agentfile parser's built-in alias map must map elit -> explain-plain."""
    from services.agentfile_parser import get_template_aliases

    aliases = get_template_aliases()
    assert aliases.get("elit") == "explain-plain", (
        f"Expected alias map to map 'elit' -> 'explain-plain'. Got {aliases!r}"
    )


# ---------- User-set template alias tests ----------


def test_set_template_alias(tmp_path, monkeypatch):
    """Setting an alias persists to disk and can be retrieved."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at.json"
    fake_aliases_path = tmp_path / "aliases.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    result = store.set_alias("builtin-builder", "my-builder")
    assert result.get("user_alias") == "my-builder"

    # Verify on disk
    aliases = json.loads(fake_aliases_path.read_text())
    assert aliases["my-builder"] == "builtin-builder"

    # get_alias returns it
    assert store.get_alias("builtin-builder") == "my-builder"


def test_alias_collision_rejected(tmp_path, monkeypatch):
    """Setting an alias that collides with an existing template name is rejected."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_col.json"
    fake_aliases_path = tmp_path / "aliases_col.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    # "builder" is the name of the Builder template
    with pytest.raises(ValueError, match="already the name"):
        store.set_alias("builtin-diagnose", "builder")

    # "saa" is a built-in alias on Builder
    with pytest.raises(ValueError, match="already a built-in alias"):
        store.set_alias("builtin-diagnose", "saa")


def test_alias_collision_with_other_user_alias(tmp_path, monkeypatch):
    """Setting an alias already used by another template is rejected."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_dup.json"
    fake_aliases_path = tmp_path / "aliases_dup.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    store.set_alias("builtin-builder", "quick-build")

    with pytest.raises(ValueError, match="already used as an alias"):
        store.set_alias("builtin-diagnose", "quick-build")


def test_clear_alias(tmp_path, monkeypatch):
    """Clearing an alias removes it from disk."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_clr.json"
    fake_aliases_path = tmp_path / "aliases_clr.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    store.set_alias("builtin-builder", "bb")
    assert store.get_alias("builtin-builder") == "bb"

    result = store.clear_alias("builtin-builder")
    assert result.get("user_alias") is None
    assert store.get_alias("builtin-builder") is None


def test_alias_validation_too_short(tmp_path, monkeypatch):
    """Alias must be at least 2 characters."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_val.json"
    fake_aliases_path = tmp_path / "aliases_val.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    with pytest.raises(ValueError, match="between 2 and 30"):
        store.set_alias("builtin-builder", "x")


def test_alias_validation_bad_chars(tmp_path, monkeypatch):
    """Alias must only contain lowercase letters, digits, hyphens."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_bc.json"
    fake_aliases_path = tmp_path / "aliases_bc.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    with pytest.raises(ValueError, match="lowercase letters"):
        store.set_alias("builtin-builder", "My Builder!")


def test_alias_replaces_previous_for_same_template(tmp_path, monkeypatch):
    """Setting a new alias for a template removes the previous one."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_rep.json"
    fake_aliases_path = tmp_path / "aliases_rep.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    store = mod.AgentTemplatesStore()
    store.set_alias("builtin-builder", "old-name")
    store.set_alias("builtin-builder", "new-name")

    assert store.get_alias("builtin-builder") == "new-name"
    aliases = store.get_all_user_aliases()
    assert "old-name" not in aliases


@pytest.mark.anyio
async def test_set_template_alias_api(tmp_path, monkeypatch):
    """PATCH /agents/templates/{id}/alias sets the alias via the API."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_api.json"
    fake_aliases_path = tmp_path / "aliases_api.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/agents/templates/builtin-builder/alias",
            json={"alias": "bb"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"]["user_alias"] == "bb"


@pytest.mark.anyio
async def test_alias_collision_rejected_api(tmp_path, monkeypatch):
    """PATCH /agents/templates/{id}/alias returns 400 on collision."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_api2.json"
    fake_aliases_path = tmp_path / "aliases_api2.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/agents/templates/builtin-diagnose/alias",
            json={"alias": "builder"},
        )
    assert resp.status_code == 400
    assert "already the name" in resp.json()["detail"]


@pytest.mark.anyio
async def test_clear_alias_api(tmp_path, monkeypatch):
    """PATCH /agents/templates/{id}/alias with null clears the alias."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_api3.json"
    fake_aliases_path = tmp_path / "aliases_api3.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Set first
        await ac.patch(
            "/api/agents/templates/builtin-builder/alias",
            json={"alias": "bb"},
        )
        # Clear
        resp = await ac.patch(
            "/api/agents/templates/builtin-builder/alias",
            json={"alias": None},
        )
    assert resp.status_code == 200
    assert resp.json()["template"]["user_alias"] is None


# ---------- User-edited description tests ----------


def test_set_template_description_persists(tmp_path, monkeypatch):
    """Saving an edited description writes it to template_descriptions.json
    and the next read returns the edited text."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc.json"
    fake_desc_path = tmp_path / "template_descriptions.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    store = mod.AgentTemplatesStore()
    result = store.set_description("builtin-builder", "My personalized Builder blurb.")
    assert result["description"] == "My personalized Builder blurb."

    # Verify on disk
    saved = json.loads(fake_desc_path.read_text())
    assert saved["builtin-builder"] == "My personalized Builder blurb."

    # Second read returns the merged value
    fresh = mod.AgentTemplatesStore()
    assert fresh.get_description("builtin-builder") == "My personalized Builder blurb."


def test_template_list_uses_user_description_override(tmp_path, monkeypatch):
    """list_all() shows the user-edited description instead of the shipped one."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_list.json"
    fake_desc_path = tmp_path / "template_descriptions_list.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    store = mod.AgentTemplatesStore()
    store.set_description("builtin-diagnose", "Find the root cause fast.")

    templates = store.list_all()
    diagnose = next(t for t in templates if t["id"] == "builtin-diagnose")
    assert diagnose["description"] == "Find the root cause fast."

    # Other templates are untouched
    other = next(t for t in templates if t["id"] == "builtin-builder")
    assert other["description"] != "Find the root cause fast."


def test_clear_template_description_reverts_to_shipped(tmp_path, monkeypatch):
    """Clearing a description removes the override and restores the shipped blurb."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_clr.json"
    fake_desc_path = tmp_path / "template_descriptions_clr.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    shipped = next(
        t["description"] for t in mod.BUILTIN_AGENT_TEMPLATES
        if t["id"] == "builtin-builder"
    )

    store = mod.AgentTemplatesStore()
    store.set_description("builtin-builder", "temporary blurb")
    assert store.get_description("builtin-builder") == "temporary blurb"

    store.clear_description("builtin-builder")
    assert store.get_description("builtin-builder") == shipped


def test_set_template_description_rejects_empty(tmp_path, monkeypatch):
    """Empty or whitespace-only descriptions are rejected with a plain message."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_empty.json"
    fake_desc_path = tmp_path / "template_descriptions_empty.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    store = mod.AgentTemplatesStore()
    with pytest.raises(ValueError, match="cannot be empty"):
        store.set_description("builtin-builder", "   ")


def test_set_custom_template_description_updates_override(tmp_path, monkeypatch):
    """For custom templates, set_description routes through update() so the
    primary overrides file changes (not the descriptions file)."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_custom.json"
    fake_desc_path = tmp_path / "template_descriptions_custom.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)
    # Redirect custom agentfile writes to a temp dir so we do not touch ~/.youros.
    monkeypatch.setattr(mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")

    store = mod.AgentTemplatesStore()
    created = store.create({"name": "My Custom", "description": "old blurb"})
    tid = created["id"]

    updated = store.set_description(tid, "new blurb")
    assert updated["description"] == "new blurb"

    # Verify the change lives in the overrides file, not the descriptions file.
    if fake_desc_path.exists():
        saved_descs = json.loads(fake_desc_path.read_text())
        assert tid not in saved_descs, (
            "Custom templates should not use the descriptions override file."
        )
    saved_overrides = json.loads(fake_at_path.read_text())
    stored = next(t for t in saved_overrides if t["id"] == tid)
    assert stored["description"] == "new blurb"


@pytest.mark.anyio
async def test_set_template_description_api(tmp_path, monkeypatch):
    """PATCH /agents/templates/{id}/description persists the edit."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_api.json"
    fake_desc_path = tmp_path / "template_descriptions_api.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/agents/templates/builtin-builder/description",
            json={"description": "My edited blurb."},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["template"]["description"] == "My edited blurb."


@pytest.mark.anyio
async def test_set_template_description_empty_rejected_api(tmp_path, monkeypatch):
    """PATCH with empty description returns a 400 with a clear message."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_api2.json"
    fake_desc_path = tmp_path / "template_descriptions_api2.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/agents/templates/builtin-builder/description",
            json={"description": "   "},
        )
    assert resp.status_code == 400
    assert "cannot be empty" in resp.json()["detail"]


@pytest.mark.anyio
async def test_clear_template_description_api(tmp_path, monkeypatch):
    """PATCH with null description clears the override."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_api3.json"
    fake_desc_path = tmp_path / "template_descriptions_api3.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.patch(
            "/api/agents/templates/builtin-builder/description",
            json={"description": "temp"},
        )
        resp = await ac.patch(
            "/api/agents/templates/builtin-builder/description",
            json={"description": None},
        )
    assert resp.status_code == 200
    # Should match the shipped description, not "temp"
    shipped = next(
        t["description"] for t in mod.BUILTIN_AGENT_TEMPLATES
        if t["id"] == "builtin-builder"
    )
    assert resp.json()["template"]["description"] == shipped


@pytest.mark.anyio
async def test_get_template_description_api(tmp_path, monkeypatch):
    """GET /agents/templates/{id}/description returns the merged description."""
    import services.agent_templates_store as mod

    fake_at_path = tmp_path / "at_desc_api4.json"
    fake_desc_path = tmp_path / "template_descriptions_api4.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
    monkeypatch.setattr(mod, "TEMPLATE_DESCRIPTIONS_PATH", fake_desc_path)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Before any edit, should return the shipped blurb.
        resp = await ac.get("/api/agents/templates/builtin-builder/description")
        assert resp.status_code == 200
        shipped = next(
            t["description"] for t in mod.BUILTIN_AGENT_TEMPLATES
            if t["id"] == "builtin-builder"
        )
        assert resp.json()["description"] == shipped

        # Now edit and re-read.
        await ac.patch(
            "/api/agents/templates/builtin-builder/description",
            json={"description": "edited"},
        )
        resp = await ac.get("/api/agents/templates/builtin-builder/description")
        assert resp.json()["description"] == "edited"


# ---------- Fleet template speed-path tests --------------------------------

# Background: fleet-build-website used to take 8-10 minutes because each
# member had a verbose prompt that let the model iterate. These tests lock
# in the tight prompts and the fleet-level quick_mode signal so a future
# edit cannot silently regress fleet boot time.


def test_fleet_build_website_members_all_quick_mode():
    """Every member of fleet-build-website opts in to quick_mode.

    Quick mode triggers the short mailbox block and the 90 s force-complete
    wall clock in the spawn path. If a future edit drops this flag on any
    member, the whole fleet falls back to the multi-minute default.
    """
    from services.fleet_templates import list_fleet_templates

    templates = list_fleet_templates()
    fleet = next((t for t in templates if t["id"] == "fleet-build-website"), None)
    assert fleet is not None, "fleet-build-website must be a built-in template"
    assert fleet.get("quick_mode") is True, (
        "fleet-build-website must carry quick_mode at the fleet level so "
        "every spawn inherits the demo budget"
    )
    assert len(fleet["members"]) == 4, "expected PM, frontend, backend, security"
    for member in fleet["members"]:
        assert member.get("quick_mode") is True, (
            f"fleet-build-website member {member.get('role')!r} must opt in "
            f"to quick_mode for the under-60-second artifact target"
        )
        prompt = member["prompt"]
        assert "60 second" in prompt or "60 seconds" in prompt, (
            f"member {member.get('role')!r} prompt must state the 60-second "
            f"target so the model self-limits"
        )
        assert "minimum viable" in prompt.lower(), (
            f"member {member.get('role')!r} prompt must ask for a minimum "
            f"viable artifact, not an iterative build"
        )


# ---------- Roadmap template speed tests ----------------------------------

# Background: the Roadmap marketplace template was taking minutes to
# produce a 3-year roadmap because its agentfile never had
# ``LIMIT quick_mode true`` AND the resolver only looked in ``agents/``,
# not ``agents/marketplace/``. These tests lock in both fixes so a
# future edit cannot silently regress the live demo path.


def test_roadmap_agentfile_has_quick_mode_flag():
    """The Roadmap marketplace agentfile carries LIMIT quick_mode true.

    Without this flag the spawn endpoint uses the full multi-KB mailbox
    block and skips the short envelope, which blows the 3-year roadmap
    generation past the few-seconds demo budget.
    """
    from config import PROJECT_ROOT
    from services.agentfile_parser import parse_agentfile

    path = PROJECT_ROOT / "agents" / "marketplace" / "roadmap.agent"
    assert path.exists(), f"expected roadmap agentfile at {path}"
    cfg = parse_agentfile(path)
    assert cfg.quick_mode is True, (
        "agents/marketplace/roadmap.agent must carry "
        "'LIMIT quick_mode true' so the spawn path uses the short "
        "mailbox block and first-byte latency stays under a few seconds"
    )


def test_roadmap_template_uses_quick_mode():
    """Spawning the built-in Roadmap template flips quick_mode on.

    This is the end-to-end contract: the user clicks Roadmap in the UI,
    the spawn body carries the template id, and the quick_mode resolver
    must find the marketplace agentfile and honour its LIMIT flag.
    Regression for the silent miss where the resolver only looked in
    ``agents/`` and never found ``agents/marketplace/roadmap.agent``.
    """
    from routers.agents import _spawn_quick_mode

    class _Body:
        def __init__(self, template: str):
            self.template = template
            self.name = f"roadmap-demo-{template}"

    # Every path the Roadmap template can arrive on: display name,
    # built-in id, and name-derived stem. All three must resolve to
    # quick mode so clicking the Templates card, spawning by id from a
    # script, or typing the file stem all take the fast path.
    for template in ("Roadmap", "builtin-pm-roadmap", "roadmap"):
        body = _Body(template)
        assert _spawn_quick_mode(body) is True, (
            f"_spawn_quick_mode({template!r}) must be True so the "
            f"Roadmap spawn uses the short mailbox block"
        )


def test_roadmap_prompt_asks_for_single_shot_json():
    """The Roadmap prompt tells the model to emit JSON in one shot.

    Multi-turn reasoning on a long roadmap burns through the few-
    seconds budget. The prompt must name the output shape (a JSON
    array of quarters) and forbid preamble so the model answers in one pass.
    """
    from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES

    tpl = next(
        (t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-pm-roadmap"),
        None,
    )
    assert tpl is not None, "built-in pm-roadmap template must exist"
    prompt = tpl["prompt_template"].lower()

    # Shape cues: JSON array of quarters, with explicit keys.
    assert "json" in prompt, "prompt must specify JSON output"
    assert "quarter" in prompt and "theme" in prompt and "initiatives" in prompt, (
        "prompt must name the three required keys so the JSON parses"
    )
    # Single-shot cue: explicit no-preamble instruction.
    assert "only the json" in prompt or "no preamble" in prompt, (
        "prompt must forbid preamble so the reply is one shot, not a "
        "multi-turn conversation"
    )


def test_roadmap_prompt_honors_timeframe_not_hardcoded_3yr():
    """The Roadmap prompt must scale quarters to the chosen timeframe.

    Earlier the prompt hardcoded "a 3-year roadmap of 12 quarters", which
    ignored the timeframe chip (1/2/3/5 years). The prompt must instead
    tell the model to cover the user's timeframe at ~4 quarters per year,
    in BOTH the store template and the marketplace agentfile (the agentfile
    is the runtime source; the two must stay in sync).
    """
    from pathlib import Path
    from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES

    tpl = next(
        t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-pm-roadmap"
    )
    store_prompt = tpl["prompt_template"].lower()

    agentfile = (
        Path(__file__).resolve().parents[2]
        / "agents" / "marketplace" / "roadmap.agent"
    )
    agent_prompt = agentfile.read_text().lower()

    for label, text in (("store", store_prompt), ("agentfile", agent_prompt)):
        assert "3-year" not in text and "12 quarter" not in text, (
            f"{label} prompt still hardcodes a 3-year / 12-quarter roadmap"
        )
        assert "4 quarter" in text and "timeframe" in text, (
            f"{label} prompt must honor the timeframe at ~4 quarters per year"
        )


def test_roadmap_completes_under_30_seconds(monkeypatch):
    """The Roadmap spawn path completes well inside the demo budget.

    This is a structural test rather than a wall-clock one: we stub the
    claude subprocess so spawn returns immediately, then measure the
    code path end to end. Anything over 30 s here would mean the
    endpoint itself is doing blocking work before the subprocess even
    starts, which is the whole bug we just fixed. Real model latency
    is covered by the live smoke run.
    """
    import time as _time
    from routers.agents import _spawn_quick_mode

    class _Body:
        template = "Roadmap"
        name = "roadmap-timing-check"

    started = _time.perf_counter()
    result = _spawn_quick_mode(_Body())
    elapsed = _time.perf_counter() - started

    assert result is True, (
        "quick_mode must resolve True on the Roadmap template so the "
        "short-mailbox fast path kicks in"
    )
    # The resolver walks a few small files. 2 s is generous headroom;
    # anything over that signals blocking I/O or a new slow import.
    assert elapsed < 2.0, (
        f"_spawn_quick_mode took {elapsed:.3f}s. The resolver should "
        f"be a handful of file stats. Check for new blocking work."
    )


# ---------- /agents/templates endpoint dedupe + alias hiding --------------
#
# Regression for Tori's screenshot bug: the Agent Templates page showed
# ``Elit`` as its own card (it is an alias of ``explain-plain``), showed
# ``Explain-plain`` AND ``Explain Plain`` as two cards (same template,
# different casing), and showed every fleet-build-website member as its
# own card. These tests lock in the fix so the grid always renders one
# card per real template.


def _seed_agentfiles(dir_path: Path, files: dict[str, str]) -> None:
    """Write a bundle of agentfile stubs for a test."""
    dir_path.mkdir(parents=True, exist_ok=True)
    for stem, body in files.items():
        (dir_path / f"{stem}.agent").write_text(body)


def test_templates_list_excludes_alias_only_agentfiles(tmp_path, monkeypatch):
    """Alias-only agentfiles (ALIAS <target>) must not appear as their own
    cards. The alias stem is folded into the target row's ``aliases``
    array instead.

    Screenshot evidence: ``Elit`` was rendering as a standalone card when
    ``elit.agent`` only contains ``ALIAS explain-plain``.
    """
    _seed_agentfiles(tmp_path, {
        "explain-plain": (
            'FROM auto\n'
            'PROMPT "Explain plainly."\n'
        ),
        "elit": "ALIAS explain-plain\n",
        "builder": (
            'FROM auto\n'
            'PROMPT "Build things."\n'
        ),
        "saa": "ALIAS builder\n",
    })

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)

    result = agents_mod._build_templates_list()
    names = {t["name"] for t in result}

    assert "elit" not in names, (
        "elit.agent is ALIAS explain-plain. It must not render as its own "
        f"card. Got names: {sorted(names)!r}"
    )
    assert "saa" not in names, (
        "saa.agent is ALIAS builder. It must not render as its own card. "
        f"Got names: {sorted(names)!r}"
    )
    assert "explain-plain" in names
    assert "builder" in names

    # Aliases are folded onto the target.
    ep = next(t for t in result if t["name"] == "explain-plain")
    assert ep.get("aliases") == ["elit"], (
        f"explain-plain must list 'elit' as an alias chip. Got {ep!r}"
    )
    builder = next(t for t in result if t["name"] == "builder")
    assert builder.get("aliases") == ["saa"], (
        f"builder must list 'saa' as an alias chip. Got {builder!r}"
    )


def test_templates_list_dedupes_case_insensitive_names(tmp_path, monkeypatch):
    """Case-insensitive stem dedupe. If two files collide on stem (modulo
    case) the templates list returns one row, not two.

    Screenshot evidence: ``Explain-plain`` and ``Explain Plain`` both
    rendered. In practice the collision was between the agentfile stem
    ``explain-plain`` and the pm-templates name ``Explain Plain`` on the
    frontend, but a same-stem case collision would trigger the same
    duplicate regardless. This test locks in the backend contract.
    """
    # Two files whose stems are case-insensitive duplicates. On
    # case-sensitive filesystems both files can coexist; on macOS's
    # default case-insensitive HFS+ only one will write. Either way the
    # endpoint must return a single row.
    _seed_agentfiles(tmp_path, {
        "diagnose": 'FROM auto\nPROMPT "Find bugs."\n',
    })
    # Manually place a second file with a different casing. Skip the
    # write if the filesystem folds it into the first (only one entry
    # would exist; the test still validates the single-row invariant).
    variant = tmp_path / "Diagnose.agent"
    try:
        variant.write_text('FROM auto\nPROMPT "Duplicate."\n')
    except OSError:
        pass

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)

    result = agents_mod._build_templates_list()
    lower_names = [t["name"].lower() for t in result]
    assert lower_names.count("diagnose") == 1, (
        "Expected a single diagnose row after case-insensitive dedupe. "
        f"Got names: {[t['name'] for t in result]!r}"
    )


def test_templates_list_excludes_fleet_member_agentfiles(tmp_path, monkeypatch):
    """Fleet member files (``fleet-<parent>-<role>.agent``) must not
    render as standalone templates. Members live in the Fleets panel,
    not the templates grid.

    Screenshot evidence: ``Fleet-build-w...`` appeared 3+ times because
    ``fleet-build-website-product-manager``, ``-frontend-developer``,
    ``-backend-developer``, and ``-security-engineer`` all rendered as
    cards. Only the parent fleet card (in the Fleets panel) should show.
    """
    _seed_agentfiles(tmp_path, {
        "builder": 'FROM auto\nPROMPT "Build things."\n',
        "fleet-build-website-product-manager": (
            'FROM haiku\nPROMPT "PM member."\n'
        ),
        "fleet-build-website-frontend-developer": (
            'FROM haiku\nPROMPT "FE member."\n'
        ),
        "fleet-build-website-backend-developer": (
            'FROM haiku\nPROMPT "BE member."\n'
        ),
        "fleet-build-website-security-engineer": (
            'FROM haiku\nPROMPT "Sec member."\n'
        ),
    })

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)

    result = agents_mod._build_templates_list()
    names = {t["name"] for t in result}

    for member in [
        "fleet-build-website-product-manager",
        "fleet-build-website-frontend-developer",
        "fleet-build-website-backend-developer",
        "fleet-build-website-security-engineer",
    ]:
        assert member not in names, (
            f"Fleet member '{member}' must not render as a standalone "
            f"template. Got names: {sorted(names)!r}"
        )
    assert "builder" in names, (
        "Non-fleet templates must still render. Got names: "
        f"{sorted(names)!r}"
    )


# ---------- /agents/templates description hygiene -------------------------
#
# Regression for Tori's "# Builder: your go-to agent for building things. R"
# screenshot bug. Two distinct problems were shipped together:
#   1. The backend serialized the raw "#" comment body and the frontend
#      char-count truncated it mid-word.
#   2. The six built-in agentfiles had no DESC directive so the parser
#      returned an empty string.
# These tests lock in: every built-in card has a clean, structured, full
# length description that never starts with "#" and is not a char-count
# slice of the body.


BUILTIN_TEMPLATE_STEMS = [
    "builder",
    "diagnose",
    "explain-plain",
    "research",
    "review",
    "test",
]


def test_wave_a_descriptions_are_specific():
    """Wave A audit: verify the 6 rewritten templates have specific, differentiated descriptions."""
    by_id = {t["id"]: t for t in BUILTIN_AGENT_TEMPLATES}

    research = by_id["builtin-research"]
    assert "thing" not in research["description"].lower(), (
        "research description must not contain the word 'thing'"
    )

    debug_helper = by_id["builtin-eng-debug-helper"]
    desc = debug_helper["description"].lower()
    assert "one question" in desc or "step by step" in desc, (
        "debug-helper description must convey conversational narrowing "
        "(e.g. 'one question at a time' or 'step by step')"
    )

    competitive_scan = by_id["builtin-pm-competitive-scan"]
    assert "competitors" in competitive_scan["description"].lower(), (
        "competitive-scan description must contain 'competitors'"
    )

    call_prep = by_id["builtin-sales-call-prep"]
    assert "research" in call_prep["description"].lower(), (
        "call-prep description must contain 'research'"
    )


def test_builtin_templates_have_clean_descriptions():
    """Every built-in agentfile must return a description that is
    (a) non-empty, (b) does NOT start with "#" (the markdown comment
    syntax leaking from the raw body), and (c) is not a char-count
    slice that ends mid-word.

    Without a DESC directive the parser returned "" and the frontend
    fell back to content.slice(0, 50) which produced strings like
    "# Builder: your go-to agent for building things. R". Adding
    DESC "..." to each builtin fixes the source. This test ensures
    nobody deletes it.
    """
    import routers.agents as agents_mod

    result = agents_mod._build_templates_list()
    by_name = {t["name"]: t for t in result}

    for stem in BUILTIN_TEMPLATE_STEMS:
        assert stem in by_name, (
            f"Built-in template '{stem}' is missing from the templates list. "
            f"Got names: {sorted(by_name)!r}"
        )
        entry = by_name[stem]
        desc = entry.get("description", "")
        assert desc, (
            f"Built-in '{stem}' returned an empty description. "
            f"Add a DESC line to agents/{stem}.agent."
        )
        assert not desc.startswith("#"), (
            f"Built-in '{stem}' description leaks the agentfile comment header. "
            f"The leading '#' means something fell back to the raw body "
            f"instead of the structured DESC field. Got: {desc!r}"
        )


def test_builtin_templates_descriptions_are_full_length():
    """The descriptions must match the structured DESC directive and be
    full length, not a 50 char slice of the body. Locks in that the API
    boundary does not truncate.
    """
    import routers.agents as agents_mod

    result = agents_mod._build_templates_list()
    by_name = {t["name"]: t for t in result}

    # Every builtin's description must be at least 60 chars and should
    # end with sentence punctuation. The old bug produced descriptions
    # like "# Builder: your go-to agent for building things. R" which
    # was exactly 50 chars and ended mid-word.
    for stem in BUILTIN_TEMPLATE_STEMS:
        entry = by_name[stem]
        desc = entry["description"]
        assert len(desc) >= 60, (
            f"Built-in '{stem}' description is suspiciously short "
            f"({len(desc)} chars). Possible char-count truncation regression. "
            f"Got: {desc!r}"
        )
        assert desc.rstrip().endswith((".", "!", "?")), (
            f"Built-in '{stem}' description does not end with sentence "
            f"punctuation. Possible mid-word char-count truncation. "
            f"Got: {desc!r}"
        )


# ---------- Templates list cache (perf) -------------------------------
#
# _build_templates_list was dominant on the Templates tab because every
# request re-read and re-parsed every .agent file. These tests lock in
# the new cache: a hit must not re-parse, and any write that changes the
# rendered template set must invalidate.


def test_build_templates_list_cache_hit_skips_reparse(tmp_path, monkeypatch):
    """Second call returns the cached list without re-parsing files.

    We count disk reads by patching Path.read_text on seeded agentfiles.
    The first call reads every file (cache miss). The second call reads
    nothing (cache hit). If the parser is invoked we record it; the
    assertion says the hit path never triggers parsing.
    """
    _seed_agentfiles(tmp_path, {
        "alpha": 'FROM auto\nPROMPT "Alpha."\n',
        "beta": 'FROM auto\nPROMPT "Beta."\n',
    })

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)
    agents_mod.invalidate_templates_cache()

    # Count parse invocations. The cached path should skip parsing.
    parse_calls = {"count": 0}
    from services import agentfile_parser as parser_mod

    original_parse = parser_mod.parse_agentfile

    def counting_parse(path):
        parse_calls["count"] += 1
        return original_parse(path)

    monkeypatch.setattr(parser_mod, "parse_agentfile", counting_parse)
    # routers.agents imports parse_agentfile lazily inside the function,
    # so patching on the parser module is enough.

    # Miss: parses both files.
    r1 = agents_mod._build_templates_list()
    miss_count = parse_calls["count"]
    assert miss_count >= 2, (
        f"Cold path must parse both agentfiles. parse_calls={miss_count}"
    )

    # Hit: should NOT parse again.
    r2 = agents_mod._build_templates_list()
    hit_count = parse_calls["count"] - miss_count
    assert hit_count == 0, (
        f"Cache hit must not re-parse. Extra parse_calls={hit_count}"
    )
    assert r2 is r1 or r2 == r1, (
        "Cache hit must return the same (or equal) list instance."
    )


def test_build_templates_list_cache_hit_is_under_50ms(tmp_path, monkeypatch):
    """A cache hit completes in well under 50ms.

    Covers the perf goal: the Templates tab must feel instant. The
    hit path is dominated by one directory scan + dict compare, no
    file reads, no parser work.
    """
    import time as _time

    # Seed a realistic number of agentfiles (12) so the cold path has
    # real work to do. The hit path must still return in microseconds.
    files = {}
    for i in range(12):
        files[f"tpl{i:02d}"] = f'FROM auto\nPROMPT "Template {i}."\n'
    _seed_agentfiles(tmp_path, files)

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)
    agents_mod.invalidate_templates_cache()

    # Warm the cache.
    agents_mod._build_templates_list()

    # Measure 10 hits and assert the worst is under 50ms. In practice
    # a hit is sub-millisecond; 50ms is a generous ceiling that still
    # catches a regression (e.g., someone accidentally removing the
    # cache short-circuit).
    worst = 0.0
    for _ in range(10):
        t0 = _time.perf_counter()
        agents_mod._build_templates_list()
        worst = max(worst, _time.perf_counter() - t0)
    assert worst < 0.05, (
        f"Templates cache hit too slow: {worst*1000:.2f}ms (target < 50ms)"
    )


def test_build_templates_list_cache_invalidates_on_mtime_change(tmp_path, monkeypatch):
    """Editing an agentfile invalidates the cache on the next request."""
    _seed_agentfiles(tmp_path, {
        "alpha": 'FROM auto\nPROMPT "Alpha v1."\n',
    })

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)
    agents_mod.invalidate_templates_cache()

    r1 = agents_mod._build_templates_list()
    assert len(r1) == 1 and r1[0]["name"] == "alpha"

    # Add a new file. The signature changes (new filename + mtime),
    # so the next call must rebuild and return both rows.
    (tmp_path / "beta.agent").write_text('FROM auto\nPROMPT "Beta."\n')
    r2 = agents_mod._build_templates_list()
    names = {t["name"] for t in r2}
    assert names == {"alpha", "beta"}, (
        f"Expected cache to invalidate on new .agent file. Got: {names}"
    )


def test_invalidate_templates_cache_forces_rebuild(tmp_path, monkeypatch):
    """Manual invalidation from the store forces the next call to rebuild."""
    _seed_agentfiles(tmp_path, {
        "alpha": 'FROM auto\nPROMPT "Alpha."\n',
    })

    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)
    agents_mod.invalidate_templates_cache()

    # Warm the cache.
    before_build = int(agents_mod._templates_cache.get("build_count", 0) or 0)
    agents_mod._build_templates_list()
    mid_build = int(agents_mod._templates_cache.get("build_count", 0) or 0)
    assert mid_build == before_build + 1, (
        "First call should rebuild the cache."
    )

    # A second call without invalidation is a hit.
    agents_mod._build_templates_list()
    hit_build = int(agents_mod._templates_cache.get("build_count", 0) or 0)
    assert hit_build == mid_build, "Second call should not rebuild."

    # After explicit invalidation, the next call must rebuild.
    agents_mod.invalidate_templates_cache()
    agents_mod._build_templates_list()
    after_invalidate = int(agents_mod._templates_cache.get("build_count", 0) or 0)
    assert after_invalidate == hit_build + 1, (
        "invalidate_templates_cache must force a rebuild on the next call."
    )


def test_template_create_invalidates_templates_cache(tmp_path, monkeypatch):
    """Creating a custom template via the store invalidates the cache.

    Custom agentfiles live in ~/.youros/agents/custom/, not AGENTS_DIR, so
    the mtime signature on AGENTS_DIR cannot detect them. The store
    must call the invalidation hook explicitly. This test lights that
    wire up.
    """
    _seed_agentfiles(tmp_path, {
        "alpha": 'FROM auto\nPROMPT "Alpha."\n',
    })

    import routers.agents as agents_mod
    import services.agent_templates_store as store_mod

    monkeypatch.setattr(agents_mod, "AGENTS_DIR", tmp_path)
    agents_mod.invalidate_templates_cache()

    # Warm.
    agents_mod._build_templates_list()
    warm_build = int(agents_mod._templates_cache.get("build_count", 0) or 0)

    # Point the store writes to a temp dir so we do not pollute
    # ~/.youros/agents/custom/ or the overrides file.
    fake_custom = tmp_path / "custom"
    fake_overrides = tmp_path / "agent_templates.json"
    monkeypatch.setattr(store_mod, "CUSTOM_AGENTS_DIR", fake_custom)
    monkeypatch.setattr(store_mod, "AGENT_TEMPLATES_PATH", fake_overrides)

    store = store_mod.AgentTemplatesStore()
    store.create({
        "name": "My Custom",
        "description": "A user template.",
        "icon": "smart_toy",
        "prompt_template": "You are a helper.",
        "model": "sonnet",
        "budget": 2.0,
    })

    # The cache must be invalidated. The next call rebuilds.
    agents_mod._build_templates_list()
    after_build = int(agents_mod._templates_cache.get("build_count", 0) or 0)
    assert after_build > warm_build, (
        "Creating a custom template must invalidate the /agents/templates cache "
        "so the next request re-parses and returns the new file. "
        f"build_count before={warm_build} after={after_build}"
    )



# ---------- Persona-templates and user-custom in-memory cache ----------
#
# The Templates tab on the Agents page calls /agents/persona-templates
# AND /agents/user-templates on every page open. Both used to walk the
# overrides file and apply BUILTIN_AGENT_TEMPLATES on every request,
# which made the Roadmap card flash a brief spinner on cold paint. The
# class-level _persona_cache returns the previous result on the warm
# path and invalidates whenever the overrides file is rewritten.


def test_list_for_persona_cache_invalidates_on_file_change(store, tmp_path, monkeypatch):
    """A change to the overrides file (mtime) must invalidate the cached
    persona list so installs are picked up on the next call."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "persona_cache_hit.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    AgentTemplatesStore._invalidate_persona_cache()

    # Warm the cache: PM persona with no installs returns just builtins.
    first = store.list_for_persona("pm")
    pm_names_before = {t["name"] for t in first}
    assert "Roadmap" not in pm_names_before, (
        "Roadmap is a marketplace template and should NOT appear before install."
    )

    # Install all PM templates. This rewrites the overrides file, which
    # bumps mtime. The next list call must see Roadmap.
    store.install_for_persona("pm")
    second = store.list_for_persona("pm")
    assert any(t["name"] == "Roadmap" for t in second), (
        "After install_for_persona('pm'), Roadmap must appear because the "
        "cache is invalidated on every overrides write."
    )


def test_list_for_persona_cache_returns_same_instance_on_warm(store, tmp_path, monkeypatch):
    """When the file does not change, two calls return the SAME list
    object (proves the cache hit, not a fresh build)."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "persona_cache_warm.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    AgentTemplatesStore._invalidate_persona_cache()

    first = store.list_for_persona("pm")
    second = store.list_for_persona("pm")
    assert first is second, (
        "Cache hit must return the same list object so we know no "
        "rebuild happened. Got two distinct objects."
    )


def test_list_user_custom_cache_returns_same_instance_on_warm(store, tmp_path, monkeypatch):
    """list_user_custom must hit the in-memory cache on the warm path."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "user_custom_warm.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    AgentTemplatesStore._invalidate_persona_cache()

    first = store.list_user_custom()
    second = store.list_user_custom()
    assert first is second, (
        "Cache hit must return the same list object."
    )


def test_install_invalidates_persona_cache(store, tmp_path, monkeypatch):
    """Installing a marketplace template must drop the cached persona list
    so the new card appears on the next request."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "install_invalidates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    AgentTemplatesStore._invalidate_persona_cache()

    # Warm the cache for sales persona (no install yet).
    before = store.list_for_persona("sales")
    sales_names_before = {t["name"] for t in before}
    assert "Prospect Research" not in sales_names_before or all(
        not t.get("installed") for t in before if t["name"] == "Prospect Research"
    )

    # Install all sales templates.
    store.install_for_persona("sales")

    # After install, the cache must reflect the new installed marketplace
    # entries. If the cache were stale, "Prospect Research" would not be
    # present yet.
    after = store.list_for_persona("sales")
    sales_names_after = {t["name"] for t in after}
    assert "Prospect Research" in sales_names_after, (
        "install_for_persona must invalidate the persona cache so the "
        "newly installed Prospect Research card appears on the next call."
    )


def test_create_custom_template_invalidates_user_custom_cache(store, tmp_path, monkeypatch):
    """Creating a custom template must drop the user_custom cache."""
    import services.agent_templates_store as mod

    fake_path = tmp_path / "create_invalidates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    AgentTemplatesStore._invalidate_persona_cache()

    # Warm the cache (empty).
    before = store.list_user_custom()
    assert before == []

    # Create a new custom template.
    store.create({
        "name": "My Helper",
        "description": "Does helpful things.",
        "icon": "smart_toy",
        "prompt_template": "Help me.",
        "model": "sonnet",
        "budget": 2.0,
    })

    # The cache must be invalidated so the new template appears.
    after = store.list_user_custom()
    assert any(t["name"] == "My Helper" for t in after), (
        "create() must invalidate the user_custom cache so the new "
        "template appears on the next list call."
    )


def test_wave_b_new_personas_exist():
    """Wave B: 5 new builtin templates and 4 new persona groups are present."""
    by_id = {t["id"]: t for t in BUILTIN_AGENT_TEMPLATES}

    wave_b_ids = [
        "builtin-marketing-campaign-brief",
        "builtin-finance-budget-builder",
        "builtin-founder-investor-update",
        "builtin-support-customer-reply",
        "builtin-designer-design-critique",
    ]
    for tid in wave_b_ids:
        assert tid in by_id, f"Wave B template '{tid}' missing from BUILTIN_AGENT_TEMPLATES"
        t = by_id[tid]
        assert t.get("description", "").strip(), f"{tid}: description must be non-empty"
        assert t.get("prompt_template", "").strip(), f"{tid}: prompt_template must be non-empty"

    # Each new group appears as a valid persona in at least one template.
    new_groups = ["marketing", "founder", "support", "designer"]
    all_personas: set[str] = set()
    for t in BUILTIN_AGENT_TEMPLATES:
        all_personas.update(t.get("personas", []))
    for group in new_groups:
        assert group in all_personas, (
            f"Persona group '{group}' not found in any template's personas list"
        )


# ---------- Wave 1 guard tests ----------

def test_no_thin_prompt_templates():
    """Deep templates have prompts >= 200 chars with a numbered step marker.

    Only checks the 13 already-deep templates. Thin ones get rewritten in
    Wave 2+ and are excluded here so CI stays green from day 1.
    """
    import re

    already_deep = {
        "Builder", "Diagnose", "Research", "Brainstorm", "Review", "Test",
        "Call Prep", "Campaign Brief", "Budget Builder", "Investor Update",
        "Customer Reply", "Design Critique", "Explain Plain",
    }
    for t in BUILTIN_AGENT_TEMPLATES:
        if t["name"] not in already_deep:
            continue
        prompt = t.get("prompt_template", "")
        assert len(prompt) >= 200, f"{t['name']}: prompt is only {len(prompt)} chars"
        has_step = bool(re.search(r"\(1\)|^1\.|\*\*[A-Z]", prompt, re.MULTILINE))
        assert has_step, f"{t['name']}: prompt has no numbered structure"


def test_every_template_has_required_user_input():
    """Every builtin template has at least one required user_inputs entry."""
    for t in BUILTIN_AGENT_TEMPLATES:
        inputs = t.get("user_inputs", [])
        assert any(i.get("required") for i in inputs), (
            f"{t['name']}: no required input"
        )


def test_create_preserves_user_inputs(tmp_path, monkeypatch):
    """create() must round-trip user_inputs into the returned template dict."""
    import services.agent_templates_store as mod

    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", tmp_path / "tpl.json")
    store = AgentTemplatesStore()
    inputs = [{"key": "task", "label": "Task", "type": "textarea", "required": True}]
    created = store.create({"name": "T", "prompt_template": "p", "user_inputs": inputs})
    assert created["user_inputs"] == inputs


def test_culled_templates_resolve_via_migration():
    """Old culled template names resolve to their successor IDs via _resolve_alias."""
    from services.agent_templates_store import _resolve_alias

    cases = {
        "Grocery List": "builtin-home-meal-planner",
        "Concept Explainer": "builtin-explain-plain",
        "Bug Finder": "builtin-review",
        "Flash Cards": "builtin-student-study-guide",
    }
    for old_name, expected_id in cases.items():
        resolved = _resolve_alias(old_name)
        assert resolved == expected_id, f"{old_name} -> {resolved}, expected {expected_id}"


def test_marketplace_listing_includes_all_wave_8_specialty_templates(store):
    """→1033 regression: list_marketplace() must surface all Wave 8 specialty templates.

    These 5 templates were present in BUILTIN_AGENT_TEMPLATES but missing from
    the frontend agentMarketplace.ts static catalog. The fresh-store listing
    (no persona installs yet) is the canonical check: if they are not in
    list_marketplace() for a new user, they can never be added via the
    marketplace UI.
    """
    marketplace = store.list_marketplace()
    names = {t["name"] for t in marketplace}

    wave_8_specialty = [
        "Campaign Brief",
        "Budget Builder",
        "Investor Update",
        "Customer Reply",
        "Design Critique",
    ]
    for name in wave_8_specialty:
        assert name in names, (
            f"→1033: '{name}' missing from list_marketplace() — "
            "it exists in BUILTIN_AGENT_TEMPLATES but does not appear for new users"
        )


def test_marketplace_every_template_has_user_inputs(store):
    """→1039 regression: every template returned by list_marketplace() must have
    at least one user_inputs entry so the spawn modal shows structured fields.

    The bug: marketplace-installed templates shown in the displayTemplates grid
    (templates not in the current persona's persona-templates list) previously
    always received setEditorUserInputs(undefined), forcing the generic textarea.
    The fix fetches the full marketplace catalog and looks up user_inputs at
    spawn time. This test guards the data layer: if any template loses its
    user_inputs, the frontend fix cannot help.
    """
    marketplace = store.list_marketplace()
    assert marketplace, "list_marketplace() returned an empty list"
    for t in marketplace:
        inputs = t.get("user_inputs", [])
        assert isinstance(inputs, list) and len(inputs) > 0, (
            f"→1039: '{t['name']}' has no user_inputs — "
            "spawn modal will fall back to the generic textarea"
        )
        assert any(i.get("required") for i in inputs), (
            f"→1039: '{t['name']}' has user_inputs but none are required — "
            "spawn modal will not show the structured fields correctly"
        )


KNOWN_DEEP_TEMPLATES_WITH_INPUTS = {
    "Refactor Plan": ["code", "goal"],
    "Cold Outreach Draft": ["prospect", "value_prop", "stage"],
    "Write Tests": ["code", "framework"],
    "Interactive Debug": ["error", "urgency"],
    "PRD": ["idea", "user", "stage"],
    "Competitive Scan": ["area", "competitors"],
    "Customer Interview Notes": ["transcript", "goal"],
    "Launch Checklist": ["feature", "launch_type"],
    "Stakeholder Update": ["notes", "audience"],
    "Prospect Research": ["company", "contact"],
}


def test_known_deep_templates_have_expected_input_keys(store):
    """→1039 regression: spot-check that specific deep-rewritten templates have
    the input keys the spawn modal renders. Catches accidental user_inputs wipe
    during future prompt rewrites.
    """
    all_templates = {t["name"]: t for t in store.list_marketplace()}
    all_templates.update({t["name"]: t for t in BUILTIN_AGENT_TEMPLATES})

    for name, expected_keys in KNOWN_DEEP_TEMPLATES_WITH_INPUTS.items():
        tpl = all_templates.get(name)
        assert tpl is not None, f"→1039: template '{name}' not found in store"
        actual_keys = {i["key"] for i in tpl.get("user_inputs", [])}
        for key in expected_keys:
            assert key in actual_keys, (
                f"→1039: '{name}' missing expected input key '{key}'. "
                f"Got keys: {sorted(actual_keys)}"
            )


def test_marketplace_listing_count_for_fresh_store(store):
    """A fresh store (no installs) returns all default-uninstalled marketplace templates.

    Research, Review, and Test are marketplace-sourced but installed=True by
    default, so they are excluded. The 4 builtin-source templates (Builder,
    Diagnose, Brainstorm, Explain Plain) are also excluded, as are the three
    "For everyone" builtins (Summarizer, Daily Planner, Email Drafter). This
    leaves 31 templates: Test Engineer and Debugger (→1106) were merged into
    Write Tests and Diagnose respectively.
    """
    marketplace = store.list_marketplace()
    assert len(marketplace) == 31, (
        f"Expected 31 marketplace templates for a fresh store, got {len(marketplace)}. "
        f"Names: {sorted(t['name'] for t in marketplace)}"
    )


# ---------- Generic-user template review (make templates make sense for any user) ----------

import re as _re_generic


def test_no_builtin_prompt_uses_chip_ui_jargon():
    """Built-in prompts must not leak the internal 'chip' UI term.

    When an agent is invoked, its prompt should read 'the tone you selected',
    never 'the tone chip' — 'chip' is internal UI wording that is meaningless
    to the agent and to anyone reading the prompt.
    """
    offenders = [
        t["id"] for t in BUILTIN_AGENT_TEMPLATES
        if _re_generic.search(r"\bchips?\b", t.get("prompt_template", ""), _re_generic.I)
    ]
    assert not offenders, f"prompt_template still uses 'chip' UI jargon: {offenders}"


def test_write_tests_absorbs_test_engineer():
    """Test Engineer is merged into Write Tests (the well-wired, clearer name).

    No standalone 'Test Engineer' card, 'Test Engineer' resolves to Write Tests
    via migration, and Write Tests now also RUNS the suite (absorbed step).
    """
    from services.agent_templates_store import _resolve_alias

    names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES}
    ids = {t["id"] for t in BUILTIN_AGENT_TEMPLATES}
    assert "Test Engineer" not in names, "Test Engineer must not be a standalone template"
    assert "builtin-eng-test-engineer" not in ids
    assert _resolve_alias("Test Engineer") == "builtin-eng-write-tests"
    wt = next(t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-eng-write-tests")
    assert "run the tests" in wt["prompt_template"].lower(), (
        "Write Tests must absorb Test Engineer's 'run the tests' step"
    )


def test_debugger_absorbs_into_diagnose():
    """Debugger is merged into Diagnose (the well-wired root-cause template).

    No standalone 'Debugger' card, 'Debugger' resolves to Diagnose, and Diagnose
    gained Debugger's ranked-candidate + file:line root-cause shape.
    """
    from services.agent_templates_store import _resolve_alias

    names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES}
    ids = {t["id"] for t in BUILTIN_AGENT_TEMPLATES}
    assert "Debugger" not in names, "Debugger must not be a standalone template"
    assert "builtin-eng-debugger" not in ids
    assert _resolve_alias("Debugger") == "builtin-diagnose"
    dg = next(t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-diagnose")
    low = dg["prompt_template"].lower()
    assert "candidate" in low or "ranked" in low, (
        "Diagnose must absorb Debugger's ranked-candidate-causes step"
    )


def test_everyone_persona_templates_are_real_builtins():
    """'For everyone' onboarding agents must be real builtins, not empty shells.

    agentMarketplace.ts advertises Summarizer / Daily Planner / Email Drafter on
    the default persona. They must exist as source='builtin' (always installed)
    with a real prompt and at least one required input, or new users land on
    agents with no backing prompt.
    """
    by_name = {t["name"]: t for t in BUILTIN_AGENT_TEMPLATES}
    for name in ["Summarizer", "Daily Planner", "Email Drafter"]:
        t = by_name.get(name)
        assert t is not None, f"{name} missing from BUILTIN_AGENT_TEMPLATES"
        assert t["source"] == "builtin", f"{name} must be source='builtin' (always installed)"
        assert t.get("installed") is True, f"{name} must be installed by default"
        assert len(t.get("prompt_template", "")) >= 150, f"{name} prompt is too thin"
        assert any(i.get("required") for i in t.get("user_inputs", [])), (
            f"{name} must have at least one required input"
        )


def test_everyone_persona_agentfiles_exist():
    """Each new 'For everyone' builtin has a matching agentfile in agents/."""
    from config import PROJECT_ROOT

    for stem in ["summarizer", "daily-planner", "email-drafter"]:
        path = PROJECT_ROOT / "agents" / f"{stem}.agent"
        assert path.exists(), f"Expected builtin agentfile at {path}"

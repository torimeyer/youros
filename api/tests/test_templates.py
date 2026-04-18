"""Tests for unified template store, alias resolution, persona install, and migrations.

Covers:
1. Alias resolution: spawning with name "saa" resolves to Builder template.
2. Persona-scoped install: installing PM persona does not install engineer templates.
3. Rename migration: disk store with old name "PRD Draft" loads correctly as "PRD".
4. list_installed only returns source=builtin + installed marketplace + custom.
5. list_marketplace returns only uninstalled marketplace entries.
6. install_for_persona installs exactly the right set.
7. get_by_name_or_alias works for aliases.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_templates_store import (
    AgentTemplatesStore,
    BUILTIN_AGENT_TEMPLATES,
    MIGRATIONS,
    _resolve_alias,
    _apply_migrations,
    AGENT_TEMPLATES_PATH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store(tmp_path: Path) -> AgentTemplatesStore:
    """Return a store instance that writes to a temp file."""
    import services.agent_templates_store as _mod
    store = AgentTemplatesStore()
    store_path = tmp_path / "agent_templates.json"

    def _ensure_exists():
        store_path.parent.mkdir(parents=True, exist_ok=True)
        if not store_path.exists():
            store_path.write_text("[]")

    def _load_overrides():
        _ensure_exists()
        try:
            raw = json.loads(store_path.read_text())
            if isinstance(raw, list):
                return _apply_migrations(raw)
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(data):
        store_path.write_text(json.dumps(data))

    store._path = store_path
    store._ensure_exists = _ensure_exists
    store._load_overrides = _load_overrides
    store._save = _save
    return store


# ---------------------------------------------------------------------------
# Alias resolution (unit, no disk)
# ---------------------------------------------------------------------------

def test_resolve_alias_saa_finds_builder():
    """'saa' alias must resolve to the Builder template id."""
    tid = _resolve_alias("saa")
    assert tid == "builtin-builder", f"Expected builtin-builder, got {tid}"


def test_resolve_alias_comprehensive_finds_builder():
    """'comprehensive' alias must resolve to the Builder template id."""
    tid = _resolve_alias("comprehensive")
    assert tid == "builtin-builder"


def test_resolve_alias_builder_by_name():
    """Direct name match 'Builder' must resolve."""
    tid = _resolve_alias("Builder")
    assert tid == "builtin-builder"


def test_resolve_alias_prd_draft_finds_prd():
    """Old name 'PRD Draft' must resolve to the PRD template via alias."""
    tid = _resolve_alias("PRD Draft")
    assert tid == "builtin-pm-prd", f"Expected builtin-pm-prd, got {tid}"


def test_resolve_alias_unknown_returns_none():
    tid = _resolve_alias("nonexistent-template-xyz")
    assert tid is None


def test_resolve_alias_case_insensitive():
    """Alias resolution must be case-insensitive."""
    assert _resolve_alias("SAA") == "builtin-builder"
    assert _resolve_alias("sAa") == "builtin-builder"


# ---------------------------------------------------------------------------
# Migration table (unit)
# ---------------------------------------------------------------------------

def test_migrations_prd_draft():
    assert MIGRATIONS["PRD Draft"] == "PRD"


def test_apply_migrations_renames_prd_draft():
    templates = [{"id": "builtin-pm-prd-draft", "name": "PRD Draft", "builtin": True}]
    result = _apply_migrations(templates)
    assert result[0]["name"] == "PRD"


def test_apply_migrations_renames_comprehensive():
    templates = [{"id": "custom-1", "name": "Comprehensive"}]
    result = _apply_migrations(templates)
    assert result[0]["name"] == "Builder"


def test_apply_migrations_leaves_unchanged():
    templates = [{"id": "custom-2", "name": "My Custom Template"}]
    result = _apply_migrations(templates)
    assert result[0]["name"] == "My Custom Template"


# ---------------------------------------------------------------------------
# Builtin template shape
# ---------------------------------------------------------------------------

def test_builtin_templates_have_required_fields():
    required = {"id", "name", "aliases", "description", "icon", "prompt_template",
                "model", "budget", "source", "personas", "installed", "builtin"}
    for t in BUILTIN_AGENT_TEMPLATES:
        missing = required - set(t.keys())
        assert not missing, f"Template {t.get('id')} missing fields: {missing}"


def test_builtin_builder_is_always_installed():
    builder = next(t for t in BUILTIN_AGENT_TEMPLATES if t["name"] == "Builder")
    assert builder["source"] == "builtin"
    assert builder["installed"] is True


def test_pm_templates_are_marketplace_not_installed():
    pm_templates = [t for t in BUILTIN_AGENT_TEMPLATES if "pm" in t.get("personas", [])]
    assert len(pm_templates) > 0
    for t in pm_templates:
        assert t["source"] == "marketplace"
        assert t["installed"] is False


def test_engineer_templates_not_in_pm_personas():
    eng_templates = [t for t in BUILTIN_AGENT_TEMPLATES if "engineer" in t.get("personas", [])]
    for t in eng_templates:
        assert "pm" not in t.get("personas", []), (
            f"Engineer template {t['name']} should not be in PM personas"
        )


def test_prd_template_renamed():
    """The PRD template must have name 'PRD', not 'PRD Draft'."""
    names = [t["name"] for t in BUILTIN_AGENT_TEMPLATES]
    assert "PRD" in names, "PRD template missing"
    assert "PRD Draft" not in names, "Old name 'PRD Draft' still present"


def test_capitalized_agentfile_names():
    """Agentfile-backed templates must use capitalized names."""
    agentfile_names = [
        t["name"] for t in BUILTIN_AGENT_TEMPLATES if t["source"] == "builtin"
    ]
    # Verify all builtin agentfile-backed templates use TitleCase names
    for name in agentfile_names:
        assert name[0].isupper(), f"Expected capitalized name, got: '{name}'"


# ---------------------------------------------------------------------------
# Store methods (with tmp disk)
# ---------------------------------------------------------------------------

def test_list_installed_returns_builtins(tmp_path):
    store = make_store(tmp_path)
    installed = store.list_installed()
    names = [t["name"] for t in installed]
    # All source=builtin templates must always appear
    for name in ("Builder", "Diagnose", "Research", "Review", "Test"):
        assert name in names, f"Expected '{name}' in list_installed()"


def test_list_installed_excludes_uninstalled_marketplace(tmp_path):
    store = make_store(tmp_path)
    installed = store.list_installed()
    uninstalled_marketplace = [
        t for t in installed
        if t.get("source") == "marketplace" and not t.get("installed", False)
    ]
    assert len(uninstalled_marketplace) == 0, (
        f"list_installed returned uninstalled marketplace templates: "
        f"{[t['name'] for t in uninstalled_marketplace]}"
    )


def test_list_marketplace_returns_uninstalled(tmp_path):
    store = make_store(tmp_path)
    marketplace = store.list_marketplace()
    # PRD is a marketplace template that starts uninstalled
    names = [t["name"] for t in marketplace]
    assert "PRD" in names


def test_install_for_persona_pm_only(tmp_path):
    """Installing PM persona must install PM templates but NOT engineer templates."""
    store = make_store(tmp_path)
    installed = store.install_for_persona("pm")
    installed_names = {t["name"] for t in installed}

    # PM templates should be installed
    assert "PRD" in installed_names or "Competitive Scan" in installed_names, (
        "At least one PM template should be installed"
    )
    # Engineer templates should NOT be installed
    eng_names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES if "engineer" in t.get("personas", [])}
    overlap = installed_names & eng_names
    assert not overlap, f"Engineer templates installed by PM persona: {overlap}"


def test_install_for_persona_engineer_only(tmp_path):
    """Installing engineer persona must not install PM templates."""
    store = make_store(tmp_path)
    installed = store.install_for_persona("engineer")
    installed_names = {t["name"] for t in installed}

    pm_names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES if "pm" in t.get("personas", [])}
    overlap = installed_names & pm_names
    assert not overlap, f"PM templates installed by engineer persona: {overlap}"


def test_install_for_persona_idempotent(tmp_path):
    """Calling install_for_persona twice should not double-install."""
    store = make_store(tmp_path)
    first = store.install_for_persona("pm")
    second = store.install_for_persona("pm")
    assert len(second) == 0, "Second install should return empty (already installed)"


def test_install_for_persona_then_list_installed(tmp_path):
    """After installing PM persona, list_installed should include PM templates."""
    store = make_store(tmp_path)
    store.install_for_persona("pm")
    installed = store.list_installed()
    names = [t["name"] for t in installed]
    assert "PRD" in names


def test_migration_on_load(tmp_path):
    """A disk store with old name 'PRD Draft' must load as 'PRD'."""
    store = make_store(tmp_path)
    # Write old-format data directly to the store path
    old_data = [
        {"id": "custom-abc123", "name": "PRD Draft", "description": "old", "builtin": False}
    ]
    store._path.write_text(json.dumps(old_data))
    overrides = store._load_overrides()
    names = [o["name"] for o in overrides]
    assert "PRD Draft" not in names, "Old name 'PRD Draft' should be migrated"
    assert "PRD" in names, "Migrated name 'PRD' should appear"


def test_get_by_name_or_alias_saa(tmp_path):
    """get_by_name_or_alias('saa') must return the Builder template."""
    store = make_store(tmp_path)
    tpl = store.get_by_name_or_alias("saa")
    assert tpl is not None
    assert tpl["name"] == "Builder"


def test_create_custom_includes_aliases(tmp_path):
    """Custom templates can be created with aliases."""
    store = make_store(tmp_path)
    t = store.create({
        "name": "My Template",
        "description": "Test",
        "aliases": ["mt", "mytemplate"],
        "prompt_template": "Do the thing.",
        "model": "sonnet",
        "budget": 2.0,
    })
    assert t["aliases"] == ["mt", "mytemplate"]
    assert t["id"].startswith("custom-")

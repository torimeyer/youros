"""Tests for agent_templates_store.first_runs()."""

import pytest
from services.agent_templates_store import first_runs, _FIRST_RUNS, _DEFAULT_FIRST_RUNS


def test_first_runs_returns_three_for_pm():
    result = first_runs("pm")
    assert len(result) == 3


def test_first_runs_returns_three_for_every_known_persona():
    for persona_id in _FIRST_RUNS:
        result = first_runs(persona_id)
        assert len(result) == 3, f"Expected 3 suggestions for persona '{persona_id}', got {len(result)}"


def test_first_runs_returns_three_defaults_for_unknown_persona():
    result = first_runs("unknown-persona-xyz")
    assert len(result) == 3


def test_first_runs_result_has_required_fields():
    result = first_runs("engineer")
    for item in result:
        assert "id" in item
        assert "title" in item
        assert "description" in item
        assert "icon" in item
        assert "agent_id" in item


def test_first_runs_does_not_mutate_source():
    """Verify first_runs() returns a copy so callers cannot modify the source list."""
    result = first_runs("pm")
    result.clear()
    assert len(first_runs("pm")) == 3


def test_first_runs_default_has_three_items():
    assert len(_DEFAULT_FIRST_RUNS) == 3


def test_first_runs_covers_all_personas():
    known = {"pm", "engineer", "writer", "sales", "home", "student"}
    assert set(_FIRST_RUNS.keys()) == known


# ---------------------------------------------------------------------------
# Seeder reconciliation tests (→1681)
# ---------------------------------------------------------------------------

def test_seeder_reconciles_when_prompt_template_drifts(tmp_path, monkeypatch):
    """Seeder must overwrite an on-disk agentfile when its PROMPT drifts from
    the store's prompt_template, so code updates to BUILTIN_AGENT_TEMPLATES
    propagate without requiring a fresh install or manual file deletion."""
    import services.agent_templates_store as sts
    from services.agent_templates_store import _make_agentfile_text, _name_to_stem
    from services.agentfile_parser import parse_agentfile

    fake_template = {
        "id": "test-drift",
        "name": "Test Drift Template",
        "source": "marketplace",
        "description": "Test template for drift detection",
        "prompt_template": "NEW PROMPT: do the updated thing",
    }

    # Seed an agentfile with the OLD prompt to simulate a stale on-disk file.
    mp_dir = tmp_path / "marketplace"
    mp_dir.mkdir()
    stem = _name_to_stem(fake_template["name"])
    stale_path = mp_dir / f"{stem}.agent"
    old_template = {**fake_template, "prompt_template": "OLD PROMPT: do the old thing"}
    stale_path.write_text(_make_agentfile_text(old_template))

    monkeypatch.setattr(sts, "MARKETPLACE_AGENTS_DIR", mp_dir)
    monkeypatch.setattr(sts, "BUILTIN_AGENT_TEMPLATES", [fake_template])

    sts._seed_marketplace_agentfiles()

    config = parse_agentfile(stale_path)
    assert config.prompt == "NEW PROMPT: do the updated thing", (
        f"Seeder did not reconcile drifted prompt; got: {config.prompt!r}"
    )


def test_seeder_skips_write_when_already_in_sync(tmp_path, monkeypatch):
    """Seeder must not overwrite an agentfile that is already in sync with
    the store, to avoid unnecessary disk writes."""
    import services.agent_templates_store as sts
    from services.agent_templates_store import _make_agentfile_text, _name_to_stem

    fake_template = {
        "id": "test-sync",
        "name": "Test Sync Template",
        "source": "marketplace",
        "description": "Sync test",
        "prompt_template": "PROMPT: current version",
    }

    mp_dir = tmp_path / "marketplace"
    mp_dir.mkdir()
    stem = _name_to_stem(fake_template["name"])
    sync_path = mp_dir / f"{stem}.agent"
    sync_path.write_text(_make_agentfile_text(fake_template))
    mtime_before = sync_path.stat().st_mtime

    monkeypatch.setattr(sts, "MARKETPLACE_AGENTS_DIR", mp_dir)
    monkeypatch.setattr(sts, "BUILTIN_AGENT_TEMPLATES", [fake_template])

    sts._seed_marketplace_agentfiles()

    # mtime must not change when content is already in sync.
    assert sync_path.stat().st_mtime == mtime_before, "Seeder wrote to a file already in sync"

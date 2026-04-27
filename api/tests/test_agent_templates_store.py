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

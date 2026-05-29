"""Tests for api/services/spec_types.py pure-data registry."""
from __future__ import annotations

import pytest


def test_get_spec_type_none_returns_engineering():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type(None)
    assert entry["name"] == "engineering"


def test_get_spec_type_unknown_returns_engineering():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type("nonsense_type")
    assert entry["name"] == "engineering"


def test_all_four_types_resolvable():
    from api.services.spec_types import get_spec_type
    for name in ("engineering", "vision", "customer_docs", "prototype"):
        entry = get_spec_type(name)
        assert entry["name"] == name, f"get_spec_type('{name}') returned wrong entry"


def test_engineering_required_checks():
    from api.services.spec_types import required_checks
    reqs = required_checks("engineering")
    assert reqs == ["has_ac_checkboxes", "no_vague_ac"]


def test_engineering_optional_checks():
    from api.services.spec_types import optional_checks
    opts = optional_checks("engineering")
    assert "has_file_paths" in opts
    assert "referenced_files_exist" in opts
    assert "in_repo_scope" in opts


def test_vision_required_checks():
    from api.services.spec_types import required_checks
    reqs = required_checks("vision")
    assert "has_success_measures" in reqs


def test_vision_excludes_has_file_paths():
    from api.services.spec_types import required_checks, optional_checks
    all_checks = required_checks("vision") + optional_checks("vision")
    assert "has_file_paths" not in all_checks


def test_customer_docs_required_checks():
    from api.services.spec_types import required_checks
    reqs = required_checks("customer_docs")
    assert "has_audience" in reqs


def test_prototype_required_checks():
    from api.services.spec_types import required_checks
    reqs = required_checks("prototype")
    assert "has_learning_goal" in reqs


def test_engineering_pipeline_phases():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type("engineering")
    assert entry["pipeline_phases"] == ["plan", "tasks", "build", "review"]


def test_customer_docs_pipeline_phases():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type("customer_docs")
    assert entry["pipeline_phases"] == ["draft", "review"]


def test_prototype_pipeline_phases():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type("prototype")
    assert entry["pipeline_phases"] == ["build"]


def test_vision_pipeline_phases():
    from api.services.spec_types import get_spec_type
    entry = get_spec_type("vision")
    assert entry["pipeline_phases"] == []


def test_all_entries_have_display_name():
    from api.services.spec_types import SPEC_TYPES
    for name, entry in SPEC_TYPES.items():
        assert "display_name" in entry, f"'{name}' missing display_name"
        assert entry["display_name"], f"'{name}' has empty display_name"


def test_all_entries_have_template_sections():
    from api.services.spec_types import SPEC_TYPES
    for name, entry in SPEC_TYPES.items():
        assert "template_sections" in entry, f"'{name}' missing template_sections"
        assert isinstance(entry["template_sections"], list)


def test_readiness_profile_structure():
    from api.services.spec_types import SPEC_TYPES
    for name, entry in SPEC_TYPES.items():
        profile = entry.get("readiness_profile", [])
        assert isinstance(profile, list), f"'{name}' readiness_profile is not a list"
        for item in profile:
            assert "check" in item, f"'{name}' profile item missing 'check': {item}"
            assert "required" in item, f"'{name}' profile item missing 'required': {item}"
            assert isinstance(item["required"], bool)

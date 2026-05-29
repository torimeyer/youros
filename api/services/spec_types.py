"""Pure-data registry of spec types (phase-2a, →spec-types).

No wiring to other modules. Import SPEC_TYPES or call the helpers below.
"""
from __future__ import annotations

from typing import Optional

SPEC_TYPES: dict[str, dict] = {
    "engineering": {
        "name": "engineering",
        "display_name": "Engineering",
        "template_sections": [
            "Problem",
            "Goals",
            "Non-goals",
            "Acceptance criteria",
            "Files touched",
            "Open questions",
        ],
        "readiness_profile": [
            {"check": "has_ac_checkboxes", "required": True},
            {"check": "no_vague_ac", "required": True},
            {"check": "has_file_paths", "required": False},
            {"check": "referenced_files_exist", "required": False},
            {"check": "in_repo_scope", "required": False},
        ],
        "pipeline_phases": ["plan", "tasks", "build", "review"],
    },
    "vision": {
        "name": "vision",
        "display_name": "Vision",
        "template_sections": [
            "Summary",
            "Why this matters",
            "Success measures",
            "Out of scope",
        ],
        "readiness_profile": [
            {"check": "has_success_measures", "required": True},
            {"check": "no_vague_ac", "required": False},
        ],
        "pipeline_phases": [],
    },
    "customer_docs": {
        "name": "customer_docs",
        "display_name": "Customer Docs",
        "template_sections": [
            "Audience",
            "Outline",
            "Content",
            "Review notes",
        ],
        "readiness_profile": [
            {"check": "has_audience", "required": True},
            {"check": "has_outline", "required": False},
        ],
        "pipeline_phases": ["draft", "review"],
    },
    "prototype": {
        "name": "prototype",
        "display_name": "Prototype",
        "template_sections": [
            "Learning goal",
            "Approach",
            "What we will build",
            "How we will evaluate",
        ],
        "readiness_profile": [
            {"check": "has_learning_goal", "required": True},
        ],
        "pipeline_phases": ["build"],
    },
}

_DEFAULT_TYPE = "engineering"


def get_spec_type(name: Optional[str]) -> dict:
    """Return the spec type entry for name, defaulting to engineering."""
    if name is None or name not in SPEC_TYPES:
        return SPEC_TYPES[_DEFAULT_TYPE]
    return SPEC_TYPES[name]


def required_checks(name: Optional[str]) -> list[str]:
    """Return ordered list of required check names for the given spec type."""
    entry = get_spec_type(name)
    return [item["check"] for item in entry["readiness_profile"] if item["required"]]


def optional_checks(name: Optional[str]) -> list[str]:
    """Return ordered list of optional check names for the given spec type."""
    entry = get_spec_type(name)
    return [item["check"] for item in entry["readiness_profile"] if not item["required"]]

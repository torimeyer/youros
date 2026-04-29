"""Regression tests for →950: detailed builder prompt enrichment.

Verifies that spawn_agent injects spec acceptance criteria into the prompt
when a task_id matches a linked spec, and stays silent when there is no match.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import _build_spec_ac_block


SAMPLE_DOCS = [
    {
        "title": "My Feature Spec",
        "task_ids": ["→950", "→951"],
        "acceptance_criteria": [
            {"text": "Builder prompt includes spec AC", "checked": False},
            {"text": "Backward compatible when no spec", "checked": True},
        ],
        "path": "docs/spec/my-feature-spec.md",
        "status": "ready",
    },
    {
        "title": "Unrelated Spec",
        "task_ids": ["→999"],
        "acceptance_criteria": [
            {"text": "Some other criterion", "checked": False},
        ],
        "path": "docs/spec/unrelated.md",
        "status": "ready",
    },
]


def test_ac_injected_when_task_id_matches():
    block = _build_spec_ac_block("950", SAMPLE_DOCS)
    assert block, "Expected non-empty block when task matches"
    assert "My Feature Spec" in block
    assert "Builder prompt includes spec AC" in block
    assert "Backward compatible when no spec" in block


def test_ac_uses_checkbox_format():
    block = _build_spec_ac_block("950", SAMPLE_DOCS)
    assert "- [ ] Builder prompt includes spec AC" in block
    assert "- [x] Backward compatible when no spec" in block


def test_arrow_prefix_normalized():
    """→950 and 950 should both match a spec that stores task_id as →950."""
    block_bare = _build_spec_ac_block("950", SAMPLE_DOCS)
    block_arrow = _build_spec_ac_block("→950", SAMPLE_DOCS)
    assert block_bare == block_arrow
    assert block_bare  # both non-empty


def test_no_match_returns_empty():
    block = _build_spec_ac_block("000", SAMPLE_DOCS)
    assert block == "", f"Expected empty string for unmatched task, got: {block!r}"


def test_empty_docs_returns_empty():
    block = _build_spec_ac_block("950", [])
    assert block == ""


def test_spec_with_no_ac_returns_empty():
    docs = [{"title": "No AC", "task_ids": ["950"], "acceptance_criteria": []}]
    block = _build_spec_ac_block("950", docs)
    assert block == "", "Spec with empty AC list should not inject a block"


def test_definition_of_done_language_present():
    block = _build_spec_ac_block("950", SAMPLE_DOCS)
    assert "definition of done" in block.lower()


def test_builder_template_mentions_ac():
    """Builder prompt_template should tell the agent to read AC before coding."""
    from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES

    builder = next(
        (t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-builder"),
        None,
    )
    assert builder is not None, "builtin-builder template not found"
    pt = builder["prompt_template"].lower()
    assert "acceptance criteria" in pt or "criteria" in pt, (
        "Builder prompt_template should reference acceptance criteria"
    )

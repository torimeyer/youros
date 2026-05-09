"""Tests for services.spawn_isolation.decide_isolation.

Covers the three contract points:
  1. Code-edit verbs in description/prompt default to "worktree".
  2. Research-only verbs stay in the main worktree ("none").
  3. Explicit caller values (including "none") are always respected.
"""

import logging

import pytest

pytestmark = pytest.mark.touches_agent_registry

from services.spawn_isolation import (
    ISOLATION_NONE,
    ISOLATION_WORKTREE,
    decide_isolation,
)


# ---------------------------------------------------------------------------
# Code-edit verbs -> worktree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "Fix failing test in auth router",
        "Implement retry logic for the webhook client",
        "Add a new field to AgentSpawn",
        "Refactor the policy enforcement helpers",
        "Build the Roadmap page",
        "Create migration for the labels table",
        "Edit the onboarding wizard copy",
        "saa diagnose the flaky spawn test",
        "Patch the broken import in chat_providers",
        "Update claude_code_provider to use the new tier map",
        "Write regression test for needle 240",
        "Remove the deprecated /agents/legacy endpoint",
        "Rename AgentNudge to AgentMessage across the codebase",
        "Migrate JSON store to SQLite",
    ],
)
def test_code_edit_verbs_default_to_worktree(description):
    assert (
        decide_isolation(description=description, prompt=None, explicit=None)
        == ISOLATION_WORKTREE
    )


def test_code_edit_verb_in_prompt_triggers_worktree():
    """Prompt body alone should be enough to trigger worktree isolation."""
    prompt = (
        "Please implement a small cache around the model_routing resolver "
        "so we stop paying the import cost on every spawn."
    )
    assert (
        decide_isolation(description="quick task", prompt=prompt, explicit=None)
        == ISOLATION_WORKTREE
    )


# ---------------------------------------------------------------------------
# Research verbs -> main worktree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "Explore the shape of the chat history store",
        "Review the open PRs for security issues",
        "Audit existing isolation policy usage",
        "Report on which agents hit the 90s wall clock last week",
        "Investigate why Roadmap produces 0-byte transcripts",
        "Analyze the latency of /agents/spawn",
        "Read the enterprise config and summarize the policy fields",
        "Summarize the findings from the last three probe runs",
        "Inspect the structure of the labels store",
    ],
)
def test_research_verbs_stay_in_main_worktree(description):
    assert (
        decide_isolation(description=description, prompt=None, explicit=None)
        == ISOLATION_NONE
    )


def test_no_verb_match_defaults_to_none():
    """When neither verb family matches we keep the main worktree."""
    assert (
        decide_isolation(description="ping", prompt="status?", explicit=None)
        == ISOLATION_NONE
    )


def test_empty_inputs_default_to_none():
    assert (
        decide_isolation(description=None, prompt=None, explicit=None)
        == ISOLATION_NONE
    )


# ---------------------------------------------------------------------------
# Explicit opt-out / opt-in
# ---------------------------------------------------------------------------


def test_explicit_none_forces_main_worktree_even_for_edit_verb():
    """Caller set isolation=\"none\" explicitly. Respect it."""
    result = decide_isolation(
        description="Fix the failing login test",
        prompt="implement a retry helper",
        explicit="none",
    )
    assert result == ISOLATION_NONE


def test_explicit_worktree_is_respected():
    result = decide_isolation(
        description="Explore the repo",
        prompt="just a read-only look",
        explicit="worktree",
    )
    assert result == ISOLATION_WORKTREE


def test_explicit_value_is_normalized_to_lowercase():
    result = decide_isolation(
        description="anything",
        prompt=None,
        explicit="  WORKTREE ",
    )
    assert result == "worktree"


def test_edit_verb_wins_when_both_verb_families_present():
    """If the text says both 'investigate' and 'fix', treat as code-edit."""
    description = "Investigate the bug and fix it"
    assert (
        decide_isolation(description=description, prompt=None, explicit=None)
        == ISOLATION_WORKTREE
    )


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_decision_is_logged_at_info(caplog):
    with caplog.at_level(logging.INFO, logger="services.spawn_isolation"):
        decide_isolation(
            description="Fix the thing",
            prompt=None,
            explicit=None,
            agent_name="audit-demo",
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("spawn.isolation.decided" in m for m in messages)
    assert any("audit-demo" in m for m in messages)


def test_explicit_decision_is_logged_at_info(caplog):
    with caplog.at_level(logging.INFO, logger="services.spawn_isolation"):
        decide_isolation(
            description="anything",
            prompt=None,
            explicit="none",
            agent_name="explicit-demo",
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("spawn.isolation.explicit" in m for m in messages)

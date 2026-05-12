"""Regression tests: per-spawn USD budget cap is NOT set on subscription auth.

On claude.ai subscription auth the cost is fixed per month. Passing
--max-budget-usd to `claude --print` only kills agents prematurely;
it does not save money. These tests guard against the cap being
re-introduced by accident.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


BRIDGE_PATH = (
    Path(__file__).parent.parent.parent
    / ".claude" / "hooks" / "task-isolation-bridge.sh"
)
AGENTS_PY_PATH = (
    Path(__file__).parent.parent / "routers" / "agents.py"
)


# ---------------------------------------------------------------------------
# Bridge: "budget" key must not be in the spawn body
# ---------------------------------------------------------------------------

def test_bridge_spawn_body_has_no_budget_field():
    """The task-isolation-bridge must not hardcode a budget into the spawn body.

    The bridge posts JSON to /api/agents/spawn. If it includes a "budget"
    field the server will pass --max-budget-usd to `claude --print`, which
    kills agents prematurely on subscription auth. Verified by reading the
    bridge source and checking the Python inline script.
    """
    source = BRIDGE_PATH.read_text()

    # The body dict literal must not contain a "budget" key.
    # We check that neither '"budget": 5' nor '"budget":5' appears inside
    # the inline Python block. A numeric budget sentinel like budget=5 or
    # "budget": <anything> in the body dict is the regression we're guarding.
    assert '"budget":' not in source.replace(" ", ""), (
        'task-isolation-bridge.sh must not set "budget" in the spawn body. '
        "On subscription auth the cap only kills agents early."
    )


# ---------------------------------------------------------------------------
# Spawn command: --max-budget-usd must not appear in the subprocess argv
# ---------------------------------------------------------------------------

def test_spawn_agent_cmd_has_no_max_budget_flag():
    """spawn_agent must not pass --max-budget-usd to the claude subprocess.

    On subscription auth the flag is ignored by billing but still enforced
    by the claude CLI, terminating the agent when it hits the cap.
    """
    source = AGENTS_PY_PATH.read_text()

    # Find the cmd = [...] block that spawns claude --print and check it
    # does not include --max-budget-usd.
    assert "--max-budget-usd" not in source, (
        "agents.py must not pass --max-budget-usd to the claude subprocess. "
        "Removing it ensures subscription-auth agents are not killed by a "
        "billing cap that has no effect on per-month costs."
    )


# ---------------------------------------------------------------------------
# Mailbox instruction: registration example must not embed a budget sentinel
# ---------------------------------------------------------------------------

def test_mailbox_instruction_has_no_budget_sentinel():
    """agent_mailbox_instruction must not embed '"budget": 5' (or any numeric
    budget sentinel) in the registration curl example baked into every agent's
    spawn prompt.

    When the registration example includes '"budget": 5', agents read it as
    their own spending limit. On subscription auth this causes agents to bail
    mid-investigation without committing once they perceive they have spent $5,
    even though the claude CLI is never actually passed --max-budget-usd.
    The short variant already omits budget; this guards the long variant too.
    """
    try:
        from routers.agents import agent_mailbox_instruction, agent_mailbox_instruction_short
    except ImportError:
        # Fallback: read source and check the string literally (avoids heavy
        # FastAPI import chain in lightweight CI environments).
        source = AGENTS_PY_PATH.read_text()
        assert '"budget": 5' not in source, (
            'agent_mailbox_instruction must not embed \'"budget": 5\' in the '
            "registration curl example. Remove the budget field from the "
            "registration body so agents are not told to self-limit at $5."
        )
        return

    for fn_name, fn in (
        ("agent_mailbox_instruction", agent_mailbox_instruction),
        ("agent_mailbox_instruction_short", agent_mailbox_instruction_short),
    ):
        result = fn("test-agent")
        # The registration body must not contain a numeric budget sentinel.
        # We check the serialised JSON substring rather than parsing the curl
        # command because the format is stable and easy to scan.
        assert '"budget": 5' not in result, (
            f"{fn_name} must not embed '\"budget\": 5' in the registration "
            "curl example. Remove the budget field from the registration body "
            "so agents are not told to self-limit at $5."
        )
        assert '"budget":5' not in result.replace(" ", ""), (
            f"{fn_name} must not embed a '\"budget\":5' sentinel (any spacing) "
            "in the registration curl example."
        )

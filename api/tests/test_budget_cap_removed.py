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

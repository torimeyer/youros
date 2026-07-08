"""Tests for →2522: mailbox bootstrap step prevents throwaway helper spawns.

Root cause: global settings.json denies `Bash`; `mcp__ostk__bash` is deferred
until ToolSearch loads it. Mailbox Step 0 tells agents to run curl before they
can load any shell tool → no working shell → agents fall back to spawning helper
agents (run-curl-command, execute-bash-command) to delegate single commands.

Fix: add a bootstrap step BEFORE Step 0 in both mailbox instruction forms that
tells agents to call ToolSearch to load mcp__ostk__bash first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import (
    agent_mailbox_instruction,
    agent_mailbox_instruction_short,
)


# ---------------------------------------------------------------------------
# agent_mailbox_instruction bootstrap tests
# ---------------------------------------------------------------------------

def test_mailbox_instruction_has_toolsearch_bootstrap():
    """Long-form mailbox must tell agents to load shell tools via ToolSearch."""
    text = agent_mailbox_instruction("test-agent")
    assert "ToolSearch" in text, (
        "mailbox instruction must include a ToolSearch call to load mcp__ostk__bash "
        "before Step 0, otherwise agents have no working shell and spawn helpers"
    )


def test_mailbox_instruction_bootstrap_references_bash():
    """Bootstrap must explicitly mention mcp__ostk__bash as the target tool."""
    text = agent_mailbox_instruction("test-agent")
    assert "mcp__ostk__bash" in text, (
        "bootstrap must name mcp__ostk__bash so agents know which tool to load"
    )


def test_mailbox_instruction_bootstrap_before_step_0():
    """Bootstrap step must appear BEFORE Step 0 (register) in the prompt text."""
    text = agent_mailbox_instruction("test-agent")
    toolsearch_pos = text.find("ToolSearch")
    step0_pos = text.find("Step 0")
    assert toolsearch_pos != -1, "ToolSearch must appear in mailbox instruction"
    assert step0_pos != -1, "Step 0 must appear in mailbox instruction"
    assert toolsearch_pos < step0_pos, (
        f"ToolSearch bootstrap (pos {toolsearch_pos}) must appear before "
        f"Step 0 (pos {step0_pos}) — agents read top-to-bottom and must "
        f"load the shell tool before attempting the Step 0 curl command"
    )


def test_mailbox_instruction_bootstrap_explains_why():
    """Bootstrap must explain WHY — Bash is blocked, mcp__ostk__bash is deferred."""
    text = agent_mailbox_instruction("test-agent")
    lower = text.lower()
    # Must mention that Bash is blocked OR denied
    has_bash_blocked = "bash" in lower and (
        "block" in lower or "deni" in lower or "deferred" in lower
    )
    assert has_bash_blocked, (
        "bootstrap must explain that Bash is blocked/denied and mcp__ostk__bash "
        "is deferred, so agents understand WHY they need ToolSearch first"
    )


def test_mailbox_instruction_no_helper_spawn_trigger():
    """Bootstrap must warn that missing it causes helper spawns."""
    text = agent_mailbox_instruction("test-agent")
    # Should warn about the consequence of skipping the bootstrap
    has_warning = (
        "helper" in text.lower()
        or "spawn" in text.lower()
        or "throwaway" in text.lower()
        or "deferred" in text.lower()
    )
    assert has_warning, (
        "bootstrap should warn that skipping ToolSearch causes helper-agent spawns"
    )


# ---------------------------------------------------------------------------
# agent_mailbox_instruction_short bootstrap tests
# ---------------------------------------------------------------------------

def test_mailbox_instruction_short_has_toolsearch_bootstrap():
    """Short-form mailbox must also include a ToolSearch bootstrap call."""
    text = agent_mailbox_instruction_short("test-agent")
    assert "ToolSearch" in text, (
        "short mailbox instruction must include ToolSearch bootstrap — "
        "the same Bash-denied / deferred-tool problem applies to quick-mode agents"
    )


def test_mailbox_instruction_short_bootstrap_references_bash():
    """Short-form bootstrap must name mcp__ostk__bash."""
    text = agent_mailbox_instruction_short("test-agent")
    assert "mcp__ostk__bash" in text, (
        "short-form bootstrap must name mcp__ostk__bash as the target"
    )


def test_mailbox_instruction_short_bootstrap_before_register():
    """Short-form bootstrap must appear before the Register curl line."""
    text = agent_mailbox_instruction_short("test-agent")
    toolsearch_pos = text.find("ToolSearch")
    register_pos = text.find("Register:")
    assert toolsearch_pos != -1, "ToolSearch must appear in short mailbox instruction"
    assert register_pos != -1, "Register: must appear in short mailbox instruction"
    assert toolsearch_pos < register_pos, (
        f"ToolSearch bootstrap (pos {toolsearch_pos}) must appear before "
        f"Register: (pos {register_pos}) — agents read top-to-bottom"
    )

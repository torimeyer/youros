"""Regression test for →RC3: bridge must inject completion requirement.

Root cause: bridge-spawned agents stopped after analysis with no commit and no
needle because the spawn prompt contained no obligation to produce output.
The post-agent watchers never fire after the isolation bridge exits 2 (blocks
native call), so they cannot enforce the scaffold-commit rule.

Fix: the isolation_bridge rule injects a COMPLETION REQUIREMENT clause into
every REST-spawned prompt before POSTing to /api/agents/spawn.

Wave 2 of →1288 consolidated the per-hook bash files into rule files under
.claude/hooks/lib/rules/. The isolation-bridge logic now lives at
lib/rules/isolation_bridge.sh, dispatched by pre-agent-guard.sh.

These tests verify the injection is present in the rule source so a future
refactor cannot silently remove it.
"""
from __future__ import annotations

import re
from pathlib import Path


BRIDGE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / ".claude" / "hooks" / "lib" / "rules" / "isolation_bridge.sh"
)


def _bridge_src() -> str:
    return BRIDGE_PATH.read_text()


def test_bridge_hook_exists():
    assert BRIDGE_PATH.exists(), f"hook not found at {BRIDGE_PATH}"


def test_bridge_injects_completion_requirement():
    src = _bridge_src()
    assert "COMPLETION REQUIREMENT" in src, (
        "task-isolation-bridge.sh must inject a COMPLETION REQUIREMENT clause "
        "into every spawned prompt (RC3 fix — agents were silently exiting after "
        "analysis without committing or filing a needle)"
    )


def test_bridge_completion_clause_covers_commit():
    src = _bridge_src()
    assert "git commit" in src, (
        "completion clause must mention git commit so agents know "
        "the expected output artifact"
    )


def test_bridge_completion_clause_covers_needle():
    src = _bridge_src()
    assert "ostk work add" in src, (
        "completion clause must mention ostk work add so agents know "
        "they can file a needle when a clean commit is not possible"
    )


def test_bridge_completion_clause_discourages_full_suite_block():
    src = _bridge_src()
    assert "full suite" in src or "full-suite" in src, (
        "completion clause must warn agents not to block commit on full test-suite "
        "pass (RC4 fix — http-500 agent ran 1244 tests for 22 min before committing)"
    )


def test_bridge_injected_prompt_replaces_plain_prompt():
    src = _bridge_src()
    # The old code was: "prompt": prompt or desc
    # The new code must use the injected variable, not the raw prompt.
    assert "injected_prompt" in src, (
        "body['prompt'] must reference injected_prompt, not the raw prompt variable"
    )
    # Raw form must NOT appear after the injection point.
    # Find the body dict assignment and confirm it uses injected_prompt.
    m = re.search(r'"prompt":\s*(\w+)', src)
    assert m is not None, "could not find \"prompt\": <var> in bridge body dict"
    assert m.group(1) == "injected_prompt", (
        f"body['prompt'] must be injected_prompt, found {m.group(1)!r}"
    )

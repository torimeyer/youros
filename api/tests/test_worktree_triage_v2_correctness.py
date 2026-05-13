"""
Correctness tests for worktree-triage-v2-2026-05-13.md.
Re-runs git cherry for a sample of worktrees from each section
and asserts the classification matches the rule.

Rules:
  ABSORBED  — git log main..<branch> is empty
  DUPLICATE — git log main..<branch> non-empty AND git cherry main <branch> has ZERO + lines
  UNIQUE    — git cherry main <branch> has at least one + line
"""
import subprocess
import pytest

REPO = "/Users/torimeyer/claude/torios"


def _run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=REPO)
    return r.stdout.strip()


def _log_ahead(branch):
    return _run(f"git log main..{branch} --oneline")


def _cherry_plus_lines(branch):
    out = _run(f"git cherry main {branch}")
    return [l for l in out.splitlines() if l.startswith("+")]


# --- ABSORBED sample (first 3) ---
ABSORBED_SAMPLE = [
    ("worktree-agent-build-agent-completion-push-hook-651d67",),
    ("worktree-agent-diagnose-agents-test-cancel-all-f99075",),
    ("worktree-agent-diagnose-store-drift-52cb2a2e",),
]


@pytest.mark.parametrize("branch", [b for (b,) in ABSORBED_SAMPLE])
def test_absorbed_log_empty(branch):
    """ABSORBED: git log main..<branch> must be empty."""
    assert _log_ahead(branch) == "", (
        f"Branch {branch} has commits ahead of main but was classified ABSORBED"
    )


# --- DUPLICATE sample (first 3) ---
DUPLICATE_SAMPLE = [
    ("worktree-agent-1202-slow-call-asgi-m-8c326ac5",),
    ("worktree-agent-1214-gate-self-filter-b5d38d",),
    ("worktree-agent-1215-mcp-env-parser-9a7724",),
]


@pytest.mark.parametrize("branch", [b for (b,) in DUPLICATE_SAMPLE])
def test_duplicate_no_plus_lines(branch):
    """DUPLICATE: git log ahead non-empty AND git cherry has ZERO + lines."""
    log = _log_ahead(branch)
    assert log != "", (
        f"Branch {branch} has empty log but was classified DUPLICATE (should be ABSORBED)"
    )
    plus = _cherry_plus_lines(branch)
    assert plus == [], (
        f"Branch {branch} has + lines in git cherry but was classified DUPLICATE: {plus}"
    )


# --- UNIQUE sample (first 3) ---
UNIQUE_SAMPLE = [
    ("worktree-agent-1176-tier-1-1b-delete-reaper-b0ab38",),
    ("worktree-agent-build-1134-chat-panel-21b1e90b",),
    ("worktree-agent-cache-status-clock-to-4fd6e3ce",),
]


@pytest.mark.parametrize("branch", [b for (b,) in UNIQUE_SAMPLE])
def test_unique_has_plus_lines(branch):
    """UNIQUE: git cherry must show at least one + line."""
    plus = _cherry_plus_lines(branch)
    assert len(plus) >= 1, (
        f"Branch {branch} has no + lines in git cherry but was classified UNIQUE"
    )

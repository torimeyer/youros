"""
Regression tests for →1301: Monitor silent-landing on worktree-branch commits.

Root cause: monitor-agent.sh used `git log` (not `--all`), making worktree-branch
commits invisible. The backend is also HTTPS-only, so http:// returns 000.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "monitor-agent.sh"


def _script_text() -> str:
    return SCRIPT.read_text()


def test_script_exists():
    assert SCRIPT.exists(), f"monitor-agent.sh not found at {SCRIPT}"


def test_git_has_sentinel_uses_all_flag():
    """git_has_sentinel must use --all so worktree-branch commits are visible."""
    text = _script_text()
    # Find the git_has_sentinel function body
    m = re.search(r"git_has_sentinel\(\)[^}]+}", text, re.DOTALL)
    assert m, "git_has_sentinel function not found in monitor-agent.sh"
    body = m.group(0)
    assert "--all" in body, (
        "git_has_sentinel() is missing --all flag. "
        "Without it, worktree-branch commits are invisible to the parent checkout. "
        "Fix: git log --all --oneline -20"
    )


def test_api_base_defaults_to_https():
    """API_BASE must default to https://, not http://. Backend is HTTPS-only."""
    text = _script_text()
    # Should have: API_BASE="${MYOS_API_BASE:-https://...}"
    assert "https://" in text, "monitor-agent.sh must use https:// somewhere"
    # Must not hardcode http:// for the backend
    assert 'http://127.0.0.1:8000' not in text, (
        "monitor-agent.sh hardcodes http://127.0.0.1:8000. "
        "Backend is HTTPS-only — use https:// or ${MYOS_API_BASE:-https://127.0.0.1:8000}"
    )


def test_git_log_all_finds_worktree_branch_commit():
    """
    Functional: git log --all finds a commit on a non-checked-out branch.
    git log alone (no --all) misses it, reproducing the →1301 failure mode.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Init a bare repo with one commit on main
        subprocess.run(["git", "init", "-b", "main"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, check=True, capture_output=True)
        (Path(tmpdir) / "README.md").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmpdir, check=True, capture_output=True)

        # Create a worktree-style branch (separate branch, not checked out)
        subprocess.run(
            ["git", "checkout", "-b", "worktree-agent-test-abc123"],
            cwd=tmpdir, check=True, capture_output=True
        )
        (Path(tmpdir) / "result.md").write_text("agent result")
        subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "docs(→1300): agentfile validate results"],
            cwd=tmpdir, check=True, capture_output=True
        )

        # Switch back to main (simulating parent checkout)
        subprocess.run(["git", "checkout", "main"], cwd=tmpdir, check=True, capture_output=True)

        # git log WITHOUT --all: should NOT find the sentinel
        result_no_all = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            cwd=tmpdir, capture_output=True, text=True
        )
        assert "→1300" not in result_no_all.stdout, (
            "git log without --all unexpectedly found the worktree-branch commit. "
            "Test setup may be wrong."
        )

        # git log WITH --all: MUST find the sentinel
        result_with_all = subprocess.run(
            ["git", "log", "--all", "--oneline", "-20"],
            cwd=tmpdir, capture_output=True, text=True
        )
        assert "→1300" in result_with_all.stdout, (
            "git log --all did not find the worktree-branch commit. "
            "The --all flag is broken or the test setup is wrong."
        )

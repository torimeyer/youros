"""Real-git worktree tests for →945.

No git-layer mocking. Each test uses a real `git init` repo in a tmpdir so we
verify the actual on-disk and git-tracking contract:
  - create_worktree() produces a worktree visible in `git worktree list`
  - The worktree branch is `worktree-agent-<name>`, never the parent's branch
  - Locked files from the agent's locks list are present in the checkout
  - Re-spawning the same agent name cleans up and succeeds (no orphan)
  - Two non-overlapping agents get separate real worktrees; edits don't bleed
  - remove_worktree() deregisters the worktree and deletes its branch
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.spawn_isolation import create_worktree, remove_worktree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(path: Path) -> None:
    """Create a minimal git repo with one commit so worktrees work."""
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    (path / "api").mkdir()
    (path / "api" / "router.py").write_text("# router\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _worktree_list(repo: Path) -> list[str]:
    """Return worktree paths registered with git (excluding the main one)."""
    result = _git(repo, "worktree", "list", "--porcelain")
    paths = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            wt = line.removeprefix("worktree ").strip()
            if wt != str(repo):
                paths.append(wt)
    return paths


def _branches(repo: Path) -> list[str]:
    result = _git(repo, "branch", "--format=%(refname:short)")
    return [b.strip() for b in result.stdout.splitlines() if b.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_worktree_is_visible_in_git_worktree_list(tmp_path):
    """A successful create_worktree() must register a real worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wt_path = repo / ".claude" / "worktrees" / "agent-alpha"
    branch = "worktree-agent-alpha"

    ok, err = await create_worktree(
        project_root=repo,
        agent_name="alpha",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok, f"create_worktree failed: {err}"

    # Visible in git worktree list
    listed = _worktree_list(repo)
    assert str(wt_path) in listed, (
        f"worktree not in git worktree list. Got: {listed}"
    )

    # Branch is worktree-agent-alpha, not main
    result = _git(wt_path, "branch", "--show-current")
    assert result.stdout.strip() == branch, (
        f"worktree branch is '{result.stdout.strip()}', expected '{branch}'"
    )

    # Locked files are present (full checkout, not sparse)
    assert (wt_path / "api" / "router.py").exists(), (
        "api/router.py missing from worktree checkout"
    )
    assert (wt_path / "api" / "router.py").read_text() == "# router\n"


@pytest.mark.asyncio
async def test_create_worktree_succeeds_on_respawn(tmp_path):
    """Re-spawning the same agent name must not silently alias to parent.

    This is the regression test for the core bug: without pre-clean,
    git worktree add fails because the branch/worktree still exists,
    and the handler falls back to the parent checkout.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wt_path = repo / ".claude" / "worktrees" / "agent-beta"
    branch = "worktree-agent-beta"

    # First spawn
    ok, err = await create_worktree(
        project_root=repo, agent_name="beta", branch=branch, wt_path=wt_path,
    )
    assert ok, f"first spawn failed: {err}"
    assert str(wt_path) in _worktree_list(repo)

    # Second spawn of the same name (simulates agent respawn without prior cleanup)
    ok2, err2 = await create_worktree(
        project_root=repo, agent_name="beta", branch=branch, wt_path=wt_path,
    )
    assert ok2, f"respawn failed: {err2}"

    # Must still be a real registered worktree
    listed = _worktree_list(repo)
    assert str(wt_path) in listed, f"respawn worktree not in list. Got: {listed}"

    # Branch must be the agent branch, not main
    result = _git(wt_path, "branch", "--show-current")
    assert result.stdout.strip() == branch


@pytest.mark.asyncio
async def test_two_agents_get_separate_worktrees_no_bleed(tmp_path):
    """Two non-overlapping agents must get independent worktrees.

    Edits in worktree-A must not appear in worktree-B or the parent.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wt_a = repo / ".claude" / "worktrees" / "agent-alice"
    wt_b = repo / ".claude" / "worktrees" / "agent-bob"

    ok_a, _ = await create_worktree(
        project_root=repo, agent_name="alice",
        branch="worktree-agent-alice", wt_path=wt_a,
    )
    ok_b, _ = await create_worktree(
        project_root=repo, agent_name="bob",
        branch="worktree-agent-bob", wt_path=wt_b,
    )
    assert ok_a and ok_b

    # Both registered
    listed = _worktree_list(repo)
    assert str(wt_a) in listed
    assert str(wt_b) in listed

    # Edit the same file in worktree-A only
    (wt_a / "api" / "router.py").write_text("# edited by alice\n")

    # Worktree-B must still have the original content
    content_b = (wt_b / "api" / "router.py").read_text()
    assert content_b == "# router\n", (
        f"alice's edit bled into bob's worktree: {content_b!r}"
    )

    # Parent must still have the original content
    content_parent = (repo / "api" / "router.py").read_text()
    assert content_parent == "# router\n", (
        f"alice's edit bled into parent: {content_parent!r}"
    )


@pytest.mark.asyncio
async def test_remove_worktree_deregisters_and_deletes_branch(tmp_path):
    """remove_worktree() must deregister the worktree and delete its branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wt_path = repo / ".claude" / "worktrees" / "agent-gamma"
    branch = "worktree-agent-gamma"

    ok, _ = await create_worktree(
        project_root=repo, agent_name="gamma", branch=branch, wt_path=wt_path,
    )
    assert ok
    assert str(wt_path) in _worktree_list(repo)
    assert branch in _branches(repo)

    await remove_worktree(project_root=repo, wt_path=wt_path, branch=branch)

    # Worktree deregistered
    assert str(wt_path) not in _worktree_list(repo), (
        "worktree still in git worktree list after remove"
    )
    # Branch deleted
    assert branch not in _branches(repo), (
        f"branch '{branch}' still exists after remove_worktree"
    )
    # Directory gone
    assert not wt_path.exists(), "worktree directory still on disk after remove"


@pytest.mark.asyncio
async def test_create_worktree_no_orphan_on_failure(tmp_path):
    """If worktree creation fails, no orphan directory must remain."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    # Break git so worktree add will fail: pass a non-existent start-point
    # We simulate this by temporarily breaking the project_root reference.
    wt_path = repo / ".claude" / "worktrees" / "agent-delta"
    branch = "worktree-agent-delta"

    # Monkeypatch _run_git inside create_worktree to make the final `add` fail
    # while letting the pre-clean steps run normally.
    import services.spawn_isolation as _siso

    original_run_git = _siso._run_git

    call_count = [0]

    async def _patched_run_git(*args, **kwargs):
        # Let pre-clean calls (worktree remove, branch -D) pass through.
        # Fail only the final `worktree add`.
        if "add" in args:
            call_count[0] += 1
            return 128, b"", b"fatal: simulated failure"
        return await original_run_git(*args, **kwargs)

    _siso._run_git = _patched_run_git
    try:
        ok, err = await create_worktree(
            project_root=repo, agent_name="delta", branch=branch, wt_path=wt_path,
        )
    finally:
        _siso._run_git = original_run_git

    assert not ok, "create_worktree should return False on git failure"
    assert not wt_path.exists(), (
        f"orphan directory {wt_path} was left behind after failed create_worktree"
    )
    assert call_count[0] == 1, "patched add was not called"


@pytest.mark.asyncio
async def test_create_and_remove_roundtrip_enables_respawn(tmp_path):
    """Explicit remove followed by create must produce a clean new worktree.

    This exercises the drain-cleanup → respawn path that was broken before
    the fix: after remove_worktree() the branch no longer exists, so the
    next create_worktree() succeeds without needing its own pre-clean.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wt_path = repo / ".claude" / "worktrees" / "agent-epsilon"
    branch = "worktree-agent-epsilon"

    ok, _ = await create_worktree(
        project_root=repo, agent_name="epsilon", branch=branch, wt_path=wt_path,
    )
    assert ok

    # Explicit cleanup (simulates drain path)
    await remove_worktree(project_root=repo, wt_path=wt_path, branch=branch)
    assert not wt_path.exists()
    assert branch not in _branches(repo)

    # Respawn must succeed
    ok2, err2 = await create_worktree(
        project_root=repo, agent_name="epsilon", branch=branch, wt_path=wt_path,
    )
    assert ok2, f"post-remove respawn failed: {err2}"
    assert str(wt_path) in _worktree_list(repo)

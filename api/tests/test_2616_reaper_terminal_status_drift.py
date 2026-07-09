"""Drift guard for →2616: the worktree reaper's terminal-status list must
match the backend's single source of truth.

The backend defines which agent statuses count as "finished" in ONE place:
``_TERMINAL_STATUSES`` in api/routers/agents.py (→2615). The standalone
reaper (scripts/worktree-reaper.sh) used to keep its own copy inside its
embedded python, and that copy omitted "killed", so a killed agent's
worktree was treated as still active and never cleaned (→2616).

These tests make that drift impossible to miss:

  1. The reaper's EFFECTIVE list (what it actually uses at runtime, printed
     via --print-terminal-statuses) must equal the backend constant.
  2. The reaper's hardcoded fallback snapshot (used only when the live parse
     of agents.py fails) must also equal the backend constant, so the
     snapshot is forced to stay fresh.
  3. Behavioral regression: an absorbed worktree whose owning agent is
     "killed" gets removed; one whose agent is "running" survives.

No api-package imports here on purpose: the whole point is that the shell
script cannot import the router (FastAPI + services graph), so the test
exercises the same textual contract the script relies on.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REAPER = REPO_ROOT / "scripts" / "worktree-reaper.sh"
AGENTS_ROUTER = REPO_ROOT / "api" / "routers" / "agents.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend_terminal_statuses() -> set[str]:
    """Load _TERMINAL_STATUSES from api/routers/agents.py without importing it.

    Importing routers.agents pulls FastAPI plus the whole services graph,
    which is exactly why the reaper parses the file textually. The test uses
    ast and only looks at MODULE-LEVEL assignments (tree.body, not ast.walk):
    the file also contains function-local ``_TERMINAL_STATUSES`` copies and a
    separate ``_LOCK_SWEEP_TERMINAL_STATUSES`` constant, none of which are the
    →2615 source of truth.
    """
    tree = ast.parse(AGENTS_ROUTER.read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_TERMINAL_STATUSES":
                value = node.value
                assert (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "frozenset"
                    and len(value.args) == 1
                ), "_TERMINAL_STATUSES is no longer a frozenset({...}) literal"
                found.append(set(ast.literal_eval(value.args[0])))
    assert len(found) == 1, (
        f"expected exactly one module-level _TERMINAL_STATUSES assignment "
        f"in {AGENTS_ROUTER} (→2615 made it the single source of truth), "
        f"found {len(found)}"
    )
    return found[0]


def _reaper_effective_terminal_statuses() -> set[str]:
    """Return the terminal-status set the reaper actually uses at runtime.

    Primary path: `worktree-reaper.sh --print-terminal-statuses` prints the
    effective list (live-parsed from agents.py, or the fallback snapshot).
    If the script does not support the flag yet (pre-→2616 versions), fall
    back to extracting the embedded hardcoded literal from the script source
    so the drift is still measured against the real list.
    """
    proc = subprocess.run(
        ["bash", str(REAPER), "--print-terminal-statuses"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    # Pre-→2616 script: no flag. Read the embedded TERMINAL literal.
    src = REAPER.read_text(encoding="utf-8")
    m = re.search(r"\bTERMINAL\s*=\s*\{([^}]*)\}", src)
    assert m, (
        "could not determine the reaper's terminal-status list: "
        "--print-terminal-statuses failed "
        f"(rc={proc.returncode}, stderr={proc.stderr.strip()!r}) and no "
        "embedded TERMINAL literal was found in the script"
    )
    return set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _make_fixture_repo(tmp_path: Path, agent_name: str) -> tuple[Path, Path]:
    """Minimal repo with one absorbed agent worktree. Returns (repo, wt_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("seed")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "seed")

    wt_parent = repo / ".claude" / "worktrees"
    wt_parent.mkdir(parents=True)
    wt_dir = wt_parent / f"agent-{agent_name}"
    _git(repo, "branch", f"worktree-agent-{agent_name}")
    _git(repo, "worktree", "add", "-q", str(wt_dir), f"worktree-agent-{agent_name}")
    return repo, wt_dir


def _run_reaper_apply(repo: Path) -> subprocess.CompletedProcess:
    """Run the real reaper with --apply through its agent_state.json path.

    YOUROS_ACTIVE_AGENTS is stripped so the script's own embedded python
    (the code under test) computes the active set from agent_state.json.
    REAPER_MIN_AGE_MINUTES=0 disables the →2608 recent-activity guard,
    which would otherwise protect the seconds-old fixture worktree.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("YOUROS_ACTIVE_AGENTS", None)
    env["REAPER_MIN_AGE_MINUTES"] = "0"
    return subprocess.run(
        ["bash", str(REAPER), "--apply"],
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
    )


# ---------------------------------------------------------------------------
# 1 + 2: list drift guards
# ---------------------------------------------------------------------------


def test_reaper_terminal_statuses_match_backend():
    """The reaper's effective terminal set must equal _TERMINAL_STATUSES."""
    backend = _backend_terminal_statuses()
    reaper = _reaper_effective_terminal_statuses()
    assert reaper == backend, (
        "scripts/worktree-reaper.sh terminal-status list has drifted from "
        "api/routers/agents.py _TERMINAL_STATUSES.\n"
        f"  missing from reaper: {sorted(backend - reaper)}\n"
        f"  extra in reaper:     {sorted(reaper - backend)}\n"
        "A status missing from the reaper's list makes agents in that state "
        "look active forever, so their worktrees are never cleaned (→2616)."
    )


def test_reaper_fallback_snapshot_matches_backend():
    """The script's hardcoded fallback snapshot must stay in sync too.

    The fallback only fires when agents.py cannot be parsed, but a stale
    snapshot silently reintroduces the drift on exactly the machines where
    the live parse is unavailable. Keep it fresh.
    """
    src = REAPER.read_text(encoding="utf-8")
    m = re.search(r"\bFALLBACK\s*=\s*\{([^}]*)\}", src)
    assert m, (
        "scripts/worktree-reaper.sh has no FALLBACK terminal-status snapshot; "
        "expected one next to the agents.py live parse (→2616)"
    )
    fallback = set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))
    backend = _backend_terminal_statuses()
    assert fallback == backend, (
        "the reaper's FALLBACK snapshot has drifted from agents.py "
        "_TERMINAL_STATUSES.\n"
        f"  missing from fallback: {sorted(backend - fallback)}\n"
        f"  extra in fallback:     {sorted(fallback - backend)}"
    )


# ---------------------------------------------------------------------------
# 3: behavioral regression for the reported symptom
# ---------------------------------------------------------------------------


def test_killed_agent_worktree_is_swept(tmp_path):
    """An absorbed worktree owned by a KILLED agent must be removed."""
    agent = "test-2616-killed"
    repo, wt_dir = _make_fixture_repo(tmp_path, agent)
    ostk = repo / ".ostk"
    ostk.mkdir()
    (ostk / "agent_state.json").write_text(
        json.dumps({agent: {"status": "killed", "task": "t"}})
    )

    proc = _run_reaper_apply(repo)
    assert proc.returncode == 0, (
        f"reaper failed: rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert not wt_dir.exists(), (
        "worktree of a KILLED agent survived the sweep -- 'killed' is being "
        "treated as an active status (→2616 regression).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_running_agent_worktree_survives(tmp_path):
    """Control: the same sweep must still protect a RUNNING agent."""
    agent = "test-2616-running"
    repo, wt_dir = _make_fixture_repo(tmp_path, agent)
    ostk = repo / ".ostk"
    ostk.mkdir()
    (ostk / "agent_state.json").write_text(
        json.dumps({agent: {"status": "running", "task": "t"}})
    )

    proc = _run_reaper_apply(repo)
    assert proc.returncode == 0, (
        f"reaper failed: rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert wt_dir.exists(), (
        "worktree of a RUNNING agent was removed -- active-agent protection "
        f"broke.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

"""→2963: the agent workspace folder location is one configurable setting.

The canonical resolver is ``services.spawn_isolation.worktrees_root()``:
default ``<project_root>/.claude/worktrees`` (unchanged from the historical
hard-wired location), overridable via the ``YOUROS_WORKTREES_DIR``
environment variable. An absolute override is used as-is; a relative one
resolves against the project root. Resolution happens at call time so the
current environment always wins.

Consumers verified here:
  * services.spawn_isolation itself (root resolver, canonical per-agent
    path builder, and the socket-path length logic which must measure the
    RESOLVED path's actual length, never an assumed default)
  * services.merge_debt (scans workspace folders)
  * services.transcript_resolver (maps workspace paths to transcript slugs)
  * scripts/worktree-reaper.sh (shell reaper gains the same env override)

With the env var unset, behavior must be byte-for-byte identical to the
old hard-wired path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REAPER = REPO_ROOT / "scripts" / "worktree-reaper.sh"

ENV_VAR = "YOUROS_WORKTREES_DIR"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with the override unset unless it sets it."""
    monkeypatch.delenv(ENV_VAR, raising=False)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "T", cwd=path)
    (path / "seed.txt").write_text("seed\n")
    _git("add", "seed.txt", cwd=path)
    _git("commit", "-q", "-m", "seed", cwd=path)


# ---------------------------------------------------------------------------
# (a) default resolution equals today's path
# ---------------------------------------------------------------------------

def test_default_root_is_claude_worktrees():
    import config
    from services.spawn_isolation import worktrees_root

    assert worktrees_root() == Path(config.PROJECT_ROOT) / ".claude" / "worktrees"


def test_default_root_with_explicit_project_root(tmp_path):
    from services.spawn_isolation import worktrees_root

    assert worktrees_root(tmp_path) == tmp_path / ".claude" / "worktrees"


def test_canonical_agent_path_matches_todays_layout(tmp_path):
    """worktree_path_for builds <root>/agent-<short id>, same as the spawn path."""
    from services.spawn_isolation import short_worktree_id, worktree_path_for

    name = "saa-demo"
    assert worktree_path_for(name, tmp_path) == (
        tmp_path / ".claude" / "worktrees" / f"agent-{short_worktree_id(name)}"
    )


# ---------------------------------------------------------------------------
# (b) the env override changes it everywhere, and the consumers agree
# ---------------------------------------------------------------------------

def test_absolute_override_wins(monkeypatch, tmp_path):
    from services.spawn_isolation import worktrees_root

    ws = tmp_path / "workspaces"
    monkeypatch.setenv(ENV_VAR, str(ws))
    assert worktrees_root() == ws


def test_relative_override_resolves_against_project_root(monkeypatch):
    import config
    from services.spawn_isolation import worktrees_root

    monkeypatch.setenv(ENV_VAR, "custom/agents")
    assert worktrees_root() == Path(config.PROJECT_ROOT) / "custom" / "agents"


def test_override_is_read_at_call_time(monkeypatch, tmp_path):
    """No import-time caching: flipping the env var flips the result."""
    from services.spawn_isolation import worktrees_root

    first = tmp_path / "a"
    second = tmp_path / "b"
    monkeypatch.setenv(ENV_VAR, str(first))
    assert worktrees_root() == first
    monkeypatch.setenv(ENV_VAR, str(second))
    assert worktrees_root() == second


def test_merge_debt_scans_configured_root(monkeypatch, tmp_path):
    """merge_debt counts a completed agent's workspace under the override."""
    from services.merge_debt import scan_merge_debt

    ws = tmp_path / "ws"
    agent_repo = ws / "agent-done"
    _init_repo(agent_repo)
    # Ensure a `main` ref exists at the seed commit (the default branch may
    # already be main), then move one commit ahead of it on a work branch.
    has_main = subprocess.run(
        ["git", "rev-parse", "--verify", "main"],
        cwd=agent_repo, capture_output=True, timeout=30,
    ).returncode == 0
    if not has_main:
        _git("branch", "main", cwd=agent_repo)
    _git("checkout", "-q", "-b", "work", cwd=agent_repo)
    (agent_repo / "extra.txt").write_text("extra\n")
    _git("add", "extra.txt", cwd=agent_repo)
    _git("commit", "-q", "-m", "ahead of main", cwd=agent_repo)

    monkeypatch.setenv(ENV_VAR, str(ws))
    result = scan_merge_debt(agent_statuses={"agent-done": "completed"})
    assert result["count"] == 1
    assert result["items"][0]["agent_name"] == "agent-done"
    assert result["items"][0]["worktree_path"] == str(agent_repo)


def test_merge_debt_default_is_project_root_claude_worktrees():
    """Env unset: merge_debt resolves the same default root as the resolver."""
    import config
    from services import merge_debt
    from services.spawn_isolation import worktrees_root

    assert worktrees_root() == Path(config.PROJECT_ROOT) / ".claude" / "worktrees"
    # The default root does not exist under the test-isolated project root,
    # so the scan must come back empty instead of erroring.
    assert merge_debt.scan_merge_debt(agent_statuses={}) == {"count": 0, "items": []}


def test_transcript_resolver_fragment_tracks_configured_root(monkeypatch, tmp_path):
    """The resolver's expected project-dir slug is derived from the same root."""
    from services import transcript_resolver as tr
    from services.spawn_isolation import worktrees_root

    ws = tmp_path / "ws"
    monkeypatch.setenv(ENV_VAR, str(ws))
    frag = tr.expected_project_dir_fragment("foo")
    root_slug = str(worktrees_root()).replace("/", "-").replace(".", "-")
    assert frag == f"{root_slug}-agent-foo"

    monkeypatch.delenv(ENV_VAR)
    default_frag = tr.expected_project_dir_fragment("foo")
    default_slug = str(worktrees_root()).replace("/", "-").replace(".", "-")
    assert default_frag == f"{default_slug}-agent-foo"
    assert default_frag != frag


def test_transcript_resolver_finds_transcript_under_configured_root(monkeypatch, tmp_path):
    from services import transcript_resolver as tr

    ws = tmp_path / "ws"
    monkeypatch.setenv(ENV_VAR, str(ws))
    projects = tmp_path / "projects"
    slug = str(ws / "agent-foo").replace("/", "-").replace(".", "-")
    proj_dir = projects / slug
    proj_dir.mkdir(parents=True)
    (proj_dir / "t.jsonl").write_text('{"type":"x"}\n')

    monkeypatch.setattr(tr, "_PROJECTS_DIR", projects)
    tr.clear_cache()
    try:
        assert tr.resolve_transcript("foo") == proj_dir / "t.jsonl"
    finally:
        tr.clear_cache()


def test_transcript_resolver_still_matches_dirs_from_a_prior_root(monkeypatch, tmp_path):
    """Workspaces created before a root move must still resolve (loose match)."""
    from services import transcript_resolver as tr

    monkeypatch.setenv(ENV_VAR, str(tmp_path / "new-ws"))
    projects = tmp_path / "projects"
    old_dir = projects / "-old-place-old-worktrees-agent-bar"
    old_dir.mkdir(parents=True)
    (old_dir / "old.jsonl").write_text('{"type":"x"}\n')

    monkeypatch.setattr(tr, "_PROJECTS_DIR", projects)
    tr.clear_cache()
    try:
        assert tr.resolve_transcript("bar") == old_dir / "old.jsonl"
    finally:
        tr.clear_cache()


# ---------------------------------------------------------------------------
# (c) socket-path length logic respects the configured root
# ---------------------------------------------------------------------------

def test_short_cwd_measures_actual_resolved_root_length(monkeypatch, tmp_path):
    """A long configured root must trip the sun_path guard by ACTUAL length."""
    from services.spawn_isolation import (
        SOCK_SUFFIX_LEN,
        SUN_PATH_MAX,
        short_cwd_for_worktree,
        worktree_path_for,
    )

    long_root = tmp_path / ("w" * 80)
    monkeypatch.setenv(ENV_VAR, str(long_root))
    wt = worktree_path_for("sockmath")
    assert len(str(wt)) + SOCK_SUFFIX_LEN >= SUN_PATH_MAX, "fixture must overflow"

    short = short_cwd_for_worktree(wt)
    try:
        assert short != str(wt)
        assert short.startswith("/tmp/myos-wt-")
        assert len(short) + SOCK_SUFFIX_LEN < SUN_PATH_MAX
    finally:
        p = Path(short)
        if p.is_symlink():
            p.unlink()


def test_short_cwd_keeps_real_path_when_configured_root_is_short(monkeypatch):
    from services.spawn_isolation import (
        SOCK_SUFFIX_LEN,
        SUN_PATH_MAX,
        short_cwd_for_worktree,
        worktree_path_for,
    )

    monkeypatch.setenv(ENV_VAR, "/tmp/wt2963")
    wt = worktree_path_for("tiny")
    assert len(str(wt)) + SOCK_SUFFIX_LEN < SUN_PATH_MAX
    assert short_cwd_for_worktree(wt) == str(wt)


def test_worktree_id_cap_is_deterministic_across_roots(monkeypatch):
    """Ids never depend on the configured root.

    The shell reaper's active-agent protection re-derives the 30-char short
    id with the cap hard-coded; a root-dependent cap would break that
    matching and desync ids between machines. Over-length CONFIGURED roots
    are handled by short_cwd_for_worktree's actual-length fallback instead.
    """
    from services.spawn_isolation import WORKTREE_ID_MAX_LEN, short_worktree_id

    name40 = "a" * 40
    base = short_worktree_id(name40)
    monkeypatch.setenv(ENV_VAR, "/very" * 30)
    assert short_worktree_id(name40) == base
    assert len(base) <= WORKTREE_ID_MAX_LEN
    assert short_worktree_id("short-name") == "short-name"


# ---------------------------------------------------------------------------
# (d) the shell reaper honors the same override
# ---------------------------------------------------------------------------

def _run_reaper(cwd: Path, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop(ENV_VAR, None)
    env.update(env_overrides)
    return subprocess.run(
        [str(REAPER)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_reaper_dry_run_scans_absolute_override(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ws = tmp_path / "ws"
    (ws / "agent-ghost").mkdir(parents=True)

    result = _run_reaper(repo, {ENV_VAR: str(ws)})
    assert result.returncode == 0, result.stderr
    assert "agent-ghost" in result.stdout
    assert "orphan" in result.stdout


def test_reaper_dry_run_scans_relative_override(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "wsrel" / "agent-rel").mkdir(parents=True)

    result = _run_reaper(repo, {ENV_VAR: "wsrel"})
    assert result.returncode == 0, result.stderr
    assert "agent-rel" in result.stdout


def test_reaper_default_still_scans_claude_worktrees(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".claude" / "worktrees" / "agent-ghost2").mkdir(parents=True)

    result = _run_reaper(repo, {})
    assert result.returncode == 0, result.stderr
    assert "agent-ghost2" in result.stdout

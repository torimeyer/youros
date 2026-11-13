"""Tests for runtime-neutral spawn_pipeline, reaper dual-dir support, and endpoints (->3091)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REAL_TORIOS_ROOT = Path(__file__).resolve().parents[2]
REAPER_SCRIPT = REAL_TORIOS_ROOT / "scripts" / "worktree-reaper.sh"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    strip_keys = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {k: v for k, v in os.environ.items() if k not in strip_keys}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def _init_external_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q", "-b", "main")
    _git(repo_dir, "config", "user.email", "dev@external.example")
    _git(repo_dir, "config", "user.name", "External Dev")
    (repo_dir / "README.md").write_text("# External App\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo_dir, "add", "README.md", "app.py")
    _git(repo_dir, "commit", "-q", "-m", "initial commit")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _branch_list(repo_dir: Path) -> list[str]:
    out = _git(repo_dir, "branch", "--format=%(refname:short)").stdout
    return sorted(line.strip() for line in out.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# 1. Worktree provisioning, .git/info/exclude hygiene, zero torios pollution
# ---------------------------------------------------------------------------


def test_provision_worktree_external_repo_clean_status_and_zero_torios_branches(tmp_path):
    from services.spawn_pipeline import WorktreeLease, provision_agent_worktree

    torios_branches_before = _branch_list(REAL_TORIOS_ROOT)

    external_app = tmp_path / "external_app"
    initial_sha = _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "fix-bug")
    try:
        assert isinstance(lease, WorktreeLease)
        assert lease.created is True
        assert lease.repo_root == external_app
        assert lease.worktree_path == external_app / ".youros" / "worktrees" / "agent-fix-bug"
        assert lease.worktree_path.is_dir()
        assert lease.branch_name == "worktree-agent-fix-bug"
        assert lease.base_sha == initial_sha

        # .youros/ must be excluded via .git/info/exclude, not .gitignore
        exclude_text = (external_app / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert ".youros/" in exclude_text
        assert not (external_app / ".gitignore").exists()

        # External repo git status must remain 100% clean
        status_out = _git(external_app, "status", "--porcelain").stdout
        assert status_out == ""

        # Zero branches added to torios itself
        torios_branches_after = _branch_list(REAL_TORIOS_ROOT)
        assert torios_branches_after == torios_branches_before
    finally:
        _git(external_app, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(external_app, "branch", "-D", lease.branch_name, check=False)


# ---------------------------------------------------------------------------
# 2. Target repo resolution & short socket env
# ---------------------------------------------------------------------------


def test_resolve_target_repo_precedence(tmp_path, monkeypatch):
    import config
    from services import settings_store as ss_mod
    from services.spawn_pipeline import resolve_target_repo

    explicit_repo = tmp_path / "explicit_repo"
    _init_external_repo(explicit_repo)

    env_repo = tmp_path / "env_repo"
    _init_external_repo(env_repo)

    settings_repo = tmp_path / "settings_repo"
    _init_external_repo(settings_repo)

    fallback_repo = tmp_path / "fallback_repo"
    _init_external_repo(fallback_repo)

    monkeypatch.setattr(config, "PROJECT_ROOT", fallback_repo)
    monkeypatch.setenv("OSTK_PROJECT_ROOT", str(env_repo))
    ss_mod.settings_store.update({"active_repo_path": str(settings_repo)})

    # 1. Explicit wins
    assert resolve_target_repo(str(explicit_repo)) == explicit_repo.resolve()

    # 2. OSTK_PROJECT_ROOT wins over settings and fallback
    assert resolve_target_repo() == env_repo.resolve()

    # 3. active_repo_path wins when OSTK_PROJECT_ROOT is unset and repo is valid git
    monkeypatch.delenv("OSTK_PROJECT_ROOT", raising=False)
    assert resolve_target_repo() == settings_repo.resolve()

    # 4. Non-git active_repo_path falls back to config.PROJECT_ROOT
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    ss_mod.settings_store.update({"active_repo_path": str(not_a_repo)})
    assert resolve_target_repo() == fallback_repo.resolve()


def test_build_short_socket_env_stays_under_macos_sun_len(tmp_path):
    from services.spawn_pipeline import build_short_socket_env

    deep_wt = tmp_path / ("nested_directory_" * 6) / ".youros" / "worktrees" / "agent-fix-bug"
    base_env = {"PATH": "/usr/bin:/bin"}

    env1 = build_short_socket_env(base_env, deep_wt, "fix-bug")
    env2 = build_short_socket_env(base_env, deep_wt, "fix-bug")

    assert "OSTK_PROJECT_ROOT" not in base_env
    assert env1["OSTK_PROJECT_ROOT"] == str(deep_wt)
    assert env1["YOUROS_SOCKET_DIR"].startswith("/tmp/yo-sock-")
    assert len(env1["YOUROS_SOCKET_DIR"]) < 60
    assert len(env1["YOUROS_SOCKET_DIR"] + "/ostk.sock") < 104
    assert env1["YOUROS_SOCKET_DIR"] == env2["YOUROS_SOCKET_DIR"]


# ---------------------------------------------------------------------------
# 3. Reset to save point + Merge-and-Reap + Conflict safety + Pin Decision
# ---------------------------------------------------------------------------


def test_reset_to_save_point_and_merge_and_reap_lifecycle(tmp_path):
    from services.spawn_pipeline import (
        merge_and_reap_worktree,
        provision_agent_worktree,
        reset_to_save_point,
    )

    torios_branches_before = _branch_list(REAL_TORIOS_ROOT)
    external_app = tmp_path / "external_app"
    initial_sha = _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "fix-bug")
    assert lease.base_sha == initial_sha

    # Commit a bad change + untracked file inside the worktree, then reset to save point
    (lease.worktree_path / "bad_change.py").write_text("BROKEN = True\n", encoding="utf-8")
    (lease.worktree_path / "untracked.tmp").write_text("temp\n", encoding="utf-8")
    _git(lease.worktree_path, "add", "bad_change.py")
    _git(lease.worktree_path, "commit", "-q", "-m", "bad attempt")
    assert _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip() != initial_sha

    reset_res = reset_to_save_point(lease.worktree_path, lease.base_sha, repo_root=external_app)
    assert reset_res["reset"] is True
    assert reset_res["head_sha"] == initial_sha
    assert reset_res["worktree_path"] == str(lease.worktree_path)
    assert _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip() == initial_sha
    assert not (lease.worktree_path / "bad_change.py").exists()
    assert not (lease.worktree_path / "untracked.tmp").exists()
    assert _git(lease.worktree_path, "status", "--porcelain").stdout == ""

    # Now commit the good fix inside the worktree and merge-and-reap
    (lease.worktree_path / "fix.py").write_text("FIXED = True\n", encoding="utf-8")
    _git(lease.worktree_path, "add", "fix.py")
    _git(lease.worktree_path, "commit", "-q", "-m", "good fix")
    commit_sha = _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip()

    merge_res = merge_and_reap_worktree(
        repo_root=external_app,
        branch_name=lease.branch_name,
        worktree_path=lease.worktree_path,
    )
    assert merge_res["merged"] is True
    assert merge_res["reaped"] is True
    assert merge_res["branch_name"] == "worktree-agent-fix-bug"
    assert merge_res["repo_root"] == str(external_app)
    assert merge_res["merged_sha"] == _git(external_app, "rev-parse", "HEAD").stdout.strip()
    assert merge_res["merged_sha"] == commit_sha or (external_app / "fix.py").exists()
    assert (external_app / "fix.py").read_text(encoding="utf-8") == "FIXED = True\n"

    # Worktree directory and branch must both be reaped
    assert not lease.worktree_path.exists()
    assert "worktree-agent-fix-bug" not in _branch_list(external_app)
    assert _git(external_app, "status", "--porcelain").stdout == ""
    assert _branch_list(REAL_TORIOS_ROOT) == torios_branches_before


def test_merge_and_reap_conflict_aborts_cleanly_and_preserves_worktree(tmp_path):
    from services.spawn_pipeline import merge_and_reap_worktree, provision_agent_worktree

    external_app = tmp_path / "external_conflict"
    _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "conflict-agent")
    try:
        # Divergent edits to app.py on main and worktree branch
        (external_app / "app.py").write_text("VALUE = 100\n", encoding="utf-8")
        _git(external_app, "add", "app.py")
        _git(external_app, "commit", "-q", "-m", "main edit")

        (lease.worktree_path / "app.py").write_text("VALUE = 200\n", encoding="utf-8")
        _git(lease.worktree_path, "add", "app.py")
        _git(lease.worktree_path, "commit", "-q", "-m", "agent edit")

        with pytest.raises(ValueError, match="Merge conflict"):
            merge_and_reap_worktree(
                repo_root=external_app,
                branch_name=lease.branch_name,
                worktree_path=lease.worktree_path,
            )

        # Merge must be aborted cleanly on external_app and worktree must survive
        assert not (external_app / ".git" / "MERGE_HEAD").exists()
        assert _git(external_app, "status", "--porcelain").stdout == ""
        assert lease.worktree_path.exists()
        assert lease.branch_name in _branch_list(external_app)
    finally:
        _git(external_app, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(external_app, "branch", "-D", lease.branch_name, check=False)


def test_merge_and_reap_zero_commits_reports_already_absorbed(tmp_path):
    from services.spawn_pipeline import merge_and_reap_worktree, provision_agent_worktree

    external_app = tmp_path / "external_noop"
    initial_sha = _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "noop-agent")
    res = merge_and_reap_worktree(external_app, lease.branch_name, lease.worktree_path)

    assert res["merged"] is True
    assert res["already_absorbed"] is True
    assert res["merged_sha"] == initial_sha
    assert res["reaped"] is True
    assert not lease.worktree_path.exists()
    assert lease.branch_name not in _branch_list(external_app)


def test_pin_decision_writes_durable_jsonl_without_requiring_ostk_dir(tmp_path, monkeypatch):
    from services.spawn_pipeline import pin_decision

    youros_dir = tmp_path / "youros_home"
    monkeypatch.setenv("YOUROS_HOME", str(youros_dir))

    external_app = tmp_path / "external_no_ostk"
    _init_external_repo(external_app)
    assert not (external_app / ".ostk").exists()

    res = pin_decision(
        repo_root=external_app,
        key="auth_strategy",
        value="cookie-session",
        reason="stateless refresh not needed for local app",
    )
    assert res["pinned"] is True
    decisions_file = youros_dir / "memory" / "decisions.jsonl"
    assert decisions_file.is_file()

    lines = [json.loads(line) for line in decisions_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    assert lines[0]["key"] == "auth_strategy"
    assert lines[0]["value"] == "cookie-session"
    assert lines[0]["reason"] == "stateless refresh not needed for local app"
    assert lines[0]["repo_root"] == str(external_app)


# ---------------------------------------------------------------------------
# 4. RuntimeProvider SpawnRequest / SpawnResult extensions & Reaper dual-scan
# ---------------------------------------------------------------------------


def test_runtime_provider_spawn_models_support_repo_and_save_point_fields():
    from services.runtime_provider import SpawnRequest, SpawnResult

    # Defaults remain None for 100% backwards compatibility
    req_default = SpawnRequest(name="a")
    assert req_default.repo_path is None
    assert req_default.base_sha is None

    res_default = SpawnResult(name="a")
    assert res_default.branch_name is None
    assert res_default.base_sha is None
    assert res_default.repo_path is None

    req = SpawnRequest(name="b", repo_path="/tmp/ext", base_sha="abc1234")
    res = SpawnResult(
        name="b",
        worktree_path="/tmp/ext/.youros/worktrees/agent-b",
        branch_name="worktree-agent-b",
        base_sha="abc1234",
        repo_path="/tmp/ext",
    )
    assert req.repo_path == "/tmp/ext"
    assert req.base_sha == "abc1234"
    assert res.branch_name == "worktree-agent-b"
    assert res.base_sha == "abc1234"
    assert res.repo_path == "/tmp/ext"


@pytest.mark.asyncio
async def test_reaper_discovers_and_reaps_both_claude_and_youros_worktrees(tmp_path, monkeypatch):
    from services.spawn_pipeline import provision_agent_worktree
    from services.worktree_reaper import discover_agent_worktrees, run_once

    monkeypatch.delenv("YOUROS_WORKTREES_DIR", raising=False)
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", str(REAPER_SCRIPT))
    monkeypatch.setenv("REAPER_MIN_AGE_MINUTES", "0")

    repo = tmp_path / "dual_wt_repo"
    _init_external_repo(repo)

    # 1. Create a legacy .claude/worktrees/agent-legacy worktree
    claude_wt = repo / ".claude" / "worktrees" / "agent-legacy"
    claude_wt.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "branch", "worktree-agent-legacy")
    _git(repo, "worktree", "add", "-q", str(claude_wt), "worktree-agent-legacy")

    # 2. Create a new .youros/worktrees/agent-modern worktree via spawn_pipeline
    modern_lease = provision_agent_worktree(repo, "modern")

    discovered = discover_agent_worktrees(repo)
    assert claude_wt in discovered
    assert modern_lease.worktree_path in discovered

    # Seed terminal agent_state.json so active-agent guard allows reaping both
    ostk_dir = repo / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    (ostk_dir / "agent_state.json").write_text(
        json.dumps(
            {
                "legacy": {"status": "completed"},
                "modern": {"status": "completed"},
            }
        ),
        encoding="utf-8",
    )

    with patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)), patch(
        "routers.agents.agent_metadata", {}
    ):
        result = await run_once(repo, transcripts_dir=tmp_path / "transcripts")

    assert result["worktrees_ok"] is True
    assert result["worktrees_removed"] == 2
    assert not claude_wt.exists()
    assert not modern_lease.worktree_path.exists()


# ---------------------------------------------------------------------------
# 5. FastAPI endpoints: merge-and-reap, reset-save-point, pin-decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agents_router_spawn_pipeline_endpoints(client, tmp_path, monkeypatch):
    import routers.agents as agents_mod
    from services.spawn_pipeline import provision_agent_worktree

    youros_dir = tmp_path / "youros_endpoint_home"
    monkeypatch.setenv("YOUROS_HOME", str(youros_dir))

    external_app = tmp_path / "endpoint_external_app"
    initial_sha = _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "api-worker")
    agent_name = "api-worker"
    agents_mod.agent_metadata[agent_name] = {
        "status": "completed",
        "repo_path": str(external_app),
        "worktree_path": str(lease.worktree_path),
        "worktree_branch": lease.branch_name,
        "branch_name": lease.branch_name,
        "base_sha": lease.base_sha,
        "isolation": "worktree",
    }

    try:
        # 1. Commit throwaway change, then POST /api/agents/{name}/reset-save-point
        (lease.worktree_path / "throwaway.txt").write_text("discard me\n", encoding="utf-8")
        _git(lease.worktree_path, "add", "throwaway.txt")
        _git(lease.worktree_path, "commit", "-q", "-m", "throwaway")

        r_reset = await client.post(f"/api/agents/{agent_name}/reset-save-point")
        assert r_reset.status_code == 200, r_reset.text
        assert r_reset.json()["reset"] is True
        assert r_reset.json()["head_sha"] == initial_sha
        assert not (lease.worktree_path / "throwaway.txt").exists()

        # 2. POST /api/agents/{name}/pin-decision
        r_pin = await client.post(
            f"/api/agents/{agent_name}/pin-decision",
            json={"key": "api_version", "value": "v2", "reason": "schema migration"},
        )
        assert r_pin.status_code == 200, r_pin.text
        assert r_pin.json()["pinned"] is True
        decisions_path = youros_dir / "memory" / "decisions.jsonl"
        assert decisions_path.exists()
        assert "api_version" in decisions_path.read_text(encoding="utf-8")

        # 3. Commit real work, then POST /api/agents/{name}/merge-and-reap
        (lease.worktree_path / "shipped.py").write_text("SHIPPED = 42\n", encoding="utf-8")
        _git(lease.worktree_path, "add", "shipped.py")
        _git(lease.worktree_path, "commit", "-q", "-m", "ship feature")

        r_merge = await client.post(f"/api/agents/{agent_name}/merge-and-reap")
        assert r_merge.status_code == 200, r_merge.text
        body = r_merge.json()
        assert body["merged"] is True
        assert body["reaped"] is True
        assert agents_mod.agent_metadata[agent_name].get("merged_at")
        assert (external_app / "shipped.py").exists()
        assert not lease.worktree_path.exists()

        # 4. Unknown agent returns 404
        r_missing = await client.post("/api/agents/nonexistent-agent-xyz/merge-and-reap")
        assert r_missing.status_code == 404
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)
        _git(external_app, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(external_app, "branch", "-D", lease.branch_name, check=False)


@pytest.mark.asyncio
async def test_merge_and_reap_endpoint_returns_409_on_conflict(client, tmp_path):
    import routers.agents as agents_mod
    from services.spawn_pipeline import provision_agent_worktree

    external_app = tmp_path / "conflict_endpoint_repo"
    _init_external_repo(external_app)

    lease = provision_agent_worktree(external_app, "conflict-api")
    agent_name = "conflict-api"
    agents_mod.agent_metadata[agent_name] = {
        "status": "completed",
        "repo_path": str(external_app),
        "worktree_path": str(lease.worktree_path),
        "branch_name": lease.branch_name,
        "base_sha": lease.base_sha,
        "isolation": "worktree",
    }

    try:
        (external_app / "app.py").write_text("VALUE = 10\n", encoding="utf-8")
        _git(external_app, "add", "app.py")
        _git(external_app, "commit", "-q", "-m", "main side")

        (lease.worktree_path / "app.py").write_text("VALUE = 20\n", encoding="utf-8")
        _git(lease.worktree_path, "add", "app.py")
        _git(lease.worktree_path, "commit", "-q", "-m", "agent side")

        resp = await client.post(f"/api/agents/{agent_name}/merge-and-reap")
        assert resp.status_code == 409, resp.text
        assert lease.worktree_path.exists()
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)
        _git(external_app, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(external_app, "branch", "-D", lease.branch_name, check=False)


def test_compose_spawn_meta_records_repo_path_branch_name_and_base_sha(tmp_path):
    import routers.agents as agents_mod
    from models.schemas import AgentSpawn
    from services.spawn_pipeline import provision_agent_worktree

    external_app = tmp_path / "meta_external_app"
    initial_sha = _init_external_repo(external_app)
    lease = provision_agent_worktree(external_app, "meta-worker")
    try:
        body = AgentSpawn(
            name="meta-worker",
            prompt="fix the bug",
            isolation="worktree",
            locks=["app.py"],
        )
        meta = agents_mod._compose_spawn_meta(
            body,
            model="sonnet",
            worktree_path=str(lease.worktree_path),
            worktree_branch=lease.branch_name,
            repo_path=str(external_app),
        )
        assert meta["repo_path"] == str(external_app)
        assert meta["branch_name"] == "worktree-agent-meta-worker"
        assert meta["base_sha"] == initial_sha
    finally:
        _git(external_app, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(external_app, "branch", "-D", lease.branch_name, check=False)

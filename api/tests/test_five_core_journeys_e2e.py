"""Automated Five-Journey E2E integration suite for the v6 release gate (->3097).

Exercises all 5 Core User Journeys end-to-end over FastAPI ASGI routes and real
isolated git repositories under tmp_path without mutating ~/.youros or torios
git branches:

  J1: First-run ostk trust readiness, active external repo switch, isolated
      .youros/worktrees provisioning, cryptographic receipt check, merge-and-reap.
  J2: Spec dependency wave gating via /complete-verified (409 on non-zero test
      exit keeping downstream blocked; 200 on exit-0 unblocking + auto-spawning).
  J3: Cross-runtime handoff preserving worktree branch and prior Claude commit SHA.
  J4: Mission Control multi-seat file conflict detection, clean Google-disconnected
      task-only briefing, and 14-day dogfood supervised_ratio telemetry.
  J5: Failed receipt detection, deterministic save-point rewind, and durable
      decision pinning to decisions.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REAL_TORIOS_ROOT = Path(__file__).resolve().parents[2]
_REAL_POPEN = subprocess.Popen


def _clean_root_env() -> dict[str, str]:
    """Return environment dict with workspace-override vars removed."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("OSTK_PROJECT_ROOT", "OSTK_ROOT")
    }


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git against cwd with GIT_* repo-binding env vars stripped."""
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


def _init_git_repo(repo_dir: Path, branch: str = "main") -> str:
    """Initialize an isolated git repository with an initial commit and return HEAD SHA."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q", "-b", branch)
    _git(repo_dir, "config", "user.email", "e2e@youros.local")
    _git(repo_dir, "config", "user.name", "yourOS E2E")
    (repo_dir / "README.md").write_text(f"# {repo_dir.name}\n", encoding="utf-8")
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core.py").write_text("VERSION = 1\n", encoding="utf-8")
    _git(repo_dir, "add", "README.md", "src/core.py")
    _git(repo_dir, "commit", "-q", "-m", "chore: initial scaffold", "--no-verify")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _branch_list(repo_dir: Path) -> list[str]:
    """Return sorted local branch names in repo_dir."""
    out = _git(repo_dir, "branch", "--format=%(refname:short)").stdout
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def _write_audit_jsonl(target_root: Path, entries: list[dict]) -> Path:
    """Write kernel audit entries to <target_root>/.ostk/audit.jsonl."""
    ostk_dir = target_root / ".ostk"
    ostk_dir.mkdir(parents=True, exist_ok=True)
    audit_path = ostk_dir / "audit.jsonl"
    payload = "\n".join(json.dumps(row) for row in entries) + ("\n" if entries else "")
    audit_path.write_text(payload, encoding="utf-8")
    return audit_path


@pytest.fixture(autouse=True)
def _isolate_journey_caches(tmp_path, monkeypatch):
    """Ensure per-test YOUROS_HOME and fresh session coordination locks/caches."""
    import routers.sessions as sessions_mod
    import services.provider_detection as pd_mod

    youros_dir = tmp_path / "youros_home"
    youros_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YOUROS_HOME", str(youros_dir))

    sessions_mod._coord_cache = None
    sessions_mod._coord_cache_ts = 0.0
    sessions_mod._coord_refresh_lock = asyncio.Lock()
    sessions_mod._digest_cache = None
    sessions_mod._digest_cache_ts = 0.0
    sessions_mod._digest_refresh_lock = asyncio.Lock()
    pd_mod._reset_provider_cache()
    yield
    sessions_mod._coord_cache = None
    sessions_mod._coord_cache_ts = 0.0
    pd_mod._reset_provider_cache()


# ---------------------------------------------------------------------------
# Journey 1: First-run trust health, external active-repo switch, worktree
# provisioning, cryptographic task receipt, and merge-and-reap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_1_first_run_trust_active_repo_and_merge_reap(
    client, tmp_path: Path, monkeypatch
):
    import routers.agents as agents_mod
    import routers.projects as projects_mod
    from services import provider_detection as pd_mod
    from services.spawn_pipeline import provision_agent_worktree

    torios_branches_before = _branch_list(REAL_TORIOS_ROOT)

    # 1. Check readiness (/api/status/clock) and ostk trust health (/api/secrets/key-status)
    clock_resp = await client.get("/api/status/clock")
    assert clock_resp.status_code == 200

    with (
        patch(
            "routers.secrets.ostk.secret_list",
            new=AsyncMock(return_value=[{"name": "ANTHROPIC_API_KEY", "available": True}]),
        ),
        patch.object(pd_mod, "detect_vertex_gemini", new=AsyncMock(return_value={"available": False})),
        patch.object(pd_mod, "is_claude_code_available", new=AsyncMock(return_value=True)),
        patch.object(pd_mod, "is_gemini_cli_available", new=AsyncMock(return_value=True)),
        patch.object(
            pd_mod,
            "detect_ostk_trust",
            new=AsyncMock(
                return_value={
                    "ostk_available": True,
                    "humanfile_valid": True,
                    "humanfile_warning": None,
                }
            ),
        ),
        patch("services.google_auth.is_authenticated", return_value=False),
    ):
        ks_resp = await client.get("/api/secrets/key-status")

    assert ks_resp.status_code == 200, ks_resp.text
    ks = ks_resp.json()
    assert ks["ostk_available"] is True
    assert ks["humanfile_valid"] is True
    assert ks["humanfile_warning"] is None
    assert ks["anthropic"] is True
    assert ks["anthropic_source"] == "keychain"
    assert ks["claude_subscription"] is True
    assert ks["gemini_cli"] is True

    # 2. Create external git repo pepper_ext, switch active repo, verify /api/projects/repos
    pepper_ext = tmp_path / "pepper_ext"
    initial_sha = _init_git_repo(pepper_ext, branch="main")

    torios_stub = tmp_path / "torios_stub"
    _init_git_repo(torios_stub, branch="main")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(projects_mod, "TORIOS_DIR", torios_stub)
    monkeypatch.setattr(projects_mod, "youros_home", lambda: tmp_path / "youros_home")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    agent_name = "j1-pepper-builder"
    lease = None
    try:
        with patch.dict(os.environ, _clean_root_env(), clear=True):
            sw_resp = await client.post(
                "/api/projects/active-repo",
                json={"path": str(pepper_ext)},
            )
            assert sw_resp.status_code == 200, sw_resp.text
            assert sw_resp.json()["active_repo_path"] == str(pepper_ext.resolve())

            repos_resp = await client.get("/api/projects/repos")
            assert repos_resp.status_code == 200, repos_resp.text
            repos_payload = repos_resp.json()
            assert repos_payload["active_repo_path"] == str(pepper_ext.resolve())
            by_name = {r["name"]: r for r in repos_payload["repos"]}
            assert "pepper_ext" in by_name
            assert by_name["pepper_ext"]["is_active"] is True
            assert by_name["pepper_ext"]["branch"] == "main"
            assert by_name["torios_stub"]["is_active"] is False

            # 3. Provision worktree in pepper_ext and verify zero branches leaked to torios
            lease = provision_agent_worktree(pepper_ext, agent_name)
            assert lease.worktree_path == pepper_ext / ".youros" / "worktrees" / f"agent-{agent_name}"
            assert lease.worktree_path.is_dir()
            assert lease.base_sha == initial_sha
            assert lease.branch_name not in _branch_list(REAL_TORIOS_ROOT)
            assert _branch_list(REAL_TORIOS_ROOT) == torios_branches_before

            # Commit a real fix mentioning ->901 and record exit-0 pytest run in audit.jsonl
            fix_file = lease.worktree_path / "src" / "pepper_fix.py"
            fix_file.write_text("def fixed() -> bool:\n    return True\n", encoding="utf-8")
            _git(lease.worktree_path, "add", "src/pepper_fix.py")
            _git(
                lease.worktree_path,
                "commit",
                "-q",
                "-m",
                "fix: resolve external pepper defect (→901)",
                "--no-verify",
            )
            fix_sha = _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip()
            _write_audit_jsonl(
                lease.worktree_path,
                [
                    {
                        "cmd": "pytest tests/test_pepper_fix.py",
                        "exit_code": 0,
                        "timestamp": "2026-11-13T20:00:00Z",
                    }
                ],
            )

            agents_mod.agent_metadata[agent_name] = {
                "status": "completed",
                "task_id": "→901",
                "task": "Fix pepper defect (→901)",
                "repo_path": str(pepper_ext),
                "worktree_path": str(lease.worktree_path),
                "worktree_branch": lease.branch_name,
                "branch_name": lease.branch_name,
                "base_sha": lease.base_sha,
                "isolation": "worktree",
            }

            # Verify green GET /api/tasks/901/receipt
            receipt_resp = await client.get("/api/tasks/901/receipt")
            assert receipt_resp.status_code == 200, receipt_resp.text
            receipt_body = receipt_resp.json()
            assert receipt_body["verified"] is True
            assert receipt_body["trace_linked"] is True
            assert receipt_body["commits"][0]["sha"] == fix_sha
            assert "src/pepper_fix.py" in receipt_body["changed_files"]
            assert receipt_body["test_runs"][-1]["exit_code"] == 0

            # Merge-and-reap onto pepper_ext main
            merge_resp = await client.post(f"/api/agents/{agent_name}/merge-and-reap")
            assert merge_resp.status_code == 200, merge_resp.text
            merge_data = merge_resp.json()
            assert merge_data["merged"] is True
            assert merge_data["reaped"] is True
            assert (pepper_ext / "src" / "pepper_fix.py").is_file()
            assert _git(pepper_ext, "rev-parse", "HEAD").stdout.strip() == merge_data["merged_sha"]
            assert not lease.worktree_path.exists()
            assert lease.branch_name not in _branch_list(pepper_ext)
            assert _branch_list(REAL_TORIOS_ROOT) == torios_branches_before
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)
        if lease is not None:
            _git(pepper_ext, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
            _git(pepper_ext, "branch", "-D", lease.branch_name, check=False)


# ---------------------------------------------------------------------------
# Journey 2: Spec dependency wave gating and cryptographic receipts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_2_spec_dependency_wave_and_cryptographic_receipts(
    client, tmp_path: Path
):
    from services.spawn_pipeline import provision_agent_worktree

    wave_repo = tmp_path / "wave_repo"
    base_sha = _init_git_repo(wave_repo, branch="main")
    lease = provision_agent_worktree(wave_repo, "j2-wave-worker")

    task_a = {
        "id": "→910",
        "title": "Wave 1 schema foundation",
        "status": "open",
        "priority": "P1",
    }
    task_b = {
        "id": "→911",
        "title": "Wave 2 API route blocked on schema",
        "status": "open",
        "priority": "P1",
        "blocked_by": ["910"],
    }
    seeded_tasks = [task_a, task_b]

    async def _close_task_side_effect(tid: str, closed_reason: str = "completed") -> str:
        if tid in ("910", "→910"):
            task_a["status"] = "closed"
        return f"closed {tid} ({closed_reason})"

    async def _list_tasks_side_effect(*_args, **_kwargs) -> list[dict]:
        return [dict(t) for t in seeded_tasks]

    try:
        # 1. Commit code mentioning ->910, but record failing pytest exit_code=1
        (lease.worktree_path / "src" / "schema.py").write_text(
            "SCHEMA_VERSION = 1\n", encoding="utf-8"
        )
        _git(lease.worktree_path, "add", "src/schema.py")
        _git(
            lease.worktree_path,
            "commit",
            "-q",
            "-m",
            "feat: draft wave 1 schema (→910)",
            "--no-verify",
        )
        _write_audit_jsonl(
            lease.worktree_path,
            [
                {
                    "cmd": "pytest tests/test_schema.py",
                    "exit_code": 1,
                    "timestamp": "2026-11-13T20:10:00Z",
                }
            ],
        )

        spawn_hook = AsyncMock(return_value="auto-agent-911")
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks._AUTO_SPAWN_UNBLOCKED_FN", spawn_hook),
        ):
            mock_ostk.cwd = str(wave_repo)
            mock_ostk.close_task = AsyncMock(side_effect=_close_task_side_effect)
            mock_ostk.list_tasks = AsyncMock(side_effect=_list_tasks_side_effect)

            # 2. Attempt complete-verified with exit_code=1 -> HTTP 409, task A stays open, B stays blocked
            fail_resp = await client.post(
                "/api/tasks/910/complete-verified",
                json={
                    "repo_path": str(wave_repo),
                    "worktree_path": str(lease.worktree_path),
                    "branch_name": lease.branch_name,
                    "base_sha": base_sha,
                },
            )
            assert fail_resp.status_code == 409, fail_resp.text
            fail_body = fail_resp.json()
            assert fail_body["verified"] is False
            assert any("non-zero" in r for r in fail_body["receipt"]["missing_reasons"])
            assert task_a["status"] == "open"
            assert task_b["blocked_by"] == ["910"]
            mock_ostk.close_task.assert_not_called()
            spawn_hook.assert_not_called()

            # 3. Commit fix + append exit-0 pytest run -> HTTP 200, task B unblocked and auto-spawned
            (lease.worktree_path / "src" / "schema.py").write_text(
                "SCHEMA_VERSION = 2\nVALIDATED = True\n", encoding="utf-8"
            )
            _git(lease.worktree_path, "add", "src/schema.py")
            _git(
                lease.worktree_path,
                "commit",
                "-q",
                "-m",
                "fix: green schema validation (→910)",
                "--no-verify",
            )
            _write_audit_jsonl(
                lease.worktree_path,
                [
                    {
                        "cmd": "pytest tests/test_schema.py",
                        "exit_code": 1,
                        "timestamp": "2026-11-13T20:10:00Z",
                    },
                    {
                        "cmd": "pytest tests/test_schema.py",
                        "exit_code": 0,
                        "timestamp": "2026-11-13T20:12:00Z",
                    },
                ],
            )

            ok_resp = await client.post(
                "/api/tasks/910/complete-verified",
                json={
                    "repo_path": str(wave_repo),
                    "worktree_path": str(lease.worktree_path),
                    "branch_name": lease.branch_name,
                    "base_sha": base_sha,
                },
            )

        assert ok_resp.status_code == 200, ok_resp.text
        ok_body = ok_resp.json()
        assert ok_body["verified"] is True
        assert ok_body["closed"] is True
        assert task_a["status"] == "closed"
        assert "→911" in ok_body["unblocked_tasks"]
        assert ok_body["spawned_agents"] == ["auto-agent-911"]
        spawn_hook.assert_awaited_once()
        assert spawn_hook.call_args[0][0]["id"] == "→911"
    finally:
        _git(wave_repo, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(wave_repo, "branch", "-D", lease.branch_name, check=False)


# ---------------------------------------------------------------------------
# Journey 3: Cross-runtime handoff preserves worktree branch and commit SHA
# ---------------------------------------------------------------------------


def _make_fake_async_proc(pid: int = 54321) -> MagicMock:
    """Create a stub asyncio.subprocess.Process that exits 0 without external calls."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = 0
    stdout_mock = MagicMock()
    stdout_mock.readline = AsyncMock(return_value=b"")
    stdout_mock.read = AsyncMock(return_value=b"")
    stderr_mock = MagicMock()
    stderr_mock.readline = AsyncMock(return_value=b"")
    stderr_mock.read = AsyncMock(return_value=b"")
    proc.stdout = stdout_mock
    proc.stderr = stderr_mock
    proc.stdin = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _safe_popen_stub(*popen_args, **popen_kwargs):
    """Intercept gemini/claude CLI Popen calls while letting git subprocesses run natively."""
    cmd = popen_args[0] if popen_args else popen_kwargs.get("args", [])
    exe = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd or "")
    exe_name = Path(str(exe)).name.lower()
    if exe_name in ("gemini", "claude"):
        fake_popen = MagicMock()
        fake_popen.pid = 54321
        fake_popen.returncode = 0
        fake_popen.poll = MagicMock(return_value=0)
        fake_popen.wait = MagicMock(return_value=0)
        return fake_popen
    return _REAL_POPEN(*popen_args, **popen_kwargs)


@pytest.mark.asyncio
async def test_journey_3_cross_runtime_handoff_preserves_worktree(
    client, tmp_path: Path
):
    from main import app
    import routers.agents as agents_mod
    from services.agentfile_parser import parse_agentfile
    from services.gemini_cli_provider import (
        GeminiCliRuntimeProvider,
        get_adhoc_agentfile_path,
        resolve_gemini_cli_model,
    )
    from services.runtime_provider import RuntimeProvider
    from services.spawn_pipeline import provision_agent_worktree

    handoff_repo = tmp_path / "handoff_repo"
    base_sha = _init_git_repo(handoff_repo, branch="main")
    agent_name = "j3-claude-worker"
    lease = provision_agent_worktree(handoff_repo, agent_name)

    # Prior Claude commit inside the agent worktree before hitting 5h cap
    (lease.worktree_path / "src" / "handoff_step.py").write_text(
        "STAGE = 'claude_checkpoint_1'\n", encoding="utf-8"
    )
    _git(lease.worktree_path, "add", "src/handoff_step.py")
    _git(
        lease.worktree_path,
        "commit",
        "-q",
        "-m",
        "feat: checkpoint 1 before usage cap (→930)",
        "--no-verify",
    )
    prior_claude_sha = _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip()
    assert prior_claude_sha != base_sha

    now_iso = datetime.now(timezone.utc).isoformat()
    agents_mod.agent_metadata[agent_name] = {
        "name": agent_name,
        "status": "failed",
        "source": "ui",
        "runtime": "claude",
        "model": "claude-sonnet-4-6",
        "task": "Complete cross-runtime handoff feature (→930)",
        "task_id": "→930",
        "prompt": "Finish implementing src/handoff_step.py",
        "last_error": "Rate limit reached: 5h usage cap hit",
        "rate_limited": True,
        "repo_path": str(handoff_repo),
        "worktree_path": str(lease.worktree_path),
        "worktree_branch": lease.branch_name,
        "branch_name": lease.branch_name,
        "base_sha": lease.base_sha,
        "isolation": "worktree",
        "spawned_at": now_iso,
    }

    has_handoff_route = any(
        "handoff-runtime" in getattr(route, "path", "") for route in app.routes
    )
    real_exec = asyncio.create_subprocess_exec

    async def _fake_exec(*exec_args, **exec_kwargs):
        exe_name = Path(str(exec_args[0])).name.lower() if exec_args else ""
        if exe_name == "git":
            return await real_exec(*exec_args, **exec_kwargs)
        return _make_fake_async_proc()

    try:
        if has_handoff_route:
            fake_bin_dir = tmp_path / "fake_bin"
            fake_bin_dir.mkdir(parents=True, exist_ok=True)
            fake_gemini = fake_bin_dir / "gemini"
            fake_gemini.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_gemini.chmod(0o755)

            with (
                patch("asyncio.create_subprocess_exec", side_effect=_fake_exec),
                patch("subprocess.Popen", side_effect=_safe_popen_stub),
                patch(
                    "services.gemini_cli_provider._find_gemini_binary",
                    return_value=str(fake_gemini),
                ),
                patch(
                    "services.gemini_cli_provider._gemini_signed_in",
                    return_value=True,
                ),
                patch(
                    "services.gemini_cli_provider.is_gemini_cli_available",
                    new=AsyncMock(return_value=True),
                ),
            ):
                resp = await client.post(
                    f"/api/agents/{agent_name}/handoff-runtime",
                    json={
                        "target_runtime": "gemini",
                        "target_model": "gemini-3.1-pro",
                        "transition_note": "5h cap hit",
                    },
                )
            assert resp.status_code == 200, resp.text
        else:
            # Pre-merge worktree fallback: verify GeminiCliRuntimeProvider, adhoc-default.agent,
            # handoff persistence, and worktree continuity directly.
            adhoc_path = get_adhoc_agentfile_path()
            assert adhoc_path.is_file()
            parsed_agentfile = parse_agentfile(adhoc_path)
            assert parsed_agentfile.name == "adhoc-default"
            assert "{{prompt}}" in (parsed_agentfile.prompt or "")

            provider = GeminiCliRuntimeProvider()
            assert isinstance(provider, RuntimeProvider)
            assert resolve_gemini_cli_model("gemini-3.1-pro") == "gemini-3.1-pro-preview"

            handoff_resp = await client.post(
                f"/api/agents/{agent_name}/handoff",
                json={
                    "summary": (
                        f"5h cap hit on claude-sonnet-4-6 at commit {prior_claude_sha}; "
                        "handing off worktree to gemini-3.1-pro."
                    )
                },
            )
            assert handoff_resp.status_code == 200, handoff_resp.text

            reused = provision_agent_worktree(handoff_repo, agent_name)
            assert reused.created is False
            assert reused.worktree_path == lease.worktree_path
            assert reused.branch_name == lease.branch_name

        # Worktree directory, branch, and prior Claude commit SHA must be preserved intact
        assert lease.worktree_path.is_dir()
        current_branch = _git(
            lease.worktree_path, "rev-parse", "--abbrev-ref", "HEAD"
        ).stdout.strip()
        current_sha = _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip()
        assert current_branch == lease.branch_name
        assert current_sha == prior_claude_sha
        assert (lease.worktree_path / "src" / "handoff_step.py").is_file()
    finally:
        for key in list(agents_mod.agent_metadata.keys()):
            if key.startswith("j3-") or agent_name in key:
                agents_mod.agent_metadata.pop(key, None)
        _git(handoff_repo, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(handoff_repo, "branch", "-D", lease.branch_name, check=False)


# ---------------------------------------------------------------------------
# Journey 4: Mission Control active sessions, file conflict detection,
# clean Google-disconnected briefing, and 14-day dogfood telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_4_mission_control_sessions_conflict_and_briefing(
    client, tmp_path: Path, monkeypatch
):
    from services import briefing as briefing_svc
    import services.dogfood_telemetry as dt_mod

    mc_repo = tmp_path / "mc_active_repo"
    _init_git_repo(mc_repo, branch="main")

    now = datetime.now(timezone.utc)
    ts_seat_a = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_seat_b = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Seed two active .ostk/sessions/ seats writing the same file src/core.py
    sessions_root = mc_repo / ".ostk" / "sessions"
    for sid, ts_val in (("seat-alpha", ts_seat_a), ("seat-beta", ts_seat_b)):
        seat_dir = sessions_root / sid
        seat_dir.mkdir(parents=True, exist_ok=True)
        ev = {
            "seq": 1,
            "ts": ts_val,
            "type": "event",
            "kind": "tool_call",
            "data": {
                "tool": "fs_ops",
                "input": json.dumps({"path": "src/core.py", "new_str": "VERSION = 2\n"}),
                "success": True,
                "summary": "ok",
            },
        }
        (seat_dir / "events.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")

    # Seed persisted fleet state in mc_repo so dogfood telemetry computes supervised_ratio
    (mc_repo / ".ostk" / "agent_state.json").write_text(
        json.dumps(
            {
                "fleet-supervised-1": {
                    "source": "ui",
                    "model": "claude-sonnet-4-6",
                    "spawned_at": now.isoformat(),
                    "merged_at": now.isoformat(),
                    "status": "completed",
                }
            }
        ),
        encoding="utf-8",
    )

    briefing_state_file = tmp_path / "briefing_state.json"
    seeded_open_tasks = [
        {
            "id": "→940",
            "title": "Resolve src/core.py seat conflict",
            "priority": "P1",
            "status": "open",
            "created_at": (now - timedelta(hours=3)).isoformat(),
        }
    ]

    with patch.dict(os.environ, _clean_root_env(), clear=True):
        sw_resp = await client.post(
            "/api/projects/active-repo",
            json={"path": str(mc_repo)},
        )
        assert sw_resp.status_code == 200, sw_resp.text

        # 2. Query active sessions and coordination snapshot -> both seats + src/core.py conflict
        active_resp = await client.get("/api/sessions/active")
        assert active_resp.status_code == 200, active_resp.text
        active_ids = {s["session_id"] for s in active_resp.json()["sessions"]}
        assert {"seat-alpha", "seat-beta"}.issubset(active_ids)

        with (
            patch("services.ostk.ostk.list_locks", new=AsyncMock(return_value=[])),
            patch("routers.agents.nudge_history", {}),
        ):
            coord_resp = await client.get("/api/sessions/coordination")
        assert coord_resp.status_code == 200, coord_resp.text
        coord_data = coord_resp.json()
        coord_ids = {s["id"] for s in coord_data["sessions"]}
        assert {"seat-alpha", "seat-beta"}.issubset(coord_ids)

        core_conflict = next(
            (c for c in coord_data["conflicts"] if c["path"] == "src/core.py"),
            None,
        )
        assert core_conflict is not None, f"Expected conflict on src/core.py, got {coord_data['conflicts']}"
        assert set(core_conflict["session_ids"]) == {"seat-alpha", "seat-beta"}

        # 3a. Disconnect Google and verify GET /api/briefing returns clean 200 task-only briefing
        disc_resp = await client.post("/api/secrets/google/disconnect")
        assert disc_resp.status_code == 200

        with (
            patch.object(briefing_svc, "BRIEFING_STATE_PATH", briefing_state_file),
            patch("services.google_auth.is_authenticated", return_value=False),
            patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=seeded_open_tasks)),
            patch("services.ostk.ostk.get_compounds", new=AsyncMock(return_value=[])),
            patch("services.reminders.list_upcoming", return_value=[]),
            patch("routers.briefing._maybe_regenerate_if_changed", new=AsyncMock()),
        ):
            await briefing_svc.generate_briefing()
            brief_resp = await client.get("/api/briefing")

        assert brief_resp.status_code == 200, brief_resp.text
        brief_data = brief_resp.json()
        assert brief_data["show"] is True
        assert "Your calendar is clear today." in brief_data["briefing"]
        assert "Resolve src/core.py seat conflict" in brief_data["briefing"]

        # 3b. Verify 14-day dogfood telemetry returns 200 with supervised_ratio
        monkeypatch.setattr(dt_mod, "_default_repo_roots", lambda: [mc_repo])
        monkeypatch.setattr(dt_mod, "_default_handoffs_log_path", lambda: tmp_path / "handoffs.jsonl")

        telem_resp = await client.get("/api/dashboard/dogfood-telemetry?days=14")
        assert telem_resp.status_code == 200, telem_resp.text
        telem = telem_resp.json()
        assert telem["window_days"] == 14
        assert "supervised_ratio" in telem
        assert telem["tracked_spawns"] == 1
        assert telem["untracked_terminal_sessions"] == 2
        assert telem["supervised_ratio"] == round(1 / 3, 3)


# ---------------------------------------------------------------------------
# Journey 5: Failed receipt detection, save-point rewind, and pin-decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_5_failed_receipt_rewind_and_pin_decision(
    client, tmp_path: Path
):
    import routers.agents as agents_mod
    from services.git_receipts import verify_task_receipt
    from services.spawn_pipeline import provision_agent_worktree

    rewind_repo = tmp_path / "rewind_repo"
    base_sha = _init_git_repo(rewind_repo, branch="main")
    agent_name = "j5-rewind-worker"
    lease = provision_agent_worktree(rewind_repo, agent_name)
    assert lease.base_sha == base_sha

    agents_mod.agent_metadata[agent_name] = {
        "status": "completed",
        "task_id": "→950",
        "task": "Fix root cause without workarounds (→950)",
        "repo_path": str(rewind_repo),
        "worktree_path": str(lease.worktree_path),
        "worktree_branch": lease.branch_name,
        "branch_name": lease.branch_name,
        "base_sha": base_sha,
        "isolation": "worktree",
    }

    try:
        # 1. Create bad commit with no passing test in .ostk/audit.jsonl -> fails verify_task_receipt
        bad_file = lease.worktree_path / "src" / "symptom_workaround.py"
        bad_file.write_text("WORKAROUND = True\n", encoding="utf-8")
        _git(lease.worktree_path, "add", "src/symptom_workaround.py")
        _git(
            lease.worktree_path,
            "commit",
            "-q",
            "-m",
            "fix: apply unverified symptom workaround (→950)",
            "--no-verify",
        )
        bad_sha = _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip()
        assert bad_sha != base_sha

        _write_audit_jsonl(
            lease.worktree_path,
            [{"event": "agent.step", "detail": "wrote code without running pytest"}],
        )

        direct_receipt = verify_task_receipt(
            repo_root=rewind_repo,
            worktree_path=lease.worktree_path,
            branch_name=lease.branch_name,
            base_sha=base_sha,
            task_id="→950",
        )
        assert direct_receipt.verified is False
        assert any(
            "No test execution recorded in .ostk/audit.jsonl" in r
            for r in direct_receipt.missing_reasons
        )

        http_receipt = await client.get("/api/tasks/950/receipt")
        assert http_receipt.status_code == 200, http_receipt.text
        assert http_receipt.json()["verified"] is False

        # 2. Rewind worktree via POST /api/agents/{name}/reset-save-point -> exact base_sha
        reset_resp = await client.post(f"/api/agents/{agent_name}/reset-save-point")
        assert reset_resp.status_code == 200, reset_resp.text
        reset_body = reset_resp.json()
        assert reset_body["reset"] is True
        assert reset_body["head_sha"] == base_sha
        assert _git(lease.worktree_path, "rev-parse", "HEAD").stdout.strip() == base_sha
        assert not bad_file.exists()

        # 3. Pin architectural decision via POST /api/agents/{name}/pin-decision -> decisions.jsonl
        pin_resp = await client.post(
            f"/api/agents/{agent_name}/pin-decision",
            json={
                "key": "no_symptom_fixes",
                "value": "Always fix root cause",
                "reason": "Journey 5",
            },
        )
        assert pin_resp.status_code == 200, pin_resp.text
        pin_body = pin_resp.json()
        assert pin_body["pinned"] is True
        assert pin_body["key"] == "no_symptom_fixes"

        decisions_file = Path(os.environ["YOUROS_HOME"]) / "memory" / "decisions.jsonl"
        assert decisions_file.is_file()
        rows = [
            json.loads(line)
            for line in decisions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        assert rows[0]["key"] == "no_symptom_fixes"
        assert rows[0]["value"] == "Always fix root cause"
        assert rows[0]["reason"] == "Journey 5"
        assert rows[0]["repo_root"] == str(rewind_repo.resolve())
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)
        _git(rewind_repo, "worktree", "remove", "--force", str(lease.worktree_path), check=False)
        _git(rewind_repo, "branch", "-D", lease.branch_name, check=False)

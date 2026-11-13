"""Tests for cryptographic git + ostk trace + kernel audit exit-0 receipt verifier (task ->3092)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_receipts import GitReceiptResult, verify_task_receipt


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "builder@example.com")
    _git(repo, "config", "user.name", "Builder Agent")
    (repo / ".gitignore").write_text(".ostk/\n", encoding="utf-8")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-q", "-m", "chore: initial scaffold", "--no-verify")
    return _git(repo, "rev-parse", "HEAD")


def _write_audit(repo: Path, entries: list[dict]) -> None:
    ostk_dir = repo / ".ostk"
    ostk_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries]
    (ostk_dir / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_prose_only_zero_commits_fails_verification(tmp_path: Path):
    """Zero commits ahead of base_sha cannot verify regardless of any prose claim."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    _write_audit(repo, [{"cmd": "pytest api/tests/", "exit_code": 0}])

    receipt = verify_task_receipt(
        repo_root=repo,
        base_sha=base_sha,
        task_id="42",
    )

    assert isinstance(receipt, GitReceiptResult)
    assert receipt.verified is False
    assert receipt.commits == []
    assert any("No git commits" in r for r in receipt.missing_reasons)


def test_unattributed_commit_fails_trace_link(tmp_path: Path):
    """A real commit + passing tests still fails when task_id has no commit or trace attribution."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-q", "-m", "feat: add unrelated feature", "--no-verify")
    _write_audit(repo, [{"cmd": "pytest api/tests/", "exit_code": 0}])

    receipt = verify_task_receipt(
        repo_root=repo,
        base_sha=base_sha,
        task_id="42",
    )

    assert receipt.verified is False
    assert len(receipt.commits) == 1
    assert receipt.trace_linked is False
    assert any("attribution" in r or "ostk trace" in r for r in receipt.missing_reasons)


def test_missing_audit_test_run_fails_verification(tmp_path: Path):
    """A real attributed commit without any test run in .ostk/audit.jsonl fails."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-q", "-m", "feat: implement feature (→42)", "--no-verify")
    _write_audit(repo, [{"event": "agent.spawned", "name": "fix-vitest-helper"}])

    receipt = verify_task_receipt(
        repo_root=repo,
        base_sha=base_sha,
        task_id="42",
    )

    assert receipt.verified is False
    assert receipt.trace_linked is True
    assert receipt.test_runs == []
    assert any("No test execution recorded in .ostk/audit.jsonl" in r for r in receipt.missing_reasons)


def test_nonzero_pytest_exit_code_fails_verification(tmp_path: Path):
    """Real commit mentioning ->42 with latest pytest exit_code=1 fails verification."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-q", "-m", "feat: implement verifier (→42)", "--no-verify")
    _write_audit(
        repo,
        [
            {"cmd": "pytest api/tests/", "exit_code": 0, "timestamp": "2026-11-13T20:00:00Z"},
            {"cmd": "pytest api/tests/test_git_receipts.py", "exit_code": 1, "timestamp": "2026-11-13T20:01:00Z"},
        ],
    )

    receipt = verify_task_receipt(
        repo_root=repo,
        base_sha=base_sha,
        task_id="42",
    )

    assert receipt.verified is False
    assert receipt.trace_linked is True
    assert len(receipt.test_runs) == 2
    assert receipt.test_runs[-1]["exit_code"] == 1
    assert any("non-zero" in r for r in receipt.missing_reasons)


def test_real_commit_and_exit_zero_audit_verifies_true(tmp_path: Path):
    """Real commit mentioning ->42 + exit_code=0 test run in .ostk/audit.jsonl verifies True."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    _git(repo, "checkout", "-q", "-b", "feat/task-42")
    (repo / "verifier.py").write_text("def ok() -> bool:\n    return True\n", encoding="utf-8")
    _git(repo, "add", "verifier.py")
    _git(repo, "commit", "-q", "-m", "feat: add verifier (→42)", "--no-verify")
    head_sha = _git(repo, "rev-parse", "HEAD")
    _write_audit(
        repo,
        [
            {"cmd": "pytest api/tests/test_git_receipts.py", "exit_code": 1, "timestamp": "2026-11-13T20:00:00Z"},
            {"cmd": "pytest api/tests/", "exit_code": 0, "timestamp": "2026-11-13T20:02:00Z"},
        ],
    )

    receipt = verify_task_receipt(
        repo_root=repo,
        branch_name="feat/task-42",
        base_sha=base_sha,
        task_id="→42",
    )

    assert receipt.verified is True
    assert receipt.missing_reasons == []
    assert receipt.trace_linked is True
    assert len(receipt.commits) == 1
    assert receipt.commits[0]["sha"] == head_sha
    assert receipt.commits[0]["short_sha"] == head_sha[:8]
    assert "→42" in receipt.commits[0]["subject"]
    assert "verifier.py" in receipt.changed_files
    assert "verifier.py" in receipt.diff_stat
    assert receipt.test_runs[-1]["exit_code"] == 0
    as_dict = receipt.to_dict()
    assert as_dict["verified"] is True
    assert as_dict["branch_name"] == "feat/task-42"
    assert as_dict["base_sha"] == base_sha


@pytest.mark.asyncio
async def test_complete_verified_409_when_tests_exit_nonzero(client, tmp_path: Path):
    """POST /api/tasks/42/complete-verified returns 409 and refuses to close when pytest exited 1."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "broken.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "broken.py")
    _git(repo, "commit", "-q", "-m", "feat: attempt fix (→42)", "--no-verify")
    _write_audit(repo, [{"cmd": "pytest api/tests/", "exit_code": 1}])

    spawn_hook = MagicMock(return_value="auto-task-43")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks._AUTO_SPAWN_UNBLOCKED_FN", spawn_hook),
    ):
        mock_ostk.cwd = str(repo)
        mock_ostk.close_task = AsyncMock(return_value="closed")
        mock_ostk.list_tasks = AsyncMock(
            return_value=[
                {"id": "→42", "title": "Base task", "status": "open"},
                {"id": "→43", "title": "Blocked task", "status": "open", "blocked_by": ["42"]},
            ]
        )

        resp = await client.post(
            "/api/tasks/42/complete-verified",
            json={"repo_path": str(repo), "base_sha": base_sha},
        )

    assert resp.status_code == 409
    payload = resp.json()
    assert payload["verified"] is False
    assert payload["detail"] == "Task receipt verification failed"
    assert any("non-zero" in r for r in payload["receipt"]["missing_reasons"])
    mock_ostk.close_task.assert_not_called()
    spawn_hook.assert_not_called()


@pytest.mark.asyncio
async def test_get_receipt_and_complete_verified_closes_and_autospawns_unblocked(client, tmp_path: Path):
    """Verified receipt closes task 42, unblocks task 43 (zero remaining blockers), and fires auto-spawn."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "verified_feature.py").write_text("READY = True\n", encoding="utf-8")
    _git(repo, "add", "verified_feature.py")
    _git(repo, "commit", "-q", "-m", "feat: verified work (→42)", "--no-verify")
    _write_audit(repo, [{"cmd": "pytest api/tests/", "exit_code": 0, "timestamp": "2026-11-13T20:05:00Z"}])

    # Verify GET /api/tasks/42/receipt resolves agent_metadata fallback too
    import routers.agents as agents_router

    agents_router.agent_metadata["test-agent-42"] = {
        "task_id": "→42",
        "worktree_path": str(repo),
        "base_sha": base_sha,
        "worktree_branch": "main",
    }
    try:
        get_resp = await client.get("/api/tasks/42/receipt")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["verified"] is True
        assert get_data["trace_linked"] is True
        assert len(get_data["commits"]) == 1

        spawn_hook = AsyncMock(return_value="spawned-agent-43")
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks._AUTO_SPAWN_UNBLOCKED_FN", spawn_hook),
        ):
            mock_ostk.cwd = str(repo)
            mock_ostk.close_task = AsyncMock(return_value="closed →42")
            mock_ostk.list_tasks = AsyncMock(
                return_value=[
                    {"id": "→42", "title": "Foundation task", "status": "open"},
                    {"id": "→43", "title": "Unblocked downstream", "status": "open", "blocked_by": ["42"]},
                    {
                        "id": "→44",
                        "title": "Still blocked downstream",
                        "status": "open",
                        "blocked_by": ["42", "99"],
                    },
                    {"id": "→99", "title": "Other open blocker", "status": "open"},
                ]
            )

            post_resp = await client.post(
                "/api/tasks/42/complete-verified",
                json={"repo_path": str(repo), "base_sha": base_sha},
            )

        assert post_resp.status_code == 200
        body = post_resp.json()
        assert body["verified"] is True
        assert body["closed"] is True
        assert body["unblocked_tasks"] == ["→43"]
        assert body["spawned_agents"] == ["spawned-agent-43"]
        mock_ostk.close_task.assert_awaited_once_with("→42", closed_reason="completed")
        spawn_hook.assert_awaited_once()
        spawned_task_arg = spawn_hook.call_args[0][0]
        assert spawned_task_arg["id"] == "→43"
    finally:
        agents_router.agent_metadata.pop("test-agent-42", None)


def test_omitted_task_id_and_status_ok_vitest_runner_verifies(tmp_path: Path):
    """When task_id is omitted trace_linked is True, and status='ok' vitest runs pass."""
    repo = tmp_path / "repo"
    base_sha = _init_repo(repo)
    (repo / "ui.tsx").write_text("export const ok = true;\n", encoding="utf-8")
    _git(repo, "add", "ui.tsx")
    _git(repo, "commit", "-q", "-m", "feat: add ui helper", "--no-verify")
    _write_audit(
        repo,
        [{"command": "npx vitest run src/components/ReceiptPanel.test.tsx", "status": "ok"}],
    )

    receipt = verify_task_receipt(repo_root=repo, base_sha=base_sha, task_id=None)

    assert receipt.verified is True
    assert receipt.trace_linked is True
    assert receipt.test_runs[-1]["exit_code"] == 0


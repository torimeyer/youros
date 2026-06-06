"""Regression tests for →1240: all write paths land in worktree, not main.

Root cause confirmed 2026-05-12:
  mcp__ostk__bash routes through the MAIN ostk daemon (OSTK_SOCKET points to
  the shared main socket). sh_run.rs line 24: `None => project_root.to_path_buf()`
  — when no cwd arg is given, bash defaults to the daemon's project_root (main
  checkout). Agents that call bash without cwd= commit and write to main instead
  of their worktree branch.

  b2be22a (→1225) only remapped absolute paths in the PROMPT text and added
  Rust-side fs_ops remapping in dispatch.rs. The Rust remapping only fires when
  the DAEMON process is in a worktree (checked via .git file vs directory). Since
  the shared main daemon is never in a worktree, dispatch.rs remapping is a no-op
  for all subagent calls that go through OSTK_SOCKET.

Fix (→1240):
  Inject a WORKTREE CWD header into every isolation=worktree prompt. The header
  tells the agent to always pass cwd=<worktree> to mcp__ostk__bash and use
  absolute worktree paths for mcp__ostk__fs_ops.

This file verifies:
  1. Write path isolation: file written with worktree cwd → not in main git status.
  2. Edit path isolation: file edited with worktree cwd → not in main git status.
  3. Bash/git isolation: git commit with worktree cwd → worktree branch, not main.
  4. Spawn injects the worktree cwd header when isolation=worktree.
  5. Spawn does NOT inject the header for isolation=none agents.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))


# ---------------------------------------------------------------------------
# Helpers: real git repo setup
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _setup_main_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo at tmp_path/main and return its path."""
    main = tmp_path / "main"
    main.mkdir(exist_ok=True)
    _git(["init", "-b", "main"], cwd=str(main))
    _git(["config", "user.email", "test@test.com"], cwd=str(main))
    _git(["config", "user.name", "Test"], cwd=str(main))
    # .claude/worktrees/ lives inside the repo dir; ignore it so git status
    # stays clean while worktrees exist there (mirrors production setup).
    (main / ".gitignore").write_text(".claude/\n")
    (main / "README.md").write_text("hello\n")
    _git(["add", ".gitignore", "README.md"], cwd=str(main))
    _git(["commit", "-m", "initial"], cwd=str(main))
    return main


def _setup_worktree(main: Path, wt_name: str = "agent-wt-test") -> Path:
    """Create a git worktree at main/.claude/worktrees/<wt_name>."""
    wt_root = main / ".claude" / "worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = wt_root / wt_name
    _git(
        ["worktree", "add", "-b", f"worktree-{wt_name}", str(wt_path), "main"],
        cwd=str(main),
    )
    return wt_path


def _main_status(main: Path) -> str:
    """Return `git status --short` output for the main repo."""
    result = _git(["status", "--short"], cwd=str(main), check=False)
    return result.stdout.strip()


def _worktree_log(wt_path: Path) -> list[str]:
    """Return one-line git log for commits ahead of main on the worktree branch."""
    result = _git(
        ["log", "--oneline", "main..HEAD"],
        cwd=str(wt_path),
        check=False,
    )
    return [l for l in result.stdout.strip().splitlines() if l]


# ---------------------------------------------------------------------------
# Tests 1-3: real filesystem isolation
# ---------------------------------------------------------------------------


def test_write_path_isolated_to_worktree(tmp_path):
    """File written using worktree as cwd does NOT appear in main git status.

    This simulates the 'Write tool path': a subagent opens a file for writing
    using its worktree cwd. The file must be isolated from the main checkout's
    git index so `git -C main status --short` shows no dirty entries.
    """
    main = _setup_main_repo(tmp_path)
    wt = _setup_worktree(main, "agent-write-test")

    # Simulate native Write: create a new file in the worktree
    (wt / "api").mkdir(exist_ok=True)
    (wt / "api" / "new_file.py").write_text("# written by subagent\n")

    # Main git status must be clean — the new file is in the worktree, not main
    assert _main_status(main) == "", (
        "File written to worktree appeared dirty in main git status. "
        "The Write tool path has a cwd-leak: writes are landing on main."
    )

    # Worktree itself should show something untracked (the new file or its dir)
    wt_status = _git(
        ["status", "--short", "--untracked-files=all"],
        cwd=str(wt), check=False,
    ).stdout.strip()
    assert "new_file.py" in wt_status, (
        "Expected the new file to appear untracked in the worktree"
    )


def test_edit_path_isolated_to_worktree(tmp_path):
    """File edited using worktree as cwd does NOT appear in main git status.

    This simulates the 'Edit tool path': the subagent modifies a tracked file
    within its worktree. Main's git status must remain clean.
    """
    main = _setup_main_repo(tmp_path)
    wt = _setup_worktree(main, "agent-edit-test")

    # Simulate native Edit: modify README.md inside the worktree
    readme = wt / "README.md"
    original = readme.read_text()
    readme.write_text(original + "# edited by subagent\n")

    # Main must be clean — the edit is in the worktree, not main
    assert _main_status(main) == "", (
        "File edited in worktree appeared dirty in main git status. "
        "The Edit tool path has a cwd-leak: edits are landing on main."
    )

    # Worktree shows the modification
    wt_status = _git(["status", "--short"], cwd=str(wt), check=False).stdout.strip()
    assert "README.md" in wt_status, (
        "Expected README.md to be modified in the worktree"
    )


def test_bash_git_commit_isolated_to_worktree(tmp_path):
    """git commit run with worktree as cwd lands on worktree branch, not main.

    This simulates `bash -c 'echo x > foo.txt && git add foo.txt && git commit -m test'`
    run with cwd=worktree. The commit must appear on the worktree branch and NOT
    on main. `git -C main status --short` must be empty (no dirty files from the
    worktree's commit).
    """
    main = _setup_main_repo(tmp_path)
    wt = _setup_worktree(main, "agent-bash-test")

    _git(["config", "user.email", "test@test.com"], cwd=str(wt))
    _git(["config", "user.name", "Test"], cwd=str(wt))

    # Run the full bash sequence in the worktree cwd
    (wt / "foo.txt").write_text("x\n")
    _git(["add", "foo.txt"], cwd=str(wt))
    _git(["commit", "-m", "test commit from subagent"], cwd=str(wt))

    # Main must be clean — no dirty files
    assert _main_status(main) == "", (
        "git commit in worktree made main dirty. "
        "Bash path has a cwd-leak: commits are landing on main."
    )

    # The commit must appear on the worktree branch (ahead of main)
    ahead = _worktree_log(wt)
    assert len(ahead) == 1, (
        f"Expected 1 commit ahead of main on worktree branch, got: {ahead}"
    )
    assert "test commit" in ahead[0], (
        f"Unexpected commit message on worktree branch: {ahead}"
    )

    # main branch itself must not have the new commit
    main_log = _git(
        ["log", "--oneline", "-5"],
        cwd=str(main), check=False,
    ).stdout.strip().splitlines()
    subjects = [l.split(" ", 1)[1] if " " in l else l for l in main_log]
    assert "test commit from subagent" not in subjects, (
        "Subagent commit appeared on main branch — isolation failed."
    )


# ---------------------------------------------------------------------------
# Subprocess doubles (mirror test_spawn_worktree_cwd.py)
# ---------------------------------------------------------------------------


class _FakeStdin:
    _closed = False

    def write(self, _: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        self._closed = True


class _FakeStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 99999
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


class _FakeForkProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    async def communicate(self):
        return (b"", b"")


def _install_spawn_doubles(
    monkeypatch, fork_returncode: int = 0, capture_prompt: bool = False
) -> dict[str, Any]:
    from routers import agents as agents_module
    import services.spawn_isolation as _siso

    calls: dict[str, Any] = {
        "exec": [],
        "fork_called": False,
        "wt_path": None,
        "prompt": None,
    }

    async def _fake_exec(*args, **kwargs):
        calls["exec"].append((args, kwargs))
        if args and args[0] == "git":
            if "worktree" in args and "add" in args:
                calls["fork_called"] = True
                wt_arg = next(
                    (a for a in args if "worktrees/agent-" in str(a)), None
                )
                if wt_arg:
                    calls["wt_path"] = str(wt_arg)
                return _FakeForkProc(returncode=fork_returncode)
            return _FakeForkProc(returncode=0)
        return _FakeProc()

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(_siso.asyncio, "create_subprocess_exec", _fake_exec)

    async def _noop_run(*a, **kw):
        return ""

    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    real_create_task = agents_module.asyncio.create_task

    def _maybe_noop_task(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _D:
                def cancel(self) -> None:
                    pass

                def done(self) -> bool:
                    return True

            return _D()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(agents_module.asyncio, "create_task", _maybe_noop_task)

    if capture_prompt:
        import builtins
        import os as _os
        _real_write = _os.write
        _captured: list[bytes] = []

        def _capturing_write(fd, data):
            if isinstance(data, bytes):
                _captured.append(data)
            return _real_write(fd, data)

        monkeypatch.setattr(_os, "write", _capturing_write)
        calls["_captured"] = _captured

    return calls


# ---------------------------------------------------------------------------
# Test 4: spawn injects worktree cwd header for isolation=worktree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_injects_worktree_cwd_header(tmp_path, monkeypatch):
    """isolation=worktree spawn must prepend the WORKTREE CWD →1240 header.

    The header tells agents to pass cwd=<worktree> to every mcp__ostk__bash
    call. Without it, bash defaults to the MAIN daemon project_root and writes
    land on main instead of the worktree branch.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0, capture_prompt=True)

    agent_name = "wt-cwd-header-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    wt_path = tmp_path / ".claude" / "worktrees" / f"agent-{agent_name}"

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "fix the broken test in api/tests/test_foo.py",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["/tmp/wt-cwd-header-test.log"],
                },
            )
        assert resp.status_code == 200, resp.text

        # Read back the prompt written to the temp file
        captured_bytes = b"".join(calls.get("_captured", []))
        prompt_text = captured_bytes.decode(errors="replace")

        assert "WORKTREE CWD" in prompt_text, (
            "Worktree cwd header (→1240) not found in spawned agent prompt. "
            "Agents will use the main daemon's default cwd (main checkout) for "
            "bash calls and land commits on main."
        )
        assert str(wt_path) in prompt_text, (
            f"Worktree path {wt_path} not found in the cwd header. "
            "Agent cannot know where to pass cwd=."
        )
        assert "mcp__ostk__bash" in prompt_text, (
            "Header must name mcp__ostk__bash explicitly so agent knows which tool to fix."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()
        shutil.rmtree(wt_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 5: isolation=none spawn does NOT get the worktree cwd header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolation_none_no_worktree_cwd_header(tmp_path, monkeypatch):
    """isolation=none spawns must NOT get the WORKTREE CWD header.

    Research agents don't have a worktree and shouldn't receive worktree
    instructions they can't follow.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0, capture_prompt=True)

    agent_name = "no-wt-cwd-header-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "read and summarize the docs",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "none",
                },
            )
        assert resp.status_code == 200, resp.text

        captured_bytes = b"".join(calls.get("_captured", []))
        prompt_text = captured_bytes.decode(errors="replace")

        assert "WORKTREE CWD" not in prompt_text, (
            "Worktree cwd header should not appear in isolation=none agent prompts."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()

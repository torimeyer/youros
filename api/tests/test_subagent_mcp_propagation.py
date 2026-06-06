"""Regression tests for subagent ostk MCP tool propagation.

Root cause (2026-05-10, updated 2026-05-11): when isolation="worktree" agents
have long names, the worktree path .claude/worktrees/agent-<name>/.ostk/ostk.sock
exceeds macOS sun_path (104). The ostk MCP server's bind() fails with
"path must be shorter than SUN_LEN", the kernel falls back to degraded mode,
and only static tools register (context/search/recall/nudge). bash/read/fs_ops
are missing — the subagent silently falls through to native tools, which
reintroduces the cwd-leak symptom (commits land on parent main).

Fix (2026-05-10): spawn code routes long worktree paths through a short /tmp
symlink so the resulting sock path fits sun_path.

Fix (2026-05-11, →1148): the prior fix only set the short path as cwd.
macOS getcwd() resolves symlinks so the ostk daemon still computed the socket
path from the real (long) path. Fix: also set OSTK_PROJECT_ROOT and OSTK_ROOT
to the short _spawn_cwd so the daemon uses the short path for socket binding.
CLAUDE_PROJECT_DIR remains the real path for Claude Code hook/worktree routing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.spawn_isolation import (  # noqa: E402
    SHORT_CWD_DIR,
    SOCK_SUFFIX_LEN,
    SUN_PATH_MAX,
    short_cwd_for_worktree,
)


def _sock_path_len(cwd: str) -> int:
    return len(cwd) + SOCK_SUFFIX_LEN


def test_short_path_passes_through_unchanged():
    """A worktree path that already fits sun_path must be returned as-is.

    macOS pytest tmp_path lives under /private/var/folders/... which is
    itself long enough to trigger the rewrite, so we construct the path
    explicitly under /tmp instead of using the tmp_path fixture.
    """
    import os
    short_root = Path("/tmp/myos-test-short-cwd")
    short_root.mkdir(exist_ok=True)
    wt = short_root / "agent-x"
    wt.mkdir(exist_ok=True)
    try:
        out = short_cwd_for_worktree(wt)
        assert out == str(wt), (
            f"path of length {len(str(wt))} should pass through unchanged"
        )
        assert _sock_path_len(out) < SUN_PATH_MAX
    finally:
        os.rmdir(wt)
        os.rmdir(short_root)


def test_long_path_routed_through_short_tmp_symlink():
    """A worktree path that would push the sock over sun_path must be
    rewritten to a short /tmp symlink that resolves to the worktree."""
    # Build a synthetic long path that mimics the real failure mode:
    # .../.claude/worktrees/agent-<long-name>/  ~ 110+ chars total
    from config import PROJECT_ROOT
    long_name = "fix-subagent-ostk-mcp-propagatio-7ba164"
    real_target = str(PROJECT_ROOT / ".claude" / "worktrees" / f"agent-{long_name}")
    target_path = Path(real_target)
    # The test does not need the real target to exist; we only verify the
    # symlink redirect chooses a short cwd. Stub the target so os.symlink
    # succeeds.
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.is_symlink() or target_path.exists():
        # Don't clobber the live worktree if the test runs there.
        out = short_cwd_for_worktree(target_path)
    else:
        target_path.mkdir(exist_ok=True)
        try:
            out = short_cwd_for_worktree(target_path)
        finally:
            target_path.rmdir()

    assert out != str(target_path), (
        "long path must be rewritten to a short /tmp symlink"
    )
    assert out.startswith(SHORT_CWD_DIR + "/myos-wt-"), (
        f"short cwd must live under {SHORT_CWD_DIR}/myos-wt- prefix; got {out}"
    )
    assert _sock_path_len(out) < SUN_PATH_MAX, (
        f"resulting sock path {_sock_path_len(out)} chars must fit "
        f"under sun_path {SUN_PATH_MAX}"
    )
    # The short link itself must resolve to the original worktree.
    assert Path(out).resolve() == target_path.resolve()


def test_short_link_is_idempotent_across_respawn():
    """Re-running the helper with the same long path must succeed without
    EEXIST. Re-spawn replaces a stale symlink in place."""
    from config import PROJECT_ROOT
    long_name = "test-respawn-deadbeef-cafebabe-cafef00d"
    real_target = str(PROJECT_ROOT / ".claude" / "worktrees" / f"agent-{long_name}-respawn")
    target_path = Path(real_target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not target_path.exists():
        target_path.mkdir(exist_ok=True)
        created = True
    try:
        first = short_cwd_for_worktree(target_path)
        second = short_cwd_for_worktree(target_path)
        assert first == second, "short cwd must be deterministic per worktree"
        assert Path(first).is_symlink()
        assert Path(first).resolve() == target_path.resolve()
    finally:
        if created:
            target_path.rmdir()


# ---------------------------------------------------------------------------
# Integration test: spawn env vars for long-name agents (→1148)
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


def _install_spawn_doubles_env(monkeypatch) -> dict[str, Any]:
    from routers import agents as agents_module
    import services.spawn_isolation as _siso

    calls: dict[str, Any] = {"exec": [], "fork_called": False, "spawn_env": None}

    async def _fake_exec(*args, **kwargs):
        calls["exec"].append((args, kwargs))
        if args and args[0] == "git":
            if "worktree" in args and "add" in args:
                calls["fork_called"] = True
                wt_arg = next(
                    (a for a in args if "worktrees/agent-" in str(a)), None
                )
                if wt_arg:
                    # Create the worktree dir so spawn_isolation checks pass.
                    Path(str(wt_arg)).mkdir(parents=True, exist_ok=True)
                return _FakeForkProc(returncode=0)
            return _FakeForkProc(returncode=0)
        # This is the claude subprocess call -- capture its env.
        calls["spawn_env"] = kwargs.get("env")
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
    return calls


@pytest.mark.asyncio
async def test_ostk_project_root_short_for_long_agent_name(tmp_path, monkeypatch):
    """OSTK_PROJECT_ROOT and OSTK_ROOT must be the short _spawn_cwd for long names.

    The prior fix (2026-05-10) only set the short path as cwd. macOS getcwd()
    resolves symlinks so the daemon still computed the socket path from the
    canonical (long) path via current_dir(). The daemon uses OSTK_PROJECT_ROOT
    to find its state dir and bind the socket. Without this fix, the daemon
    binds at the full worktree path (110+ chars) which exceeds macOS SUN_LEN
    (104), falls back to degraded mode, and ostk tools disappear. (→1148)
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    # Use a fixed-length project root under /tmp so the test is reproducible
    # regardless of the pytest tmp_path prefix length.
    fixed_root = Path("/tmp/myos-test-pr-long-agent-name")
    fixed_root.mkdir(exist_ok=True)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", fixed_root)
    (fixed_root / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (fixed_root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (fixed_root / ".ostk").mkdir(parents=True, exist_ok=True)

    # A name long enough that the worktree path exceeds sun_path.
    # fixed_root = 37 chars, /.claude/worktrees/agent- = 25 chars -> 62 prefix.
    # Need name > 104 - 62 - 16 = 26 chars.
    long_agent_name = "fix-subagent-ostk-mcp-not-loaded-411a0e"
    agent_metadata.pop(long_agent_name, None)
    active_agents.pop(long_agent_name, None)

    # Spawn now caps the worktree id via short_worktree_id() before building
    # the path, so the actual on-disk dir uses a shortened identifier rather
    # than the raw (long) agent name.  We compute the expected capped path
    # here so the assertions can verify both the cap and the legacy
    # short-cwd guarantees.
    from services.spawn_isolation import short_worktree_id as _short_wt_id
    capped_id = _short_wt_id(long_agent_name)
    capped_wt = fixed_root / ".claude" / "worktrees" / f"agent-{capped_id}"
    uncapped_wt = fixed_root / ".claude" / "worktrees" / f"agent-{long_agent_name}"

    calls = _install_spawn_doubles_env(monkeypatch)

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": long_agent_name,
                    "prompt": "fix the ostk mcp propagation issue",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["/tmp/fix-subagent-ostk-mcp-1148.log"],
                },
            )
        assert resp.status_code == 200, resp.text
        assert calls["fork_called"] is True, "git worktree add must be called"

        spawn_env = calls["spawn_env"]
        assert spawn_env is not None, "subprocess env was not captured"

        # The uncapped path would push the sock path over sun_path; the cap
        # is what brings it back under the limit.
        full_uncapped = str(uncapped_wt)
        assert len(full_uncapped) + SOCK_SUFFIX_LEN >= SUN_PATH_MAX, (
            f"test setup: uncapped path {len(full_uncapped)} chars should "
            f"trigger the cap (need sock path >= {SUN_PATH_MAX})"
        )

        # OSTK_PROJECT_ROOT must keep the worktree's sock path under sun_path.
        # Two valid outcomes after the cap: either the capped real path
        # already fits and is used directly, or it still overflows on the
        # current PROJECT_ROOT and the /tmp short-cwd fallback kicks in.
        pr = spawn_env.get("OSTK_PROJECT_ROOT", "")
        assert pr != full_uncapped, (
            "OSTK_PROJECT_ROOT must never be the uncapped path; "
            "the daemon would compute a socket path that exceeds macOS SUN_LEN (104)"
        )
        assert len(pr) + SOCK_SUFFIX_LEN < SUN_PATH_MAX, (
            f"OSTK_PROJECT_ROOT sock path {len(pr) + SOCK_SUFFIX_LEN} chars must "
            f"fit under sun_path {SUN_PATH_MAX}. Got OSTK_PROJECT_ROOT={pr!r}"
        )

        # OSTK_ROOT must match OSTK_PROJECT_ROOT.
        oroot = spawn_env.get("OSTK_ROOT", "")
        assert oroot == pr, (
            f"OSTK_ROOT must equal OSTK_PROJECT_ROOT; got {oroot!r} != {pr!r}"
        )

        # CLAUDE_PROJECT_DIR points at the (capped) real worktree path for
        # Claude Code hooks.  Hooks resolve from this; capping the dir is
        # safe because hooks treat it as opaque.
        cpd = spawn_env.get("CLAUDE_PROJECT_DIR", "")
        assert cpd == str(capped_wt), (
            f"CLAUDE_PROJECT_DIR must be the capped worktree path; got {cpd!r}"
        )
    finally:
        agent_metadata.pop(long_agent_name, None)
        active_agents.pop(long_agent_name, None)
        _reset_spawn_lock_registry_for_tests()
        shutil.rmtree(fixed_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_ostk_socket_env_set_for_worktree_spawn(tmp_path, monkeypatch):
    """OSTK_SOCKET must be set in _spawn_env for worktree agents.

    agent_loop.rs resolve_socket_path() checks OSTK_SOCKET before computing
    <cwd>/.ostk/ostk.sock.  Setting it to the main daemon socket path (always
    short) prevents the MCP server from falling back to degraded mode when the
    worktree path is long (→1177).

    This test verifies that OSTK_SOCKET is injected for any worktree spawn,
    regardless of whether the path would trigger the short-cwd rewrite.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    fixed_root = Path("/tmp/myos-test-ostk-socket-env")
    fixed_root.mkdir(exist_ok=True)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", fixed_root)
    (fixed_root / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (fixed_root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (fixed_root / ".ostk").mkdir(parents=True, exist_ok=True)

    long_agent_name = "fix-degraded-mcp-mode-ostk-socket-1177"
    agent_metadata.pop(long_agent_name, None)
    active_agents.pop(long_agent_name, None)

    calls = _install_spawn_doubles_env(monkeypatch)

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": long_agent_name,
                    "prompt": "fix degraded mcp mode for worktree agents",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["/tmp/fix-degraded-mcp-1177.log"],
                },
            )
        assert resp.status_code == 200, resp.text
        assert calls["fork_called"] is True, "git worktree add must be called"

        spawn_env = calls["spawn_env"]
        assert spawn_env is not None, "subprocess env was not captured"

        # OSTK_SOCKET must be set and must point to the main daemon socket.
        ostk_socket = spawn_env.get("OSTK_SOCKET", "")
        expected_main_sock = str(fixed_root / ".ostk" / "ostk.sock")
        assert ostk_socket == expected_main_sock, (
            f"OSTK_SOCKET must point at the main daemon socket ({expected_main_sock!r}); "
            f"got {ostk_socket!r}. Without this, agent_loop.rs computes the socket path "
            "from <cwd>/.ostk/ostk.sock which exceeds macOS sun_path for long agent names "
            "and the MCP server falls back to degraded mode (→1177)."
        )

        # OSTK_SOCKET must be short enough to fit sun_path on its own (it's
        # the main project root's socket, not the worktree's).
        assert len(ostk_socket) < SUN_PATH_MAX, (
            f"OSTK_SOCKET {len(ostk_socket)} chars must fit under sun_path {SUN_PATH_MAX}"
        )
    finally:
        agent_metadata.pop(long_agent_name, None)
        active_agents.pop(long_agent_name, None)
        _reset_spawn_lock_registry_for_tests()
        shutil.rmtree(fixed_root, ignore_errors=True)

"""Regression tests for subagent ostk MCP tool propagation.

Root cause (2026-05-10): when isolation="worktree" agents have long names,
the worktree path .claude/worktrees/agent-<name>/.ostk/ostk.sock exceeds
macOS sun_path (104). The ostk MCP server's bind() fails with
"path must be shorter than SUN_LEN", the kernel falls back to degraded mode,
and only static tools register (context/search/recall/nudge). bash/read/fs_ops
are missing — the subagent silently falls through to native tools, which
reintroduces the cwd-leak symptom (commits land on parent main).

Fix: spawn code routes long worktree paths through a short /tmp symlink so
the resulting sock path fits sun_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    long_name = "fix-subagent-ostk-mcp-propagatio-7ba164"
    real_target = (
        "/Users/torimeyer/claude/torios/.claude/worktrees/"
        f"agent-{long_name}"
    )
    target_path = Path(real_target)
    # The test does not need the real target to exist; we only verify the
    # symlink redirect chooses a short cwd. Stub the target so os.symlink
    # succeeds.
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.is_symlink() or target_path.exists():
        # Don't clobber the live worktree if the test runs there.
        out = short_cwd_for_worktree(target_path)
    else:
        target_path.mkdir()
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
    long_name = "test-respawn-deadbeef-cafebabe-cafef00d"
    real_target = (
        "/Users/torimeyer/claude/torios/.claude/worktrees/"
        f"agent-{long_name}-respawn"
    )
    target_path = Path(real_target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not target_path.exists():
        target_path.mkdir()
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
# Regression: OSTK_PROJECT_ROOT must use the short cwd, not the full path
# (→1148) When the worktree path is long, ostk uses OSTK_PROJECT_ROOT for
# socket location, not the actual process CWD. Setting it to the full path
# causes bind() to fail (110 > 104 chars) and degrades to static-only tools.
# ---------------------------------------------------------------------------


class _FakeStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 424242
    returncode = 0

    def __init__(self) -> None:
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


class _FakeForkProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b"", stdout: bytes = b"0\n") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self._stdout = stdout

    async def communicate(self):
        return (self._stdout, self._stderr)


@pytest.mark.asyncio
async def test_spawn_sets_ostk_root_to_short_cwd_for_long_agent_name(monkeypatch):
    """OSTK_PROJECT_ROOT and OSTK_ROOT must be the short /tmp path when the
    worktree path would push the ostk socket over macOS's 104-char sun_path.

    Root cause (→1148): spawn set OSTK_PROJECT_ROOT to the full worktree path.
    ostk uses this env var (not the process CWD) for socket location. A 94-char
    worktree path + 16-char "/.ostk/ostk.sock" = 110 > 104, causing bind() to
    fail. The kernel fell back to degraded mode: only context/search/nudge
    tools registered; bash/read/fs_ops disappeared; subagents fell through to
    native tools.

    Fix: set OSTK_PROJECT_ROOT and OSTK_ROOT to _spawn_cwd (the /tmp short path).
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from main import app
    from routers.agents import active_agents, agent_metadata
    import routers.agents as agents_module
    import services.spawn_isolation as _siso
    from httpx import ASGITransport, AsyncClient

    # Use a long agent name that makes the worktree socket path exceed 104 chars.
    # .claude/worktrees/agent-<name>/.ostk/ostk.sock needs base path + suffix.
    # PROJECT_ROOT ~30 chars + "/.claude/worktrees/agent-" 25 chars + name + 16 = >104
    # So name >= 34 chars triggers the short-cwd path. Use 40 chars to be safe.
    agent_name = "fix-subagent-ostk-mcp-not-loaded-411a0e"  # 40 chars, matches real failure

    captured_envs: list[dict] = []

    async def _fake_exec(*args, **kwargs):
        env = kwargs.get("env") or {}
        captured_envs.append(dict(env))
        if args and args[0] == "git":
            if "worktree" in args and "add" in args:
                return _FakeForkProc(returncode=0)
            return _FakeForkProc(returncode=0)
        return _FakeProc()

    real_create_task = agents_module.asyncio.create_task

    def _noop_drain_task(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _Dummy:
                def cancel(self) -> None:
                    return None

                def done(self) -> bool:
                    return True

            return _Dummy()
        return real_create_task(coro, *a, **kw)

    async def _noop_run(*args, **kwargs):
        return ""

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(_siso.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(agents_module.asyncio, "create_task", _noop_drain_task)
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "fix the ostk mcp propagation bug",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": ["/tmp/fix-subagent-ostk-mcp-1148.log"],
                },
            )
        assert resp.status_code == 200, resp.text

        # Find the claude --print subprocess call (not git)
        claude_envs = [
            env for env in captured_envs
            if "CLAUDE_PROJECT_DIR" in env or "OSTK_PROJECT_ROOT" in env
        ]
        assert claude_envs, (
            "Expected at least one subprocess call with OSTK_PROJECT_ROOT in env; "
            f"got captured envs: {[list(e.keys())[:5] for e in captured_envs]}"
        )
        env = claude_envs[-1]  # last one is the claude --print call

        ostk_root = env.get("OSTK_PROJECT_ROOT", "")
        assert ostk_root, "OSTK_PROJECT_ROOT must be set in subagent env"

        sock_path_len = len(ostk_root) + SOCK_SUFFIX_LEN
        assert sock_path_len < SUN_PATH_MAX, (
            f"OSTK_PROJECT_ROOT={ostk_root!r} makes socket path {sock_path_len} chars "
            f"which exceeds macOS sun_path limit ({SUN_PATH_MAX}). "
            f"This causes the ostk daemon bind() to fail and degrades to static-only tools."
        )
        assert env.get("OSTK_ROOT", "") == ostk_root, (
            "OSTK_ROOT must equal OSTK_PROJECT_ROOT"
        )
        # For this long agent name, the root must be the /tmp short link, not the
        # full worktree path.
        assert ostk_root.startswith("/tmp/"), (
            f"Long agent name must route OSTK_PROJECT_ROOT through /tmp short link; "
            f"got {ostk_root!r}"
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)

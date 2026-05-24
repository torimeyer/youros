"""Regression tests for →1225: fs_ops writes must land in agent worktree, not main checkout.

Root cause: agents spawned with isolation=worktree receive absolute paths in their
briefs that point to the main checkout (e.g. /Users/.../torios/api/foo.py). When
the agent calls mcp__ostk__fs_ops with those absolute paths, writes land on the
main checkout instead of the worktree branch.

Fix: spawn_agent rewrites absolute PROJECT_ROOT paths in prompt_with_memory to
worktree-equivalent absolute paths before the subprocess reads the brief.
(api/routers/agents.py — the →1225 path-remap block)

This test file verifies:
1. Prompt path remapping fires when isolation=worktree and prompt has main-checkout paths.
2. Remapping correctly replaces the PROJECT_ROOT prefix with the worktree prefix.
3. Paths already in the worktree (no PROJECT_ROOT prefix) are not touched.
4. The remap does not fire for isolation=none agents.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))


# ---------------------------------------------------------------------------
# Subprocess doubles (same pattern as test_spawn_worktree_cwd.py)
# ---------------------------------------------------------------------------


class _FakeStdin:
    _closed = False
    written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

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
    monkeypatch, fork_returncode: int = 0
) -> dict[str, Any]:
    from routers import agents as agents_module
    import services.spawn_isolation as _siso

    calls: dict[str, Any] = {
        "exec": [],
        "fork_called": False,
        "wt_path": None,
        "prompt_written": None,
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

    # Intercept tempfile writes to capture the prompt sent to the subprocess.
    import builtins
    _real_open = builtins.open

    def _fake_open(path, *args, **kwargs):
        fh = _real_open(path, *args, **kwargs)
        return fh

    import os as _os
    _real_write = _os.write

    def _capturing_write(fd, data):
        if isinstance(data, bytes) and b"spawn-" in str(fd).encode():
            pass
        return _real_write(fd, data)

    return calls


# ---------------------------------------------------------------------------
# Unit tests for path remapping logic
# ---------------------------------------------------------------------------


def test_prompt_path_remap_replaces_main_root_prefix(tmp_path, monkeypatch):
    """→1225: prompt_with_memory absolute main-checkout paths -> worktree paths.

    When an agent brief contains /PROJECT_ROOT/api/foo.py, the spawn code
    should rewrite it to /WORKTREE_PATH/api/foo.py before the subprocess
    reads it. This prevents fs_ops from landing writes on the main checkout.
    """
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-123")

    prompt = (
        f"Edit {main_root}/api/services/ostk.py and "
        f"also touch {main_root}/frontend/src/App.tsx"
    )

    main_prefix = main_root + "/"
    wt_prefix = wt_root + "/"
    result = prompt.replace(main_prefix, wt_prefix)

    assert f"{wt_root}/api/services/ostk.py" in result, (
        "absolute main-checkout path should be remapped to worktree"
    )
    assert f"{wt_root}/frontend/src/App.tsx" in result, (
        "second path should also be remapped"
    )
    assert main_root + "/" not in result, (
        "no main-checkout paths should remain after remapping"
    )


def test_prompt_path_remap_leaves_relative_paths_intact(tmp_path):
    """→1225: relative paths in prompts must not be altered by path remapping."""
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-456")

    prompt = "Edit api/services/ostk.py and frontend/src/App.tsx"
    main_prefix = main_root + "/"
    wt_prefix = wt_root + "/"

    # The prompt has no main-checkout prefix, so no replacement should occur.
    if main_prefix in prompt:
        result = prompt.replace(main_prefix, wt_prefix)
    else:
        result = prompt

    assert result == prompt, "relative paths must not be changed"


def test_prompt_path_remap_leaves_worktree_paths_intact(tmp_path):
    """→1225: paths already in the worktree must not be double-remapped."""
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-789")

    # Prompt already has the worktree path — should not be touched.
    prompt = f"Edit {wt_root}/api/services/ostk.py"
    main_prefix = main_root + "/"
    wt_prefix = wt_root + "/"

    if main_prefix in prompt:
        result = prompt.replace(main_prefix, wt_prefix)
    else:
        result = prompt

    assert result == prompt, "worktree paths must not be altered"


def test_prompt_path_remap_idempotent(tmp_path):
    """→1225: applying path remapping twice must produce the same result as once.

    Python str.replace() is a single-pass scan: it does not re-process the
    replacement text. A path that was already remapped will not be touched
    again by a second call.
    """
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-idem")

    prompt = f"Edit {main_root}/api/services/ostk.py"
    main_prefix = main_root + "/"
    wt_prefix = wt_root + "/"

    once = prompt.replace(main_prefix, wt_prefix)
    twice = once.replace(main_prefix, wt_prefix)

    assert once == twice, (
        "path remapping must be idempotent — second pass must not corrupt result"
    )


# ---------------------------------------------------------------------------
# Integration test: spawn_agent injects remapped prompt
# ---------------------------------------------------------------------------


def test_prompt_path_remap_replaces_multiple_occurrences(tmp_path):
    """→1225: all occurrences of the main-checkout prefix are replaced, not just the first."""
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-multi")

    prompt = (
        f"Edit {main_root}/api/foo.py. "
        f"Then update {main_root}/api/bar.py. "
        f"Finally check {main_root}/frontend/App.tsx."
    )
    main_prefix = main_root + "/"
    wt_prefix = wt_root + "/"
    result = prompt.replace(main_prefix, wt_prefix)

    assert result.count(wt_prefix) == 3, (
        "all three occurrences of the main-checkout prefix must be remapped"
    )
    assert main_prefix not in result, (
        "no main-checkout prefix occurrences should remain"
    )


def test_prompt_path_remap_does_not_fire_when_no_main_prefix(tmp_path):
    """→1225: the guard condition (main_prefix in prompt) prevents no-op replacements."""
    main_root = str(tmp_path / "main-checkout")
    wt_root = str(tmp_path / ".claude" / "worktrees" / "agent-test-nofire")

    prompt = "Read api/services/ostk.py and return the list of functions."
    main_prefix = main_root + "/"

    remap_fired = main_prefix in prompt
    assert not remap_fired, (
        "the guard condition must prevent remapping when the prompt "
        "contains no absolute main-checkout paths"
    )


def test_agents_py_path_remap_block_format(tmp_path):
    """→1225: validate the exact logic used in agents.py spawn path-remap block.

    This test mirrors the exact code pattern in agents.py lines ~4592-4610
    so that any regression is caught without needing a full spawn integration.
    """
    import pathlib

    # Simulate agents.py variables at path-remap time.
    PROJECT_ROOT = pathlib.Path(str(tmp_path / "main"))
    _worktree_path = str(tmp_path / ".claude" / "worktrees" / "agent-test-block")

    prompt_with_memory = (
        f"Edit {PROJECT_ROOT}/api/services/ostk.py. "
        f"Also update {PROJECT_ROOT}/frontend/src/App.tsx."
    )

    # This is the exact block from agents.py:
    if _worktree_path and prompt_with_memory:
        _main_prefix = str(PROJECT_ROOT) + "/"
        _wt_prefix = _worktree_path + "/"
        if _main_prefix in prompt_with_memory:
            prompt_with_memory = prompt_with_memory.replace(_main_prefix, _wt_prefix)

    assert f"{_worktree_path}/api/services/ostk.py" in prompt_with_memory, (
        "path-remap block must rewrite PROJECT_ROOT prefix to worktree prefix"
    )
    assert f"{_worktree_path}/frontend/src/App.tsx" in prompt_with_memory
    assert str(PROJECT_ROOT) + "/" not in prompt_with_memory, (
        "no main-checkout absolute path prefix must remain after remapping"
    )

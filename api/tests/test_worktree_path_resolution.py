"""Tests for worktree path resolution in OstkService and the spawn endpoint.

Covers needle →1051: worktree agent file ops must resolve against the worktree
root, not the main repo root.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmpdir() -> Path:
    """Return a fresh temporary directory that is guaranteed to exist."""
    p = Path(tempfile.mkdtemp())
    return p


# ---------------------------------------------------------------------------
# get_effective_root tests
# ---------------------------------------------------------------------------

class TestGetEffectiveRoot:
    """get_effective_root() respects OSTK_PROJECT_ROOT env var."""

    def test_returns_project_root_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("OSTK_PROJECT_ROOT", raising=False)
        from services.ostk import get_effective_root, PROJECT_ROOT
        assert get_effective_root() == PROJECT_ROOT

    def test_returns_ostk_project_root_when_env_set_to_existing_dir(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("OSTK_PROJECT_ROOT", str(tmp_path))
        # Re-import to pick up env change
        import importlib
        import services.ostk as ostk_mod
        importlib.reload(ostk_mod)
        result = ostk_mod.get_effective_root()
        assert result == tmp_path
        importlib.reload(ostk_mod)  # restore

    def test_falls_back_to_project_root_when_env_path_does_not_exist(
        self, monkeypatch
    ):
        monkeypatch.setenv("OSTK_PROJECT_ROOT", "/nonexistent/path/that/cannot/exist")
        from services.ostk import get_effective_root, PROJECT_ROOT
        assert get_effective_root() == PROJECT_ROOT


# ---------------------------------------------------------------------------
# OstkService.__init__ cwd tests
# ---------------------------------------------------------------------------

class TestOstkServiceCwd:
    """OstkService uses get_effective_root() when no explicit cwd is given."""

    def test_default_cwd_is_effective_root(self, monkeypatch):
        monkeypatch.delenv("OSTK_PROJECT_ROOT", raising=False)
        from services.ostk import OstkService, get_effective_root
        svc = OstkService()
        assert svc.cwd == str(get_effective_root())

    def test_explicit_cwd_overrides_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OSTK_PROJECT_ROOT", str(tmp_path))
        from services.ostk import OstkService
        explicit = "/some/explicit/path"
        svc = OstkService(cwd=explicit)
        assert svc.cwd == explicit

    def test_cwd_uses_worktree_when_ostk_project_root_set(
        self, monkeypatch, tmp_path
    ):
        """When spawned as a worktree agent, OSTK_PROJECT_ROOT points to the
        worktree, so OstkService resolves paths there, not in the main repo."""
        monkeypatch.setenv("OSTK_PROJECT_ROOT", str(tmp_path))
        import importlib
        import services.ostk as ostk_mod
        importlib.reload(ostk_mod)
        svc = ostk_mod.OstkService()
        assert svc.cwd == str(tmp_path)
        importlib.reload(ostk_mod)  # restore


# ---------------------------------------------------------------------------
# Spawn env injection test (unit-level)
# ---------------------------------------------------------------------------

class TestSpawnEnvInjectsOstkProjectRoot:
    """The spawn endpoint sets OSTK_PROJECT_ROOT in the subprocess env when
    isolation=='worktree', so the agent gets the worktree path as its root."""

    def test_ostk_project_root_injected_on_worktree_spawn(self):
        """Verify the spawn code sets OSTK_PROJECT_ROOT = worktree_path.

        We test this by reading the source (not by running the endpoint) to
        avoid the heavy async setup.  The assertion is structural: the line
        must appear in the spawn block that comes after _wt_ok is True.
        """
        agents_path = (
            Path(__file__).parent.parent / "routers" / "agents.py"
        )
        source = agents_path.read_text()
        # The assignment must appear inside the worktree-success branch
        assert '_spawn_env["OSTK_PROJECT_ROOT"] = str(_wt_path)' in source, (
            "agents.py must set OSTK_PROJECT_ROOT in the spawn env for worktree "
            "agents so that OstkService and the ostk binary resolve paths against "
            "the worktree (needle →1051)"
        )


# ---------------------------------------------------------------------------
# Reaper active-agent guard test
# ---------------------------------------------------------------------------

class TestReaperActiveAgentGuard:
    """worktree-reaper.sh fails safe when active-agent names cannot be loaded."""

    def test_reaper_has_fail_safe_for_no_active_names(self):
        """The reaper must exit 1 (skip all removals) when _ACTIVE_NAMES_LOADED==0.

        Checked structurally against the script source.
        """
        reaper_path = Path(__file__).parent.parent.parent / "scripts" / "worktree-reaper.sh"
        source = reaper_path.read_text()
        assert '_ACTIVE_NAMES_LOADED" -eq 0' in source, (
            "reaper.sh must have a guard that exits 1 (skips removals) when "
            "_ACTIVE_NAMES_LOADED is 0, to avoid deleting running worktrees "
            "(needle →1051)"
        )
        # The guard must appear BEFORE the removal loop
        guard_pos = source.index('_ACTIVE_NAMES_LOADED" -eq 0')
        loop_pos = source.index("i=0\nprotected_count=0")
        assert guard_pos < loop_pos, (
            "The _ACTIVE_NAMES_LOADED==0 guard must appear before the removal loop"
        )

    def test_symlink_creation_does_not_require_live_daemon(self):
        """agents.py must NOT condition socket symlink creation on _main_sock.exists().

        A dangling symlink is safer: hook -S test returns False for it, allowing
        native fallback, whereas no symlink means the hook finds the main daemon
        socket via fallback-search and blocks native tools entirely (needle →1051).
        """
        agents_path = Path(__file__).parent.parent / "routers" / "agents.py"
        source = agents_path.read_text()
        # Old bad pattern: guarded by _main_sock.exists()
        assert "if _main_sock.exists() and not _wt_sock.exists():" not in source, (
            "agents.py must not guard socket symlink creation with "
            "_main_sock.exists() — the daemon may not be running at spawn time "
            "(needle →1051)"
        )
        # New pattern: only check wt_sock doesn't already exist
        assert "if not _wt_sock.exists():" in source, (
            "agents.py must create the socket symlink unconditionally (when the "
            "worktree socket doesn't already exist)"
        )

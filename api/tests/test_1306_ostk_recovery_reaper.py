"""→1306: opt-in MYOS_REAPER_USE_OSTK_RECOVERY path in agent_state_prune.

Cases:
1. Flag off  — existing Python prune logic runs; subprocess.run not called
2. Flag on   — subprocess.run called with ["ostk", "recovery", "rescue"];
               Python prune logic skipped; returns (0, 0)
3. Flag on + subprocess non-zero exit — no exception raised, still (0, 0)
4. Flag on + subprocess raises OSError — no exception raised, still (0, 0)

Shell wrapper (reap-stale-fleet-ostk.sh):
5. Script exists and is executable
6. Script contains the expected ostk recovery subcommands
"""
import json
import os
import stat
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.agent_state_prune import run_startup_prune


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _old_terminal_meta() -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    return {"status": "completed", "spawned_at": ts, "completed_at": ts}


WRAPPER = Path(__file__).parents[2] / "scripts" / "reap-stale-fleet-ostk.sh"


# ---------------------------------------------------------------------------
# Flag OFF — Python prune path
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_python_prune_runs_and_removes_stale(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MYOS_REAPER_USE_OSTK_RECOVERY", raising=False)

        state_path = tmp_path / "agent_state.json"
        data = {
            "old-agent": _old_terminal_meta(),
            "live-agent": {"status": "running"},
        }
        state_path.write_text(json.dumps(data))

        with patch("subprocess.run") as mock_sub:
            kept, removed = run_startup_prune(state_path, data)

        mock_sub.assert_not_called()
        assert removed == 1
        assert kept == 1

    def test_python_prune_keeps_recent_terminal(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MYOS_REAPER_USE_OSTK_RECOVERY", raising=False)

        state_path = tmp_path / "agent_state.json"
        recent_ts = datetime.now(timezone.utc).isoformat()
        data = {"fresh": {"status": "completed", "completed_at": recent_ts}}

        with patch("subprocess.run") as mock_sub:
            kept, removed = run_startup_prune(state_path, data)

        mock_sub.assert_not_called()
        assert removed == 0
        assert kept == 1


# ---------------------------------------------------------------------------
# Flag ON — ostk recovery rescue path
# ---------------------------------------------------------------------------

class TestFlagOn:
    def test_subprocess_called_with_rescue(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MYOS_REAPER_USE_OSTK_RECOVERY", "1")

        state_path = tmp_path / "agent_state.json"
        mock_result = MagicMock(returncode=0, stdout="rescued 3 entries", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_sub:
            kept, removed = run_startup_prune(state_path, {})

        mock_sub.assert_called_once_with(
            ["ostk", "recovery", "rescue"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert (kept, removed) == (0, 0)

    def test_nonzero_exit_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MYOS_REAPER_USE_OSTK_RECOVERY", "1")

        state_path = tmp_path / "agent_state.json"
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")

        with patch("subprocess.run", return_value=mock_result):
            kept, removed = run_startup_prune(state_path, {})

        assert (kept, removed) == (0, 0)

    def test_subprocess_exception_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MYOS_REAPER_USE_OSTK_RECOVERY", "1")

        state_path = tmp_path / "agent_state.json"

        with patch("subprocess.run", side_effect=FileNotFoundError("ostk not found")):
            kept, removed = run_startup_prune(state_path, {})

        assert (kept, removed) == (0, 0)


# ---------------------------------------------------------------------------
# Shell wrapper
# ---------------------------------------------------------------------------

class TestShellWrapper:
    def test_wrapper_exists(self):
        assert WRAPPER.exists(), f"reap-stale-fleet-ostk.sh not found at {WRAPPER}"

    def test_wrapper_is_executable(self):
        mode = WRAPPER.stat().st_mode
        assert mode & stat.S_IXUSR, "reap-stale-fleet-ostk.sh is not user-executable"

    def test_wrapper_contains_assess_dry_run(self):
        text = WRAPPER.read_text()
        assert "ostk recovery assess --dry-run" in text

    def test_wrapper_contains_rescue(self):
        text = WRAPPER.read_text()
        assert "ostk recovery rescue" in text

    def test_wrapper_dry_run_is_default(self):
        text = WRAPPER.read_text()
        assert "DRY_RUN=true" in text

"""Tests for services.agent_state_prune."""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import pytest

# Ensure api/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_state_prune import prune_agent_state, run_startup_prune, _TERMINAL_STATUSES

# ── helpers ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=31)).isoformat()   # beyond 30-day retention
RECENT = (NOW - timedelta(days=5)).isoformat()  # within retention window


def _row(status: str, completed_at: Optional[str] = None, spawned_at: Optional[str] = None) -> dict:
    row: dict = {"status": status}
    if completed_at is not None:
        row["completed_at"] = completed_at
    if spawned_at is not None:
        row["spawned_at"] = spawned_at
    return row


# ── prune_agent_state unit tests ─────────────────────────────────────────────

class TestPruneAgentState:

    def test_drops_old_terminal_rows(self):
        meta = {
            "agent-old-completed": _row("completed", completed_at=OLD),
            "agent-old-failed": _row("failed", completed_at=OLD),
            "agent-old-cancelled": _row("cancelled", completed_at=OLD),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 3
        assert pruned == {}

    def test_keeps_recent_completed_rows(self):
        meta = {
            "agent-recent": _row("completed", completed_at=RECENT),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 0
        assert "agent-recent" in pruned

    def test_keeps_running_rows_regardless_of_age(self):
        meta = {
            "agent-long-running": _row("running", spawned_at=OLD),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 0
        assert "agent-long-running" in pruned

    def test_keeps_in_progress_rows(self):
        meta = {
            "agent-active": _row("in_progress", spawned_at=OLD),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 0
        assert "agent-active" in pruned

    def test_drops_row_with_no_completed_at_using_spawned_at(self):
        # Terminal + old spawned_at (no completed_at) should be dropped
        meta = {
            "agent-no-completed": _row("abandoned", spawned_at=OLD),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 1

    def test_keeps_row_with_no_timestamp(self):
        # Cannot infer age — keep it
        meta = {
            "agent-no-ts": _row("completed"),  # terminal but no timestamps
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 0
        assert "agent-no-ts" in pruned

    def test_preserves_dict_shape_for_kept_rows(self):
        extra = {"model": "sonnet", "budget": 2.0, "task": "do stuff"}
        meta = {
            "agent-keep": {**_row("completed", completed_at=RECENT), **extra},
        }
        pruned, _ = prune_agent_state(meta, now=NOW)
        assert pruned["agent-keep"]["model"] == "sonnet"
        assert pruned["agent-keep"]["budget"] == 2.0
        assert pruned["agent-keep"]["task"] == "do stuff"

    def test_noop_on_already_pruned_input(self):
        meta = {
            "agent-1": _row("running", spawned_at=RECENT),
            "agent-2": _row("completed", completed_at=RECENT),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 0
        assert len(pruned) == 2

    def test_custom_retention_days(self):
        # With retention_days=60, a 31-day-old row should be kept
        meta = {
            "agent-old": _row("completed", completed_at=OLD),  # 31 days old
        }
        pruned, removed = prune_agent_state(meta, retention_days=60, now=NOW)
        assert removed == 0
        assert "agent-old" in pruned

    def test_all_terminal_statuses_are_eligible(self):
        meta = {
            s: _row(s, completed_at=OLD)
            for s in _TERMINAL_STATUSES
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == len(_TERMINAL_STATUSES)
        assert pruned == {}

    def test_mixed_batch(self):
        meta = {
            "keep-running": _row("running", spawned_at=OLD),
            "keep-recent": _row("completed", completed_at=RECENT),
            "drop-old-1": _row("completed", completed_at=OLD),
            "drop-old-2": _row("failed", completed_at=OLD),
            "keep-no-ts": _row("failed"),
        }
        pruned, removed = prune_agent_state(meta, now=NOW)
        assert removed == 2
        assert set(pruned) == {"keep-running", "keep-recent", "keep-no-ts"}


# ── run_startup_prune integration test ───────────────────────────────────────

class TestRunStartupPrune:

    def test_backup_and_write(self, tmp_path):
        state_file = tmp_path / "agent_state.json"
        meta = {
            "old": _row("completed", completed_at=OLD),
            "live": _row("running", spawned_at=RECENT),
        }
        state_file.write_text(json.dumps(meta))

        kept, removed = run_startup_prune(state_file, meta)

        assert removed == 1
        assert kept == 1

        # Backup file created
        backups = list(tmp_path.glob("agent_state.bak-prune-*"))
        assert len(backups) == 1, "Expected exactly one backup file"

        # Written file contains only the kept row
        written = json.loads(state_file.read_text())
        assert "live" in written
        assert "old" not in written

    def test_noop_when_nothing_to_prune(self, tmp_path):
        state_file = tmp_path / "agent_state.json"
        meta = {
            "live": _row("running", spawned_at=RECENT),
        }
        state_file.write_text(json.dumps(meta))
        original_mtime = state_file.stat().st_mtime

        kept, removed = run_startup_prune(state_file, meta)

        assert removed == 0
        assert kept == 1
        # File should not have been rewritten (no backup either)
        assert not list(tmp_path.glob("agent_state.bak-prune-*"))


# ── smoke test against real agent_state.json ─────────────────────────────────

class TestSmokeRealData:

    def test_real_agent_state_if_available(self):
        """Load real agent_state.json and verify prune is safe on live data."""
        # Walk up from this file looking for a directory containing .ostk/
        # so the test works both in a plain checkout and inside a git worktree.
        search = Path(__file__).resolve().parent
        real_path = None
        for _ in range(8):
            candidate = search / ".ostk" / "agent_state.json"
            if candidate.exists():
                real_path = candidate
                break
            search = search.parent
        if real_path is None:
            pytest.skip("No real agent_state.json found")

        try:
            meta = json.loads(real_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            pytest.skip(f"Could not read real agent_state.json: {exc}")

        pruned, removed = prune_agent_state(meta)
        kept = len(pruned)

        assert kept + removed == len(meta), "Prune must be lossless: kept + removed == total"
        assert kept > 0, "Prune removed everything — retention logic may be wrong"
        # removed may be 0 if all rows are within the retention window

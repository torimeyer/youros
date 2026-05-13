"""→1308: /api/agents must drop zombie rows whose worktree dir is gone and PID is dead.

Rule from feedback_ghost_reaper_live_pid_check.md:
  "add os.kill(int(pid), 0) check before heartbeat staleness check. If
  ProcessLookupError → dead, fall through. If success or PermissionError/OSError
  → alive, skip."

Three regression tests:
1. Worktree dir gone + pid mocked dead  → row excluded from running list
2. Worktree dir exists                  → row included (still running)
3. Worktree dir gone but pid alive      → row included (process still up)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_running_meta(worktree_path: str, pid: int | None = None) -> dict:
    meta: dict = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _now_iso(),
        "last_heartbeat_at": _now_iso(),
        "worktree_path": worktree_path,
        "isolation": "worktree",
    }
    if pid is not None:
        meta["pid"] = str(pid)
    return meta


# ---------------------------------------------------------------------------
# Tests for _prune_reaped_worktree_agents
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_prune_timer():
    """Reset the rate-limit timer so prune always runs in tests."""
    import routers.agents as _mod
    orig = _mod._last_reaped_prune_time
    _mod._last_reaped_prune_time = -999999.0
    yield
    _mod._last_reaped_prune_time = orig


def test_reaped_worktree_no_pid_excluded(tmp_path):
    """Row with gone worktree and no PID is added to deleted_names."""
    import routers.agents as mod

    worktree = tmp_path / "agent-zombie-wt"
    # Don't create the dir — it's already reaped.

    name = "zombie-no-pid-1308"
    mod.agent_metadata[name] = _make_running_meta(str(worktree))

    deleted_path = tmp_path / "deleted_agents.json"

    try:
        with patch.object(mod, "DELETED_AGENTS_PATH", deleted_path):
            count = mod._prune_reaped_worktree_agents()

        assert count == 1, f"expected 1 pruned, got {count}"
        deleted = json.loads(deleted_path.read_text())
        assert name in deleted, f"{name} should be in deleted_agents.json"
        # Status must be terminal.
        assert mod.agent_metadata[name]["status"] in mod._TERMINAL_STATUSES
    finally:
        mod.agent_metadata.pop(name, None)


def test_existing_worktree_not_pruned(tmp_path):
    """Row whose worktree dir still exists must NOT be pruned."""
    import routers.agents as mod

    worktree = tmp_path / "agent-live-wt"
    worktree.mkdir()  # dir exists — agent may still be active

    name = "live-worktree-1308"
    mod.agent_metadata[name] = _make_running_meta(str(worktree))

    deleted_path = tmp_path / "deleted_agents.json"

    try:
        with patch.object(mod, "DELETED_AGENTS_PATH", deleted_path):
            count = mod._prune_reaped_worktree_agents()

        assert count == 0, f"expected 0 pruned, got {count}"
        assert not deleted_path.exists() or name not in json.loads(deleted_path.read_text())
    finally:
        mod.agent_metadata.pop(name, None)


def test_reaped_worktree_but_pid_alive_not_pruned(tmp_path):
    """Row with gone worktree but a live PID must NOT be pruned."""
    import routers.agents as mod

    worktree = tmp_path / "agent-pid-alive-wt"
    # Don't create dir.

    fake_pid = 99999
    name = "pid-alive-1308"
    mod.agent_metadata[name] = _make_running_meta(str(worktree), pid=fake_pid)

    deleted_path = tmp_path / "deleted_agents.json"

    def _mock_kill(pid, sig):
        if pid == fake_pid and sig == 0:
            return  # success → alive (raises nothing)
        raise ProcessLookupError(pid)

    try:
        with patch("os.kill", side_effect=_mock_kill):
            with patch.object(mod, "DELETED_AGENTS_PATH", deleted_path):
                count = mod._prune_reaped_worktree_agents()

        assert count == 0, f"expected 0 pruned (PID alive), got {count}"
        assert not deleted_path.exists() or name not in json.loads(deleted_path.read_text())
    finally:
        mod.agent_metadata.pop(name, None)

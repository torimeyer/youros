"""→1308: /api/agents must drop zombie rows whose worktree dir is gone and PID is dead.

Three cases:
1. Worktree gone + PID dead  → excluded from running list (terminated_stale)
2. Worktree exists           → included as running
3. Worktree gone + PID alive → included as running (live agent, worktree may not matter)
"""
import pytest
from unittest.mock import patch
from datetime import datetime, timezone


def _register(agents_mod, name, worktree_path=None, pid=None, status="running"):
    """Insert a minimal agent row into agent_metadata."""
    agents_mod.agent_metadata[name] = {
        "name": name,
        "status": status,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "worktree_path": worktree_path,
        "pid": pid,
        "source": "claude-code",
    }


@pytest.fixture(autouse=True)
def _reset_prune_timer(monkeypatch):
    """Reset the interval timer so _prune_reaped_worktree_agents always runs."""
    import routers.agents as agents_mod
    monkeypatch.setattr(agents_mod, "_last_reaped_prune_time", -999999.0)


@pytest.mark.asyncio
async def test_case1_worktree_gone_pid_dead_excluded(client, tmp_path, monkeypatch):
    """Case 1: worktree dir gone + PID dead → agent disappears from running list."""
    import routers.agents as agents_mod

    gone_path = str(tmp_path / "nonexistent-worktree")  # does not exist

    _register(agents_mod, "zombie-agent-1308", worktree_path=gone_path, pid=99999999)

    def _dead_pid(pid, sig):
        raise ProcessLookupError(f"no process {pid}")

    with patch("os.kill", side_effect=_dead_pid):
        resp = await client.get("/api/agents")

    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert "zombie-agent-1308" not in names

    # Status must have been demoted to terminated_stale.
    assert agents_mod.agent_metadata["zombie-agent-1308"]["status"] == "terminated_stale"


@pytest.mark.asyncio
async def test_case2_worktree_exists_included(client, tmp_path, monkeypatch):
    """Case 2: worktree dir still present → agent stays in running list."""
    import routers.agents as agents_mod

    live_path = str(tmp_path / "live-worktree")
    (tmp_path / "live-worktree").mkdir(exist_ok=True)  # exists

    _register(agents_mod, "live-worktree-agent-1308", worktree_path=live_path)

    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert "live-worktree-agent-1308" in names


@pytest.mark.asyncio
async def test_case3_worktree_gone_pid_alive_included(client, tmp_path, monkeypatch):
    """Case 3: worktree dir gone but PID alive → agent stays in running list."""
    import routers.agents as agents_mod

    gone_path = str(tmp_path / "reaped-worktree")  # does not exist

    _register(agents_mod, "live-pid-agent-1308", worktree_path=gone_path, pid=1)

    def _alive_pid(pid, sig):
        pass  # success → PID is alive

    with patch("os.kill", side_effect=_alive_pid):
        resp = await client.get("/api/agents")

    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert "live-pid-agent-1308" in names
    assert agents_mod.agent_metadata["live-pid-agent-1308"]["status"] == "running"

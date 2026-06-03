"""Tests for api/services/teams.py (→2147)."""

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import teams as teams_svc


@pytest.fixture(autouse=True)
def reset_teams():
    teams_svc.clear_all()
    yield
    teams_svc.clear_all()


# --- create_team ---

def test_create_team_basic():
    team = teams_svc.create_team("task-123", "build auth module")
    assert team["parent_task_id"] == "task-123"
    assert team["description"] == "build auth module"
    assert team["members"] == []
    assert team["task_ids"] == []
    assert "id" in team
    assert "created_at" in team


def test_create_team_no_description():
    team = teams_svc.create_team("task-000")
    assert team["description"] == ""


def test_create_multiple_teams_get_unique_ids():
    t1 = teams_svc.create_team("t1")
    t2 = teams_svc.create_team("t2")
    assert t1["id"] != t2["id"]


# --- add_teammate + roles ---

def test_add_teammate_with_role():
    team = teams_svc.create_team("task-456")
    tid = team["id"]
    updated = teams_svc.add_teammate(tid, "agent-alpha", "security_reviewer")
    assert len(updated["members"]) == 1
    m = updated["members"][0]
    assert m["agent_name"] == "agent-alpha"
    assert m["role"] == "security_reviewer"
    assert "joined_at" in m


def test_add_multiple_teammates_different_roles():
    team = teams_svc.create_team("task-789")
    tid = team["id"]
    teams_svc.add_teammate(tid, "agent-a", "frontend_lead")
    teams_svc.add_teammate(tid, "agent-b", "backend_lead")
    t = teams_svc.get_team(tid)
    assert len(t["members"]) == 2
    roles = {m["role"] for m in t["members"]}
    assert roles == {"frontend_lead", "backend_lead"}


def test_add_teammate_updates_role_not_duplicates():
    team = teams_svc.create_team("task-upd")
    tid = team["id"]
    teams_svc.add_teammate(tid, "agent-x", "member")
    teams_svc.add_teammate(tid, "agent-x", "lead")
    t = teams_svc.get_team(tid)
    assert len(t["members"]) == 1
    assert t["members"][0]["role"] == "lead"


def test_add_teammate_to_missing_team_raises():
    with pytest.raises(KeyError):
        teams_svc.add_teammate("nonexistent", "agent", "member")


# --- shared task list ---

def test_add_task_to_team():
    team = teams_svc.create_team("task-parent")
    tid = team["id"]
    teams_svc.add_task_to_team(tid, "subtask-1")
    teams_svc.add_task_to_team(tid, "subtask-2")
    t = teams_svc.get_team(tid)
    assert "subtask-1" in t["task_ids"]
    assert "subtask-2" in t["task_ids"]


def test_add_duplicate_task_does_not_grow_list():
    team = teams_svc.create_team("task-dedup")
    tid = team["id"]
    teams_svc.add_task_to_team(tid, "subtask-1")
    teams_svc.add_task_to_team(tid, "subtask-1")
    t = teams_svc.get_team(tid)
    assert t["task_ids"] == ["subtask-1"]


def test_add_task_to_missing_team_raises():
    with pytest.raises(KeyError):
        teams_svc.add_task_to_team("missing", "subtask-x")


# --- TeammateIdle quality gate ---

def test_teammate_idle_gate_parent_open():
    team = teams_svc.create_team("task-parent-open")
    tid = team["id"]
    teams_svc.add_teammate(tid, "worker-agent", "member")
    result = teams_svc.teammate_idle_check(
        tid, "worker-agent", open_task_ids={"task-parent-open"}
    )
    assert result["can_exit"] is False
    assert "still open" in result["reason"]
    assert result["parent_task_id"] == "task-parent-open"


def test_teammate_idle_gate_parent_closed():
    team = teams_svc.create_team("task-parent-done")
    tid = team["id"]
    teams_svc.add_teammate(tid, "worker-agent", "member")
    result = teams_svc.teammate_idle_check(
        tid, "worker-agent", open_task_ids=set()
    )
    assert result["can_exit"] is True
    assert result["parent_task_id"] == "task-parent-done"


def test_teammate_idle_gate_team_not_found():
    result = teams_svc.teammate_idle_check(
        "nonexistent-team", "any-agent", open_task_ids={"task-x"}
    )
    assert result["can_exit"] is True


def test_teammate_idle_gate_agent_not_in_team():
    team = teams_svc.create_team("task-check")
    tid = team["id"]
    result = teams_svc.teammate_idle_check(
        tid, "outsider-agent", open_task_ids={"task-check"}
    )
    assert result["can_exit"] is True
    assert "not in team" in result["reason"]


# --- get_team_for_agent ---

def test_get_team_for_agent_found():
    team = teams_svc.create_team("task-001")
    tid = team["id"]
    teams_svc.add_teammate(tid, "my-agent", "lead")
    found = teams_svc.get_team_for_agent("my-agent")
    assert found is not None
    assert found["id"] == tid


def test_get_team_for_agent_not_found():
    result = teams_svc.get_team_for_agent("ghost-agent")
    assert result is None


# --- list_teams ---

def test_list_teams_empty():
    assert teams_svc.list_teams() == []


def test_list_teams_multiple():
    teams_svc.create_team("t1")
    teams_svc.create_team("t2")
    all_teams = teams_svc.list_teams()
    assert len(all_teams) == 2


# --- remove_teammate ---

def test_remove_teammate():
    team = teams_svc.create_team("task-rm")
    tid = team["id"]
    teams_svc.add_teammate(tid, "agent-one", "lead")
    teams_svc.add_teammate(tid, "agent-two", "member")
    updated = teams_svc.remove_teammate(tid, "agent-one")
    assert len(updated["members"]) == 1
    assert updated["members"][0]["agent_name"] == "agent-two"


def test_remove_nonexistent_teammate_is_noop():
    team = teams_svc.create_team("task-noop")
    tid = team["id"]
    teams_svc.add_teammate(tid, "only-agent", "member")
    updated = teams_svc.remove_teammate(tid, "ghost")
    assert len(updated["members"]) == 1


def test_remove_teammate_missing_team_raises():
    with pytest.raises(KeyError):
        teams_svc.remove_teammate("no-such-team", "agent")


# --- delete_team ---

def test_delete_team():
    team = teams_svc.create_team("task-del")
    tid = team["id"]
    assert teams_svc.delete_team(tid) is True
    assert teams_svc.get_team(tid) is None


def test_delete_nonexistent_team_returns_false():
    assert teams_svc.delete_team("no-such") is False

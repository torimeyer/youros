"""
Tests for api/services/agent_watchdog.py and the find_transcript worktree fix.

Root cause being tested: port-release-notes-ux-bundle-to-9962ff was cancelled
while actively working because find_transcript could not find its transcript
(worktree agents have a top-level project dir, not a subagents/ subdir), and
the parent watchdog only checked progress-log mtime, not transcript bytes.

Regression tests verify:
  1. AgentWatchdog: transcript advancing suppresses stall even when log is quiet
  2. AgentWatchdog: both flatlined + pid alive + threshold exceeded = stall
  3. AgentWatchdog: pid gone = not stalled (agent exited normally)
  4. find_transcript: worktree-project top-level JSONL is found via dir name match
  5. AgentWatchdog + find_transcript integration: worktree transcript advancing
     means is_stalled() returns False even without a progress log update
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_watchdog import AgentWatchdog, WatchdogState, STALL_THRESHOLD_SECONDS
from services.heartbeat_idle import find_transcript


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worktree_transcript(fake_home: Path, agent_name: str, content: str) -> Path:
    """Mirror the path layout for a bridge-spawned worktree agent.

    ~/.claude/projects/<worktree-label>/<session>.jsonl
    where <worktree-label> contains the agent name.
    """
    project_dir = (
        fake_home
        / ".claude"
        / "projects"
        / f"-Users-torimeyer-claude-torios--claude-worktrees-agent-{agent_name}"
    )
    project_dir.mkdir(parents=True, exist_ok=True)
    transcript = project_dir / "sess-abc123.jsonl"
    transcript.write_text(content)
    return transcript


# ---------------------------------------------------------------------------
# find_transcript: worktree project dir search (regression for 2026-05-04)
# ---------------------------------------------------------------------------


def test_find_transcript_finds_worktree_project_jsonl(tmp_path):
    """find_transcript must locate JSONL in a project dir named after the worktree.

    Before the fix, bridge-spawned agents were invisible to find_transcript
    because their transcript is at ~/.claude/projects/<worktree-label>/<sess>.jsonl
    (top-level, not in subagents/). The spawn-age ceiling was the only signal,
    causing premature cancellation.
    """
    agent_name = "port-release-notes-ux-bundle-to-9962ff"
    fake_home = tmp_path / "home"
    transcript = _make_worktree_transcript(
        fake_home,
        agent_name,
        json.dumps({"type": "assistant", "content": "resolving conflicts"}) + "\n",
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        result = find_transcript(agent_name, repo_root=repo_root)

    assert result == transcript


def test_find_transcript_worktree_ignores_unrelated_dirs(tmp_path):
    """Only dirs whose name contains the agent name are searched."""
    agent_name = "my-specific-agent-abc123"
    fake_home = tmp_path / "home"

    # Create a project dir that does NOT match
    unrelated_dir = (
        fake_home / ".claude" / "projects" / "-some-other-project-dir"
    )
    unrelated_dir.mkdir(parents=True, exist_ok=True)
    unrelated_jsonl = unrelated_dir / "sess.jsonl"
    unrelated_jsonl.write_text(json.dumps({"content": "unrelated"}) + "\n")

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        result = find_transcript(agent_name, repo_root=repo_root)

    assert result is None


def test_find_transcript_worktree_prefers_freshest(tmp_path):
    """When two worktree project dirs match, return the file with the newest mtime."""
    agent_name = "fresh-agent-xyz"
    fake_home = tmp_path / "home"
    projects_root = fake_home / ".claude" / "projects"

    old_dir = projects_root / f"-foo--worktrees-agent-{agent_name}"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / "sess-old.jsonl"
    old_file.write_text("{}\n")
    os.utime(old_file, (time.time() - 300, time.time() - 300))

    new_dir = projects_root / f"-bar--worktrees-agent-{agent_name}"
    new_dir.mkdir(parents=True, exist_ok=True)
    new_file = new_dir / "sess-new.jsonl"
    new_file.write_text("{}\n")
    os.utime(new_file, (time.time() - 10, time.time() - 10))

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        result = find_transcript(agent_name, repo_root=repo_root)

    assert result == new_file


# ---------------------------------------------------------------------------
# AgentWatchdog unit tests
# ---------------------------------------------------------------------------


def _make_watchdog(
    *,
    agent_name: str = "test-agent",
    pid: int = None,
    log_path: Path = None,
    threshold: int = 90,
    repo_root: Path = None,
) -> AgentWatchdog:
    return AgentWatchdog(
        agent_name=agent_name,
        progress_log_path=log_path,
        pid=pid,
        stall_threshold_seconds=threshold,
        repo_root=repo_root,
    )


def test_watchdog_needs_two_snapshots(tmp_path):
    """is_stalled() must return False with fewer than 2 snapshots."""
    wd = _make_watchdog()
    wd.snapshot()
    assert wd.is_stalled() is False


def test_watchdog_not_stalled_below_threshold(tmp_path):
    """is_stalled() returns False when elapsed < threshold even with flat signals."""
    wd = AgentWatchdog(
        agent_name="some-agent",
        stall_threshold_seconds=3600,  # very long threshold
    )
    now = time.time()
    # Inject two states 5 seconds apart — well under threshold
    wd._states.append(WatchdogState(taken_at=now - 5, transcript_bytes=None, log_mtime=None, pid_alive=True))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=None, log_mtime=None, pid_alive=True))
    assert wd.is_stalled() is False


def test_watchdog_stalled_all_flat_pid_alive(tmp_path):
    """All signals flat + pid alive + threshold exceeded = stalled."""
    wd = AgentWatchdog(
        agent_name="zombie-agent",
        stall_threshold_seconds=90,
    )
    now = time.time()
    wd._states.append(WatchdogState(taken_at=now - 120, transcript_bytes=1000, log_mtime=now - 120, pid_alive=True))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=1000, log_mtime=now - 120, pid_alive=True))
    assert wd.is_stalled() is True


def test_watchdog_not_stalled_transcript_growing():
    """Transcript bytes growing → agent alive, even if log is quiet.

    This is the exact scenario from port-release-notes-ux-bundle-to-9962ff:
    the agent was actively resolving conflicts (transcript growing), but never
    wrote to its progress log for 9 minutes.
    """
    wd = AgentWatchdog(
        agent_name="busy-agent",
        stall_threshold_seconds=90,
    )
    now = time.time()
    log_mtime = now - 600  # log last updated 10 min ago
    wd._states.append(WatchdogState(taken_at=now - 120, transcript_bytes=10000, log_mtime=log_mtime, pid_alive=True))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=15000, log_mtime=log_mtime, pid_alive=True))  # transcript grew
    assert wd.is_stalled() is False


def test_watchdog_not_stalled_log_growing():
    """Progress log mtime advancing → agent alive (secondary signal)."""
    wd = AgentWatchdog(
        agent_name="log-writer-agent",
        progress_log_path=Path("/tmp/fake.log"),
        stall_threshold_seconds=90,
    )
    now = time.time()
    wd._states.append(WatchdogState(taken_at=now - 120, transcript_bytes=5000, log_mtime=now - 120, pid_alive=True))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=5000, log_mtime=now - 10, pid_alive=True))  # log updated
    assert wd.is_stalled() is False


def test_watchdog_not_stalled_pid_gone():
    """pid gone → agent exited, not a stall (completed or crashed)."""
    wd = AgentWatchdog(
        agent_name="exited-agent",
        stall_threshold_seconds=90,
    )
    now = time.time()
    wd._states.append(WatchdogState(taken_at=now - 120, transcript_bytes=1000, log_mtime=None, pid_alive=True))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=1000, log_mtime=None, pid_alive=False))
    assert wd.is_stalled() is False


def test_watchdog_no_pid_no_transcript_no_log_over_threshold():
    """No signals available + threshold exceeded: returns True (assume stall)."""
    wd = AgentWatchdog(
        agent_name="ghost-agent",
        stall_threshold_seconds=90,
    )
    now = time.time()
    wd._states.append(WatchdogState(taken_at=now - 120, transcript_bytes=None, log_mtime=None, pid_alive=None))
    wd._states.append(WatchdogState(taken_at=now, transcript_bytes=None, log_mtime=None, pid_alive=None))
    assert wd.is_stalled() is True


def test_watchdog_snapshot_reads_real_transcript(tmp_path):
    """snapshot() reads transcript bytes via find_transcript worktree search."""
    agent_name = "snapshot-agent-xyz"
    fake_home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    transcript = _make_worktree_transcript(
        fake_home,
        agent_name,
        "x" * 5000,
    )

    wd = AgentWatchdog(
        agent_name=agent_name,
        stall_threshold_seconds=90,
        repo_root=repo_root,
    )

    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        state = wd.snapshot()

    assert state.transcript_bytes == 5000


def test_watchdog_integration_transcript_growing_not_stalled(tmp_path):
    """Integration: two snapshots where transcript grows → not stalled.

    Simulates the port-release-notes scenario: an agent actively resolving
    conflicts, transcript growing but no log writes.
    """
    agent_name = "conflict-resolver-xyz"
    fake_home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)

    transcript = _make_worktree_transcript(
        fake_home,
        agent_name,
        "initial content\n",
    )

    wd = AgentWatchdog(
        agent_name=agent_name,
        stall_threshold_seconds=90,
        repo_root=repo_root,
    )

    # First snapshot
    t_start = time.time() - 120
    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        with patch("time.time", return_value=t_start):
            wd.snapshot()

    # Simulate transcript growth (agent is working)
    transcript.write_text("initial content\n" + "more tool calls\n" * 100)

    # Second snapshot (120s later)
    with patch("services.heartbeat_idle.Path.home", return_value=fake_home):
        with patch("time.time", return_value=time.time()):
            wd.snapshot()

    assert wd.is_stalled() is False


# ---------------------------------------------------------------------------
# Regression: the fake stall that killed port-release-notes
# ---------------------------------------------------------------------------


def test_regression_progress_log_quiet_but_transcript_growing_not_stall():
    """Regression: the exact pattern that killed port-release-notes-ux-bundle.

    Log quiet for 9+ minutes, transcript growing, pid alive, threshold=90s.
    Must return False (NOT stalled).
    """
    wd = AgentWatchdog(
        agent_name="port-release-notes-ux-bundle-to-9962ff",
        progress_log_path=Path("/tmp/release-notes-port.log"),
        stall_threshold_seconds=90,
    )
    now = time.time()
    # Log last written at 00:21:32 (9 minutes ago)
    log_stale = now - 540
    wd._states.append(
        WatchdogState(taken_at=now - 540, transcript_bytes=500_000, log_mtime=log_stale, pid_alive=True)
    )
    # 9 min later: log still at same mtime, but transcript has grown by ~500KB
    wd._states.append(
        WatchdogState(taken_at=now, transcript_bytes=1_036_453, log_mtime=log_stale, pid_alive=True)
    )
    assert wd.is_stalled() is False, (
        "Agent was actively resolving conflicts — transcript grew 500KB. "
        "Log silence alone must not trigger stall detection."
    )

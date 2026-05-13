"""Tests for the background-Bash-task completion snapshot in standing-rules.sh.

The hook scans /private/tmp/claude-<uid>/<proj-key>/<sid>/tasks/*.output
for files newer than last-user-turn.stamp and emits a
"BACKGROUND BASH TASKS COMPLETED SINCE YOUR LAST TURN" block so
pclaude proactively surfaces background task completions instead of
waiting for the user to ask.

Root path is overridable via MYOS_TASK_BASE_DIR so tests never touch
the real system temp tree.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "hooks"
    / "standing-rules.sh"
)

SESSION_ID = "test-session-abc123"
PROJ_DIR = "/fake/testproj"
PROJ_KEY = "-fake-testproj"


def _run_hook(env: dict) -> str:
    """Run standing-rules.sh and return combined stdout."""
    base_env = {
        "HOME": env["HOME"],
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLAUDE_SESSION_ID": SESSION_ID,
        "CLAUDE_PROJECT_DIR": PROJ_DIR,
        # Point backend at nothing so the /api/agents calls time-out fast
        "MYOS_BACKEND_URL": "https://127.0.0.1:1",
        # Disable tori-specific HUMANFILE rules (not needed here)
        # by not setting MYOS_CONFIG_PATH (default path won't exist in temp home)
    }
    base_env.update(env)
    result = subprocess.run(
        ["bash", str(HOOK)],
        capture_output=True,
        text=True,
        env=base_env,
        timeout=20,
    )
    return result.stdout


def _make_home(tmp: Path) -> Path:
    """Create a minimal fake HOME with the stamp file pre-populated.

    The hook reads last-user-turn.stamp BEFORE overwriting it, so the stamp
    must exist and contain a past epoch.  .output files created after this
    call will have a newer mtime than the epoch stored in the stamp.
    """
    home = tmp / "home"
    hooks_dir = home / ".myos" / "hooks"
    hooks_dir.mkdir(parents=True)
    stamp = hooks_dir / "last-user-turn.stamp"
    # Write a past epoch so any .output file created afterwards is "newer"
    past_epoch = int(time.time()) - 10
    stamp.write_text(str(past_epoch))
    return home


def _make_task_output(base: Path, content: str = "task output here\n") -> Path:
    """Create a fake task .output file in the expected directory tree."""
    task_dir = base / PROJ_KEY / SESSION_ID / "tasks"
    task_dir.mkdir(parents=True)
    output_file = task_dir / "btask001.output"
    # Ensure mtime is strictly after the stamp epoch written in _make_home.
    # The hook uses -newermt with the epoch value, so a 1s gap is enough.
    time.sleep(1.1)
    output_file.write_text(content)
    return output_file


def test_snapshot_emitted_when_task_completed():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = _make_home(tmp_p)
        base = tmp_p / "taskbase"
        _make_task_output(base, content="build succeeded\n4183 passed\n")

        out = _run_hook({
            "HOME": str(home),
            "MYOS_TASK_BASE_DIR": str(base),
        })

    assert "BACKGROUND BASH TASKS COMPLETED SINCE YOUR LAST TURN" in out, (
        f"Expected snapshot block in hook output, got:\n{out[:2000]}"
    )
    assert "btask001" in out


def test_snapshot_shows_task_count():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = _make_home(tmp_p)
        base = tmp_p / "taskbase"
        task_dir = base / PROJ_KEY / SESSION_ID / "tasks"
        task_dir.mkdir(parents=True)
        time.sleep(1.1)  # ensure mtime > stored epoch in stamp
        (task_dir / "btask_a.output").write_text("done a\n")
        (task_dir / "btask_b.output").write_text("done b\n")

        out = _run_hook({
            "HOME": str(home),
            "MYOS_TASK_BASE_DIR": str(base),
        })

    assert "2 task(s)" in out, f"Expected '2 task(s)' in output, got:\n{out[:2000]}"


def test_snapshot_shows_tail_output():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = _make_home(tmp_p)
        base = tmp_p / "taskbase"
        _make_task_output(base, content="line1\nline2\nthe final line\n")

        out = _run_hook({
            "HOME": str(home),
            "MYOS_TASK_BASE_DIR": str(base),
        })

    assert "the final line" in out, f"Expected tail content in output, got:\n{out[:2000]}"


def test_no_snapshot_when_no_completed_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = _make_home(tmp_p)
        base = tmp_p / "taskbase"
        # Create the tasks dir but no .output files
        (base / PROJ_KEY / SESSION_ID / "tasks").mkdir(parents=True)

        out = _run_hook({
            "HOME": str(home),
            "MYOS_TASK_BASE_DIR": str(base),
        })

    assert "BACKGROUND BASH TASKS COMPLETED" not in out


def test_no_snapshot_when_task_predates_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = _make_home(tmp_p)
        base = tmp_p / "taskbase"
        task_dir = base / PROJ_KEY / SESSION_ID / "tasks"
        task_dir.mkdir(parents=True)
        # Write the task file BEFORE updating the stamp so it predates the epoch
        (task_dir / "old_task.output").write_text("old output\n")
        time.sleep(1.1)  # ensure stamp epoch is strictly after the file mtime
        stamp = home / ".myos" / "hooks" / "last-user-turn.stamp"
        # +2 ensures the epoch is strictly after the file's subsecond mtime
        # even if int() truncates to the same second as the file creation.
        stamp.write_text(str(int(time.time()) + 2))

        out = _run_hook({
            "HOME": str(home),
            "MYOS_TASK_BASE_DIR": str(base),
        })

    assert "BACKGROUND BASH TASKS COMPLETED" not in out, (
        f"Old task should not appear in snapshot, got:\n{out[:2000]}"
    )

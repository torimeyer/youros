"""
Regression test for →2039: needle stuck at in_progress after owning agent dies.

Root cause: when an agent registers, ostk.set_task_in_progress() writes
in_progress to issues.jsonl. If the agent goes terminal without a release
path running, the needle stays in_progress on disk and shows as phantom
in-progress work in the UI.

The fix is in _set_agent_status (agents.py:78-87): every terminal transition
calls _fire_release_needle_if_orphaned, which resets the JSONL status back
to open if no other live agent holds the needle.

These tests verify the fix and also confirm the bug would reappear if the
release call were removed.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


NEEDLE_ID = "2039"


def _read_needle_status(issues_path: Path, needle_id: str) -> str:
    """Read the stored status of a needle from issues.jsonl."""
    bare = needle_id.lstrip("→").strip()
    for line in issues_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = str(entry.get("id", "")).lstrip("→").strip()
        if raw == bare:
            return entry.get("status", "")
    return ""


def _make_issues_jsonl(tmp_path: Path, needle_id: str, status: str) -> Path:
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True)
    issues_path = needles_dir / "issues.jsonl"
    issues_path.write_text(
        json.dumps({
            "id": f"→{needle_id}",
            "title": "sticky in_progress test task",
            "status": status,
        }) + "\n"
    )
    return issues_path


@pytest.mark.parametrize("terminal_status", [
    "completed",
    "failed",
    "cancelled",
    "terminated_stale",
    "killed",
    "stopped",
    "abandoned",
])
def test_needle_released_when_agent_goes_terminal(tmp_path, terminal_status):
    """→2039: needle must flip from in_progress to open when its agent goes terminal.

    Fails without the _fire_release_needle_if_orphaned call in _set_agent_status.
    Passes with the fix in place (agents.py:78-87).
    """
    issues_path = _make_issues_jsonl(tmp_path, NEEDLE_ID, "in_progress")
    assert _read_needle_status(issues_path, NEEDLE_ID) == "in_progress"

    import routers.agents as agents_module
    from routers.agents import _set_agent_status
    from services.ostk import ostk

    agent_name = f"test-2039-terminal-{terminal_status}"
    saved = dict(agents_module.agent_metadata)
    agents_module.agent_metadata.clear()
    agents_module.agent_metadata[agent_name] = {
        "status": "running",
        "needle_id": NEEDLE_ID,
        "source": "claude-code",
    }

    try:
        with patch.object(ostk, "cwd", str(tmp_path)):
            _set_agent_status(agent_name, terminal_status)
    finally:
        agents_module.agent_metadata.clear()
        agents_module.agent_metadata.update(saved)

    status_after = _read_needle_status(issues_path, NEEDLE_ID)
    assert status_after == "open", (
        f"→2039 regression: needle stayed '{status_after}' after agent "
        f"transitioned to '{terminal_status}'. Expected 'open'."
    )


def test_needle_not_released_if_another_live_agent_holds_it(tmp_path):
    """A needle held by a second live agent must NOT be released when one agent goes terminal."""
    issues_path = _make_issues_jsonl(tmp_path, NEEDLE_ID, "in_progress")

    import routers.agents as agents_module
    from routers.agents import _set_agent_status
    from services.ostk import ostk

    saved = dict(agents_module.agent_metadata)
    agents_module.agent_metadata.clear()
    agents_module.agent_metadata["dying-agent"] = {
        "status": "running",
        "needle_id": NEEDLE_ID,
        "source": "claude-code",
    }
    agents_module.agent_metadata["still-live-agent"] = {
        "status": "running",
        "needle_id": NEEDLE_ID,
        "source": "claude-code",
    }

    try:
        with patch.object(ostk, "cwd", str(tmp_path)):
            _set_agent_status("dying-agent", "completed")
    finally:
        agents_module.agent_metadata.clear()
        agents_module.agent_metadata.update(saved)

    status_after = _read_needle_status(issues_path, NEEDLE_ID)
    assert status_after == "in_progress", (
        f"Needle should remain in_progress when another live agent still holds it, "
        f"got '{status_after}'."
    )


def test_needle_not_released_if_already_open(tmp_path):
    """release_needle_sync is idempotent: open needle stays open."""
    issues_path = _make_issues_jsonl(tmp_path, NEEDLE_ID, "open")

    import routers.agents as agents_module
    from routers.agents import _set_agent_status
    from services.ostk import ostk

    saved = dict(agents_module.agent_metadata)
    agents_module.agent_metadata.clear()
    agents_module.agent_metadata["test-agent-open"] = {
        "status": "running",
        "needle_id": NEEDLE_ID,
        "source": "claude-code",
    }

    try:
        with patch.object(ostk, "cwd", str(tmp_path)):
            _set_agent_status("test-agent-open", "completed")
    finally:
        agents_module.agent_metadata.clear()
        agents_module.agent_metadata.update(saved)

    status_after = _read_needle_status(issues_path, NEEDLE_ID)
    assert status_after == "open"


def test_bug_reproduced_when_release_call_is_skipped(tmp_path):
    """Confirms the →2039 bug surfaces when _fire_release_needle_if_orphaned is patched out.

    This is the RED test: proves the assertion fails without the fix.
    The fix in _set_agent_status is what makes the parametrized test above GREEN.
    """
    issues_path = _make_issues_jsonl(tmp_path, NEEDLE_ID, "in_progress")

    import routers.agents as agents_module
    from routers.agents import _set_agent_status
    from services.ostk import ostk

    saved = dict(agents_module.agent_metadata)
    agents_module.agent_metadata.clear()
    agents_module.agent_metadata["bug-agent"] = {
        "status": "running",
        "needle_id": NEEDLE_ID,
        "source": "claude-code",
    }

    try:
        with patch.object(ostk, "cwd", str(tmp_path)):
            with patch("routers.agents._fire_release_needle_if_orphaned"):
                _set_agent_status("bug-agent", "completed")
    finally:
        agents_module.agent_metadata.clear()
        agents_module.agent_metadata.update(saved)

    status_after = _read_needle_status(issues_path, NEEDLE_ID)
    assert status_after == "in_progress", (
        "Expected the bug to reproduce when release is skipped, "
        f"but needle status was '{status_after}'."
    )

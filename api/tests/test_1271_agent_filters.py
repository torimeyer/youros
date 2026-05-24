"""Tests for is_user_spawned_agent — daemon source exclusion (→1271)."""
import pytest
from services.agent_filters import is_user_spawned_agent


def _agent(**kwargs):
    base = {"name": "test-agent", "source": "claude-code", "model": "sonnet", "status": "running"}
    base.update(kwargs)
    return base


def test_daemon_source_excluded():
    assert is_user_spawned_agent(_agent(source="daemon")) is False


def test_hook_source_excluded():
    assert is_user_spawned_agent(_agent(source="hook")) is False


def test_claude_code_source_included():
    assert is_user_spawned_agent(_agent(source="claude-code")) is True


def test_chat_source_excluded():
    assert is_user_spawned_agent(_agent(source="chat")) is False


def test_audit_source_excluded():
    assert is_user_spawned_agent(_agent(source="audit")) is False


# →1674/→1675: inferred-name fallback must match frontend isMainSession
def test_inferred_name_no_task_no_description_excluded():
    """Inferred claude-code-XXXX with no task/description = main session or pre-registration row.
    Backend excludes from running_count; frontend must match to keep badge in sync with Active Sessions."""
    assert is_user_spawned_agent({
        "name": "claude-code-abcdef12",
        "source": "claude-code",
        "model": "sonnet",
    }) is False


def test_inferred_name_with_task_included():
    """Once an inferred-name agent registers a task it is a real subagent and must appear."""
    assert is_user_spawned_agent({
        "name": "claude-code-abcdef12",
        "source": "claude-code",
        "model": "sonnet",
        "task": "Fix delete-all 403",
    }) is True


def test_inferred_name_with_description_included():
    """A non-session description makes the agent visible (e.g. spawned with custom desc)."""
    assert is_user_spawned_agent({
        "name": "claude-code-abcdef12",
        "source": "claude-code",
        "model": "sonnet",
        "description": "Some custom description",
    }) is True


def test_inferred_name_main_session_description_excluded():
    """Audit-stamped main session description always excluded."""
    assert is_user_spawned_agent({
        "name": "claude-code-abcdef12",
        "source": "claude-code",
        "model": "sonnet",
        "description": "Claude Code session (cwd: /Users/tori)",
    }) is False

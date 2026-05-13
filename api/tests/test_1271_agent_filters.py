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

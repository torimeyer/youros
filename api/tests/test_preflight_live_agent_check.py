"""Unit tests for the pre-flight live-agent guard in conftest.

TDD: these tests are written before the implementation so the first run
is RED. The implementation lives in api/tests/conftest.py as:
  - _check_no_live_agents(curl_stdout: str) -> None
  - _preflight_no_live_agents autouse fixture

The tests mock the curl response rather than hitting the real backend.
"""

import json

import pytest


def _load_checker():
    """Import _check_no_live_agents from conftest or raise ImportError."""
    import importlib
    import sys
    from pathlib import Path

    conftest_path = Path(__file__).parent / "conftest.py"
    spec = importlib.util.spec_from_file_location("conftest_module", conftest_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._check_no_live_agents


# ---------------------------------------------------------------------------
# Tests for _check_no_live_agents helper
# ---------------------------------------------------------------------------


def test_raises_usage_error_when_live_claude_code_agent():
    """Guard must raise UsageError when a running claude-code agent is present."""
    _check = _load_checker()
    fake_agents = [
        {"name": "my-live-agent", "status": "running", "source": "claude-code"}
    ]
    fake_stdout = json.dumps(fake_agents)
    with pytest.raises(pytest.UsageError, match="my-live-agent"):
        _check(fake_stdout)


def test_allows_empty_registry():
    """Guard must not raise when no agents exist."""
    _check = _load_checker()
    _check("[]")


def test_allows_non_claude_code_source():
    """Guard must not raise for running agents from other sources."""
    _check = _load_checker()
    fake_agents = [
        {"name": "bridge-agent", "status": "running", "source": "task-bridge"}
    ]
    _check(json.dumps(fake_agents))


def test_allows_completed_claude_code_agent():
    """Guard must not raise for claude-code agents that are not running."""
    _check = _load_checker()
    fake_agents = [
        {"name": "old-agent", "status": "completed", "source": "claude-code"}
    ]
    _check(json.dumps(fake_agents))


def test_raises_lists_all_live_agents():
    """Error message must name every live agent, not just the first."""
    _check = _load_checker()
    fake_agents = [
        {"name": "agent-alpha", "status": "running", "source": "claude-code"},
        {"name": "agent-beta", "status": "running", "source": "claude-code"},
    ]
    with pytest.raises(pytest.UsageError) as exc_info:
        _check(json.dumps(fake_agents))
    msg = str(exc_info.value)
    assert "agent-alpha" in msg
    assert "agent-beta" in msg


def test_handles_malformed_json_gracefully():
    """Guard must not crash on bad curl output (backend down, etc.)."""
    _check = _load_checker()
    _check("curl: (7) Failed to connect")


def test_handles_non_list_json():
    """Guard must not crash when backend returns a dict instead of a list."""
    _check = _load_checker()
    _check('{"error": "not found"}')

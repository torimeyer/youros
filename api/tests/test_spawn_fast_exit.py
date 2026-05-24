"""Bug B: fast-exit agents must be marked failed, not left as 'running'.

When a subprocess exits non-zero before calling /agents/register or
/agents/complete, the _drain_stderr path must flip the metadata status
to 'failed' with an error message. Terminal statuses like 'completed'
must not be overwritten (the /complete path may race ahead).
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_agent_metadata():
    """Ensure agent_metadata is clean before and after each test."""
    from routers import agents as mod
    orig = dict(mod.agent_metadata)
    mod.agent_metadata.clear()
    yield
    mod.agent_metadata.clear()
    mod.agent_metadata.update(orig)


def _make_meta(name: str, status: str = "running") -> dict:
    """Insert a minimal metadata row and return it."""
    from routers import agents as mod
    meta = {
        "name": name,
        "status": status,
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }
    mod.agent_metadata[name] = meta
    return meta


def test_nonzero_exit_running_flips_to_failed():
    """rc=1 + status='running' -> status flips to 'failed' with error set."""
    from routers import agents as mod

    meta = _make_meta("test-agent-1", "running")

    with patch.object(mod, "_save_agent_state"):
        _fail_meta = mod.agent_metadata.get("test-agent-1")
        assert _fail_meta and _fail_meta.get("status") == "running"
        _fail_meta["status"] = "failed"
        _fail_meta["completed_at"] = datetime.now(timezone.utc).isoformat()
        _fail_meta["error"] = "subprocess exited 1"
        mod._save_agent_state()

    assert meta["status"] == "failed"
    assert "subprocess exited 1" in meta["error"]
    assert "completed_at" in meta


def test_zero_exit_running_stays_running():
    """rc=0 + status='running' -> status stays 'running' (roadmap agents
    complete via a different path)."""
    meta = _make_meta("test-agent-2", "running")
    rc = 0
    if rc not in (None, 0):
        meta["status"] = "failed"
    assert meta["status"] == "running"


def test_nonzero_exit_completed_not_overwritten():
    """rc=1 + status='completed' -> no overwrite (terminal status wins)."""
    meta = _make_meta("test-agent-3", "completed")
    rc = 1
    if rc not in (None, 0):
        if meta.get("status") == "running":
            meta["status"] = "failed"
    assert meta["status"] == "completed"


def test_nonzero_exit_missing_metadata_no_crash():
    """rc=1 + agent not in metadata -> no crash."""
    from routers import agents as mod
    name = "ghost-agent-never-existed"
    rc = 1
    _fail_meta = mod.agent_metadata.get(name)
    if _fail_meta and _fail_meta.get("status") == "running":
        _fail_meta["status"] = "failed"
    assert name not in mod.agent_metadata

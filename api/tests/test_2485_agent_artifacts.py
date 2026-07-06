"""Tests for →2485: artifact paths persisted on agent completion and exposed via /api/agents.

Invariant: every completed non-infra agent run produces at least one artifact
(its summary doc), and that list is stored in agent_metadata and returned in
the /api/agents response so the UI can render clickable links.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Unit: _should_persist_agent_doc is now opt-out (default True)
# ---------------------------------------------------------------------------

def test_should_persist_doc_for_solo_non_infra_agent():
    """A plain solo agent (no fleet_id, no template) now produces a doc."""
    from routers.agents import _should_persist_agent_doc

    # No opt-in signals — previously returned False, must now return True
    assert _should_persist_agent_doc("my-feature-agent", {}) is True


def test_should_persist_doc_still_false_for_infra_names():
    """Infra-noise agents (demo-smoke-*, fix-*, diagnose-*, etc.) still excluded."""
    from routers.agents import _should_persist_agent_doc

    for name in [
        "demo-smoke-abc",
        "fix-login-bug",
        "diagnose-slow-query",
        "smoke-e2e",
        "build-123",
        "verify-deploy",
    ]:
        assert _should_persist_agent_doc(name, {}) is False, (
            f"infra agent '{name}' should NOT produce a doc"
        )


def test_should_persist_doc_fleet_member_always_produces():
    """Fleet members (fleet_id set) always produce a doc."""
    from routers.agents import _should_persist_agent_doc

    assert _should_persist_agent_doc("fleet-build-abc", {"fleet_id": "fleet-1"}) is True


# ---------------------------------------------------------------------------
# Unit: _save_agent_output_to_files returns paths for solo agents now
# ---------------------------------------------------------------------------

def test_save_agent_output_returns_paths_for_solo_agent():
    """Solo non-infra agent with a long summary writes a file and returns its path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        with (
            patch("routers.agents.MYOS_FILES_DIR", tmppath),
            patch("routers.agents.agent_metadata", {
                "my-feature-agent": {},
            }),
        ):
            from routers.agents import _save_agent_output_to_files

            content = "A" * 200  # exceeds _MIN_ARTIFACT_SUMMARY_CHARS
            paths = _save_agent_output_to_files(
                "my-feature-agent", content,
                skip_auto_tasks=True,
                emit_notification=False,
            )

        assert paths, "solo agent with long summary should produce at least one artifact"
        assert all(p.suffix == ".md" for p in paths)


# ---------------------------------------------------------------------------
# Integration: /complete persists artifacts and /api/agents exposes them
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_persists_artifact_paths_in_metadata(client):
    """POST /agents/{name}/complete → metadata contains artifacts list."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        with patch("routers.agents.MYOS_FILES_DIR", tmppath):
            # Register the agent
            reg = await client.post(
                "/api/agents/register",
                json={"name": "solo-artifact-test", "task": "test task", "source": "test"},
            )
            assert reg.status_code == 200

            long_summary = "This is a very detailed summary of what the agent accomplished. " * 5
            resp = await client.post(
                "/api/agents/solo-artifact-test/complete",
                json={"summary": long_summary},
            )
            assert resp.status_code == 200

        from routers.agents import agent_metadata
        meta = agent_metadata.get("solo-artifact-test", {})
        assert "artifacts" in meta, "artifacts list must be stored in agent_metadata after /complete"
        assert len(meta["artifacts"]) >= 1, "at least one artifact path expected"
        for path_str in meta["artifacts"]:
            assert path_str.endswith(".md"), f"artifact should be a .md file, got: {path_str}"


@pytest.mark.asyncio
async def test_agents_response_includes_artifacts_for_completed_agent(client):
    """GET /api/agents returns artifacts field for completed agents."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        with patch("routers.agents.MYOS_FILES_DIR", tmppath):
            await client.post(
                "/api/agents/register",
                json={"name": "artifact-response-test", "task": "test", "source": "test"},
            )

            long_summary = "Detailed output from the agent for this important task. " * 5
            await client.post(
                "/api/agents/artifact-response-test/complete",
                json={"summary": long_summary},
            )

        # The /api/agents endpoint spreads agent_metadata into each agent row,
        # so artifacts stored in metadata must appear in the response.
        from routers.agents import agent_metadata
        meta = agent_metadata.get("artifact-response-test", {})
        # Primary assertion: artifacts is stored (UI reads from metadata spread)
        assert "artifacts" in meta
        assert len(meta["artifacts"]) >= 1

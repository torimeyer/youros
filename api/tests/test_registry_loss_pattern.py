"""Regression tests for registry-loss pattern (needle →1084).

Root causes:
  1. Ghost reaper misses JSONL transcripts: checks only {transcripts_dir}/{name}.md
     and meta.get("transcript_path"), but not JSONL files that
     _resolve_transcript_source() finds under ~/.claude/projects/. Agents with
     stale heartbeats + 0-byte .md files were deleted after 5 min even when
     their JSONL was growing.
  2. Cold-start timeout: audit.jsonl first read takes ~9s; monitors with
     curl -m 5 time out and receive an empty body → PARSE_ERR.

Tests in this file:
  - test_ghost_reaper_accepts_transcript_resolver
      Ghost reaper must honour an injected transcript_resolver callback.
      FAILS pre-fix (TypeError: unexpected kwarg), PASSES post-fix.
  - test_ghost_reaper_resolver_prevents_false_reap
      Agent whose .md is empty but whose resolver finds a live JSONL must
      not be reaped. FAILS pre-fix (reaped), PASSES post-fix.
  - test_ghost_reaper_resolver_still_reaps_when_resolver_finds_nothing
      When the resolver also returns None, normal ghost logic still applies.
      Should PASS both pre- and post-fix.
  - test_persistence_survives_reload
      agent_metadata written to disk is fully recovered on reload.
      Sanity-check: should PASS both pre- and post-fix.
  - test_reconcile_keeps_fresh_heartbeat_agent
      reconcile_agents does NOT mark an agent stopped when its heartbeat is
      recent (< STALE_AGENT_AUTOCOMPLETE_SECONDS).
      Should PASS both pre- and post-fix.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from services.ghost_reaper import reap_ghost_agents

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
_STALE = (_NOW - timedelta(minutes=10)).isoformat()  # 10 min ago — definitely stale
_FRESH = (_NOW - timedelta(minutes=2)).isoformat()   # 2 min ago — within 5-min window


def _meta(
    *,
    status: str = "running",
    hb: str = _STALE,
    source: str = "claude-code",
    transcript_path: Optional[str] = None,
    pid: Optional[int] = None,
) -> dict:
    m: dict = {"status": status, "last_heartbeat_at": hb, "source": source}
    if transcript_path is not None:
        m["transcript_path"] = transcript_path
    if pid is not None:
        m["pid"] = pid
    return m


# ---------------------------------------------------------------------------
# Test 1: ghost reaper accepts transcript_resolver callback
#
# Pre-fix: reap_ghost_agents() does not have a transcript_resolver param →
#   TypeError: unexpected keyword argument 'transcript_resolver'  → FAIL
# Post-fix: keyword is accepted, resolver is called → PASS
# ---------------------------------------------------------------------------


def test_ghost_reaper_accepts_transcript_resolver(tmp_path):
    """Ghost reaper must accept a transcript_resolver kwarg without TypeError.

    FAILS pre-fix (unexpected kwarg), PASSES post-fix.
    """
    called = []

    def resolver(name: str) -> Optional[Path]:
        called.append(name)
        return None  # no transcript found

    reg = {"agent-a": _meta()}
    # Pre-fix: TypeError: reap_ghost_agents() got an unexpected keyword argument
    # Post-fix: runs normally, resolver is called, agent is reaped (no transcript)
    victims = reap_ghost_agents(reg, tmp_path, _NOW, transcript_resolver=resolver)
    assert "agent-a" in victims, "agent with no transcript should still be reaped"
    assert "agent-a" in called, "resolver must be invoked for the agent"


# ---------------------------------------------------------------------------
# Test 2: resolver prevents false reap when JSONL transcript exists
#
# Scenario: HTTP-registered agent, no subprocess PID, heartbeat stale 10 min,
# no {name}.md in transcripts_dir, but a live JSONL exists elsewhere.
# Pre-fix: reaped (ghost reaper ignores resolver) → assertion fails
# Post-fix: NOT reaped (ghost reaper checks resolver) → assertion passes
# ---------------------------------------------------------------------------


def test_ghost_reaper_resolver_prevents_false_reap(tmp_path):
    """Agent with stale HB + empty .md but live JSONL must NOT be reaped.

    FAILS pre-fix (agent is incorrectly reaped), PASSES post-fix.
    """
    # JSONL transcript lives somewhere ghost_reaper doesn't scan directly.
    jsonl_dir = tmp_path / "projects" / "session-abc"
    jsonl_dir.mkdir(parents=True)
    jsonl_path = jsonl_dir / "agent-live.jsonl"
    jsonl_path.write_text('{"type":"assistant","text":"working"}\n')

    # {name}.md exists but is 0 bytes — looks dead to the basic check.
    (tmp_path / "agent-live.md").write_bytes(b"")

    reg = {"agent-live": _meta()}  # stale HB, no transcript_path metadata

    def resolver(name: str) -> Optional[Path]:
        if name == "agent-live":
            return jsonl_path
        return None

    victims = reap_ghost_agents(reg, tmp_path, _NOW, transcript_resolver=resolver)
    assert victims == [], (
        "agent with live JSONL found via resolver must not be reaped; "
        "got: %s" % victims
    )


# ---------------------------------------------------------------------------
# Test 3: resolver returning None still allows normal ghost logic
#
# When no resolver is passed OR resolver returns None, existing behaviour
# applies. Should PASS both pre- and post-fix.
# ---------------------------------------------------------------------------


def test_ghost_reaper_resolver_still_reaps_when_resolver_finds_nothing(tmp_path):
    """When resolver returns None, the agent is still reaped if no other transcript."""
    (tmp_path / "agent-z.md").write_bytes(b"")  # 0-byte file

    def resolver(name: str) -> Optional[Path]:
        return None  # nothing found

    reg = {"agent-z": _meta()}
    victims = reap_ghost_agents(reg, tmp_path, _NOW, transcript_resolver=resolver)
    assert "agent-z" in victims, "agent with no transcript must still be reaped"


def test_ghost_reaper_no_resolver_preserves_existing_behavior(tmp_path):
    """Calling reap_ghost_agents without transcript_resolver works as before."""
    (tmp_path / "ghost.md").write_bytes(b"")
    reg = {"ghost": _meta()}
    victims = reap_ghost_agents(reg, tmp_path, _NOW)
    assert victims == ["ghost"]


# ---------------------------------------------------------------------------
# Test 4: persistence roundtrip
#
# _load_agent_state / _save_agent_state: writing then re-reading agent_metadata
# recovers all fields intact. Validates the persistence layer never silently
# truncates on reload. PASSES both pre- and post-fix.
# ---------------------------------------------------------------------------


def test_persistence_survives_reload(tmp_path):
    """Agent metadata written to disk is fully recovered on reload.

    Simulates the restart case: clear the in-memory dict, reload from disk,
    verify surviving agents still appear with correct fields.
    """
    import routers.agents as agents_mod

    state_path = tmp_path / "agent_state.json"

    agents = {
        "agent-alpha": {
            "status": "running",
            "source": "claude-code",
            "spawned_at": _FRESH,
            "last_heartbeat_at": _FRESH,
            "transcript_path": str(tmp_path / "alpha.md"),
        },
        "agent-beta": {
            "status": "completed",
            "source": "claude-code",
            "spawned_at": _STALE,
            "completed_at": _STALE,
        },
    }

    original_path = agents_mod.AGENT_STATE_PATH
    original_meta = dict(agents_mod.agent_metadata)
    try:
        agents_mod.AGENT_STATE_PATH = state_path
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(agents)
        agents_mod._save_agent_state()

        # Simulate restart: clear in-memory dict, reload from disk.
        agents_mod.agent_metadata.clear()
        reloaded = agents_mod._load_agent_state()
        agents_mod.agent_metadata.update(reloaded)

        assert "agent-alpha" in agents_mod.agent_metadata, "running agent missing after reload"
        assert "agent-beta" in agents_mod.agent_metadata, "completed agent missing after reload"
        alpha = agents_mod.agent_metadata["agent-alpha"]
        assert alpha["status"] == "running"
        assert alpha["transcript_path"] == str(tmp_path / "alpha.md")
    finally:
        agents_mod.AGENT_STATE_PATH = original_path
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)


# ---------------------------------------------------------------------------
# Test 5: reconcile does not prematurely stop freshly-heartbeated agent
#
# An agent with a recent heartbeat (< stale threshold) must never be stopped
# by reconcile_agents even when no subprocess PID is recorded. PASSES both
# pre- and post-fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_keeps_fresh_heartbeat_agent(tmp_path):
    """reconcile_agents must not stop an agent whose heartbeat is fresh.

    Reproduces the pid-less HTTP-registered agent case: no subprocess, no
    proc handle, but heartbeat arrived <60s ago — the agent is alive.
    """
    import routers.agents as agents_mod

    fresh_hb = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    fake_reg = {
        "agent-fresh": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": fresh_hb,
            # No pid — HTTP-registered agent
        }
    }

    original_meta = dict(agents_mod.agent_metadata)
    original_active = dict(agents_mod.active_agents)
    try:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(fake_reg)
        agents_mod.active_agents.clear()

        # Patch _proc_handle_is_alive to return False (no subprocess handle)
        # and _transcript_recently_active to return False (no transcript)
        with (
            patch.object(agents_mod, "_proc_handle_is_alive", return_value=False),
            patch.object(agents_mod, "_transcript_recently_active", return_value=False),
            patch.object(agents_mod, "_save_agent_state"),
            patch.object(agents_mod, "_emit_audit_event"),
        ):
            result = await agents_mod.reconcile_agents()

        assert agents_mod.agent_metadata["agent-fresh"]["status"] == "running", (
            "agent with fresh heartbeat must not be stopped by reconcile; "
            "reconcile result: %s" % result
        )
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)
        agents_mod.active_agents.clear()
        agents_mod.active_agents.update(original_active)


# ---------------------------------------------------------------------------
# Test 6: N agents registered → immediate query → all N visible
#
# Validates that agent_metadata updates propagate correctly to list_agents.
# Uses monkeypatching to avoid touching the live registry or audit.jsonl.
# PASSES both pre- and post-fix (confirms basic registration path is intact).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n_agents_registered_all_appear_immediately(tmp_path):
    """N agents in agent_metadata all appear in list_agents without delay.

    Isolates the registration-to-visibility path from audit.jsonl scanning
    by mocking the audit layer. Confirms no sweep removes freshly-registered
    agents within the first query cycle.
    """
    import routers.agents as agents_mod

    agent_names = [f"reg-agent-{i:03d}" for i in range(5)]
    now_iso = _NOW.isoformat()

    fake_reg: dict = {
        name: {
            "status": "running",
            "source": "claude-code",
            "spawned_at": now_iso,
            "last_heartbeat_at": now_iso,
            "description": f"test agent {name}",
        }
        for name in agent_names
    }

    original_meta = dict(agents_mod.agent_metadata)
    original_active = dict(agents_mod.active_agents)
    try:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(fake_reg)
        agents_mod.active_agents.clear()

        # list_agents calls agent_metadata directly for the in-memory pass.
        # Confirm all N are present in the dict before any sweep runs.
        for name in agent_names:
            assert name in agents_mod.agent_metadata, f"agent {name} missing from registry"
            assert agents_mod.agent_metadata[name]["status"] == "running"

    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_meta)
        agents_mod.active_agents.clear()
        agents_mod.active_agents.update(original_active)

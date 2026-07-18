"""→2936: REST-registered agents (no log_path) must still resolve their own log.

Verified bug: an agent whose only backend contact is POST /api/agents/register
(the saa flavor: harness-spawned, self-registering, no log_path in the body)
never gets a per-agent log resolved:

- register stores a session-link (the orchestrator's top-level session JSONL)
  or nothing, and the own-log resolver correctly refuses to count that file.
- The resolver's subagents/agent-*.jsonl scan strict-matches the agent name
  against the FIRST line of each candidate (the spawn prompt). Real saa briefs
  carry the name only in two shapes the matcher does not know:
    1. ``Locks: [/tmp/<name>.log]``            (saa-2952-tasks-declutter, 1MB
       real transcript, shown as transcript_bytes=0 for its whole run)
    2. ``... /api/agents/register (curl ...) with name <name>, then ...``
       (saa-ledger-audit, 330KB real transcript, shown as 60 bytes: the
       "completed externally" stub the resolver falls back to at step 5)
- Result: transcript_bytes and per_agent_transcript_bytes fall back to 0, a
  wrong tiny file, or the 60-byte completion stub.

Fix under test: _jsonl_strict_match, _first_line_matches_needle, and
_extract_agent_name (which must stay in lockstep) learn both shapes, so
_resolve_own_log_path and _get_per_agent_transcript_bytes find the real
subagents JSONL for register-only agents.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.agents as agents_module  # noqa: E402
from routers.agents import (  # noqa: E402
    _extract_agent_name,
    _first_line_matches_needle,
    _get_per_agent_transcript_bytes,
    _get_transcript_metrics,
    _jsonl_strict_match,
    _resolve_own_log_path,
    agent_metadata,
)


@pytest.fixture(autouse=True)
def _reset_caches():
    """Cold every resolver cache before and after each test (same idiom as
    test_2893): resolvers memoize per name and per glob sweep."""
    agents_module._reset_transcript_resolver_cache()
    agents_module._reset_candidates_cache()
    agents_module._reset_meta_candidates_cache()
    agents_module._transcript_metrics_cache.clear()
    agents_module._own_log_cache.clear()
    agents_module._per_agent_bytes_cache.clear()
    yield
    agents_module._reset_transcript_resolver_cache()
    agents_module._reset_candidates_cache()
    agents_module._reset_meta_candidates_cache()
    agents_module._transcript_metrics_cache.clear()
    agents_module._own_log_cache.clear()
    agents_module._per_agent_bytes_cache.clear()


# ---------------------------------------------------------------------------
# First-line content mirroring the real spawn briefs (verbatim shapes from
# agent-ad4d39f027f12e660.jsonl and agent-ac923c4d6e591f191.jsonl).
# ---------------------------------------------------------------------------

def _locks_flavor_first_line(name: str) -> str:
    """The saa-2952 flavor: name appears ONLY in the Locks header."""
    content = (
        f"Locks: [/tmp/{name}.log]\n\n"
        "You are working on this task ONLY. Pin to it. Use ostk MCP tools "
        "(read, search, bash, fs_ops). Register on spawn: POST "
        "https://127.0.0.1:8000/api/agents/register (curl -sSk "
        "--connect-timeout 3 -m 5) with your agent name, then heartbeat a "
        "current_step every 2 minutes. Long test runs log to "
        "/tmp/pytest-full.log, never pipe."
    )
    return json.dumps({
        "parentUuid": None,
        "isSidechain": True,
        "type": "user",
        "message": {"role": "user", "content": content},
    })


def _with_name_flavor_first_line(name: str) -> str:
    """The saa-ledger-audit flavor: 'register ... with name <name>, then ...'."""
    content = (
        "You are doing a READ-AND-REPORT audit; the only writes you may make "
        "are closing ledger rows. Register on spawn: POST "
        "https://127.0.0.1:8000/api/agents/register (curl -sSk "
        f"--connect-timeout 3 -m 5) with name {name}, then heartbeat with a "
        "current_step every 2 minutes."
    )
    return json.dumps({
        "parentUuid": None,
        "isSidechain": True,
        "type": "user",
        "message": {"role": "user", "content": content},
    })


# ---------------------------------------------------------------------------
# Unit: the three lockstep matchers learn both shapes
# ---------------------------------------------------------------------------

def test_first_line_matches_locks_shape():
    line = _locks_flavor_first_line("saa-2936-fixture").lower()
    assert _first_line_matches_needle(line, "saa-2936-fixture") is True


def test_first_line_matches_with_name_shape():
    line = _with_name_flavor_first_line("saa-ledger-fixture").lower()
    assert _first_line_matches_needle(line, "saa-ledger-fixture") is True


def test_first_line_no_match_for_non_lock_tmp_log_without_locks_header():
    """A bare /tmp/<x>.log mention with no Locks header is not attribution."""
    line = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": "tail /tmp/some-agent.log for errors"},
    }).lower()
    assert _first_line_matches_needle(line, "some-agent") is False


def test_first_line_no_match_for_other_agent_name():
    line = _locks_flavor_first_line("saa-2936-fixture").lower()
    assert _first_line_matches_needle(line, "saa-other-agent") is False


def test_jsonl_strict_match_locks_shape(tmp_path):
    p = tmp_path / "agent-abc.jsonl"
    p.write_text(_locks_flavor_first_line("saa-2936-fixture") + "\n")
    assert _jsonl_strict_match(p, "saa-2936-fixture") is True


def test_jsonl_strict_match_with_name_shape(tmp_path):
    p = tmp_path / "agent-def.jsonl"
    p.write_text(_with_name_flavor_first_line("saa-ledger-fixture") + "\n")
    assert _jsonl_strict_match(p, "saa-ledger-fixture") is True


def test_extract_agent_name_from_locks_header():
    line = _locks_flavor_first_line("saa-2936-fixture").lower()
    assert _extract_agent_name(line) == "saa-2936-fixture"


def test_extract_agent_name_from_locks_header_multi_lock():
    line = 'content: "locks: [/tmp/saa-first.log, /tmp/saa-second.log]"'
    assert _extract_agent_name(line) == "saa-first"


def test_extract_agent_name_from_register_with_name():
    line = _with_name_flavor_first_line("saa-ledger-fixture").lower()
    assert _extract_agent_name(line) == "saa-ledger-fixture"


def test_extract_agent_name_strong_patterns_still_win():
    """A register-POST body name beats the weaker Locks/with-name shapes."""
    line = (
        '"name": "explicit-agent" ... locks: [/tmp/saa-lock-name.log] '
        "register with name other-name"
    )
    assert _extract_agent_name(line) == "explicit-agent"


# ---------------------------------------------------------------------------
# Integration: register-only agents resolve their own subagents JSONL
# ---------------------------------------------------------------------------

def _project_label(project_root: Path) -> str:
    return str(project_root).replace("/", "-").lstrip("-")


def _setup_register_only_agent(
    tmp_path: Path,
    monkeypatch,
    name: str,
    first_line: str,
) -> tuple[Path, Path]:
    """A register-only agent: metadata row with a session-link transcript_path
    (what _link_session_jsonl stores) and a real subagents JSONL whose first
    line is the spawn brief. Returns (project_root, subagent_jsonl)."""
    project_root = tmp_path / "repo"
    project_root.mkdir(exist_ok=True)
    (project_root / "transcripts").mkdir(exist_ok=True)
    projects_root = tmp_path / "projects"
    project_dir = projects_root / f"-{_project_label(project_root)}"
    session_id = "e8224b5f-8d67-4b06-9493-af1397a06fb1"

    # Orchestrator's own session JSONL (top-level) — the session-link target.
    project_dir.mkdir(parents=True, exist_ok=True)
    orch = project_dir / f"{session_id}.jsonl"
    orch.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "orchestrator"}})
        + "\n"
    )

    # The agent's real log.
    sub_dir = project_dir / session_id / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub = sub_dir / "agent-a1b2c3d4e5f60718.jsonl"
    body = first_line + "\n" + json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "working " * 50}]},
    }) + "\n"
    sub.write_text(body)

    monkeypatch.setattr(agents_module, "_claude_code_projects_dir", lambda: projects_root)
    agent_metadata[name] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "transcript_path": str(orch),
        "transcript_path_source": "session-link",
    }
    return project_root, sub


def test_resolve_own_log_for_locks_flavor_register_only_agent(tmp_path, monkeypatch):
    name = "saa-2936-fixture"
    project_root, sub = _setup_register_only_agent(
        tmp_path, monkeypatch, name, _locks_flavor_first_line(name)
    )
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resolved = _resolve_own_log_path(name)
            assert resolved == sub, (
                f"own-log resolution must find the subagents JSONL, got {resolved}"
            )
            assert _get_per_agent_transcript_bytes(name) == sub.stat().st_size
    finally:
        agent_metadata.pop(name, None)


def test_resolve_own_log_for_with_name_flavor_register_only_agent(tmp_path, monkeypatch):
    name = "saa-ledger-fixture"
    project_root, sub = _setup_register_only_agent(
        tmp_path, monkeypatch, name, _with_name_flavor_first_line(name)
    )
    try:
        with patch("config.PROJECT_ROOT", project_root):
            resolved = _resolve_own_log_path(name)
            assert resolved == sub
            assert _get_per_agent_transcript_bytes(name) == sub.stat().st_size
    finally:
        agent_metadata.pop(name, None)


def test_metrics_prefer_real_jsonl_over_completion_stub(tmp_path, monkeypatch):
    """The saa-ledger-audit end state: after completion the stub markdown
    exists (60 bytes). Metrics must count the real JSONL, not the stub."""
    name = "saa-2936-stub-fixture"
    project_root, sub = _setup_register_only_agent(
        tmp_path, monkeypatch, name, _locks_flavor_first_line(name)
    )
    stub = project_root / "transcripts" / f"{name}.md"
    stub.write_text(f"Agent '{name}' completed (registered externally).\n")
    try:
        with patch("config.PROJECT_ROOT", project_root):
            metrics = _get_transcript_metrics(name)
            assert metrics["transcript_bytes"] == sub.stat().st_size, (
                f"metrics counted {metrics['transcript_bytes']} bytes — the "
                f"{stub.stat().st_size}-byte completion stub instead of the real log"
            )
    finally:
        agent_metadata.pop(name, None)

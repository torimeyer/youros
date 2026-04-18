"""Regression tests for two Files-tab leak classes that slipped past
the narrow-agent-outputs gate.

Bug 1: ``_is_roadmap_agent`` previously matched any agent whose name
contained "roadmap", so a diagnose subagent named
``roadmap-e2e-task-leak`` was misclassified as a Roadmap run and its
summary landed in ``~/.myos/files/`` with ``kind: roadmap`` front
matter. Fix: strictly template-driven. Name-based matching removed.

Bug 2: The boot-time retroactive sweep
(``_retroactively_save_agent_summaries``) constructed a pseudo
front-matter with ``template: retroactive`` and wrote a .md for every
agent that had a memory file, bypassing the ``fleet_id`` /
``_is_roadmap_agent`` / ``template_produces_doc`` opt-in gate entirely.
Fix: shared ``_should_persist_agent_doc`` helper is called from both
the live ``/complete`` writer and the retroactive sweep.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Bug 1: _is_roadmap_agent must be template-driven, not name-driven
# ---------------------------------------------------------------------------


def test_is_roadmap_agent_name_with_roadmap_and_no_template_returns_false():
    """A diagnose subagent named ``roadmap-e2e-task-leak`` (no template,
    or template="none") must not be classified as a Roadmap run. This
    is the exact false-positive that leaked the
    ``roadmap-e2e-task-leak-2026-04-17T04-39.md`` artifact."""
    from routers.agents import _is_roadmap_agent

    assert _is_roadmap_agent("roadmap-e2e-task-leak", template_raw="none") is False
    assert _is_roadmap_agent("roadmap-e2e-task-leak", template_raw="") is False
    assert _is_roadmap_agent("roadmap-false-positive", template_raw=None) is False  # type: ignore[arg-type]


def test_is_roadmap_agent_starts_with_roadmap_and_no_template_returns_false():
    """Historical fallback matched ``name.startswith("roadmap")`` even
    with no template. That path is removed."""
    from routers.agents import _is_roadmap_agent

    assert _is_roadmap_agent("roadmap-custom-probe-123", template_raw="") is False


def test_is_roadmap_agent_with_roadmap_template_returns_true():
    """Real Roadmap runs carry ``template: "Roadmap"`` (or a builtin
    variant) in agent_metadata. Those must still classify correctly
    regardless of what the agent name looks like."""
    from routers.agents import _is_roadmap_agent

    assert _is_roadmap_agent("my-roadmap-agent", template_raw="Roadmap") is True
    assert _is_roadmap_agent("whatever-name", template_raw="roadmap") is True
    assert _is_roadmap_agent("x", template_raw="pm-roadmap") is True
    assert _is_roadmap_agent("x", template_raw="builtin-pm-roadmap") is True


def test_is_roadmap_agent_non_roadmap_template_returns_false():
    """A PRD or custom template is NOT a Roadmap run even if its name
    happens to contain "roadmap"."""
    from routers.agents import _is_roadmap_agent

    assert _is_roadmap_agent("roadmap-notifier", template_raw="PRD") is False
    assert _is_roadmap_agent("roadmap-ish", template_raw="custom-build") is False


# ---------------------------------------------------------------------------
# Shared gate helper contract
# ---------------------------------------------------------------------------


def test_should_persist_agent_doc_requires_opt_in_signal():
    """With none of the three opt-in signals, the gate returns False."""
    from routers.agents import _should_persist_agent_doc

    assert _should_persist_agent_doc("some-solo-agent", {}) is False
    assert _should_persist_agent_doc("some-solo-agent", None) is False
    assert (
        _should_persist_agent_doc(
            "some-solo-agent", {"template": "none", "status": "completed"}
        )
        is False
    )


def test_should_persist_agent_doc_allows_fleet_member():
    from routers.agents import _should_persist_agent_doc

    assert (
        _should_persist_agent_doc(
            "fleet-build-website-product-manager",
            {"fleet_id": "fleet-build-website", "template": ""},
        )
        is True
    )


def test_should_persist_agent_doc_allows_roadmap_template():
    from routers.agents import _should_persist_agent_doc

    assert (
        _should_persist_agent_doc(
            "roadmap-run-123", {"template": "Roadmap"}
        )
        is True
    )


def test_should_persist_agent_doc_allows_produces_doc_template():
    from routers.agents import _should_persist_agent_doc

    assert (
        _should_persist_agent_doc(
            "prd-run",
            {"template": "PRD", "template_produces_doc": True},
        )
        is True
    )


def test_should_persist_agent_doc_rejects_diagnose_name_with_name_only():
    """A diagnose-prefixed name must be rejected even if it starts with
    "roadmap" somehow (can't, but prove the infra-name filter fires).
    """
    from routers.agents import _should_persist_agent_doc

    assert (
        _should_persist_agent_doc(
            "diagnose-roadmap-issue", {"template": "none"}
        )
        is False
    )


# ---------------------------------------------------------------------------
# Bug 2: retroactive sweep must honour the shared gate
# ---------------------------------------------------------------------------


def _write_memory_json(path: Path, text: str) -> None:
    """Helper: write an agent_memory JSON file with a single summary
    that clears the 50-char minimum."""
    payload = {
        "summaries": [
            {
                "text": text,
                "saved_at": "2026-04-16T01:02:03+00:00",
            }
        ]
    }
    path.write_text(json.dumps(payload))


def test_retroactive_sweep_skips_solo_agent_without_optin(tmp_path):
    """The retroactive sweep must NOT write a .md for a solo agent
    whose metadata lacks fleet_id, a Roadmap template, and the
    produces_doc flag. This is the settings-cost-tracking-rename leak
    class.
    """
    from routers import agents as agents_mod

    mem_dir = tmp_path / "agent_memory"
    files_dir = tmp_path / "files"
    mem_dir.mkdir()

    agent_name = "settings-cost-tracking-rename"
    _write_memory_json(
        mem_dir / f"{agent_name}.json",
        "Renamed the Cost Tracking card header to Spend Overview so the "
        "label matches the rest of the sidebar vocabulary.",
    )

    # No metadata entry at all for this agent: the gate must reject.
    agents_mod.agent_metadata.pop(agent_name, None)

    with patch("services.agent_memory.AGENT_MEMORY_DIR", mem_dir), patch.object(
        agents_mod, "MYOS_FILES_DIR", files_dir
    ):
        written = agents_mod._retroactively_save_agent_summaries()

    assert written == 0, "Sweep must not write for solo agent without opt-in"
    # Directory may not exist (sweep never wrote) or exist but be empty.
    if files_dir.exists():
        assert list(files_dir.glob("*.md")) == [], (
            f"Expected no .md files, got {list(files_dir.glob('*.md'))}"
        )


def test_retroactive_sweep_skips_diagnose_named_agent(tmp_path):
    """Even when an agent's memory exists, a name matching the infra
    regex (diagnose-*, fix-*, rename-*, cleanup-*, etc.) must be
    skipped unless fleet_id is present.
    """
    from routers import agents as agents_mod

    mem_dir = tmp_path / "agent_memory"
    files_dir = tmp_path / "files"
    mem_dir.mkdir()

    agent_name = "diagnose-roadmap-issue"
    _write_memory_json(
        mem_dir / f"{agent_name}.json",
        "Investigated the roadmap display issue and shipped a two-line "
        "CSS fix. Regression test added.",
    )
    # Even with a Roadmap template, the infra-name filter rejects
    # because the name starts with diagnose- and no fleet_id is set.
    agents_mod.agent_metadata[agent_name] = {"template": "Roadmap"}

    try:
        with patch(
            "services.agent_memory.AGENT_MEMORY_DIR", mem_dir
        ), patch.object(agents_mod, "MYOS_FILES_DIR", files_dir):
            written = agents_mod._retroactively_save_agent_summaries()
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)

    assert written == 0
    if files_dir.exists():
        assert list(files_dir.glob("*.md")) == []


def test_retroactive_sweep_writes_for_fleet_member(tmp_path):
    """Regression guard: legitimate fleet members still get replayed.
    Without this, my fix might over-correct and suppress real work.
    """
    from routers import agents as agents_mod

    mem_dir = tmp_path / "agent_memory"
    files_dir = tmp_path / "files"
    mem_dir.mkdir()

    agent_name = "fleet-build-website-product-manager"
    _write_memory_json(
        mem_dir / f"{agent_name}.json",
        "Wrote the PRD for the Build a Website fleet. Audience, key "
        "pages, success criteria all covered.",
    )
    agents_mod.agent_metadata[agent_name] = {
        "template": "product-manager",
        "fleet_id": "fleet-build-website",
    }

    try:
        with patch(
            "services.agent_memory.AGENT_MEMORY_DIR", mem_dir
        ), patch.object(agents_mod, "MYOS_FILES_DIR", files_dir):
            written = agents_mod._retroactively_save_agent_summaries()
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)

    assert written == 1
    produced = list(files_dir.glob("*.md"))
    assert len(produced) == 1
    text = produced[0].read_text()
    assert "retroactive: true" in text
    assert "fleet_id: fleet-build-website" in text
    assert "kind: fleet-output" in text


def test_retroactive_sweep_writes_for_roadmap_template(tmp_path):
    """Regression guard: real Roadmap agents still get replayed with
    kind: roadmap front matter."""
    from routers import agents as agents_mod

    mem_dir = tmp_path / "agent_memory"
    files_dir = tmp_path / "files"
    mem_dir.mkdir()

    agent_name = "quarterly-plan"
    _write_memory_json(
        mem_dir / f"{agent_name}.json",
        "Drafted a four-quarter roadmap covering onboarding, billing, "
        "invites, and analytics themes.",
    )
    agents_mod.agent_metadata[agent_name] = {"template": "Roadmap"}

    try:
        with patch(
            "services.agent_memory.AGENT_MEMORY_DIR", mem_dir
        ), patch.object(agents_mod, "MYOS_FILES_DIR", files_dir):
            written = agents_mod._retroactively_save_agent_summaries()
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)

    assert written == 1
    produced = list(files_dir.glob("*.md"))
    assert len(produced) == 1
    text = produced[0].read_text()
    assert "kind: roadmap" in text


# ---------------------------------------------------------------------------
# Live /complete path also honours the shared gate (Bug 1 end-to-end)
# ---------------------------------------------------------------------------


def test_save_agent_output_skips_solo_agent_with_roadmap_in_name(tmp_path):
    """End-to-end proof that a name-containing-roadmap diagnose agent
    calling the live artifact writer does NOT create a .md.
    """
    from routers import agents as agents_mod

    files_dir = tmp_path / "files"
    agent_name = "roadmap-false-positive-probe"
    agents_mod.agent_metadata[agent_name] = {
        "template": "none",
        "status": "running",
    }
    long_summary = (
        "Investigated the roadmap false-positive classifier and confirmed "
        "the fix works end to end. No artifact should land here." * 2
    )
    try:
        with patch.object(agents_mod, "MYOS_FILES_DIR", files_dir):
            paths = agents_mod._save_agent_output_to_files(
                agent_name, long_summary
            )
    finally:
        agents_mod.agent_metadata.pop(agent_name, None)

    assert paths == []
    if files_dir.exists():
        assert list(files_dir.glob("*.md")) == []

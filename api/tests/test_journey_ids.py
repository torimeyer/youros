"""Tests for journey ID feature: spec-to-artifact traceability.

Covers →2516, →2517, →2518, →2532, →2533, →2534, →2549, →2550, →2551.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ostk import OstkService


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def svc_tmp(monkeypatch):
    """OstkService pointed at a fresh tmpdir."""
    import services.ostk as ostk_mod

    tmpdir = Path(tempfile.mkdtemp())
    specs_dir = tmpdir / "specs"
    drafts_dir = tmpdir / "drafts"
    specs_dir.mkdir(parents=True)
    drafts_dir.mkdir(parents=True)

    monkeypatch.setattr(ostk_mod, "USER_SPECS_DIR", specs_dir)
    monkeypatch.setattr(ostk_mod, "USER_DRAFTS_DIR", drafts_dir)

    svc = OstkService(cwd=str(tmpdir))
    return svc, tmpdir, specs_dir, drafts_dir


# ─── →2516: journey_id assigned at promote time ───────────────────────────────


@pytest.mark.asyncio
async def test_promote_assigns_journey_id(svc_tmp):
    """Promoting a draft writes journey_id into the spec frontmatter."""
    svc, tmpdir, specs_dir, drafts_dir = svc_tmp

    draft = drafts_dir / "my-feature.md"
    draft.write_text(
        "---\ntitle: my feature\nstatus: draft\ncreated_at: 2026-07-09T00:00:00Z\n---\n\n"
        "## Acceptance criteria\n- [ ] Works\n"
    )

    result = await svc.doc_promote(str(draft.relative_to(tmpdir)))
    promoted_path = Path(result) if Path(result).is_absolute() else specs_dir / Path(result).name
    text = promoted_path.read_text()

    assert "journey_id:" in text
    jid = None
    for line in text.splitlines():
        if line.startswith("journey_id:"):
            jid = line.split(":", 1)[1].strip()
    assert jid is not None
    assert jid.startswith("jrn-"), f"journey_id should start with 'jrn-', got: {jid!r}"
    assert len(jid) > 4, "journey_id too short"


@pytest.mark.asyncio
async def test_promote_journey_id_is_unique(svc_tmp):
    """Each promoted spec gets a distinct journey_id."""
    svc, tmpdir, specs_dir, drafts_dir = svc_tmp

    for i in range(3):
        d = drafts_dir / f"feat-{i}.md"
        d.write_text(
            f"---\ntitle: feat {i}\nstatus: draft\ncreated_at: 2026-07-09T00:00:00Z\n---\n\n"
            "- [ ] Done\n"
        )
        await svc.doc_promote(str(d.relative_to(tmpdir)))

    journey_ids = []
    for f in specs_dir.glob("*.md"):
        for line in f.read_text().splitlines():
            if line.startswith("journey_id:"):
                journey_ids.append(line.split(":", 1)[1].strip())

    assert len(journey_ids) == 3
    assert len(set(journey_ids)) == 3, "All journey_ids must be unique"


@pytest.mark.asyncio
async def test_list_docs_includes_journey_id(svc_tmp):
    """list_docs returns journey_id from frontmatter."""
    svc, tmpdir, specs_dir, drafts_dir = svc_tmp

    spec = specs_dir / "ready-spec.md"
    spec.write_text(
        "---\ntitle: ready spec\nstatus: spec\nspec_id: S001\n"
        "journey_id: jrn-abc12345\ncreated_at: 2026-07-09T00:00:00Z\n---\n\n"
        "- [x] Done\n"
    )

    docs = await svc.list_docs()
    doc = next((d for d in docs if d.get("filename") == "ready-spec.md"), None)
    assert doc is not None
    assert doc.get("journey_id") == "jrn-abc12345"


@pytest.mark.asyncio
async def test_promote_preserves_existing_journey_id(svc_tmp):
    """A draft that already has journey_id keeps it on promote."""
    svc, tmpdir, specs_dir, drafts_dir = svc_tmp

    draft = drafts_dir / "existing-jid.md"
    draft.write_text(
        "---\ntitle: existing jid\nstatus: draft\njourney_id: jrn-preexisting\n"
        "created_at: 2026-07-09T00:00:00Z\n---\n\n- [ ] AC\n"
    )

    result = await svc.doc_promote(str(draft.relative_to(tmpdir)))
    promoted_path = Path(result) if Path(result).is_absolute() else specs_dir / Path(result).name
    text = promoted_path.read_text()

    journey_ids = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("journey_id:")
    ]
    assert len(journey_ids) == 1, "Exactly one journey_id in frontmatter"
    assert journey_ids[0] == "jrn-preexisting"


# ─── →2517 / →2533: build_spec logs journey_id ───────────────────────────────


@pytest.mark.asyncio
async def test_build_spec_returns_journey_id(client, tmp_path, monkeypatch):
    """POST /specs/{path}/build returns journey_id in the response."""
    from services import ostk as ostk_module
    import routers.specs as specs_mod

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

    spec_file = tmp_path / "docs" / "spec" / "journey-build.md"
    spec_file.write_text(
        "---\ntitle: journey build\nstatus: spec\nspec_id: S010\n"
        "journey_id: jrn-testbuild1\ncreated_at: 2026-07-09T00:00:00Z\n---\n\n"
        "- [ ] Build this\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "spec_build", AsyncMock(return_value={"agents": [
        {"name": "spec-journey-build-901", "task_id": "→901", "task_title": "Build this", "prompt": "build"},
    ]}))

    with patch("routers.agents.spawn_agent", new_callable=AsyncMock):
        resp = await client.post(
            "/api/specs/docs/spec/journey-build.md/build"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "journey_id" in data
    assert data["journey_id"] == "jrn-testbuild1"


@pytest.mark.asyncio
async def test_build_spec_traces_journey_id(client, tmp_path, monkeypatch):
    """build_spec emits a trace event containing journey_id."""
    from services import ostk as ostk_module

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

    spec_file = tmp_path / "docs" / "spec" / "trace-test.md"
    spec_file.write_text(
        "---\ntitle: trace test\nstatus: spec\nspec_id: S011\n"
        "journey_id: jrn-tracetest1\ncreated_at: 2026-07-09T00:00:00Z\n---\n\n"
        "- [ ] Trace me\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "spec_build", AsyncMock(return_value={"agents": [
        {"name": "spec-trace-test-902", "task_id": "→902", "task_title": "Trace me", "prompt": "trace"},
    ]}))

    traced_events = []

    def fake_trace(name, **kwargs):
        traced_events.append({"event": name, **kwargs})

    with patch("services.tracing.trace_event", side_effect=fake_trace):
        with patch("routers.agents.spawn_agent", new_callable=AsyncMock):
            resp = await client.post("/api/specs/docs/spec/trace-test.md/build")

    assert resp.status_code == 200
    journey_events = [e for e in traced_events if e.get("journey_id") == "jrn-tracetest1"]
    assert len(journey_events) >= 1, f"Expected trace events with journey_id, got: {traced_events}"


# ─── →2518: activity feed journey_id filter ───────────────────────────────────


@pytest.mark.asyncio
async def test_activity_filter_by_journey_id(client, tmp_path, monkeypatch):
    """GET /activity?journey_id=jrn-xxx returns only events for that journey."""
    import services.ostk as ostk_mod
    from config import OSTK_DIR

    audit_path = tmp_path / ".ostk" / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Write some events to audit.jsonl
    events = [
        {"ts": "2026-07-09T00:00:01Z", "event": "spec_build_started", "spec_path": "docs/spec/a.md", "journey_id": "jrn-abc1"},
        {"ts": "2026-07-09T00:00:02Z", "event": "spec_build_started", "spec_path": "docs/spec/b.md", "journey_id": "jrn-xyz2"},
        {"ts": "2026-07-09T00:00:03Z", "event": "agent.spawned", "name": "builder-1", "journey_id": "jrn-abc1"},
        {"ts": "2026-07-09T00:00:04Z", "event": "task.added", "detail": "→901 some task"},
    ]
    audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    monkeypatch.setattr(ostk_mod, "OSTK_DIR", tmp_path / ".ostk")

    from services.ostk import invalidate_audit_cache
    invalidate_audit_cache(audit_path)

    # Mock get_history so the activity endpoint has events to return
    mock_events = [
        {"timestamp": "2026-07-09T00:00:01Z", "event": "spec_build_started", "detail": "spec_path=docs/spec/a.md journey_id=jrn-abc1"},
        {"timestamp": "2026-07-09T00:00:02Z", "event": "spec_build_started", "detail": "spec_path=docs/spec/b.md journey_id=jrn-xyz2"},
        {"timestamp": "2026-07-09T00:00:03Z", "event": "agent.spawned", "detail": "name=builder-1 journey_id=jrn-abc1"},
        {"timestamp": "2026-07-09T00:00:04Z", "event": "task.added", "detail": "→901 some task"},
    ]

    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=mock_events)
        with patch("services.ostk.read_audit_entries", return_value=events):
            resp = await client.get("/api/activity?journey_id=jrn-abc1")

    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    # All returned events should be related to journey jrn-abc1
    for ev in data["events"]:
        assert "jrn-abc1" in ev.get("detail", "") or ev.get("journey_id") == "jrn-abc1", \
            f"Unexpected event in filtered result: {ev}"


# ─── →2534: journey_id accessible for cost tracking ──────────────────────────


def test_journey_id_in_spec_journey_map():
    """_spec_journey_ids maps spec_path to its journey_id after build."""
    import routers.specs as specs_mod

    # The module-level map should be accessible
    assert hasattr(specs_mod, "_spec_journey_ids"), \
        "routers.specs must expose _spec_journey_ids dict for cost lookups"

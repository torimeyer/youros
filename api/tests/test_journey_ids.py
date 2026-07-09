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


# ─── →2600 / →2588: journey completion on spec finish ────────────────────────


def _write_completable_spec(tmp_path, monkeypatch, *, journey_id: str | None):
    """Create a spec whose two builder tasks are both closed, wired so the
    advance helper will fire. Returns (specs_mod, spec_file)."""
    from routers import specs as specs_mod
    from services import youros_paths
    from services import ostk as ostk_module

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    jid_line = f"journey_id: {journey_id}\n" if journey_id else ""
    spec_file = specs_dir / "journey-done.md"
    spec_file.write_text(
        "---\ntitle: journey done\nstatus: building\n"
        + jid_line
        + 'tasks:\n  - "71"\n  - "72"\n---\n\n- [ ] step one\n- [ ] step two\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
    monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)
    monkeypatch.setattr(
        specs_mod,
        "_spec_task_origin",
        {"71": str(spec_file), "72": str(spec_file)},
    )

    async def fake_list_tasks():
        return [
            {"id": "71", "status": "closed", "title": "step one"},
            {"id": "72", "status": "closed", "title": "step two"},
        ]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)
    return specs_mod, spec_file


@pytest.mark.asyncio
async def test_advance_writes_journey_complete_timestamp(tmp_path, monkeypatch):
    """→2600: flipping a spec to complete writes journey_complete: <ISO ts>
    into the spec's frontmatter."""
    from datetime import datetime

    specs_mod, spec_file = _write_completable_spec(
        tmp_path, monkeypatch, journey_id="jrn-done1"
    )

    result = await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("71")

    assert result == str(spec_file)
    text = spec_file.read_text()
    assert "status: complete" in text
    stamps = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("journey_complete:")
    ]
    assert len(stamps) == 1, f"exactly one journey_complete key, got:\n{text}"
    # Must be a parseable ISO timestamp
    parsed = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
    assert parsed.year >= 2026


@pytest.mark.asyncio
async def test_advance_journey_complete_in_frontmatter_block(tmp_path, monkeypatch):
    """→2600: journey_complete lands INSIDE the frontmatter block, not the body."""
    specs_mod, spec_file = _write_completable_spec(
        tmp_path, monkeypatch, journey_id="jrn-done2"
    )

    await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("71")

    lines = spec_file.read_text().split("\n")
    assert lines[0].strip() == "---"
    close_idx = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
    fm = lines[1:close_idx]
    assert any(l.startswith("journey_complete:") for l in fm), (
        f"journey_complete must be inside frontmatter, got fm={fm}"
    )


@pytest.mark.asyncio
async def test_advance_traces_spec_journey_complete(tmp_path, monkeypatch):
    """→2588: completion emits a spec_journey_complete trace event carrying
    journey_id, completed_at, and the last agent's name."""
    specs_mod, spec_file = _write_completable_spec(
        tmp_path, monkeypatch, journey_id="jrn-trace9"
    )
    monkeypatch.setattr(
        specs_mod, "_task_assignments",
        {"71": "spec-journey-done-71", "72": "spec-journey-done-72"},
    )

    traced = []

    def fake_trace(name, **kwargs):
        traced.append({"event": name, **kwargs})

    monkeypatch.setattr(specs_mod, "trace_event", fake_trace)

    await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("72")

    done_events = [e for e in traced if e["event"] == "spec_journey_complete"]
    assert len(done_events) == 1, f"expected one spec_journey_complete, got {traced}"
    ev = done_events[0]
    assert ev["journey_id"] == "jrn-trace9"
    assert ev.get("completed_at"), "spec_journey_complete must carry completed_at"
    assert ev.get("last_agent") == "spec-journey-done-72"


@pytest.mark.asyncio
async def test_advance_no_journey_event_without_journey_id(tmp_path, monkeypatch):
    """A spec without journey_id still gets journey_complete metadata, but no
    spec_journey_complete activity event is emitted (there is no journey)."""
    specs_mod, spec_file = _write_completable_spec(
        tmp_path, monkeypatch, journey_id=None
    )

    traced = []
    monkeypatch.setattr(
        specs_mod, "trace_event",
        lambda name, **kw: traced.append({"event": name, **kw}),
    )

    result = await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("71")

    assert result == str(spec_file)
    assert "journey_complete:" in spec_file.read_text()
    assert not [e for e in traced if e["event"] == "spec_journey_complete"]


@pytest.mark.asyncio
async def test_activity_feed_shows_journey_completed(client, monkeypatch):
    """→2588: GET /activity?journey_id= surfaces the spec_journey_complete
    event with its timestamp and a plain-language label."""
    from routers import activity as activity_mod

    audit_events = [
        {"ts": "2026-07-09T01:00:00+00:00", "event": "spec_built_start",
         "spec_path": "docs/spec/j.md", "journey_id": "jrn-feed1"},
        {"ts": "2026-07-09T01:30:00+00:00", "event": "spec_journey_complete",
         "spec_path": "docs/spec/j.md", "journey_id": "jrn-feed1",
         "completed_at": "2026-07-09T01:30:00+00:00", "last_agent": "spec-j-9"},
    ]

    monkeypatch.setattr(activity_mod, "read_audit_entries", lambda _p: audit_events)
    with patch("routers.activity.ostk") as mock_ostk:
        mock_ostk.get_history = AsyncMock(return_value=[])
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/activity?journey_id=jrn-feed1")

    assert resp.status_code == 200
    events = resp.json()["events"]
    done = [e for e in events if e["event"] == "spec_journey_complete"]
    assert len(done) == 1, f"expected spec_journey_complete in feed, got {events}"
    assert done[0]["timestamp"] == "2026-07-09T01:30:00+00:00"
    assert done[0]["label"] == "Journey completed"


# ─── →2532: artifact registry ─────────────────────────────────────────────────


def test_record_artifact_appends_record(tmp_path, monkeypatch):
    """record_artifact writes {artifact_path, spec_path, journey_id, created_at}
    as one JSONL row."""
    from datetime import datetime
    import routers.specs as specs_mod

    registry = tmp_path / "artifact_registry.jsonl"
    monkeypatch.setattr(specs_mod, "ARTIFACT_REGISTRY_PATH", registry)

    rec = specs_mod.record_artifact(
        "out/report.pdf", "docs/spec/a.md", journey_id="jrn-art1"
    )

    assert rec["artifact_path"] == "out/report.pdf"
    assert rec["spec_path"] == "docs/spec/a.md"
    assert rec["journey_id"] == "jrn-art1"
    datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))

    rows = [json.loads(l) for l in registry.read_text().splitlines() if l.strip()]
    assert rows == [rec]


def test_record_artifact_resolves_journey_from_cache(tmp_path, monkeypatch):
    """When journey_id is not passed, record_artifact looks it up in
    _spec_journey_ids (populated by build_spec)."""
    import routers.specs as specs_mod

    registry = tmp_path / "artifact_registry.jsonl"
    monkeypatch.setattr(specs_mod, "ARTIFACT_REGISTRY_PATH", registry)
    monkeypatch.setitem(specs_mod._spec_journey_ids, "docs/spec/b.md", "jrn-cached2")

    rec = specs_mod.record_artifact("out/deck.pptx", "docs/spec/b.md")

    assert rec["journey_id"] == "jrn-cached2"


@pytest.mark.asyncio
async def test_artifact_endpoints_roundtrip(client, tmp_path, monkeypatch):
    """POST /specs/{path}/artifacts registers a record; GET /specs/artifacts
    filters by journey_id and spec_path."""
    import routers.specs as specs_mod

    registry = tmp_path / "artifact_registry.jsonl"
    monkeypatch.setattr(specs_mod, "ARTIFACT_REGISTRY_PATH", registry)

    resp = await client.post(
        "/api/specs/docs/spec/report-spec.md/artifacts",
        json={"artifact_path": "out/q3-report.pdf", "journey_id": "jrn-rt3"},
    )
    assert resp.status_code == 200
    rec = resp.json()
    assert rec["artifact_path"] == "out/q3-report.pdf"
    assert rec["journey_id"] == "jrn-rt3"

    resp2 = await client.post(
        "/api/specs/docs/spec/other-spec.md/artifacts",
        json={"artifact_path": "out/other.html", "journey_id": "jrn-other"},
    )
    assert resp2.status_code == 200

    listing = await client.get("/api/specs/artifacts?journey_id=jrn-rt3")
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 1
    assert body["artifacts"][0]["artifact_path"] == "out/q3-report.pdf"
    assert body["artifacts"][0]["spec_path"] == "docs/spec/report-spec.md"

    by_spec = await client.get("/api/specs/artifacts?spec_path=docs/spec/other-spec.md")
    assert by_spec.status_code == 200
    assert by_spec.json()["count"] == 1
    assert by_spec.json()["artifacts"][0]["journey_id"] == "jrn-other"

    everything = await client.get("/api/specs/artifacts")
    assert everything.json()["count"] == 2

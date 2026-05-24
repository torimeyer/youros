"""Tests for narrative router v2 — promote + list (summary) + single draft (→1451).

AC SC-007: POST /api/narrative/draft/{id}/promote
AC SC-008: GET /api/narrative/drafts (summary fields), GET /api/narrative/draft/{id}

Tests use tmp_path via MYOS_DIR env var — never touch real ~/.myos/.
RED tests for →1658 added below (draft body must be non-trivial, heading must say Progress Updates).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_draft(tmp_path: Path, draft_id: str, audience: str = "exec", source_count: int = 2) -> dict:
    """Write a fake draft JSON into tmp_path/narratives/ and return the data."""
    narratives_dir = tmp_path / "narratives"
    narratives_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "draft_id": draft_id,
        "audience": audience,
        "window_days": 7,
        "markdown": f"# Exec Update\n\nTest draft {draft_id}",
        "source_refs": [{"kind": "spec", "id": f"s{i}", "title": f"Spec {i}", "meta": {}} for i in range(source_count)],
        "created_at": "2026-05-18T02:00:00+00:00",
    }
    (narratives_dir / f"{draft_id}.json").write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# SC-008: GET /api/narrative/drafts — summary fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_drafts_returns_summary_fields(client, tmp_path):
    """List endpoint returns summary fields: draft_id, created_at, audience, source_count."""
    draft_id = "aaaa-1111-test"
    _write_draft(tmp_path, draft_id, audience="board", source_count=3)

    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    data = resp.json()
    assert "drafts" in data
    assert len(data["drafts"]) == 1
    summary = data["drafts"][0]
    assert summary["draft_id"] == draft_id
    assert summary["audience"] == "board"
    assert summary["source_count"] == 3
    assert "created_at" in summary
    # Must NOT return full markdown in list response
    assert "markdown" not in summary


@pytest.mark.asyncio
async def test_list_drafts_newest_first(client, tmp_path):
    """Drafts are returned newest first (by file mtime)."""
    import time

    narratives_dir = tmp_path / "narratives"
    narratives_dir.mkdir(parents=True, exist_ok=True)

    _write_draft(tmp_path, "older-draft")
    time.sleep(0.05)  # ensure different mtime
    _write_draft(tmp_path, "newer-draft")

    with patch("routers.narrative.NARRATIVES_DIR", narratives_dir):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    ids = [d["draft_id"] for d in resp.json()["drafts"]]
    assert ids[0] == "newer-draft"
    assert ids[1] == "older-draft"


@pytest.mark.asyncio
async def test_list_drafts_empty_when_no_files(client, tmp_path):
    """Returns empty list (not 500) when no drafts exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/drafts")

    assert resp.status_code == 200
    assert resp.json()["drafts"] == []


# ---------------------------------------------------------------------------
# SC-008: GET /api/narrative/draft/{id} — single draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_single_draft_returns_full_json(client, tmp_path):
    """Single draft endpoint returns complete draft JSON including markdown."""
    draft_id = "bbbb-2222-test"
    data = _write_draft(tmp_path, draft_id)

    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get(f"/api/narrative/draft/{draft_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["draft_id"] == draft_id
    assert "markdown" in body
    assert body["markdown"] == data["markdown"]
    assert "source_refs" in body


@pytest.mark.asyncio
async def test_get_single_draft_404_when_not_found(client, tmp_path):
    """Returns 404 when draft ID does not exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.get("/api/narrative/draft/no-such-id")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SC-007: POST /api/narrative/draft/{id}/promote
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promote_writes_spec_file(client, tmp_path):
    """Promote writes a markdown spec to ~/.myos/specs/narrative-{id}.md."""
    draft_id = "cccc-3333-test"
    _write_draft(tmp_path, draft_id, audience="exec")

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "999"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    body = resp.json()
    assert "spec_path" in body

    spec_file = Path(body["spec_path"])
    assert spec_file.exists(), "Spec file must be written to disk"

    content = spec_file.read_text()
    assert "title:" in content
    assert "status: spec" in content
    assert f"source: narrative-{draft_id}" in content
    assert "created_at:" in content


@pytest.mark.asyncio
async def test_promote_creates_specs_dir_if_missing(client, tmp_path):
    """Promote creates ~/.myos/specs/ if it doesn't already exist."""
    draft_id = "dddd-4444-test"
    _write_draft(tmp_path, draft_id)

    specs_dir = tmp_path / "specs"
    assert not specs_dir.exists()

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "888"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    assert specs_dir.exists(), "specs/ directory must be created automatically"


@pytest.mark.asyncio
async def test_promote_returns_task_id(client, tmp_path):
    """Promote returns task_id from the created tracking task."""
    draft_id = "eeee-5555-test"
    _write_draft(tmp_path, draft_id)

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", new=AsyncMock(return_value={"task_id": "42"})),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "42"


@pytest.mark.asyncio
async def test_promote_404_when_draft_not_found(client, tmp_path):
    """Promote returns 404 when draft does not exist."""
    with patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"):
        resp = await client.post("/api/narrative/draft/no-such-draft/promote")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_creates_exec_update_labeled_task(client, tmp_path):
    """Promote calls create_task with exec-update-appropriate title and source."""
    draft_id = "ffff-6666-test"
    _write_draft(tmp_path, draft_id, audience="board")

    captured_calls = []

    async def _fake_create(body):
        captured_calls.append(body)
        return {"task_id": "77"}

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", side_effect=_fake_create),
    ):
        resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert resp.status_code == 200
    assert len(captured_calls) == 1
    task_body = captured_calls[0]
    assert task_body.source == "narrative"
    assert task_body.source_ref == draft_id
    assert "exec" in task_body.title.lower() or "board" in task_body.title.lower() or "update" in task_body.title.lower()


# ---------------------------------------------------------------------------
# BDD invariant: After promote, spec list includes new spec AND tracking task exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bdd_promote_then_spec_exists_and_task_created(client, tmp_path):
    """BDD invariant: POST /promote → spec file on disk AND tracking task recorded.

    Given a persisted narrative draft,
    When POST /api/narrative/draft/{id}/promote is called,
    Then a spec file at ~/.myos/specs/narrative-{id}.md exists with valid frontmatter,
    And a tracking task was created with source='narrative' and source_ref=draft_id.
    """
    draft_id = "bdd-invariant-test"
    _write_draft(tmp_path, draft_id, audience="exec", source_count=2)

    task_calls = []

    async def _record_task(body):
        task_calls.append(body)
        return {"task_id": "bdd-task-id"}

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.tasks.create_task", side_effect=_record_task),
    ):
        promote_resp = await client.post(f"/api/narrative/draft/{draft_id}/promote")

    assert promote_resp.status_code == 200
    body = promote_resp.json()

    # 1. Spec file exists with valid frontmatter
    spec_path = Path(body["spec_path"])
    assert spec_path.exists()
    content = spec_path.read_text()
    assert "status: spec" in content
    assert f"source: narrative-{draft_id}" in content

    # 2. Tracking task was created
    assert len(task_calls) == 1
    assert task_calls[0].source == "narrative"
    assert task_calls[0].source_ref == draft_id

    # 3. task_id is echoed back
    assert body["task_id"] == "bdd-task-id"


# ---------------------------------------------------------------------------
# →1658: Draft body must be a real summary, not a placeholder
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# →1659: Generator must feed real content (spec bodies, closed needles, git log)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_sources_enriches_spec_with_body_excerpt(tmp_path):
    """_gather_sources must include body_excerpt in spec meta (RED → GREEN in →1659).

    Currently the generator only passes the file path — the model never sees
    the spec content and cannot write a grounded update. After the fix,
    meta['body_excerpt'] must be a non-empty slice of the file body.
    """
    from routers.narrative import _gather_sources

    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    spec_file = spec_dir / "my-feature.md"
    spec_file.write_text(
        "# My Feature\n\n"
        "This spec covers the real implementation of the calendar default month feature. "
        "The goal is to show the current month by default when the user opens the calendar widget."
    )

    with (
        patch("services.atlassian.is_connected", return_value=False),
        patch("services.ostk.USER_SPECS_DIR", spec_dir),
    ):
        sources = await _gather_sources(window_days=7)

    spec_sources = [s for s in sources if s["kind"] == "spec"]
    assert spec_sources, "Expected at least one spec source"
    spec = spec_sources[0]
    assert "body_excerpt" in spec["meta"], (
        "Spec meta must include 'body_excerpt' — currently it only has 'path'. "
        "Fix: read spec file in _gather_sources and add body_excerpt to meta."
    )
    excerpt = spec["meta"]["body_excerpt"]
    assert len(excerpt) > 20, "body_excerpt must be non-empty"
    assert any(
        kw in excerpt for kw in ("My Feature", "calendar", "real implementation")
    ), f"body_excerpt must contain real spec content. Got: {excerpt!r}"


@pytest.mark.asyncio
async def test_gather_sources_includes_closed_needles(tmp_path):
    """_gather_sources must include recently closed needles as sources (RED → GREEN in →1659).

    The model needs concrete shipped work to write 'key progress' — closed
    needles are the ground truth. Currently none are fetched.
    """
    from routers.narrative import _gather_sources
    from unittest.mock import AsyncMock

    mock_closed_tasks = [
        {
            "id": "1001",
            "title": "Ship calendar default month feature",
            "status": "closed",
            "closed_at": "2026-05-22T10:00:00Z",
            "priority": "P1",
        },
    ]

    with (
        patch("services.atlassian.is_connected", return_value=False),
        patch("services.ostk.ostk.list_tasks", new=AsyncMock(return_value=mock_closed_tasks)),
    ):
        sources = await _gather_sources(window_days=7)

    needle_sources = [s for s in sources if s["kind"] == "closed_needle"]
    assert needle_sources, (
        "Expected closed_needle sources — currently _gather_sources never calls "
        "ostk.list_tasks(status='closed'). Fix: add that call in _gather_sources."
    )
    assert needle_sources[0]["title"] == "Ship calendar default month feature"


@pytest.mark.asyncio
async def test_gather_sources_includes_recent_git_commits():
    """_gather_sources must include recent git commits as sources (RED → GREEN in →1659).

    Commit messages are the most reliable signal of what shipped. The generator
    currently ignores git history entirely.
    """
    from routers.narrative import _gather_sources

    with patch("services.atlassian.is_connected", return_value=False):
        sources = await _gather_sources(window_days=30)

    commit_sources = [s for s in sources if s["kind"] == "git_commit"]
    assert commit_sources, (
        "Expected git_commit sources — currently _gather_sources never calls git log. "
        "Fix: add subprocess call to git log --since=N days ago --oneline."
    )


@pytest.mark.asyncio
async def test_build_markdown_prompt_includes_spec_excerpt():
    """_build_markdown_async must pass spec body excerpts in the model prompt (RED → GREEN in →1659).

    Currently the prompt only lists titles. After the fix, the model must see
    actual spec content so it can reference real work.
    """
    from routers.narrative import _build_markdown_async

    captured: list[dict] = []

    class _MockBlock:
        text = "Progress update with real content."

    class _MockResponse:
        content = [_MockBlock()]

    class _MockMessages:
        async def create(self, *, model, max_tokens, messages, system=None, **kwargs):
            if system:
                captured.append({"role": "system", "content": system})
            captured.extend(messages)
            return _MockResponse()

    class _MockClient:
        messages = _MockMessages()

    sources = [
        {
            "kind": "spec",
            "id": "myspec.md",
            "title": "Calendar Default Month",
            "meta": {
                "path": "/fake/spec.md",
                "body_excerpt": "This spec covers the calendar widget defaulting to the current month on open.",
            },
        }
    ]

    with patch("routers.narrative.get_ai_client", new=AsyncMock(return_value=_MockClient())):
        await _build_markdown_async("exec", 7, sources)

    assert captured, "Model must be called"
    all_text = " ".join(
        str(m.get("content", "")) for m in captured
        if isinstance(m.get("content"), str)
    )
    assert "calendar widget defaulting" in all_text or "body_excerpt" in all_text.lower(), (
        f"Spec body excerpt must appear in the model prompt. "
        f"Currently only titles are passed. Got prompt:\n{all_text[:500]}"
    )


@pytest.mark.asyncio
async def test_build_markdown_prompt_explicitly_forbids_brackets():
    """The prompt sent to the model must explicitly forbid [bracket] placeholders (RED → GREEN in →1659).

    Without explicit instruction the model hedges with '[X data unavailable]' etc.
    """
    from routers.narrative import _build_markdown_async

    captured: list[dict] = []

    class _MockBlock:
        text = "Clean update."

    class _MockResponse:
        content = [_MockBlock()]

    class _MockMessages:
        async def create(self, *, model, max_tokens, messages, system=None, **kwargs):
            if system:
                captured.append({"role": "system", "content": system if isinstance(system, str) else str(system)})
            captured.extend(messages)
            return _MockResponse()

    class _MockClient:
        messages = _MockMessages()

    sources = [{"kind": "spec", "id": "s.md", "title": "Spec One", "meta": {}}]

    with patch("routers.narrative.get_ai_client", new=AsyncMock(return_value=_MockClient())):
        await _build_markdown_async("exec", 7, sources)

    assert captured, "Model must be called"
    full_prompt = " ".join(
        str(m.get("content", "")) for m in captured
        if isinstance(m.get("content"), str)
    ).lower()
    assert "bracket" in full_prompt or "placeholder" in full_prompt or "no [" in full_prompt, (
        f"Prompt must explicitly forbid bracket placeholders. "
        f"Currently the prompt has no such instruction. Prompt text:\n{full_prompt[:600]}"
    )

@pytest.mark.asyncio
async def test_draft_body_is_non_trivial_when_sources_exist(client, tmp_path):
    """POST /narrative/draft returns markdown whose summary is non-trivial.

    RED for →1658: a real summary must be generated when an AI client is
    available. This test mocks _build_markdown_async to return real content
    so we confirm the full pipeline persists it — the actual AI call is
    tested separately via integration tests.

    Acceptance: markdown length > 100 chars AND does not contain the
    placeholder string '_(Draft generated from available sources'.
    """
    from unittest.mock import AsyncMock, patch

    real_summary = (
        "# Progress Update — Exec (7-day window)\n\n"
        "This week the team closed three needles, shipped the calendar default-month "
        "feature, and fixed agent name display in the WelcomeBack widget. "
        "The main blocker resolved was the claude_code_provider PROJECT_ROOT bug "
        "that prevented AI-generated summaries from rendering. "
        "Next up: merge the Progress Updates rename and validate the demo flow end-to-end."
    )

    with (
        patch("routers.narrative.NARRATIVES_DIR", tmp_path / "narratives"),
        patch("routers.narrative._build_markdown_async", new=AsyncMock(return_value=real_summary)),
        patch("routers.narrative._gather_sources", new=AsyncMock(return_value=[
            {"kind": "spec", "id": "foo.md", "title": "Foo", "meta": {}},
        ])),
    ):
        resp = await client.post("/api/narrative/draft", json={"audience": "exec", "window_days": 7})

    assert resp.status_code == 200
    body = resp.json()
    md = body.get("markdown", "")
    assert len(md) > 100, f"Summary too short ({len(md)} chars): {md!r}"
    assert "_(Draft generated from available sources" not in md, "Placeholder text must not appear"
    assert "Progress Update" in md, "Heading must say 'Progress Update'"

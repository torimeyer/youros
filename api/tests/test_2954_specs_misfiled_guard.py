"""→2954: the Specs page must never show documents that are not specs.

An investigation report (markdown with headings and tables but no spec
structure) sat in ~/.youros/drafts/ and the app listed it as a third
unfinished spec. The listing must recognize the shape every real spec is
guaranteed to have and report anything else as misfiled instead of
showing it as a spec.

Recognition rule: every file the spec pipeline creates opens with a
closed YAML frontmatter block that carries a ``status:`` key (drafts get
``status: draft`` from every creation endpoint; doc_promote() writes
``status: spec`` plus promoted_at/spec_id even when the draft had no
frontmatter). Reports and notes are plain markdown without that block.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.ostk import is_spec_shaped, ostk
from services.spec_templates import canonical_spec_template_body
import services.ostk as ostk_module


REPORT_MD = (
    "# Investigation: save hang in the dev proxy\n\n"
    "## What happened\n\n"
    "Saves hung with zero retries.\n\n"
    "| symptom | count |\n"
    "|---------|-------|\n"
    "| hang    | 3     |\n\n"
    "## Verdict\n\n"
    "The proxy swallows errors silently.\n"
)

DRAFT_MD = "---\ntitle: A real plan\nstatus: draft\n---\n\n" + canonical_spec_template_body()

PROMOTED_MD = (
    "---\n"
    "title: A promoted spec\n"
    "status: spec\n"
    "promoted_at: 2026-07-01T00:00:00Z\n"
    "spec_id: S099\n"
    "journey_id: jrn-deadbeef\n"
    "---\n\n"
    "## Problem\n\nSomething.\n\n"
    "## Acceptance criteria\n\n- [ ] one thing\n"
)


# --- Recognition rule -------------------------------------------------------


def test_is_spec_shaped_accepts_template_draft():
    """The exact text every draft endpoint writes counts as a spec."""
    assert is_spec_shaped(DRAFT_MD) is True


def test_is_spec_shaped_accepts_promoted_spec():
    """Promoted frontmatter (status/promoted_at/spec_id) counts as a spec."""
    assert is_spec_shaped(PROMOTED_MD) is True


def test_is_spec_shaped_accepts_headerless_promote_output():
    """doc_promote prepends a status-only block when the draft had no
    frontmatter; that output must still count as a spec."""
    text = (
        "---\nstatus: spec\npromoted_at: 2026-07-01T00:00:00Z\n"
        "spec_id: S100\njourney_id: jrn-cafef00d\n---\n\n- [x] done\n"
    )
    assert is_spec_shaped(text) is True


def test_is_spec_shaped_rejects_investigation_report():
    """Plain markdown with headings and tables is not a spec."""
    assert is_spec_shaped(REPORT_MD) is False


def test_is_spec_shaped_rejects_frontmatter_without_marker_keys():
    """A report that happens to carry author/date frontmatter is still
    not a spec: no status/spec_id/promoted_at key."""
    text = "---\nauthor: agent\ndate: 2026-07-19\n---\n\n# Findings\n"
    assert is_spec_shaped(text) is False


def test_is_spec_shaped_rejects_unclosed_frontmatter():
    """A leading horizontal rule with a stray 'status:' line later in the
    body is not a closed frontmatter block."""
    text = "---\n# Report\n\nThe status: confirmed broken.\n"
    assert is_spec_shaped(text) is False


# --- Listing exclusion (service level) --------------------------------------


@pytest.mark.asyncio
async def test_list_docs_excludes_misfiled_and_reports_them(caplog):
    """A non-spec markdown file in either user dir is excluded from the
    docs list, returned via misfiled_out, and logged as a warning."""
    specs_dir: Path = ostk_module.USER_SPECS_DIR
    drafts_dir: Path = ostk_module.USER_DRAFTS_DIR
    specs_dir.mkdir(parents=True, exist_ok=True)
    drafts_dir.mkdir(parents=True, exist_ok=True)

    (drafts_dir / "real-plan.md").write_text(DRAFT_MD)
    (drafts_dir / "proxy-report.md").write_text(REPORT_MD)
    (specs_dir / "stray-notes.md").write_text(REPORT_MD)

    misfiled: list[str] = []
    with caplog.at_level("WARNING", logger="services.ostk"):
        docs = await ostk.list_docs(misfiled_out=misfiled)

    filenames = {d["filename"] for d in docs}
    assert "real-plan.md" in filenames
    assert "proxy-report.md" not in filenames
    assert "stray-notes.md" not in filenames
    assert sorted(misfiled) == ["proxy-report.md", "stray-notes.md"]
    assert "proxy-report.md" in caplog.text
    assert "stray-notes.md" in caplog.text


@pytest.mark.asyncio
async def test_list_docs_default_call_still_excludes_misfiled():
    """Callers that do not pass misfiled_out (counts, recent) still never
    see the misfiled file."""
    drafts_dir: Path = ostk_module.USER_DRAFTS_DIR
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "proxy-report.md").write_text(REPORT_MD)

    docs = await ostk.list_docs()
    assert "proxy-report.md" not in {d["filename"] for d in docs}


# --- API surface ------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_specs_reports_misfiled_separately(client, monkeypatch):
    """GET /api/specs lists the real draft, keeps the report out of docs,
    and returns its filename under the separate misfiled field."""
    drafts_dir: Path = ostk_module.USER_DRAFTS_DIR
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "real-plan.md").write_text(DRAFT_MD)
    (drafts_dir / "proxy-report.md").write_text(REPORT_MD)
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs")
    assert resp.status_code == 200
    data = resp.json()
    filenames = {d["filename"] for d in data["docs"]}
    assert "real-plan.md" in filenames
    assert "proxy-report.md" not in filenames
    assert data["misfiled"] == ["proxy-report.md"]


@pytest.mark.asyncio
async def test_spec_counts_ignores_misfiled(client, monkeypatch):
    """The sidebar badge must not count a misfiled report as an
    unfinished spec (the original incident)."""
    drafts_dir: Path = ostk_module.USER_DRAFTS_DIR
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "real-plan.md").write_text(DRAFT_MD)
    (drafts_dir / "proxy-report.md").write_text(REPORT_MD)
    monkeypatch.setattr(ostk, "list_tasks", AsyncMock(return_value=[]))

    resp = await client.get("/api/specs/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["unfinished"] == 1

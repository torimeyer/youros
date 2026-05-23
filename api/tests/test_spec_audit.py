"""Tests for spec audit service (→1469).

FR-001: script exits 0 with report, exits 1 if any score < 5.
FR-002: services/spec_audit.py exposes audit logic.
FR-003: GET /api/specs/audit returns the JSON report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.spec_audit import (
    TEMPLATE_SECTIONS,
    audit_spec_file,
    audit_all_specs,
    compute_shipped,
    compute_husk_status,
    compute_stage,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_SPEC = """\
---
title: Full spec
status: spec
---

## Problem
Something is broken.

## Goals
Fix it.

## Non-goals
Don't break other things.

## Solution
Here is how.

## Edge cases
None.

## Success criteria
It works.

## Acceptance criteria
- [ ] FR-001: thing works

## Verification
Run the tests.

## USER FEEDBACK
Looks good.

## DECISION
Approved.
"""

HALF_SPEC = """\
---
title: Half spec
status: spec
---

## Problem
Something is broken.

## Goals
Fix it.

## Non-goals
Don't break other things.

## Solution
Here is how.

## Edge cases
None.
"""

EMPTY_SPEC = """\
---
title: Empty spec
status: spec
---

Just some prose here, no sections at all.
"""


@pytest.fixture
def spec_dir(tmp_path):
    (tmp_path / "full.md").write_text(FULL_SPEC)
    (tmp_path / "half.md").write_text(HALF_SPEC)
    (tmp_path / "empty.md").write_text(EMPTY_SPEC)
    return tmp_path


# ---------------------------------------------------------------------------
# Unit: TEMPLATE_SECTIONS constant
# ---------------------------------------------------------------------------


def test_template_sections_has_ten():
    assert len(TEMPLATE_SECTIONS) == 10


# ---------------------------------------------------------------------------
# Unit: audit_spec_file
# ---------------------------------------------------------------------------


def test_audit_full_spec(tmp_path):
    f = tmp_path / "full.md"
    f.write_text(FULL_SPEC)
    result = audit_spec_file(f)
    assert result["score"] == 10
    assert result["sections_missing"] == []
    assert len(result["sections_present"]) == 10


def test_audit_half_spec(tmp_path):
    f = tmp_path / "half.md"
    f.write_text(HALF_SPEC)
    result = audit_spec_file(f)
    assert result["score"] == 5
    assert len(result["sections_present"]) == 5
    assert len(result["sections_missing"]) == 5


def test_audit_empty_spec(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text(EMPTY_SPEC)
    result = audit_spec_file(f)
    assert result["score"] == 0
    assert result["sections_present"] == []
    assert len(result["sections_missing"]) == 10


def test_audit_result_shape(tmp_path):
    f = tmp_path / "full.md"
    f.write_text(FULL_SPEC)
    result = audit_spec_file(f)
    assert "path" in result
    assert "frontmatter" in result
    assert "sections_present" in result
    assert "sections_missing" in result
    assert "score" in result


def test_audit_frontmatter_parsed(tmp_path):
    f = tmp_path / "full.md"
    f.write_text(FULL_SPEC)
    result = audit_spec_file(f)
    assert result["frontmatter"]["title"] == "Full spec"
    assert result["frontmatter"]["status"] == "spec"


def test_audit_missing_frontmatter(tmp_path):
    f = tmp_path / "nofm.md"
    f.write_text("## Problem\nHello.\n")
    result = audit_spec_file(f)
    assert result["frontmatter"] == {}
    assert result["score"] >= 1


# ---------------------------------------------------------------------------
# Unit: audit_all_specs
# ---------------------------------------------------------------------------


def test_audit_all_returns_summary(spec_dir):
    report = audit_all_specs(spec_dirs=[spec_dir])
    assert "specs" in report
    assert "summary" in report
    summary = report["summary"]
    assert summary["total"] == 3
    assert "score_avg" in summary
    assert "fully_templated" in summary
    assert "partial" in summary


def test_audit_all_counts_fully_templated(spec_dir):
    report = audit_all_specs(spec_dirs=[spec_dir])
    assert report["summary"]["fully_templated"] == 1  # only full.md


def test_audit_all_score_avg(spec_dir):
    report = audit_all_specs(spec_dirs=[spec_dir])
    # full=10, half=5, empty=0 → avg = 5.0
    assert abs(report["summary"]["score_avg"] - 5.0) < 0.01


def test_audit_all_empty_dir(tmp_path):
    report = audit_all_specs(spec_dirs=[tmp_path])
    assert report["summary"]["total"] == 0


def test_audit_all_multiple_dirs(tmp_path):
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "a.md").write_text(FULL_SPEC)
    (d2 / "b.md").write_text(EMPTY_SPEC)
    report = audit_all_specs(spec_dirs=[d1, d2])
    assert report["summary"]["total"] == 2


# ---------------------------------------------------------------------------
# Integration: GET /api/specs/audit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_specs_audit_returns_200(tmp_path):
    (tmp_path / "a.md").write_text(FULL_SPEC)
    with patch("routers.specs.audit_all_specs") as mock_audit:
        mock_audit.return_value = {
            "specs": [],
            "summary": {"total": 0, "fully_templated": 0, "partial": 0, "score_avg": 0.0},
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/specs/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "specs" in body
    assert "summary" in body


# ---------------------------------------------------------------------------
# Path resolution: docs/spec resolved against repo root, not CWD (→1498)
# ---------------------------------------------------------------------------


def test_docs_spec_resolved_against_repo_root(tmp_path, monkeypatch):
    """audit_all_specs must find docs/spec even when CWD is /tmp/."""
    # Build a fake repo tree under tmp_path.
    docs_spec = tmp_path / "docs" / "spec"
    docs_spec.mkdir(parents=True)
    (docs_spec / "a.md").write_text(FULL_SPEC)

    # Point MYOS_REPO_ROOT at our fake repo; CWD stays wherever pytest set it.
    monkeypatch.setenv("MYOS_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir("/tmp")

    # Re-import to pick up the new env var (module-level _REPO_ROOT is set at import).
    import importlib
    import services.spec_audit as sa
    importlib.reload(sa)

    report = sa.audit_all_specs(spec_dirs=[sa._REPO_ROOT / "docs" / "spec"])
    assert report["summary"]["total"] >= 1
    paths = [r["path"] for r in report["specs"]]
    assert any("docs/spec" in p or "docs" + "/" + "spec" in p for p in paths), paths


def test_myos_specs_resolved_from_home(tmp_path, monkeypatch):
    """~/.myos/specs is always resolved via Path.home(), not CWD."""
    fake_home = tmp_path / "home"
    myos_specs = fake_home / ".myos" / "specs"
    myos_specs.mkdir(parents=True)
    (myos_specs / "b.md").write_text(FULL_SPEC)

    monkeypatch.setenv("HOME", str(fake_home))

    from pathlib import Path as _Path
    report = audit_all_specs(spec_dirs=[_Path.home() / ".myos" / "specs"])
    assert report["summary"]["total"] >= 1
    paths = [r["path"] for r in report["specs"]]
    assert any(".myos" in p for p in paths), paths


def test_audit_includes_both_dirs_with_env_var(tmp_path, monkeypatch):
    """With MYOS_REPO_ROOT set, default audit_all_specs picks up both dirs."""
    # Fake repo docs/spec.
    docs_spec = tmp_path / "docs" / "spec"
    docs_spec.mkdir(parents=True)
    (docs_spec / "x.md").write_text(FULL_SPEC)

    # Fake ~/.myos/specs using HOME override.
    fake_home = tmp_path / "home"
    myos_specs = fake_home / ".myos" / "specs"
    myos_specs.mkdir(parents=True)
    (myos_specs / "y.md").write_text(HALF_SPEC)

    monkeypatch.setenv("MYOS_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir("/tmp")

    import importlib
    import services.spec_audit as sa
    importlib.reload(sa)

    report = sa.audit_all_specs()
    assert report["summary"]["total"] == 2, report["summary"]
    paths = [r["path"] for r in report["specs"]]
    assert any("docs" in p for p in paths), paths
    assert any(".myos" in p for p in paths), paths


@pytest.mark.anyio
async def test_get_specs_audit_shape(tmp_path):
    mock_report = {
        "specs": [
            {
                "path": str(tmp_path / "a.md"),
                "frontmatter": {"title": "A"},
                "sections_present": ["Problem"],
                "sections_missing": ["Goals", "Non-goals", "Solution",
                                     "Edge cases", "Success criteria",
                                     "Acceptance criteria", "Verification",
                                     "USER FEEDBACK", "DECISION"],
                "score": 1,
            }
        ],
        "summary": {"total": 1, "fully_templated": 0, "partial": 1, "score_avg": 1.0},
    }
    with patch("routers.specs.audit_all_specs", return_value=mock_report):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/specs/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total"] == 1
    assert data["specs"][0]["score"] == 1


# ===========================================================================
# Tests for →1512: compute_shipped / compute_husk_status / compute_stage
# ===========================================================================

SPEC_WITH_FILES_AND_NEEDLES = """\
---
title: Shipped spec
status: spec
---

## Solution

References: `api/services/spec_audit.py`, `app/src/pages/Specs.tsx`

→1234 fixed the backend.
→5678 fixed the frontend.
"""

SPEC_NO_REFS = """\
---
title: Simple spec
status: spec
---

## Problem
Something needed fixing.

## Acceptance criteria
- [x] FR-001: thing works
- [x] FR-002: other thing works
"""

PLACEHOLDER_AC_SPEC = """\
---
title: Husk placeholder
status: draft
---

## Acceptance criteria
- [ ] (replace with your first criterion)
- [ ] (replace with your second criterion)
"""

EMPTY_DRAFT = """\
---
title: Empty
status: draft
---
"""

REAL_DRAFT = """\
---
title: Real draft
status: draft
---

## Problem
Users can't do the thing.

## Acceptance criteria
- [ ] FR-001: Users can do the thing
- [ ] FR-002: Edge case handled
"""


# ---------------------------------------------------------------------------
# compute_shipped
# ---------------------------------------------------------------------------


def test_compute_shipped_no_refs(tmp_path):
    f = tmp_path / "no_refs.md"
    f.write_text(SPEC_NO_REFS)
    result = compute_shipped(f, repo_root=tmp_path, needle_statuses={})
    assert hasattr(result, "is_shipped")
    assert hasattr(result, "missing_files")
    assert hasattr(result, "open_needles")


def test_compute_shipped_all_files_exist_no_needles(tmp_path):
    # Create referenced files
    (tmp_path / "api" / "services").mkdir(parents=True)
    (tmp_path / "api" / "services" / "spec_audit.py").write_text("")
    (tmp_path / "app" / "src" / "pages").mkdir(parents=True)
    (tmp_path / "app" / "src" / "pages" / "Specs.tsx").write_text("")
    f = tmp_path / "spec.md"
    f.write_text(SPEC_WITH_FILES_AND_NEEDLES)
    result = compute_shipped(
        f,
        repo_root=tmp_path,
        needle_statuses={"1234": "closed", "5678": "closed"},
    )
    assert result.is_shipped is True
    assert result.missing_files == []
    assert result.open_needles == []


def test_compute_shipped_missing_file(tmp_path):
    f = tmp_path / "spec.md"
    f.write_text(SPEC_WITH_FILES_AND_NEEDLES)
    result = compute_shipped(
        f,
        repo_root=tmp_path,
        needle_statuses={"1234": "closed", "5678": "closed"},
    )
    assert result.is_shipped is False
    assert len(result.missing_files) > 0


def test_compute_shipped_open_needle(tmp_path):
    (tmp_path / "api" / "services").mkdir(parents=True)
    (tmp_path / "api" / "services" / "spec_audit.py").write_text("")
    (tmp_path / "app" / "src" / "pages").mkdir(parents=True)
    (tmp_path / "app" / "src" / "pages" / "Specs.tsx").write_text("")
    f = tmp_path / "spec.md"
    f.write_text(SPEC_WITH_FILES_AND_NEEDLES)
    result = compute_shipped(
        f,
        repo_root=tmp_path,
        needle_statuses={"1234": "closed", "5678": "open"},
    )
    assert result.is_shipped is False
    assert "5678" in result.open_needles


def test_compute_shipped_no_refs_is_shipped(tmp_path):
    f = tmp_path / "spec.md"
    f.write_text(SPEC_NO_REFS)
    result = compute_shipped(f, repo_root=tmp_path, needle_statuses={})
    assert result.is_shipped is True


# ---------------------------------------------------------------------------
# compute_husk_status
# ---------------------------------------------------------------------------


def test_compute_husk_placeholder_acs(tmp_path):
    f = tmp_path / "husk.md"
    f.write_text(PLACEHOLDER_AC_SPEC)
    result = compute_husk_status(f)
    assert result.is_husk is True
    assert result.reason is not None and len(result.reason) > 0


def test_compute_husk_empty_draft(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text(EMPTY_DRAFT)
    result = compute_husk_status(f)
    assert result.is_husk is True


def test_compute_husk_real_draft_not_husk(tmp_path):
    f = tmp_path / "real.md"
    f.write_text(REAL_DRAFT)
    result = compute_husk_status(f)
    assert result.is_husk is False


def test_compute_husk_result_shape(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text(EMPTY_DRAFT)
    result = compute_husk_status(f)
    assert hasattr(result, "is_husk")
    assert hasattr(result, "reason")


# ---------------------------------------------------------------------------
# compute_stage  (→1561: 3-stage model — draft / ready / in_progress)
# ---------------------------------------------------------------------------


def test_compute_stage_draft_from_status():
    spec = {"path": "docs/draft/foo.md", "status": "draft"}
    result = compute_stage(spec)
    assert result == "draft"


def test_compute_stage_spec_status_with_open_needle_body_refs_is_ready():
    """status='spec' + open body needle refs → 'ready' (body refs are not a building signal).

    Renamed from test_compute_stage_in_progress — that test asserted the →1617 bug.
    """
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "~/.myos/specs/foo.md", "status": "spec"}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=["1234"])
    husk = HuskResult(is_husk=False, reason="")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "ready"


def test_compute_stage_ready():
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "~/.myos/specs/foo.md", "status": "spec"}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=[])
    husk = HuskResult(is_husk=False, reason="")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "ready"


def test_compute_stage_husk_is_draft():
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "docs/draft/foo.md", "status": "draft"}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=[])
    husk = HuskResult(is_husk=True, reason="no content")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "draft"


# ---------------------------------------------------------------------------
# compute_stage — bug fix →1617: open needle body refs don't signal in_progress
# ---------------------------------------------------------------------------


def test_compute_stage_ready_spec_with_open_needle_body_refs():
    """Bug →1617: status='spec' + open body needle refs → 'ready', NOT 'in_progress'.

    Open needle mentions in the body are future-work descriptions present in
    every healthy ready spec. They are NOT a building signal.
    """
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "~/.myos/specs/foo.md", "status": "spec", "task_ids": []}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=["1234"])
    husk = HuskResult(is_husk=False, reason="")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "ready"


def test_compute_stage_in_progress_from_frontmatter_status():
    """Bug →1617: status='in-progress' in frontmatter → 'in_progress', not needle body refs."""
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "~/.myos/specs/foo.md", "status": "in-progress"}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=[])
    husk = HuskResult(is_husk=False, reason="")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "in_progress"


def test_compute_stage_building_from_frontmatter_status():
    """Bug →1617: status='building' in frontmatter → 'in_progress'."""
    from services.spec_audit import ShippedResult, HuskResult
    spec = {"path": "~/.myos/specs/foo.md", "status": "building"}
    shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=[])
    husk = HuskResult(is_husk=False, reason="")
    assert compute_stage(spec, husk=husk, shipped=shipped) == "in_progress"


def test_silent_auto_archive_on_completion(tmp_path):
    """Specs meeting the shipped condition have is_shipped=True — auto-archive
    logic in list_specs moves them off the board. Verify compute_shipped
    returns is_shipped=True when all needles closed and all files exist."""
    from services.spec_audit import ShippedResult, compute_shipped, HuskResult

    # Create a real file to reference
    ref_file = tmp_path / "app" / "src" / "pages" / "Specs.tsx"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("// real file")

    spec_text = f"""\
---
status: spec
---
# Done spec

See app/src/pages/Specs.tsx for the implementation.

References: →9001
"""
    spec_file = tmp_path / "done.md"
    spec_file.write_text(spec_text)

    needle_statuses = {"9001": "closed"}
    result = compute_shipped(spec_file, repo_root=tmp_path, needle_statuses=needle_statuses)
    assert result.is_shipped is True
    assert result.open_needles == []

    # compute_stage never sees a shipped spec in the 3-stage model;
    # the list endpoint archives it first. But if somehow called,
    # it returns "ready" (no open needles, not a husk, not draft).
    husk = HuskResult(is_husk=False, reason="")
    stage = compute_stage({"status": "spec"}, husk=husk, shipped=result)
    assert stage == "ready"

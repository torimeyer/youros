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

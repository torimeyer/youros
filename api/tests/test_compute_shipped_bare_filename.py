"""Tests for →2163 fix: compute_shipped ignores bare filenames (no path separator).

Root cause: _FILE_REF_RE matches bare names like `SpecReview.tsx` as well as full
relative paths like `app/src/components/SpecReview.tsx`. The existence check runs
`(repo_root / ref).exists()`, which for a bare name resolves to the repo root.
`SpecReview.tsx` doesn't exist at the root, so it lands in missing_files and
is_shipped is False even though all features were merged.

Fix: tighten the plausibility filter to require at least one `/` in the ref,
matching the original intent ("plausible relative paths").
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.spec_audit import compute_shipped


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Minimal repo tree that mirrors the GitHub spec's referenced files."""
    (tmp_path / "app" / "src" / "components").mkdir(parents=True)
    (tmp_path / "app" / "src" / "components" / "SpecReview.tsx").write_text("// exists")
    (tmp_path / "app" / "src" / "pages").mkdir(parents=True)
    (tmp_path / "app" / "src" / "pages" / "GitHub.tsx").write_text("// exists")
    (tmp_path / "api" / "routers").mkdir(parents=True)
    (tmp_path / "api" / "routers" / "github.py").write_text("# exists")
    (tmp_path / "api" / "services").mkdir(parents=True)
    (tmp_path / "api" / "services" / "github.py").write_text("# exists")
    return tmp_path


def _spec_with_bare_ref(path: Path) -> None:
    """Write a spec that mentions SpecReview.tsx as a bare name AND as a full path."""
    path.write_text(
        "---\nstatus: spec\ntitle: Test\n---\n\n"
        "## Problem\nGitHub integration is incomplete.\n\n"
        "## Goals\n- Link specs to PRs.\n\n"
        "## Non-goals\n- No real-time sync.\n\n"
        "## Solution\nFrontend: `SpecReview.tsx` reads the `github_pr` field.\n"
        "Full path also present: `app/src/components/SpecReview.tsx`.\n"
        "Page: `app/src/pages/GitHub.tsx`.\n"
        "Backend: `api/routers/github.py`, `api/services/github.py`.\n"
        "Tracked by →2163.\n\n"
        "## Success criteria\n- Badge shows PR state.\n\n"
        "## Acceptance criteria\n- [ ] Badge is read-only.\n"
    )


def test_bare_filename_does_not_block_shipped(repo: Path, tmp_path: Path) -> None:
    """A bare filename like SpecReview.tsx must not appear in missing_files.

    The file exists in the repo tree at app/src/components/SpecReview.tsx.
    Without the fix, compute_shipped checks (repo_root / 'SpecReview.tsx') which
    doesn't exist and incorrectly marks the spec as not shipped.
    """
    spec = tmp_path / "spec.md"
    _spec_with_bare_ref(spec)
    # →2487: is_shipped requires at least one closed needle; added →2163 to the spec.
    result = compute_shipped(spec, repo_root=repo, needle_statuses={"2163": "closed"})
    assert "SpecReview.tsx" not in result.missing_files, (
        f"Bare filename 'SpecReview.tsx' must not appear in missing_files. "
        f"Got: {result.missing_files}"
    )
    assert result.is_shipped, (
        f"Spec should be shipped when all path-qualified refs exist. "
        f"missing_files={result.missing_files}"
    )


def test_missing_full_path_still_blocks_shipped(repo: Path, tmp_path: Path) -> None:
    """A full relative path that truly doesn't exist must still block shipping."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nstatus: spec\ntitle: Test\n---\n\n"
        "## Solution\nSee `api/services/new_nonexistent_module.py`.\n\n"
        "## Acceptance criteria\n- [ ] Works.\n"
    )
    result = compute_shipped(spec, repo_root=repo, needle_statuses={})
    assert "api/services/new_nonexistent_module.py" in result.missing_files
    assert not result.is_shipped


def test_open_needle_blocks_shipped(repo: Path, tmp_path: Path) -> None:
    """Open needle references must still block shipping (existing behaviour)."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nstatus: spec\ntitle: Test\n---\n\n"
        "## Solution\nTracked by →2163.\n\n"
        "## Acceptance criteria\n- [ ] Done.\n"
    )
    result = compute_shipped(
        spec, repo_root=repo, needle_statuses={"2163": "open"}
    )
    assert "2163" in result.open_needles
    assert not result.is_shipped


def test_closed_needle_does_not_block_shipped(repo: Path, tmp_path: Path) -> None:
    """Closed needle references must not block shipping."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nstatus: spec\ntitle: Test\n---\n\n"
        "## Solution\nTracked by →2163.\n\n"
        "## Acceptance criteria\n- [ ] Done.\n"
    )
    result = compute_shipped(
        spec, repo_root=repo, needle_statuses={"2163": "closed"}
    )
    assert result.open_needles == []
    assert result.is_shipped

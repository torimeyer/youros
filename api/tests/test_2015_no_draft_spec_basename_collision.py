"""Guard against draft/spec basename collisions (→2015).

Background: `docs/spec/` is now tracked in git, so promoted specs are
version-controlled directly. Before this, the workaround was to mirror
locked decisions into the git-tracked `docs/draft/` copy, which produced
confusing duplicate files: a draft and a spec for the same title, in
different statuses.

This lint fails when the SAME basename exists in both `docs/draft/` and
`docs/spec/`. When a draft is promoted, the draft copy should be removed
(its content now lives in the tracked spec), so the two directories should
never share a filename.

This is a lint, not a redesign of the docs pipeline. It walks the real
repo directories and also unit-tests the collision-detection helper.
"""
from __future__ import annotations

from pathlib import Path

# api/tests/<this file> -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DRAFT_DIR = REPO_ROOT / "docs" / "draft"
SPEC_DIR = REPO_ROOT / "docs" / "spec"


def _basenames(directory: Path) -> set[str]:
    """Return the set of markdown basenames directly under `directory`."""
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.glob("*.md") if p.is_file()}


def find_draft_spec_collisions(draft_dir: Path, spec_dir: Path) -> set[str]:
    """Return basenames that exist in BOTH draft_dir and spec_dir."""
    return _basenames(draft_dir) & _basenames(spec_dir)


def test_no_draft_spec_basename_collision_in_repo():
    """No file may exist in both docs/draft/ and docs/spec/ with the same name.

    A collision means a promoted spec still has a stale draft mirror. Remove
    the draft (the spec is the source of truth) to clear it.
    """
    collisions = find_draft_spec_collisions(DRAFT_DIR, SPEC_DIR)
    assert not collisions, (
        "These basenames exist in BOTH docs/draft/ and docs/spec/. "
        "A promoted spec should not keep a draft mirror; delete the draft "
        f"copy so the spec is the single source of truth: {sorted(collisions)}"
    )


def test_collision_helper_detects_shared_basename(tmp_path):
    """The helper flags a shared basename across the two directories."""
    draft = tmp_path / "draft"
    spec = tmp_path / "spec"
    draft.mkdir(exist_ok=True)
    spec.mkdir(exist_ok=True)

    (draft / "shared-title.md").write_text("draft fragment\n")
    (spec / "shared-title.md").write_text("full spec\n")
    (draft / "draft-only.md").write_text("only a draft\n")
    (spec / "spec-only.md").write_text("only a spec\n")

    collisions = find_draft_spec_collisions(draft, spec)
    assert collisions == {"shared-title.md"}


def test_collision_helper_clean_when_no_overlap(tmp_path):
    """The helper reports no collisions when basenames do not overlap."""
    draft = tmp_path / "draft"
    spec = tmp_path / "spec"
    draft.mkdir(exist_ok=True)
    spec.mkdir(exist_ok=True)

    (draft / "draft-only.md").write_text("only a draft\n")
    (spec / "spec-only.md").write_text("only a spec\n")

    assert find_draft_spec_collisions(draft, spec) == set()


def test_collision_helper_tolerates_missing_dirs(tmp_path):
    """A missing draft or spec directory yields no collisions, not an error."""
    spec = tmp_path / "spec"
    spec.mkdir(exist_ok=True)
    (spec / "spec-only.md").write_text("only a spec\n")

    # draft dir does not exist
    assert find_draft_spec_collisions(tmp_path / "draft", spec) == set()

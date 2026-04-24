"""Tests for the pre-merge gate helpers.

Covers:
- file-diff-to-suite heuristic (`infer_suite`)
- status-file writing (`write_status`)

See docs/spawn_premerge_gate.md for the design note.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The helper lives outside the api/ package, under scripts/lib/. Add
# the scripts/ dir to sys.path so we can import it as lib.worktree_suite.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from lib.worktree_suite import (  # noqa: E402
    infer_suite,
    write_status,
    status_path,
)


# ---------- infer_suite ----------------------------------------------------


def test_infer_suite_backend_only():
    assert infer_suite(["api/routers/drive.py", "api/tests/test_drive.py"]) == "backend"


def test_infer_suite_frontend_only():
    assert infer_suite(["app/src/pages/Tasks.tsx", "app/src/lib/api.ts"]) == "frontend"


def test_infer_suite_both():
    assert (
        infer_suite(["api/routers/gmail.py", "app/src/pages/Gmail.tsx"]) == "both"
    )


def test_infer_suite_none_for_docs_only():
    assert infer_suite(["docs/README.md", "README.md", "CHANGELOG.md"]) == "none"


def test_infer_suite_none_for_config_only():
    # Root-level configs should not trigger either suite.
    assert infer_suite([".gitignore", "pyproject.toml", "package.json"]) == "none"


def test_infer_suite_none_for_scripts_only():
    # Shell script changes do not trigger vitest or pytest.
    assert infer_suite(["scripts/worktree-gate.sh"]) == "none"


def test_infer_suite_empty_input():
    assert infer_suite([]) == "none"


def test_infer_suite_ignores_blank_lines():
    assert infer_suite(["", "  ", "api/foo.py"]) == "backend"


def test_infer_suite_non_code_under_api_does_not_trigger():
    # A markdown file under api/ should not trigger pytest.
    assert infer_suite(["api/NOTES.md"]) == "none"


def test_infer_suite_frontend_html():
    assert infer_suite(["app/index.html"]) == "frontend"


# ---------- write_status ---------------------------------------------------


def test_write_status_ready(tmp_path: Path):
    out = write_status(
        repo_root=tmp_path,
        branch="worktree-agent-abc",
        path="/abs/worktree/agent-abc",
        status="ready",
        suite="backend",
        ran_at=datetime(2026, 4, 23, 18, 0, 0, tzinfo=timezone.utc),
    )
    assert out == status_path(tmp_path, "worktree-agent-abc")
    assert out.exists()
    data = json.loads(out.read_text())
    assert data == {
        "branch": "worktree-agent-abc",
        "path": "/abs/worktree/agent-abc",
        "status": "ready",
        "suite": "backend",
        "failing_tests": [],
        "ran_at": "2026-04-23T18:00:00Z",
    }


def test_write_status_parked_records_failing_tests(tmp_path: Path):
    out = write_status(
        repo_root=tmp_path,
        branch="worktree-agent-xyz",
        path="/abs/worktree/agent-xyz",
        status="parked",
        suite="both",
        failing_tests=["api/tests/test_foo.py::test_bar", "app/src/x.test.tsx"],
    )
    data = json.loads(out.read_text())
    assert data["status"] == "parked"
    assert data["failing_tests"] == [
        "api/tests/test_foo.py::test_bar",
        "app/src/x.test.tsx",
    ]


def test_write_status_sanitises_branch_with_slashes(tmp_path: Path):
    out = write_status(
        repo_root=tmp_path,
        branch="user/foo/bar",
        path="/x",
        status="ready",
        suite="none",
    )
    # No subdirs in the status dir; slashes become underscores.
    assert out.parent == tmp_path / ".ostk" / "worktree_status"
    assert out.name == "user_foo_bar.json"


def test_write_status_rejects_bad_status(tmp_path: Path):
    with pytest.raises(ValueError):
        write_status(
            repo_root=tmp_path,
            branch="b",
            path="/x",
            status="maybe",  # type: ignore[arg-type]
            suite="none",
        )


def test_write_status_rejects_bad_suite(tmp_path: Path):
    with pytest.raises(ValueError):
        write_status(
            repo_root=tmp_path,
            branch="b",
            path="/x",
            status="ready",
            suite="sideways",  # type: ignore[arg-type]
        )


def test_write_status_creates_parent_dir(tmp_path: Path):
    # Parent dir does not exist yet.
    assert not (tmp_path / ".ostk").exists()
    write_status(
        repo_root=tmp_path,
        branch="b",
        path="/x",
        status="ready",
        suite="none",
    )
    assert (tmp_path / ".ostk" / "worktree_status").is_dir()

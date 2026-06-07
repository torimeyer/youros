"""Tests for spec-auto-status Phase 2a: wrapper-launched claim path (→1424).

RED phase: written before implementation exists.

Coverage:
- Backend: POST /api/specs/{path}/claim accepts source=wrapper
- CLI: scripts/mychat parses --spec <slug> and --with gemini|claude
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Backend tests: claim endpoint accepts source=wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_spec_source_wrapper(client, tmp_path, monkeypatch):
    """POST /api/specs/{path}/claim with source=wrapper records a wrapper claim."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

    spec_file = tmp_path / "docs" / "spec" / "test-wrapper-claim.md"
    spec_file.write_text(
        "---\ntitle: test wrapper claim\nstatus: spec\n---\n\n- [ ] do a thing\n"
    )

    monkeypatch.setattr(ostk_module.ostk, "_run", AsyncMock(return_value=[]))

    resp = await client.post(
        "/api/specs/docs/spec/test-wrapper-claim.md/claim",
        json={"agent": "terminal-pid-12345", "source": "wrapper"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["claim"]["source"] == "wrapper"
    assert data["claim"]["agent"] == "terminal-pid-12345"
    assert "started_at" in data["claim"]


@pytest.mark.asyncio
async def test_claim_spec_wrapper_returns_task_ids(client, tmp_path, monkeypatch):
    """POST /claim returns task_ids list (empty is fine for a fresh spec)."""
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

    spec_file = tmp_path / "docs" / "spec" / "test-claim-taskids.md"
    spec_file.write_text(
        "---\ntitle: claim taskids\nstatus: spec\n---\n\n- [ ] step one\n"
    )

    async def fake_run(*args, **kwargs):
        return []

    monkeypatch.setattr(ostk_module.ostk, "_run", fake_run)

    resp = await client.post(
        "/api/specs/docs/spec/test-claim-taskids.md/claim",
        json={"agent": "wrapper-agent", "source": "wrapper"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "task_ids" in data
    assert isinstance(data["task_ids"], list)


@pytest.mark.asyncio
async def test_claim_spec_missing_returns_404(client, tmp_path, monkeypatch):
    """POST /claim for a non-existent spec returns 404."""
    from routers import specs as specs_router

    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

    resp = await client.post(
        "/api/specs/docs/spec/does-not-exist.md/claim",
        json={"agent": "wrapper", "source": "wrapper"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CLI tests: scripts/mychat argument parsing
# ---------------------------------------------------------------------------

MYCHAT_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "mychat"


def test_mychat_script_exists():
    """scripts/mychat must exist."""
    assert MYCHAT_SCRIPT.exists(), f"scripts/mychat not found at {MYCHAT_SCRIPT}"


def test_mychat_script_is_executable():
    """scripts/mychat must be executable."""
    assert os.access(MYCHAT_SCRIPT, os.X_OK), "scripts/mychat is not executable"


def test_mychat_help_exits_cleanly():
    """scripts/mychat --help exits 0 and mentions --spec."""
    result = subprocess.run(
        [sys.executable, str(MYCHAT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "--spec" in combined, f"--spec not in help output: {combined!r}"


def test_mychat_parse_spec_and_with(tmp_path, monkeypatch):
    """scripts/mychat --spec <slug> --with gemini resolves slug and picks gemini CLI.

    The script is tested in dry-run mode (MYCHAT_DRY_RUN=1) so it parses args
    and resolves paths without making network calls or exec'ing a real CLI.
    """
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    spec_file = specs_dir / "my-feature.md"
    spec_file.write_text("---\ntitle: my feature\nstatus: spec\n---\n")

    env = os.environ.copy()
    env["MYCHAT_DRY_RUN"] = "1"
    env["MYCHAT_SPECS_DIR"] = str(specs_dir)
    env["MYCHAT_API_BASE"] = "http://127.0.0.1:9999"  # unreachable; dry-run skips POST

    result = subprocess.run(
        [sys.executable, str(MYCHAT_SCRIPT), "--spec", "my-feature", "--with", "gemini"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    out = result.stdout
    assert "my-feature" in out, f"slug not echoed: {out!r}"
    assert "gemini" in out, f"cli choice not echoed: {out!r}"


def test_mychat_parse_spec_default_claude(tmp_path, monkeypatch):
    """scripts/mychat --spec <slug> defaults to claude if --with is omitted."""
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(exist_ok=True)
    (specs_dir / "default-cli.md").write_text("---\ntitle: default cli\nstatus: spec\n---\n")

    env = os.environ.copy()
    env["MYCHAT_DRY_RUN"] = "1"
    env["MYCHAT_SPECS_DIR"] = str(specs_dir)
    env["MYCHAT_API_BASE"] = "http://127.0.0.1:9999"

    result = subprocess.run(
        [sys.executable, str(MYCHAT_SCRIPT), "--spec", "default-cli"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "claude" in result.stdout, f"expected claude default: {result.stdout!r}"


def test_mychat_slug_not_found_exits_nonzero(tmp_path):
    """scripts/mychat --spec nonexistent exits non-zero with a clear message."""
    env = os.environ.copy()
    env["MYCHAT_DRY_RUN"] = "1"
    env["MYCHAT_SPECS_DIR"] = str(tmp_path)
    env["MYCHAT_API_BASE"] = "http://127.0.0.1:9999"

    result = subprocess.run(
        [sys.executable, str(MYCHAT_SCRIPT), "--spec", "no-such-slug"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode != 0, "expected non-zero exit for missing spec"
    combined = result.stdout + result.stderr
    assert "no-such-slug" in combined or "not found" in combined.lower(), (
        f"expected slug name in error: {combined!r}"
    )

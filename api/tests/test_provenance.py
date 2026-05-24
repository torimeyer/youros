"""Tests for the provenance sidecar service and /files/provenance endpoint."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit: services.provenance
# ---------------------------------------------------------------------------


def test_write_then_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "report.md"
        target.write_text("# Report\n\nsome content")

        from services.provenance import read_sidecar, write_sidecar

        write_sidecar(target, agent_name="Builder", task_id="42", prompt_summary="Write a report")
        result = read_sidecar(target)

        assert result is not None
        assert result["agent_name"] == "Builder"
        assert result["task_id"] == "42"
        assert result["prompt_summary"] == "Write a report"
        assert "created_at" in result

        sidecar_path = Path(tmpdir) / "report.md.provenance.json"
        assert sidecar_path.is_file()


def test_read_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        from services.provenance import read_sidecar

        result = read_sidecar(Path(tmpdir) / "nonexistent.md")
        assert result is None


def test_write_sidecar_survives_bad_path(caplog):
    """write_sidecar must not raise when the directory is unwritable."""
    import logging

    from services.provenance import write_sidecar

    with caplog.at_level(logging.WARNING, logger="services.provenance"):
        write_sidecar("/no/such/dir/file.md", agent_name="x")

    assert any("provenance" in r.message for r in caplog.records)


def test_prompt_summary_is_truncated():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "out.md"
        target.write_text("x")

        from services.provenance import read_sidecar, write_sidecar

        long_prompt = "a" * 500
        write_sidecar(target, agent_name="Agent", prompt_summary=long_prompt)
        result = read_sidecar(target)
        assert result is not None
        assert len(result["prompt_summary"]) <= 200


# ---------------------------------------------------------------------------
# Integration: GET /files/provenance endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_files_provenance_endpoint_returns_dict(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        target = tmppath / "agent-output.md"
        target.write_text("# Output\n\ncontent here")

        sidecar = tmppath / "agent-output.md.provenance.json"
        sidecar.write_text(json.dumps({
            "agent_name": "Builder",
            "task_id": "7",
            "created_at": "2026-04-25T12:00:00+00:00",
            "prompt_summary": "Build stuff",
        }))

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get(
                f"/api/files/provenance?path={target.name}"
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_name"] == "Builder"
    assert data["task_id"] == "7"


@pytest.mark.asyncio
async def test_files_provenance_endpoint_empty_when_no_sidecar(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        target = tmppath / "no-sidecar.md"
        target.write_text("# Hi")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get(
                f"/api/files/provenance?path={target.name}"
            )

    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_save_agent_output_creates_sidecar():
    """_save_agent_output_to_files writes a .provenance.json sidecar."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        with (
            patch("routers.agents.MYOS_FILES_DIR", tmppath),
            patch("routers.agents.agent_metadata", {
                "fleet-builder": {
                    "fleet_id": "f1",
                    "fleet_name": "Test Fleet",
                    "task_id": "99",
                    "template": "",
                }
            }),
        ):
            from routers.agents import _save_agent_output_to_files

            content = "A" * 200  # exceeds _MIN_ARTIFACT_SUMMARY_CHARS
            paths = _save_agent_output_to_files(
                "fleet-builder", content, skip_auto_tasks=True, emit_notification=False
            )

        assert paths, "expected at least one file written"
        for p in paths:
            sidecar = Path(str(p) + ".provenance.json")
            assert sidecar.is_file(), f"sidecar missing for {p}"
            data = json.loads(sidecar.read_text())
            assert data["agent_name"] == "fleet-builder"

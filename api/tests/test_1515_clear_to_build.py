"""Failing tests for →1515: clear_to_build field rename + demotion.

TDD: these tests are written BEFORE implementation and will fail RED
until the routers rename gemini_ready → clear_to_build and add demotion
logic (ready item that fails clarity → effective_status=draft, needs_clarity=True).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_ready import Readiness, ReadinessCheck


def _make_readiness(ready: bool) -> Readiness:
    checks = [
        ReadinessCheck(name=n, passed=ready, detail="ok" if ready else "fail")
        for n in [
            "plan_path_present", "file_exists", "has_ac_checkboxes", "no_vague_ac",
            "has_file_paths", "ac_count_threshold", "referenced_files_exist",
            "in_repo_scope", "is_unblocked",
        ]
    ]
    return Readiness(ready=ready, file_path=None, checks=checks)


# ---------------------------------------------------------------------------
# Specs router — field rename
# ---------------------------------------------------------------------------

class TestSpecsFieldRename:
    @pytest.mark.asyncio
    async def test_response_has_clear_to_build_not_gemini_ready(self, client, monkeypatch):
        with (
            patch("routers.specs.ostk") as mock_ostk,
            patch("services.gemini_ready.compute_spec_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_docs = AsyncMock(return_value=[
                {"id": "s1", "title": "Test spec", "status": "ready", "path": "/tmp/test.md"}
            ])
            resp = await client.get("/api/specs")

        assert resp.status_code == 200
        doc = resp.json()["docs"][0]
        assert "clear_to_build" in doc, "should use clear_to_build, not gemini_ready"
        assert "gemini_ready" not in doc, "gemini_ready field must be gone"

    @pytest.mark.asyncio
    async def test_response_has_clear_to_build_checks_not_gemini_ready_checks(self, client, monkeypatch):
        with (
            patch("routers.specs.ostk") as mock_ostk,
            patch("services.gemini_ready.compute_spec_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_docs = AsyncMock(return_value=[
                {"id": "s1", "title": "Test spec", "status": "ready", "path": "/tmp/test.md"}
            ])
            resp = await client.get("/api/specs")

        doc = resp.json()["docs"][0]
        assert "clear_to_build_checks" in doc
        assert "gemini_ready_checks" not in doc

    @pytest.mark.asyncio
    async def test_ready_spec_failing_clarity_is_demoted_to_draft(self, client, monkeypatch):
        with (
            patch("routers.specs.ostk") as mock_ostk,
            patch("services.gemini_ready.compute_spec_readiness", return_value=_make_readiness(False)),
        ):
            mock_ostk.list_docs = AsyncMock(return_value=[
                {"id": "s1", "title": "Unclear spec", "status": "ready", "path": "/tmp/test.md"}
            ])
            resp = await client.get("/api/specs")

        doc = resp.json()["docs"][0]
        assert doc["clear_to_build"] is False
        # specs surface needs_clarity (informs, never blocks — b4fba786); effective_status not set
        assert doc["needs_clarity"] is True

    @pytest.mark.asyncio
    async def test_ready_spec_passing_clarity_is_not_demoted(self, client, monkeypatch):
        with (
            patch("routers.specs.ostk") as mock_ostk,
            patch("services.gemini_ready.compute_spec_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_docs = AsyncMock(return_value=[
                {"id": "s1", "title": "Clear spec", "status": "ready", "path": "/tmp/test.md"}
            ])
            resp = await client.get("/api/specs")

        doc = resp.json()["docs"][0]
        assert doc["clear_to_build"] is True
        assert doc.get("needs_clarity") is not True
        assert doc.get("effective_status") != "draft"

    @pytest.mark.asyncio
    async def test_draft_spec_failing_clarity_not_demoted(self, client, monkeypatch):
        """Draft items are already draft — demotion only applies to ready items."""
        with (
            patch("routers.specs.ostk") as mock_ostk,
            patch("services.gemini_ready.compute_spec_readiness", return_value=_make_readiness(False)),
        ):
            mock_ostk.list_docs = AsyncMock(return_value=[
                {"id": "s1", "title": "Draft spec", "status": "draft", "path": "/tmp/test.md"}
            ])
            resp = await client.get("/api/specs")

        doc = resp.json()["docs"][0]
        assert doc.get("effective_status") != "draft" or doc.get("status") == "draft"
        assert doc.get("needs_clarity") is True


# ---------------------------------------------------------------------------
# Tasks router — field rename
# ---------------------------------------------------------------------------

class TestTasksFieldRename:
    @pytest.mark.asyncio
    async def test_response_has_clear_to_build_not_gemini_ready(self, client):
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks.task_labels_store") as mock_tls,
            patch("services.gemini_ready.compute_task_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "→1", "title": "Test task", "status": "open", "priority": "P1", "description": ""}
            ])
            mock_tls.get_all_assignments = MagicMock(return_value={})
            resp = await client.get("/api/tasks")

        assert resp.status_code == 200
        task = resp.json()["tasks"][0]
        assert "clear_to_build" in task, "should use clear_to_build, not gemini_ready"
        assert "gemini_ready" not in task, "gemini_ready field must be gone"

    @pytest.mark.asyncio
    async def test_response_has_clear_to_build_checks_not_gemini_ready_checks(self, client):
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks.task_labels_store") as mock_tls,
            patch("services.gemini_ready.compute_task_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "→1", "title": "Test task", "status": "open", "priority": "P1", "description": ""}
            ])
            mock_tls.get_all_assignments = MagicMock(return_value={})
            resp = await client.get("/api/tasks")

        task = resp.json()["tasks"][0]
        assert "clear_to_build_checks" in task
        assert "gemini_ready_checks" not in task

    @pytest.mark.asyncio
    async def test_ready_task_failing_clarity_is_demoted_to_draft(self, client):
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks.task_labels_store") as mock_tls,
            patch("services.gemini_ready.compute_task_readiness", return_value=_make_readiness(False)),
        ):
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "→1", "title": "Unclear task", "status": "ready", "priority": "P1", "description": ""}
            ])
            mock_tls.get_all_assignments = MagicMock(return_value={})
            resp = await client.get("/api/tasks")

        task = resp.json()["tasks"][0]
        assert task["clear_to_build"] is False
        assert task["effective_status"] == "draft", "ready+fails_clarity should have effective_status=draft"
        assert task["needs_clarity"] is True

    @pytest.mark.asyncio
    async def test_ready_task_passing_clarity_is_not_demoted(self, client):
        with (
            patch("routers.tasks.ostk") as mock_ostk,
            patch("routers.tasks.task_labels_store") as mock_tls,
            patch("services.gemini_ready.compute_task_readiness", return_value=_make_readiness(True)),
        ):
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "→1", "title": "Clear task", "status": "ready", "priority": "P1", "description": ""}
            ])
            mock_tls.get_all_assignments = MagicMock(return_value={})
            resp = await client.get("/api/tasks")

        task = resp.json()["tasks"][0]
        assert task["clear_to_build"] is True
        assert task.get("needs_clarity") is not True
        assert task.get("effective_status") != "draft"

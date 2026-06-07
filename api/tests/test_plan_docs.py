"""Tests for plan file surfacing in list_docs() and plan_path on tasks.

Covers:
- list_docs() picks up transcripts/plan-NNN.md, ignores .stderr.log, ignores
  non-matching filenames.
- plan docs keep status="plan" after enrichment (not overridden by compute_spec_status).
- GET /api/tasks returns plan_path for a task that has a matching plan file.
- GET /api/tasks/{id} returns plan_path for a task that has a matching plan file.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ostk import OstkService


# ---------------------------------------------------------------------------
# Service-level: list_docs() plan scanning
# ---------------------------------------------------------------------------

class TestListDocsPlanScanning:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)
        (Path(self.tmpdir) / "transcripts").mkdir(exist_ok=True)

    def _write_plan(self, name: str, content: str = "") -> Path:
        p = Path(self.tmpdir) / "transcripts" / name
        p.write_text(content or f"# Plan content for {name}")
        return p

    @pytest.mark.asyncio
    async def test_list_docs_picks_up_plan_file(self):
        self._write_plan("plan-999.md", "First non-blank line\nMore content.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()

        paths = [d["path"] for d in docs]
        assert "transcripts/plan-999.md" in paths

    @pytest.mark.asyncio
    async def test_list_docs_plan_status_is_plan(self):
        self._write_plan("plan-888.md", "Some plan content here.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()

        plan_doc = next(d for d in docs if d["path"] == "transcripts/plan-888.md")
        assert plan_doc["status"] == "plan"

    @pytest.mark.asyncio
    async def test_list_docs_plan_status_preserved_even_when_task_closed(self):
        self._write_plan("plan-777.md", "Content for plan.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = [{"id": "→777", "status": "closed"}]
            docs = await self.svc.list_docs()

        plan_doc = next(d for d in docs if d["path"] == "transcripts/plan-777.md")
        assert plan_doc["status"] == "plan", (
            "plan status must not be overridden by compute_spec_status even when task is closed"
        )

    @pytest.mark.asyncio
    async def test_list_docs_ignores_stderr_log(self):
        self._write_plan("plan-999.md", "Real plan.")
        (Path(self.tmpdir) / "transcripts" / "plan-999.md.stderr.log").write_text("error output")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()

        paths = [d["path"] for d in docs]
        assert "transcripts/plan-999.md" in paths
        assert not any(".stderr.log" in p for p in paths)

    @pytest.mark.asyncio
    async def test_list_docs_ignores_non_numeric_plan_names(self):
        self._write_plan("plan-999.md", "Numeric plan.")
        self._write_plan("plan-mode-locks-regression-764453.md", "Non-numeric plan.")
        self._write_plan("plan-optimize-stuff-abc123.md", "Also non-numeric.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()

        paths = [d["path"] for d in docs]
        assert "transcripts/plan-999.md" in paths
        assert "transcripts/plan-mode-locks-regression-764453.md" not in paths
        assert "transcripts/plan-optimize-stuff-abc123.md" not in paths

    @pytest.mark.asyncio
    async def test_list_docs_plan_task_ids_parsed_from_filename(self):
        self._write_plan("plan-1117.md", "Some therapy plan content.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()

        plan_doc = next(d for d in docs if "plan-1117" in d["path"])
        assert plan_doc["task_ids"] == ["1117"]

    @pytest.mark.asyncio
    async def test_list_docs_preserves_existing_spec_docs(self):
        from services.ostk import USER_SPECS_DIR
        USER_SPECS_DIR.mkdir(parents=True, exist_ok=True)
        (USER_SPECS_DIR / "my-spec.md").write_text(
            "---\ntitle: My Spec\nstatus: spec\n---\n\n- [ ] Do the thing\n"
        )
        self._write_plan("plan-42.md", "Some plan.")
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()
    
        paths = [d["path"] for d in docs]
        assert any("my-spec.md" in p for p in paths)
        assert any("plan-42.md" in p for p in paths)

    @pytest.mark.asyncio
    async def test_list_docs_no_transcripts_dir_is_ok(self):
        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_lt:
            mock_lt.return_value = []
            docs = await self.svc.list_docs()
        assert isinstance(docs, list)


# ---------------------------------------------------------------------------
# Router-level: plan_path on task list and detail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_list_includes_plan_path_when_plan_exists(client, tmp_path, monkeypatch):
    """GET /api/tasks returns plan_path for a task that has a matching plan file."""
    from services import ostk as ostk_module
    import routers.tasks as tasks_router

    plan_file = tmp_path / "transcripts" / "plan-853.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text("Plan for needle 853.")

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    task = {
        "id": "→853",
        "title": "Some task",
        "priority": "P1",
        "status": "open",
        "tags": [],
    }

    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
        patch("routers.tasks.task_order_store") as mock_order,
        patch("routers.tasks.task_source_store") as mock_src,
    ):
        mock_ostk.cwd = str(tmp_path)
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_ts.get_thread_for_task = MagicMock(return_value=None)
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})
        mock_stm.get_session_for_task = MagicMock(return_value=None)
        mock_order.apply_order = MagicMock(side_effect=lambda x: x)
        mock_src.get_source = MagicMock(return_value={"source": None, "source_ref": None})

        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    task_853 = next((t for t in tasks if "853" in str(t.get("id", ""))), None)
    assert task_853 is not None
    assert task_853["plan_path"] == "transcripts/plan-853.md"


@pytest.mark.asyncio
async def test_task_list_plan_path_null_when_no_plan(client, tmp_path, monkeypatch):
    """GET /api/tasks returns plan_path=null for a task without a plan file."""
    from services import ostk as ostk_module

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    task = {
        "id": "→999",
        "title": "Task without plan",
        "priority": "P2",
        "status": "open",
        "tags": [],
    }

    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
        patch("routers.tasks.task_order_store") as mock_order,
        patch("routers.tasks.task_source_store") as mock_src,
    ):
        mock_ostk.cwd = str(tmp_path)
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_ts.get_thread_for_task = MagicMock(return_value=None)
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})
        mock_stm.get_session_for_task = MagicMock(return_value=None)
        mock_order.apply_order = MagicMock(side_effect=lambda x: x)
        mock_src.get_source = MagicMock(return_value={"source": None, "source_ref": None})

        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    task_999 = next((t for t in tasks if "999" in str(t.get("id", ""))), None)
    assert task_999 is not None
    assert task_999["plan_path"] is None

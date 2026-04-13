import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ostk import OstkService


# --- Hay converted service-level tests ---

class TestHayConversion:
    """Test the hay conversion tracking in OstkService."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ostk_dir = Path(self.tmpdir) / ".ostk"
        self.ostk_dir.mkdir()
        self.audit_path = self.ostk_dir / "audit.jsonl"
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_mark_hay_converted_creates_event(self):
        self.audit_path.write_text("")
        await self.svc._mark_hay_converted("build a rocket", "added →042")
        lines = [json.loads(l) for l in self.audit_path.read_text().strip().splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0]["event"] == "hay.converted"
        assert lines[0]["straw"] == "build a rocket"
        assert lines[0]["task_id"] == "→042"

    @pytest.mark.asyncio
    async def test_mark_hay_converted_no_task_id(self):
        self.audit_path.write_text("")
        await self.svc._mark_hay_converted("vague idea", "task created")
        lines = [json.loads(l) for l in self.audit_path.read_text().strip().splitlines() if l.strip()]
        assert lines[0]["task_id"] == ""

    @pytest.mark.asyncio
    async def test_list_converted_hay_returns_items(self):
        events = [
            {"event": "hay.filed", "straw": "idea A", "timestamp": "2026-04-04T20:00:00Z"},
            {"event": "hay.converted", "straw": "idea A", "task_id": "→001", "timestamp": "2026-04-04T21:00:00Z"},
            {"event": "hay.filed", "straw": "idea B", "timestamp": "2026-04-04T20:00:00Z"},
        ]
        self.audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        result = await self.svc.list_converted_hay()
        assert len(result) == 1
        assert result[0]["straw"] == "idea A"
        assert result[0]["task_id"] == "→001"

    @pytest.mark.asyncio
    async def test_list_converted_hay_empty_audit(self):
        self.audit_path.write_text("")
        result = await self.svc.list_converted_hay()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_converted_hay_no_file(self):
        # audit.jsonl doesn't exist
        result = await self.svc.list_converted_hay()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_hay_excludes_converted(self):
        """list_hay with exclude_converted=True should filter out converted items."""
        events = [
            {"event": "hay.converted", "straw": "converted idea", "task_id": "→001", "timestamp": "2026-04-04T21:00:00Z"},
        ]
        self.audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        # Mock the CLI call
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (
                "── unclustered ──\n"
                "  ~ converted idea\n"
                "  ~ fresh idea\n"
            )
            result = await self.svc.list_hay(exclude_converted=True)

        assert "fresh idea" in result["unclustered"]
        assert "converted idea" not in result["unclustered"]

    @pytest.mark.asyncio
    async def test_list_hay_no_exclude(self):
        """list_hay with exclude_converted=False should return all items."""
        events = [
            {"event": "hay.converted", "straw": "converted idea", "task_id": "→001", "timestamp": "2026-04-04T21:00:00Z"},
        ]
        self.audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (
                "── unclustered ──\n"
                "  ~ converted idea\n"
                "  ~ fresh idea\n"
            )
            result = await self.svc.list_hay(exclude_converted=False)

        assert "converted idea" in result["unclustered"]
        assert "fresh idea" in result["unclustered"]


# --- Hay parser unit tests ---

class TestHayParser:
    """Test OstkService._parse_hay directly."""

    def setup_method(self):
        self.svc = OstkService()

    def test_parse_clusters_and_unclustered(self):
        output = (
            "── clusters (discovered) ──\n"
            "design  (3 hay)\n"
            "  ~ color scheme for dashboard\n"
            "  ~ pick fonts\n"
            "  ~ icon set\n"
            "\n"
            "── unclustered ──\n"
            "  ~ random thought about cats\n"
            "  ~ buy groceries\n"
        )
        result = self.svc._parse_hay(output)
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["name"] == "design"
        assert result["clusters"][0]["count"] == 3
        assert len(result["clusters"][0]["items"]) == 3
        assert "color scheme for dashboard" in result["clusters"][0]["items"]
        assert len(result["unclustered"]) == 2
        assert "random thought about cats" in result["unclustered"]

    def test_parse_multiple_clusters(self):
        output = (
            "── clusters ──\n"
            "design  (2 hay)\n"
            "  ~ color scheme\n"
            "  ~ fonts\n"
            "api  (1 hay)\n"
            "  ~ add endpoints\n"
            "\n"
            "── unclustered ──\n"
            "  ~ stray idea\n"
        )
        result = self.svc._parse_hay(output)
        assert len(result["clusters"]) == 2
        assert result["clusters"][0]["name"] == "design"
        assert result["clusters"][0]["count"] == 2
        assert result["clusters"][1]["name"] == "api"
        assert result["clusters"][1]["count"] == 1
        assert len(result["unclustered"]) == 1

    def test_parse_empty_output(self):
        result = self.svc._parse_hay("")
        assert result["clusters"] == []
        assert result["unclustered"] == []

    def test_parse_unclustered_only(self):
        output = (
            "── unclustered ──\n"
            "  ~ just a lone thought\n"
        )
        result = self.svc._parse_hay(output)
        assert result["clusters"] == []
        assert len(result["unclustered"]) == 1
        assert result["unclustered"][0] == "just a lone thought"

    def test_parse_clusters_only(self):
        output = (
            "── clusters (discovered) ──\n"
            "infra  (2 hay)\n"
            "  ~ set up CI\n"
            "  ~ configure Docker\n"
        )
        result = self.svc._parse_hay(output)
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["name"] == "infra"
        assert result["unclustered"] == []

    def test_cluster_name_and_count_parsed(self):
        output = (
            "── clusters ──\n"
            "frontend  (5 hay)\n"
            "  ~ item one\n"
        )
        result = self.svc._parse_hay(output)
        assert result["clusters"][0]["name"] == "frontend"
        assert result["clusters"][0]["count"] == 5


# --- API endpoint tests ---

@pytest.mark.asyncio
async def test_list_ideas(client):
    mock_hay = {"clusters": [{"name": "design", "count": 2, "items": ["a", "b"]}], "unclustered": ["c"]}
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        mock_ostk.list_converted_hay = AsyncMock(return_value=[])
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    data = resp.json()
    assert "clusters" in data
    assert "unclustered" in data
    assert len(data["clusters"]) == 1
    assert data["clusters"][0]["name"] == "design"
    # unclustered items are now annotated objects with straw/staleness/source
    assert len(data["unclustered"]) == 1


@pytest.mark.asyncio
async def test_add_idea(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.add_hay = AsyncMock(return_value="added hay")
        resp = await client.post("/api/ideas", json={"thought": "What about dark mode?"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "added hay"
    mock_ostk.add_hay.assert_called_once_with("What about dark mode?")


@pytest.mark.asyncio
async def test_compile_ideas(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.compile_hay = AsyncMock(return_value="compiled 5 hay into 2 clusters")
        resp = await client.post("/api/ideas/compile")

    assert resp.status_code == 200
    assert "compiled" in resp.json()["result"]
    mock_ostk.compile_hay.assert_called_once_with(dry_run=False)


@pytest.mark.asyncio
async def test_compile_ideas_dry_run(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.compile_hay = AsyncMock(return_value="would compile 5 hay")
        resp = await client.post("/api/ideas/compile?dry_run=true")

    assert resp.status_code == 200
    mock_ostk.compile_hay.assert_called_once_with(dry_run=True)


@pytest.mark.asyncio
async def test_list_ideas_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.list_hay = AsyncMock(side_effect=OstkError("hay broken"))
        resp = await client.get("/api/ideas")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_add_idea_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.add_hay = AsyncMock(side_effect=OstkError("failed"))
        resp = await client.post("/api/ideas", json={"thought": "test"})

    assert resp.status_code == 400


# --- DELETE /api/ideas/{straw} ---

@pytest.mark.asyncio
async def test_delete_idea(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.delete_hay = AsyncMock(return_value="deleted hay: buy groceries")
        resp = await client.delete("/api/ideas/buy%20groceries")

    assert resp.status_code == 200
    assert "deleted" in resp.json()["result"]
    mock_ostk.delete_hay.assert_called_once_with("buy groceries", include_converted=True)


@pytest.mark.asyncio
async def test_delete_idea_not_found(client):
    from services.ostk import OstkError
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.delete_hay = AsyncMock(side_effect=OstkError("hay item not found: nope"))
        resp = await client.delete("/api/ideas/nope")

    assert resp.status_code == 404


# --- POST /api/ideas/convert ---
#
# The convert endpoint now calls break_down_idea first. Every test below
# mocks the breakdown service so we do not hit Claude and so we can assert
# the exact task list the router produces.

from services.idea_breakdown import BreakdownTask, IdeaBreakdownResult


def _single_task_breakdown(title: str, priority: str = "P1") -> IdeaBreakdownResult:
    return IdeaBreakdownResult(
        needs_clarification=False,
        question=None,
        tasks=[
            BreakdownTask(title=title, description="", priority=priority, order=0)
        ],
    )


@pytest.mark.asyncio
async def test_convert_idea_to_task(client):
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("build a rocket"))):
        mock_ostk.convert_hay_to_task = AsyncMock(return_value="added \u2192042: build a rocket [P1]")
        resp = await client.post("/api/ideas/convert", json={"straw": "build a rocket"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert body["tasks"][0]["title"] == "build a rocket"
    mock_ostk.convert_hay_to_task.assert_called_once_with(
        straw="build a rocket", priority="P1", delete_hay=False
    )


@pytest.mark.asyncio
async def test_convert_idea_custom_priority(client):
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("urgent idea", priority="P0"))):
        mock_ostk.convert_hay_to_task = AsyncMock(return_value="added \u2192042: urgent idea [P0]")
        resp = await client.post("/api/ideas/convert", json={
            "straw": "urgent idea", "priority": "P0", "delete_hay": False
        })

    assert resp.status_code == 200
    mock_ostk.convert_hay_to_task.assert_called_once_with(
        straw="urgent idea", priority="P0", delete_hay=False
    )


@pytest.mark.asyncio
async def test_convert_idea_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("bad idea"))):
        mock_ostk.convert_hay_to_task = AsyncMock(side_effect=OstkError("failed"))
        resp = await client.post("/api/ideas/convert", json={"straw": "bad idea"})

    assert resp.status_code == 400


# --- GET /api/ideas?status=converted ---

@pytest.mark.asyncio
async def test_list_converted_ideas(client):
    converted = [
        {"straw": "build a rocket", "task_id": "→042", "converted_at": "2026-04-04T20:00:00Z"},
    ]
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.list_converted_hay = AsyncMock(return_value=converted)
        resp = await client.get("/api/ideas?status=converted")

    assert resp.status_code == 200
    data = resp.json()
    assert "converted" in data
    assert len(data["converted"]) == 1
    assert data["converted"][0]["straw"] == "build a rocket"
    assert data["converted"][0]["task_id"] == "→042"


@pytest.mark.asyncio
async def test_list_converted_ideas_empty(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.list_converted_hay = AsyncMock(return_value=[])
        resp = await client.get("/api/ideas?status=converted")

    assert resp.status_code == 200
    assert resp.json()["converted"] == []


@pytest.mark.asyncio
async def test_list_active_ideas_excludes_converted(client):
    """The default list (status=active) should call list_hay with exclude_converted=True."""
    mock_hay = {"clusters": [], "unclustered": ["fresh idea"]}
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        mock_ostk.list_converted_hay = AsyncMock(return_value=[])
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    data = resp.json()
    assert "unclustered" in data
    mock_ostk.list_hay.assert_called_once_with(exclude_converted=True)


# --- Auto-label regression for idea-to-task path (needle →137) ------------
# A task created from an idea (POST /api/ideas/convert) used to skip the
# auto-label suggester entirely because it called ostk.add_task at the
# service level, bypassing the task router. The fix routes every task
# creation path through the shared services.task_labeling helper.

@pytest.mark.asyncio
async def test_convert_idea_triggers_auto_label(client):
    """Converting an idea to a task must run the auto-label suggester."""
    import asyncio as _asyncio

    fake_label = {
        "id": "l-product",
        "name": "product",
        "color": "#8b5cf6",
        "is_new": False,
    }

    async def fake_suggest(title, desc, labels):
        return [fake_label]

    captured = {}

    def fake_replace(task_id, label_ids):
        captured["task_id"] = task_id
        captured["label_ids"] = list(label_ids)
        return list(label_ids)

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("complete notification system"))), \
         patch("services.task_labeling.suggest_labels", new=fake_suggest), \
         patch("services.task_labeling.task_labels_store") as mock_tls, \
         patch("services.task_labeling.settings_store") as mock_settings, \
         patch("services.task_labeling.labels_store") as mock_labels:
        mock_ostk.convert_hay_to_task = AsyncMock(
            return_value="added \u2192301: complete notification system [P1]"
        )
        mock_settings.get = MagicMock(return_value=True)
        mock_labels.list_labels = MagicMock(return_value=[])
        mock_tls.replace_auto_applied = MagicMock(side_effect=fake_replace)

        resp = await client.post(
            "/api/ideas/convert",
            json={"straw": "complete notification system", "priority": "P1"},
        )
        # Give the background task a chance to run.
        await _asyncio.sleep(0.05)

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "\u2192301"
    assert captured.get("task_id") == "\u2192301"
    assert captured.get("label_ids") == ["l-product"]


@pytest.mark.asyncio
async def test_convert_idea_skips_auto_label_when_setting_off(client):
    """If auto_label_tasks is False, conversion must not call the suggester."""
    import asyncio as _asyncio
    suggest_called = {"count": 0}

    async def fake_suggest(title, desc, labels):
        suggest_called["count"] += 1
        return []

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("another idea"))), \
         patch("services.task_labeling.suggest_labels", new=fake_suggest), \
         patch("services.task_labeling.settings_store") as mock_settings:
        mock_ostk.convert_hay_to_task = AsyncMock(
            return_value="added \u2192302: another idea [P1]"
        )
        mock_settings.get = MagicMock(return_value=False)

        resp = await client.post(
            "/api/ideas/convert", json={"straw": "another idea"}
        )
        await _asyncio.sleep(0.05)

    assert resp.status_code == 200
    assert suggest_called["count"] == 0


@pytest.mark.asyncio
async def test_convert_idea_returns_task_id(client):
    """The conversion endpoint should echo the first task id for the UI."""
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_single_task_breakdown("parsed task", priority="P0"))), \
         patch("services.task_labeling.settings_store") as mock_settings:
        mock_ostk.convert_hay_to_task = AsyncMock(
            return_value="added \u2192303: parsed task [P0]"
        )
        mock_settings.get = MagicMock(return_value=False)
        resp = await client.post(
            "/api/ideas/convert",
            json={"straw": "parsed task", "priority": "P0"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "\u2192303"
    assert body["status"] == "created"


# --- New tests for idea breakdown flow -----------------------------------

def _multi_task_breakdown() -> IdeaBreakdownResult:
    return IdeaBreakdownResult(
        needs_clarification=False,
        question=None,
        tasks=[
            BreakdownTask(title="Sketch layout", description="Draw wireframes.", priority="P1", order=0),
            BreakdownTask(title="Pick colors", description="Choose the palette.", priority="P2", order=1),
            BreakdownTask(title="Ship draft", description="Upload to staging.", priority="P2", order=2),
        ],
    )


def _clarification_breakdown(question: str) -> IdeaBreakdownResult:
    return IdeaBreakdownResult(
        needs_clarification=True,
        question=question,
        tasks=[],
    )


@pytest.mark.asyncio
async def test_convert_idea_creates_multiple_tasks(client):
    """A multi-task breakdown should call ostk once per task."""
    async def fake_add_task(title, priority, description=""):
        return f"added \u2192900: {title} [{priority}]"

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=AsyncMock(return_value=_multi_task_breakdown())), \
         patch("services.task_labeling.settings_store") as mock_settings:
        mock_ostk.convert_hay_to_task = AsyncMock(
            return_value="added \u2192900: Sketch layout [P1]"
        )
        mock_ostk.add_task = AsyncMock(side_effect=fake_add_task)
        mock_settings.get = MagicMock(return_value=False)

        resp = await client.post(
            "/api/ideas/convert",
            json={"straw": "Redesign landing page", "priority": "P1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert len(body["tasks"]) == 3
    assert body["tasks"][0]["title"] == "Sketch layout"
    assert body["tasks"][1]["title"] == "Pick colors"
    assert body["tasks"][2]["title"] == "Ship draft"
    # First task converts the hay, the rest go through add_task.
    mock_ostk.convert_hay_to_task.assert_called_once()
    assert mock_ostk.add_task.call_count == 2


@pytest.mark.asyncio
async def test_convert_idea_returns_clarification_when_vague(client):
    """When Claude cannot break the idea down, no tasks are created."""
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch(
             "routers.ideas.break_down_idea",
             new=AsyncMock(return_value=_clarification_breakdown("What platform, web or mobile?")),
         ):
        mock_ostk.convert_hay_to_task = AsyncMock()
        mock_ostk.add_task = AsyncMock()

        resp = await client.post(
            "/api/ideas/convert",
            json={"straw": "build an app"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "needs_clarification"
    assert "platform" in body["question"].lower()
    assert "straw" in body
    # No tasks were created.
    mock_ostk.convert_hay_to_task.assert_not_called()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_answer_endpoint_creates_tasks(client):
    """Posting an answer retries breakdown with extra_context and creates tasks."""
    mock_breakdown = AsyncMock(return_value=_multi_task_breakdown())

    async def fake_add_task(title, priority, description=""):
        return f"added \u2192901: {title} [{priority}]"

    with patch("routers.ideas.ostk") as mock_ostk, \
         patch("routers.ideas.break_down_idea", new=mock_breakdown), \
         patch("services.task_labeling.settings_store") as mock_settings:
        mock_ostk.convert_hay_to_task = AsyncMock(
            return_value="added \u2192901: Sketch layout [P1]"
        )
        mock_ostk.add_task = AsyncMock(side_effect=fake_add_task)
        mock_settings.get = MagicMock(return_value=False)

        resp = await client.post(
            "/api/ideas/answer",
            json={"straw": "build an app", "answer": "web", "priority": "P1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert len(body["tasks"]) == 3
    # The answer should have been passed as extra_context.
    call_kwargs = mock_breakdown.call_args.kwargs
    assert call_kwargs.get("extra_context") == "web"


@pytest.mark.asyncio
async def test_answer_endpoint_can_return_another_question(client):
    """The answer endpoint can also come back with another clarifying question."""
    with patch("routers.ideas.ostk") as mock_ostk, \
         patch(
             "routers.ideas.break_down_idea",
             new=AsyncMock(return_value=_clarification_breakdown("Is it for individuals or teams?")),
         ):
        mock_ostk.convert_hay_to_task = AsyncMock()
        mock_ostk.add_task = AsyncMock()

        resp = await client.post(
            "/api/ideas/answer",
            json={"straw": "build an app", "answer": "web"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "needs_clarification"
    assert "teams" in body["question"].lower() or "individuals" in body["question"].lower()
    mock_ostk.convert_hay_to_task.assert_not_called()
    mock_ostk.add_task.assert_not_called()


# ── Needle 336: ostk compile for idea triage ──────────────────────


class TestCompileIdeaTriage:
    """Test that OstkService.compile_ideas delegates correctly."""

    @pytest.mark.asyncio
    async def test_compile_ideas_delegates_to_compile_hay(self):
        """compile_ideas should delegate to compile_hay."""
        svc = OstkService(cwd="/tmp")
        mock_compile = AsyncMock(return_value="compiled")
        with patch.object(svc, "compile_hay", mock_compile):
            result = await svc.compile_ideas(dry_run=False)
        assert result == "compiled"
        mock_compile.assert_called_once_with(dry_run=False)

    @pytest.mark.asyncio
    async def test_compile_ideas_with_dry_run(self):
        """compile_ideas passes dry_run through."""
        svc = OstkService(cwd="/tmp")
        mock_compile = AsyncMock(return_value="dry run")
        with patch.object(svc, "compile_hay", mock_compile):
            result = await svc.compile_ideas(dry_run=True)
        assert result == "dry run"
        mock_compile.assert_called_once_with(dry_run=True)

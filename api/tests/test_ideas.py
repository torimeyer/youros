import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    data = resp.json()
    assert "clusters" in data
    assert "unclustered" in data
    assert len(data["clusters"]) == 1
    assert data["clusters"][0]["name"] == "design"
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
    mock_ostk.delete_hay.assert_called_once_with("buy groceries")


@pytest.mark.asyncio
async def test_delete_idea_not_found(client):
    from services.ostk import OstkError
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.delete_hay = AsyncMock(side_effect=OstkError("hay item not found: nope"))
        resp = await client.delete("/api/ideas/nope")

    assert resp.status_code == 404


# --- POST /api/ideas/convert ---

@pytest.mark.asyncio
async def test_convert_idea_to_task(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.convert_hay_to_task = AsyncMock(return_value="created task")
        resp = await client.post("/api/ideas/convert", json={"straw": "build a rocket"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "created task"
    mock_ostk.convert_hay_to_task.assert_called_once_with(
        straw="build a rocket", priority="P1", delete_hay=False
    )


@pytest.mark.asyncio
async def test_convert_idea_custom_priority(client):
    with patch("routers.ideas.ostk") as mock_ostk:
        mock_ostk.convert_hay_to_task = AsyncMock(return_value="created task")
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
    with patch("routers.ideas.ostk") as mock_ostk:
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
        resp = await client.get("/api/ideas")

    assert resp.status_code == 200
    data = resp.json()
    assert "unclustered" in data
    mock_ostk.list_hay.assert_called_once_with(exclude_converted=True)

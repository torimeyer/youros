import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService, OstkError


# --- Service-level tests ---


class TestSearchNear:
    """Test the search_near method on OstkService."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ostk_dir = Path(self.tmpdir) / ".ostk"
        self.ostk_dir.mkdir(exist_ok=True)
        self.audit_path = self.ostk_dir / "audit.jsonl"
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_returns_tasks_from_near(self):
        """Tasks from ostk work near should be parsed and returned."""
        near_output = (
            '── 1 open needles near "search" ──\n'
            "\n"
            "  SPHERE 5 (point: →086, 1 members):\n"
            "    →086 [P1] Add concept search across tasks and ideas\n"
        )
        self.audit_path.write_text("")

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = near_output
            result = await self.svc.search_near("search")

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == "→086"
        assert result["tasks"][0]["priority"] == "P1"
        assert result["tasks"][0]["title"] == "Add concept search across tasks and ideas"
        assert result["query"] == "search"

    @pytest.mark.asyncio
    async def test_empty_results_when_no_matches(self):
        """When nothing matches, tasks list should be empty."""
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = OstkError("no open needles matching")
            result = await self.svc.search_near("xyznonexistent")

        assert result["tasks"] == []
        assert result["query"] == "xyznonexistent"

    @pytest.mark.asyncio
    async def test_multiple_tasks_returned(self):
        """Should return multiple tasks when they match."""
        near_output = (
            '\u2500\u2500 2 open needles near "agents" \u2500\u2500\n'
            "\n"
            "  SPHERE 2 (point: \u2192095, 2 members):\n"
            "    \u2192098 [P2] Add delegation view for agents\n"
            "    \u2192095 [P1] Build agent monitor\n"
        )
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = near_output
            result = await self.svc.search_near("agents")

        assert len(result["tasks"]) == 2


class TestParseNearOutput:
    """Test the _parse_near_output parser directly."""

    def setup_method(self):
        self.svc = OstkService()

    def test_parse_single_result(self):
        output = (
            '── 1 open needles near "search" ──\n'
            "\n"
            "  SPHERE 5 (point: →086, 1 members):\n"
            "    →086 [P1] Add concept search\n"
        )
        result = self.svc._parse_near_output(output)
        assert len(result) == 1
        assert result[0]["id"] == "→086"
        assert result[0]["priority"] == "P1"
        assert result[0]["title"] == "Add concept search"

    def test_parse_multiple_results(self):
        output = (
            '── 2 open needles near "agents" ──\n'
            "\n"
            "  SPHERE 2 (point: →095, 2 members):\n"
            "    →098 [P2] Add delegation view for agents\n"
            "    →095 [P1] Build agent monitor\n"
        )
        result = self.svc._parse_near_output(output)
        assert len(result) == 2
        ids = [r["id"] for r in result]
        assert "→098" in ids
        assert "→095" in ids

    def test_parse_empty_output(self):
        result = self.svc._parse_near_output("")
        assert result == []

    def test_parse_no_match_message(self):
        output = 'no open needles matching "foobar"'
        result = self.svc._parse_near_output(output)
        assert result == []

    def test_parse_ignores_header_lines(self):
        output = (
            '── 1 open needles near "test" ──\n'
            "some header info\n"
            "  SPHERE 1 (point: →001, 1 members):\n"
            "    →001 [P0] Critical bug fix\n"
        )
        result = self.svc._parse_near_output(output)
        assert len(result) == 1
        assert result[0]["priority"] == "P0"


# --- API endpoint tests ---


@pytest.mark.asyncio
async def test_search_endpoint_returns_results(client):
    mock_results = {
        "tasks": [{"id": "→086", "priority": "P1", "title": "Add concept search"}],
        "query": "search",
    }
    with patch("routers.search.ostk") as mock_ostk:
        mock_ostk.search_near = AsyncMock(return_value=mock_results)
        resp = await client.get("/api/search?q=search")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["id"] == "→086"
    assert data["query"] == "search"


@pytest.mark.asyncio
async def test_search_endpoint_empty_query_rejected(client):
    """Empty query string should return 422 (validation error)."""
    resp = await client.get("/api/search?q=")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_endpoint_missing_query_rejected(client):
    """Missing q parameter should return 422."""
    resp = await client.get("/api/search")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_endpoint_no_results(client):
    mock_results = {"tasks": [], "query": "xyznonexistent"}
    with patch("routers.search.ostk") as mock_ostk:
        mock_ostk.search_near = AsyncMock(return_value=mock_results)
        resp = await client.get("/api/search?q=xyznonexistent")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == []


@pytest.mark.asyncio
async def test_search_endpoint_ostk_error(client):
    with patch("routers.search.ostk") as mock_ostk:
        mock_ostk.search_near = AsyncMock(side_effect=OstkError("search failed"))
        resp = await client.get("/api/search?q=broken")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_search_endpoint_calls_search_near(client):
    mock_results = {"tasks": [], "ideas": [], "query": "agents"}
    with patch("routers.search.ostk") as mock_ostk:
        mock_ostk.search_near = AsyncMock(return_value=mock_results)
        resp = await client.get("/api/search?q=agents")

    assert resp.status_code == 200
    mock_ostk.search_near.assert_called_once_with("agents")

"""Tests for the delegation / radiate feature.

Verifies:
1. OstkService._parse_radiate correctly parses CLI output
2. The /api/agents/delegate endpoint returns parsed delegation data
3. Edge cases: empty output, single target, multiple rings
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.ostk import OstkService


# ---------- Parser unit tests ----------

SAMPLE_OUTPUT = """\
── radiate from →084 [P1|d2] ──
  Build smart focus recommendation using compounds
  max radius: 1

◉ POINT (radius 0): →084
○ RING 1 (2 needles, 2 open):
    →087 [P1] Build activity timeline using ostk history
    →089 [P1] Build session diff view

── delegation frontier (2 targets) ──
  :spawn →089 → Build session diff view
  :spawn →087 → Build activity timeline using ostk history
"""

SINGLE_TARGET_OUTPUT = """\
── radiate from →098 [P2|d1] ──
  Add delegation view for agents
  max radius: 1

◉ POINT (radius 0): →098
○ RING 1 (1 needles, 1 open):
    →095 [P2] Surface agent permission requests

── delegation frontier (1 targets) ──
  :spawn →095 → Surface agent permission requests
"""

EMPTY_FRONTIER_OUTPUT = """\
── radiate from →001 [P0|d0] ──
  Setup project
  max radius: 0

◉ POINT (radius 0): →001

── delegation frontier (0 targets) ──
"""


def test_parse_radiate_full():
    """Parser extracts point, rings, and delegation targets from typical output."""
    result = OstkService._parse_radiate(SAMPLE_OUTPUT)

    assert result["point"] == "→084"
    assert result["point_title"] == "Build smart focus recommendation using compounds"

    assert len(result["rings"]) == 1
    ring = result["rings"][0]
    assert ring["radius"] == 1
    assert ring["total"] == 2
    assert ring["open"] == 2
    assert len(ring["needles"]) == 2
    assert ring["needles"][0]["id"] == "→087"
    assert ring["needles"][0]["priority"] == "P1"
    assert ring["needles"][0]["title"] == "Build activity timeline using ostk history"
    assert ring["needles"][1]["id"] == "→089"

    assert len(result["delegation_targets"]) == 2
    assert result["delegation_targets"][0]["id"] == "→089"
    assert result["delegation_targets"][0]["title"] == "Build session diff view"
    assert result["delegation_targets"][1]["id"] == "→087"
    assert result["delegation_targets"][1]["title"] == "Build activity timeline using ostk history"


def test_parse_radiate_single_target():
    """Parser works with a single delegation target."""
    result = OstkService._parse_radiate(SINGLE_TARGET_OUTPUT)

    assert result["point"] == "→098"
    assert result["point_title"] == "Add delegation view for agents"
    assert len(result["delegation_targets"]) == 1
    assert result["delegation_targets"][0]["id"] == "→095"
    assert result["delegation_targets"][0]["title"] == "Surface agent permission requests"

    assert len(result["rings"]) == 1
    assert result["rings"][0]["needles"][0]["id"] == "→095"


def test_parse_radiate_empty_frontier():
    """Parser returns empty targets when no delegation candidates exist."""
    result = OstkService._parse_radiate(EMPTY_FRONTIER_OUTPUT)

    assert result["point"] == "→001"
    assert result["point_title"] == "Setup project"
    assert len(result["delegation_targets"]) == 0
    assert len(result["rings"]) == 0


def test_parse_radiate_empty_string():
    """Parser handles completely empty input."""
    result = OstkService._parse_radiate("")

    assert result["point"] is None
    assert result["point_title"] == ""
    assert result["delegation_targets"] == []
    assert result["rings"] == []


# ---------- API endpoint tests ----------

@pytest.mark.asyncio
async def test_delegate_endpoint_returns_data():
    """GET /api/agents/delegate returns parsed radiate data."""
    mock_radiate = AsyncMock(return_value={
        "point": "→084",
        "point_title": "Build smart focus",
        "rings": [
            {
                "radius": 1,
                "total": 2,
                "open": 2,
                "needles": [
                    {"id": "→087", "priority": "P1", "title": "Build timeline"},
                    {"id": "→089", "priority": "P1", "title": "Build diff view"},
                ],
            }
        ],
        "delegation_targets": [
            {"id": "→089", "title": "Build diff view"},
            {"id": "→087", "title": "Build timeline"},
        ],
    })

    with patch.object(OstkService, "work_radiate", mock_radiate):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents/delegate")

    assert resp.status_code == 200
    data = resp.json()
    assert data["point"] == "→084"
    assert len(data["delegation_targets"]) == 2
    assert data["delegation_targets"][0]["id"] == "→089"


@pytest.mark.asyncio
async def test_delegate_endpoint_with_needle_id():
    """GET /api/agents/delegate?needle_id=098 passes the ID through."""
    mock_radiate = AsyncMock(return_value={
        "point": "→098",
        "point_title": "Add delegation view",
        "rings": [],
        "delegation_targets": [],
    })

    with patch.object(OstkService, "work_radiate", mock_radiate):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents/delegate?needle_id=098")

    assert resp.status_code == 200
    # Verify the needle ID was passed to the service
    mock_radiate.assert_called_once_with("098")


@pytest.mark.asyncio
async def test_delegate_endpoint_error():
    """GET /api/agents/delegate returns 500 when ostk fails."""
    from services.ostk import OstkError

    mock_radiate = AsyncMock(side_effect=OstkError("no needles found"))

    with patch.object(OstkService, "work_radiate", mock_radiate):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents/delegate")

    assert resp.status_code == 500
    assert "no needles found" in resp.json()["detail"]

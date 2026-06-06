"""Tests for ostk primitive endpoints: handoff, context-pages, arrive, note."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.mark.asyncio
async def test_handoff_endpoint_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        with patch("routers.agents.OSTK_DIR", ostk_dir):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/my-agent/handoff",
                    json={"summary": "Finished phase 1. Tests green. Next: merge."},
                )
        assert resp.status_code == 200
        handoff_file = ostk_dir / "handoffs" / "my-agent.md"
        assert handoff_file.exists()
        assert "Finished phase 1" in handoff_file.read_text()


@pytest.mark.asyncio
async def test_context_pages_empty_when_dir_missing():
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        with patch("routers.agents.OSTK_DIR", ostk_dir):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/context-pages")
        assert resp.status_code == 200
        assert resp.json() == {"pages": []}


@pytest.mark.asyncio
async def test_context_pages_lists_page_files():
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        memory_dir = ostk_dir / "memory"
        memory_dir.mkdir(exist_ok=True)
        (memory_dir / "spec-plan.page").write_text("big content here")
        (memory_dir / "test-output.page").write_text("pass")
        with patch("routers.agents.OSTK_DIR", ostk_dir):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/context-pages")
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()["pages"]}
        assert names == {"spec-plan", "test-output"}


@pytest.mark.asyncio
async def test_arrive_endpoint_writes_record():
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        with patch("routers.agents.OSTK_DIR", ostk_dir):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/builder-1/arrive",
                    json={"milestone": "tests green"},
                )
        assert resp.status_code == 200
        arrive_file = ostk_dir / "agents" / "builder-1" / "arrive.json"
        assert arrive_file.exists()
        record = json.loads(arrive_file.read_text())
        assert record["milestone"] == "tests green"
        assert record["agent"] == "builder-1"


@pytest.mark.asyncio
async def test_note_post_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        with patch("routers.agents.OSTK_DIR", ostk_dir):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                post_resp = await client.post(
                    "/api/agents/my-agent/note",
                    json={"content": "Chose asyncio.Lock over threading.Lock for GIL reasons."},
                )
                assert post_resp.status_code == 200
                get_resp = await client.get("/api/agents/my-agent/notes")
        assert get_resp.status_code == 200
        notes = get_resp.json()["notes"]
        assert len(notes) == 1
        assert "asyncio.Lock" in notes[0]["content"]

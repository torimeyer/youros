"""Tests for the agentfile form helpers and form endpoints."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agentfile_parser import agentfile_to_form, form_to_agentfile, parse_agentfile
from main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- agentfile_to_form tests ----------

class TestAgentfileToForm:
    def test_full_agentfile(self):
        text = (
            'NAME my-agent\n'
            'DESC "Does something useful"\n'
            'FROM claude-sonnet-4-6\n'
            'PROMPT "You are a helper."\n'
            'TOOL bash\n'
            'TOOL read\n'
            'BETA feature-x\n'
        )
        result = agentfile_to_form(text)
        assert result["model"] == "claude-sonnet-4-6"
        assert result["instructions"] == "You are a helper."
        assert result["tools"] == ["bash", "read"]
        assert result["description"] == "Does something useful"
        assert result["tags"] == ["feature-x"]

    def test_missing_fields_get_defaults(self):
        result = agentfile_to_form("FROM auto\n")
        assert result["model"] == "auto"
        assert result["instructions"] == ""
        assert result["tools"] == []
        assert result["description"] == ""
        assert result["tags"] == []

    def test_empty_text(self):
        result = agentfile_to_form("")
        assert result["model"] == "auto"
        assert result["instructions"] == ""

    def test_round_trip(self):
        """form→agentfile→form produces same fields."""
        original = {
            "name": "test-agent",
            "description": "A test",
            "model": "claude-haiku-4-5",
            "tools": ["bash", "write"],
            "instructions": "Do things carefully.",
            "tags": [],
        }
        text = form_to_agentfile(original)
        result = agentfile_to_form(text)
        assert result["model"] == original["model"]
        assert result["instructions"] == original["instructions"]
        assert set(result["tools"]) == set(original["tools"])
        assert result["description"] == original["description"]

    def test_round_trip_all_tools(self):
        """Tools list survives a round-trip."""
        form = {
            "name": "tool-agent",
            "description": "",
            "model": "auto",
            "tools": ["bash", "read", "write", "search", "web"],
            "instructions": "Use all tools.",
            "tags": ["beta-flag"],
        }
        text = form_to_agentfile(form)
        result = agentfile_to_form(text)
        assert set(result["tools"]) == set(form["tools"])
        assert result["tags"] == ["beta-flag"]


# ---------- form_to_agentfile tests ----------

class TestFormToAgentfile:
    def test_produces_parseable_output(self, tmp_path):
        form = {
            "name": "my-bot",
            "description": "Useful bot",
            "model": "claude-sonnet-4-6",
            "tools": ["bash"],
            "instructions": "Be helpful.",
            "tags": [],
        }
        text = form_to_agentfile(form)
        assert "FROM claude-sonnet-4-6" in text
        assert "PROMPT" in text
        assert "TOOL bash" in text

    def test_empty_tools_not_emitted(self):
        form = {
            "name": "simple",
            "description": "",
            "model": "auto",
            "tools": [],
            "instructions": "Simple.",
            "tags": [],
        }
        text = form_to_agentfile(form)
        assert "TOOL" not in text

    def test_missing_instructions_omits_prompt(self):
        form = {
            "name": "quiet",
            "description": "",
            "model": "auto",
            "tools": [],
            "instructions": "",
            "tags": [],
        }
        text = form_to_agentfile(form)
        assert "PROMPT" not in text


# ---------- endpoint tests ----------

class TestFormEndpoints:
    @pytest.mark.asyncio
    async def test_get_form_not_found(self, client):
        resp = await client.get("/api/agentfiles/nonexistent-xyz/form")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_put_form_not_found(self, client):
        resp = await client.put(
            "/api/agentfiles/nonexistent-xyz/form",
            json={
                "name": "nonexistent-xyz",
                "description": "",
                "model": "auto",
                "tools": [],
                "instructions": "",
                "tags": [],
            },
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_get_form_returns_fields(self, client, tmp_path):
        agent_path = tmp_path / "test-form-agent.agent"
        agent_path.write_text(
            'NAME test-form-agent\n'
            'DESC "Test form agent"\n'
            'FROM claude-sonnet-4-6\n'
            'PROMPT "Do testing."\n'
            'TOOL bash\n'
        )
        from routers import agentfiles as af_router
        original_dir = af_router.USER_AGENTFILES_DIR
        af_router.USER_AGENTFILES_DIR = tmp_path
        try:
            resp = await client.get("/api/agentfiles/test-form-agent/form")
            assert resp.status_code == 200
            data = resp.json()
            assert data["model"] == "claude-sonnet-4-6"
            assert data["instructions"] == "Do testing."
            assert "bash" in data["tools"]
        finally:
            af_router.USER_AGENTFILES_DIR = original_dir

    @pytest.mark.asyncio
    async def test_put_form_saves(self, client, tmp_path):
        agent_path = tmp_path / "save-test.agent"
        agent_path.write_text(
            'NAME save-test\n'
            'FROM auto\n'
            'PROMPT "Old instructions."\n'
        )
        from routers import agentfiles as af_router
        original_dir = af_router.USER_AGENTFILES_DIR
        af_router.USER_AGENTFILES_DIR = tmp_path
        try:
            resp = await client.put(
                "/api/agentfiles/save-test/form",
                json={
                    "name": "save-test",
                    "description": "Updated",
                    "model": "claude-haiku-4-5",
                    "tools": ["read"],
                    "instructions": "New instructions.",
                    "tags": [],
                },
            )
            assert resp.status_code == 200
            updated_text = agent_path.read_text()
            assert "claude-haiku-4-5" in updated_text
            assert "New instructions." in updated_text
        finally:
            af_router.USER_AGENTFILES_DIR = original_dir

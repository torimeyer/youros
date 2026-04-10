"""Tests for the agentfiles router.

Covers listing, reading, creating, and launching Agentfile templates.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from routers.agentfiles import _parse_agentfile, _agentfile_to_text, AgentfileCreate


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- parse tests ----------

class TestParseAgentfile:
    def test_parse_valid_agentfile(self, tmp_path):
        """A well-formed Agentfile should parse all directives correctly."""
        af = tmp_path / "example.agent"
        af.write_text(
            '# Agentfile — example agent\n'
            '# Does example things.\n'
            '\n'
            'FROM auto\n'
            'PROMPT "You are an example agent."\n'
            'TOOL shell\n'
            'TOOL file:read\n'
            'LIMIT tokens 150000\n'
            'BOOT ostk boot\n'
            'PIN default\n'
        )
        result = _parse_agentfile(af)
        assert result["name"] == "example"
        assert result["model"] == "auto"
        assert result["prompt"] == "You are an example agent."
        assert result["tools"] == ["shell", "file:read"]
        assert result["token_limit"] == 150000
        assert result["boot"] == "ostk boot"
        assert result["pin"] == "default"
        assert "example agent" in result["description"]

    def test_parse_missing_file(self, tmp_path):
        """Parsing a non-existent file should return defaults, not crash."""
        af = tmp_path / "missing.agent"
        result = _parse_agentfile(af)
        assert result["name"] == "missing"
        assert result["model"] == "auto"
        assert result["prompt"] == ""
        assert result["tools"] == []

    def test_parse_minimal_agentfile(self, tmp_path):
        """An Agentfile with only FROM and PROMPT should parse fine."""
        af = tmp_path / "minimal.agent"
        af.write_text('FROM opus\nPROMPT "Do the thing."\n')
        result = _parse_agentfile(af)
        assert result["model"] == "opus"
        assert result["prompt"] == "Do the thing."

    def test_parse_description_from_comment(self, tmp_path):
        """The description comes from the comment line after the title."""
        af = tmp_path / "desc.agent"
        af.write_text(
            '# Agentfile — my agent\n'
            '# This does something special.\n'
            'FROM auto\n'
        )
        result = _parse_agentfile(af)
        assert "my agent" in result["description"]
        assert "something special" in result["description"]


# ---------- serialization tests ----------

class TestAgentfileToText:
    def test_roundtrip(self, tmp_path):
        """Creating an Agentfile and parsing it back should preserve fields."""
        data = AgentfileCreate(
            name="roundtrip",
            prompt="Hello world",
            model="opus",
            tools=["shell", "file:read"],
            token_limit=100000,
            description="Test round trip",
        )
        text = _agentfile_to_text(data)
        af = tmp_path / "roundtrip.agent"
        af.write_text(text)

        parsed = _parse_agentfile(af)
        assert parsed["model"] == "opus"
        assert parsed["prompt"] == "Hello world"
        assert "shell" in parsed["tools"]
        assert "file:read" in parsed["tools"]
        assert parsed["token_limit"] == 100000

    def test_name_sanitization(self):
        """Names with spaces and special chars should be cleaned."""
        data = AgentfileCreate(
            name="My Cool Agent!",
            prompt="test",
        )
        text = _agentfile_to_text(data)
        assert "# Agentfile" in text
        assert "My Cool Agent!" in text


# ---------- endpoint tests ----------

class TestListEndpoint:
    @pytest.mark.asyncio
    async def test_list_agentfiles(self, client, tmp_path):
        """GET /api/agentfiles should return built-in templates."""
        with patch("routers.agentfiles.AGENTS_DIR", tmp_path):
            # Create a test Agentfile
            af = tmp_path / "test.agent"
            af.write_text('FROM auto\nPROMPT "test"\n')

            resp = await client.get("/api/agentfiles")
            assert resp.status_code == 200
            data = resp.json()
            assert "agentfiles" in data
            names = [a["name"] for a in data["agentfiles"]]
            assert "test" in names

    @pytest.mark.asyncio
    async def test_list_includes_user_agentfiles(self, client, tmp_path):
        """User-created Agentfiles in ~/.myos/agentfiles/ should also appear."""
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()

        (builtin_dir / "builtin.agent").write_text('FROM auto\nPROMPT "builtin"\n')
        (user_dir / "custom.agent").write_text('FROM opus\nPROMPT "custom"\n')

        with patch("routers.agentfiles.AGENTS_DIR", builtin_dir), \
             patch("routers.agentfiles.USER_AGENTFILES_DIR", user_dir):
            resp = await client.get("/api/agentfiles")
            assert resp.status_code == 200
            names = [a["name"] for a in resp.json()["agentfiles"]]
            assert "builtin" in names
            assert "custom" in names


class TestGetEndpoint:
    @pytest.mark.asyncio
    async def test_get_existing(self, client, tmp_path):
        """GET /api/agentfiles/{name} should return parsed config and raw text."""
        af = tmp_path / "myagent.agent"
        af.write_text('FROM opus\nPROMPT "do stuff"\nTOOL shell\n')

        with patch("routers.agentfiles.AGENTS_DIR", tmp_path):
            resp = await client.get("/api/agentfiles/myagent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "myagent"
            assert data["model"] == "opus"
            assert data["prompt"] == "do stuff"
            assert "raw" in data
            assert "FROM opus" in data["raw"]

    @pytest.mark.asyncio
    async def test_get_not_found(self, client, tmp_path):
        """GET /api/agentfiles/{name} for a missing file should return 404."""
        with patch("routers.agentfiles.AGENTS_DIR", tmp_path), \
             patch("routers.agentfiles.USER_AGENTFILES_DIR", tmp_path / "nope"):
            resp = await client.get("/api/agentfiles/nonexistent")
            assert resp.status_code == 404


class TestCreateEndpoint:
    @pytest.mark.asyncio
    async def test_create_agentfile(self, client, tmp_path):
        """POST /api/agentfiles should write a new file in the user directory."""
        with patch("routers.agentfiles.USER_AGENTFILES_DIR", tmp_path):
            resp = await client.post("/api/agentfiles", json={
                "name": "my-custom",
                "prompt": "Do custom things",
                "model": "opus",
                "tools": ["shell"],
                "token_limit": 50000,
                "description": "A custom agent",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["created"] == "my-custom"

            # Verify file was written
            created_file = tmp_path / "my-custom.agent"
            assert created_file.exists()
            content = created_file.read_text()
            assert "FROM opus" in content
            assert "Do custom things" in content

    @pytest.mark.asyncio
    async def test_create_bad_name(self, client, tmp_path):
        """POST /api/agentfiles with an empty name should return 400."""
        with patch("routers.agentfiles.USER_AGENTFILES_DIR", tmp_path):
            resp = await client.post("/api/agentfiles", json={
                "name": "!!!",
                "prompt": "test",
            })
            assert resp.status_code == 400


class TestRunEndpoint:
    @pytest.mark.asyncio
    async def test_run_not_found(self, client, tmp_path):
        """POST /api/agentfiles/{name}/run for a missing file should return 404."""
        with patch("routers.agentfiles.AGENTS_DIR", tmp_path), \
             patch("routers.agentfiles.USER_AGENTFILES_DIR", tmp_path / "nope"):
            resp = await client.post("/api/agentfiles/nonexistent/run")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_run_agentfile(self, client, tmp_path):
        """POST /api/agentfiles/{name}/run should launch ostk run as a subprocess."""
        af = tmp_path / "runner.agent"
        af.write_text('FROM auto\nPROMPT "run test"\n')

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("routers.agentfiles.AGENTS_DIR", tmp_path), \
             patch("routers.agentfiles.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch("routers.agentfiles.asyncio.sleep", new_callable=AsyncMock):
            mock_exec.return_value = mock_proc
            resp = await client.post("/api/agentfiles/runner/run", json={
                "env_passthrough": ["API_KEY"],
                "runtime": "host",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["launched"] == "runner"
            assert data["pid"] == 12345

            # Verify the command was called with correct args
            call_args = mock_exec.call_args[0]
            assert "ostk" in call_args[0]
            assert "run" in call_args
            assert "--env-passthrough" in call_args
            assert "API_KEY" in call_args

    @pytest.mark.asyncio
    async def test_run_ostk_not_found(self, client, tmp_path):
        """If ostk is not on PATH, the endpoint should return 500."""
        af = tmp_path / "noostk.agent"
        af.write_text('FROM auto\nPROMPT "test"\n')

        with patch("routers.agentfiles.AGENTS_DIR", tmp_path), \
             patch("routers.agentfiles.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = FileNotFoundError("ostk not found")
            resp = await client.post("/api/agentfiles/noostk/run")
            assert resp.status_code == 500
            assert "ostk CLI not found" in resp.json()["detail"]

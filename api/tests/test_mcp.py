"""Tests for MCP server integration in ToriChat.

Covers:
- MCP server config stored and retrieved from settings
- _get_mcp_servers filters disabled and URL-less entries
- agent_anthropic uses the beta API when MCP servers are configured
- mcp_tool_use and mcp_tool_result blocks are emitted over WebSocket
- ostk mcp_list() parsing and the /settings/mcp-servers endpoint
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Settings schema tests ---


def test_settings_schema_has_mcp_servers_field():
    from models.schemas import Settings
    s = Settings()
    assert hasattr(s, "mcp_servers")
    assert s.mcp_servers == []


def test_settings_schema_mcp_servers_stored_correctly():
    from models.schemas import Settings
    s = Settings(mcp_servers=[{"name": "stitch", "url": "https://stitch.example.com", "enabled": True}])
    assert len(s.mcp_servers) == 1
    assert s.mcp_servers[0]["name"] == "stitch"


# --- API settings tests ---


@pytest.fixture
def settings_file_with_mcp(tmp_path):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({
        "os_name": "ToriOS",
        "dark_mode": True,
        "accent_color": "blue",
        "default_model": "@claude",
        "features": {"Chat": True, "Tasks": True, "Hay/Ideas": True, "Agents": True, "Projects": True, "Docs": True, "Transcripts": False},
        "notifications": {"agent_complete": True, "agent_needs_input": True, "agent_failed": True, "approval_needed": True},
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "mcp_servers": [
            {"name": "stitch", "url": "https://stitch.example.com", "auth_token": "", "enabled": True},
        ],
    }))
    return sf


@pytest.mark.asyncio
async def test_get_settings_returns_mcp_servers(client, settings_file_with_mcp):
    with patch("services.settings_store.SETTINGS_PATH", settings_file_with_mcp):
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "mcp_servers" in data
    assert len(data["mcp_servers"]) == 1
    assert data["mcp_servers"][0]["name"] == "stitch"


@pytest.mark.asyncio
async def test_patch_settings_adds_mcp_server(client, settings_file_with_mcp):
    new_servers = [
        {"name": "stitch", "url": "https://stitch.example.com", "auth_token": "", "enabled": True},
        {"name": "gmail", "url": "https://gmail-mcp.example.com", "auth_token": "tok123", "enabled": True},
    ]
    with patch("services.settings_store.SETTINGS_PATH", settings_file_with_mcp):
        resp = await client.patch("/api/settings", json={"mcp_servers": new_servers})
    assert resp.status_code == 200
    saved = json.loads(settings_file_with_mcp.read_text())
    assert len(saved["mcp_servers"]) == 2
    assert saved["mcp_servers"][1]["name"] == "gmail"


@pytest.mark.asyncio
async def test_patch_settings_removes_mcp_server(client, settings_file_with_mcp):
    with patch("services.settings_store.SETTINGS_PATH", settings_file_with_mcp):
        resp = await client.patch("/api/settings", json={"mcp_servers": []})
    assert resp.status_code == 200
    saved = json.loads(settings_file_with_mcp.read_text())
    assert saved["mcp_servers"] == []


# --- _get_mcp_servers filtering tests ---


def test_get_mcp_servers_returns_enabled_with_url():
    from services.chat_providers import ChatService
    svc = ChatService()
    servers = [
        {"name": "stitch", "url": "https://stitch.example.com", "enabled": True},
        {"name": "disabled", "url": "https://example.com", "enabled": False},
        {"name": "no_url", "url": "", "enabled": True},
    ]
    with patch("services.chat_providers.settings_store") as mock_store:
        mock_store.get.return_value = servers
        result = svc._get_mcp_servers()

    assert len(result) == 1
    assert result[0]["name"] == "stitch"


def test_get_mcp_servers_empty_when_none_configured():
    from services.chat_providers import ChatService
    svc = ChatService()
    with patch("services.chat_providers.settings_store") as mock_store:
        mock_store.get.return_value = []
        result = svc._get_mcp_servers()
    assert result == []


def test_get_mcp_servers_treats_missing_enabled_as_true():
    from services.chat_providers import ChatService
    svc = ChatService()
    servers = [{"name": "stitch", "url": "https://stitch.example.com"}]
    with patch("services.chat_providers.settings_store") as mock_store:
        mock_store.get.return_value = servers
        result = svc._get_mcp_servers()
    assert len(result) == 1


# --- agent_anthropic uses beta API when MCP servers present ---


@pytest.mark.asyncio
async def test_agent_anthropic_uses_beta_when_mcp_configured():
    """When MCP servers are configured, the beta API must be used."""
    from services.chat_providers import ChatService
    svc = ChatService()

    fake_ws = AsyncMock()
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "Hello from MCP"

    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_response.usage.input_tokens = 10
    fake_response.usage.output_tokens = 5

    beta_create = AsyncMock(return_value=fake_response)
    regular_create = AsyncMock(return_value=fake_response)

    fake_client = MagicMock()
    fake_client.beta.messages.create = beta_create
    fake_client.messages.create = regular_create

    mcp_servers = [{"name": "stitch", "url": "https://stitch.example.com", "auth_token": "", "enabled": True}]

    with patch("services.chat_providers.settings_store") as mock_store, \
         patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")), \
         patch("anthropic.AsyncAnthropic", return_value=fake_client):
        mock_store.get.side_effect = lambda key, default=None: (
            "sk-test" if key == "anthropic_api_key" else
            mcp_servers if key == "mcp_servers" else
            default
        )
        await svc.agent_anthropic([{"role": "user", "content": "hi"}], fake_ws)

    beta_create.assert_called_once()
    regular_create.assert_not_called()
    call_kwargs = beta_create.call_args.kwargs
    assert "mcp_servers" in call_kwargs
    assert call_kwargs["betas"] == ["mcp-client-2025-04-04"]


@pytest.mark.asyncio
async def test_agent_anthropic_uses_regular_api_without_mcp():
    """Without MCP servers configured, the regular (non-beta) API is used."""
    from services.chat_providers import ChatService
    svc = ChatService()

    fake_ws = AsyncMock()
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "Hello"

    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_response.usage.input_tokens = 5
    fake_response.usage.output_tokens = 3

    beta_create = AsyncMock(return_value=fake_response)
    regular_create = AsyncMock(return_value=fake_response)

    fake_client = MagicMock()
    fake_client.beta.messages.create = beta_create
    fake_client.messages.create = regular_create

    with patch("services.chat_providers.settings_store") as mock_store, \
         patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")), \
         patch("anthropic.AsyncAnthropic", return_value=fake_client):
        mock_store.get.side_effect = lambda key, default=None: (
            "sk-test" if key == "anthropic_api_key" else
            [] if key == "mcp_servers" else
            default
        )
        await svc.agent_anthropic([{"role": "user", "content": "hi"}], fake_ws)

    regular_create.assert_called_once()
    beta_create.assert_not_called()


@pytest.mark.asyncio
async def test_agent_anthropic_emits_mcp_tool_use_event():
    """mcp_tool_use blocks must be sent to the frontend via WebSocket."""
    from services.chat_providers import ChatService
    svc = ChatService()

    fake_ws = AsyncMock()

    mcp_use_block = MagicMock()
    mcp_use_block.type = "mcp_tool_use"
    mcp_use_block.id = "mcp-1"
    mcp_use_block.name = "create_design"
    mcp_use_block.server_name = "stitch"
    mcp_use_block.input = {"prompt": "make it blue"}

    mcp_result_block = MagicMock()
    mcp_result_block.type = "mcp_tool_result"
    mcp_result_block.tool_use_id = "mcp-1"
    mcp_result_block.content = "Design created"
    mcp_result_block.is_error = False

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Done!"

    fake_response = MagicMock()
    fake_response.content = [mcp_use_block, mcp_result_block, text_block]
    fake_response.usage.input_tokens = 20
    fake_response.usage.output_tokens = 10

    fake_client = MagicMock()
    fake_client.beta.messages.create = AsyncMock(return_value=fake_response)

    mcp_servers = [{"name": "stitch", "url": "https://stitch.example.com", "enabled": True}]

    with patch("services.chat_providers.settings_store") as mock_store, \
         patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")), \
         patch("anthropic.AsyncAnthropic", return_value=fake_client):
        mock_store.get.side_effect = lambda key, default=None: (
            "sk-test" if key == "anthropic_api_key" else
            mcp_servers if key == "mcp_servers" else
            default
        )
        await svc.agent_anthropic([{"role": "user", "content": "create a design"}], fake_ws)

    sent_types = [call.args[0]["type"] for call in fake_ws.send_json.call_args_list]
    assert "mcp_tool_use" in sent_types
    assert "mcp_tool_result" in sent_types
    assert "token" in sent_types
    assert "done" in sent_types

    mcp_use_msg = next(c.args[0] for c in fake_ws.send_json.call_args_list if c.args[0]["type"] == "mcp_tool_use")
    assert mcp_use_msg["data"]["tool"] == "create_design"
    assert mcp_use_msg["data"]["server"] == "stitch"


# --- OstkService.mcp_list() parsing tests ---


def test_parse_mcp_list_tabular_format():
    """Parses tab-separated output with header and separator lines."""
    from services.ostk import OstkService
    raw = (
        "name    command\n"
        "────    ───────\n"
        "linear  npx -y @anthropic/linear-mcp-server\n"
        "github  gh mcp-server\n"
    )
    result = OstkService._parse_mcp_list(raw)
    assert len(result) == 2
    assert result[0] == {"name": "linear", "command": "npx -y @anthropic/linear-mcp-server"}
    assert result[1] == {"name": "github", "command": "gh mcp-server"}


def test_parse_mcp_list_colon_format():
    """Parses 'name: command' format."""
    from services.ostk import OstkService
    raw = "linear: npx -y @anthropic/linear-mcp-server\ngithub: gh mcp-server"
    result = OstkService._parse_mcp_list(raw)
    assert len(result) == 2
    assert result[0]["name"] == "linear"
    assert result[0]["command"] == "npx -y @anthropic/linear-mcp-server"


def test_parse_mcp_list_empty_string():
    """Empty output returns empty list."""
    from services.ostk import OstkService
    assert OstkService._parse_mcp_list("") == []


def test_parse_mcp_list_only_headers():
    """Output with only headers and separator lines returns empty list."""
    from services.ostk import OstkService
    raw = "name    command\n────    ───────\n"
    result = OstkService._parse_mcp_list(raw)
    assert result == []


@pytest.mark.asyncio
async def test_mcp_list_returns_empty_when_none_configured():
    """mcp_list returns [] when ostk reports no configured servers."""
    from services.ostk import OstkService
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "no MCP servers configured\n\nadd to .ostk/HUMANFILE:\n\n  mcp:\n    linear: npx -y @anthropic/linear-mcp-server"
        result = await svc.mcp_list()
    assert result == []


@pytest.mark.asyncio
async def test_mcp_list_returns_parsed_servers():
    """mcp_list returns parsed server list when servers are configured."""
    from services.ostk import OstkService
    svc = OstkService()
    raw_output = (
        "name    command\n"
        "────    ───────\n"
        "linear  npx -y @anthropic/linear-mcp-server\n"
        "github  gh mcp-server\n"
    )
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = raw_output
        result = await svc.mcp_list()
    assert len(result) == 2
    assert result[0]["name"] == "linear"


@pytest.mark.asyncio
async def test_mcp_list_returns_empty_on_error():
    """mcp_list returns [] when ostk command fails."""
    from services.ostk import OstkService, OstkError
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = OstkError("command failed")
        result = await svc.mcp_list()
    assert result == []


# --- /settings/mcp-servers endpoint tests ---


@pytest.fixture
def settings_file_for_mcp_endpoint(tmp_path):
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({
        "os_name": "ToriOS",
        "mcp_servers": [
            {"name": "stitch", "url": "https://stitch.example.com", "auth_token": "", "enabled": True},
        ],
    }))
    return sf


@pytest.mark.asyncio
async def test_mcp_servers_endpoint_returns_both_sources(client, settings_file_for_mcp_endpoint):
    """The /settings/mcp-servers endpoint returns ostk and manual servers."""
    ostk_servers = [{"name": "linear", "command": "npx -y @anthropic/linear-mcp-server"}]

    with patch("services.settings_store.SETTINGS_PATH", settings_file_for_mcp_endpoint), \
         patch("routers.settings.ostk") as mock_ostk:
        mock_ostk.mcp_list = AsyncMock(return_value=ostk_servers)
        resp = await client.get("/api/settings/mcp-servers")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["ostk_servers"]) == 1
    assert data["ostk_servers"][0]["name"] == "linear"
    assert len(data["manual_servers"]) == 1
    assert data["manual_servers"][0]["name"] == "stitch"


@pytest.mark.asyncio
async def test_mcp_servers_endpoint_empty_ostk(client, settings_file_for_mcp_endpoint):
    """Returns empty ostk list when no servers are configured in ostk."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file_for_mcp_endpoint), \
         patch("routers.settings.ostk") as mock_ostk:
        mock_ostk.mcp_list = AsyncMock(return_value=[])
        resp = await client.get("/api/settings/mcp-servers")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ostk_servers"] == []
    assert len(data["manual_servers"]) == 1


@pytest.mark.asyncio
async def test_mcp_servers_endpoint_no_manual_servers(client, tmp_path):
    """Returns empty manual list when no servers are in settings."""
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"os_name": "ToriOS"}))

    ostk_servers = [{"name": "github", "command": "gh mcp-server"}]
    with patch("services.settings_store.SETTINGS_PATH", sf), \
         patch("routers.settings.ostk") as mock_ostk:
        mock_ostk.mcp_list = AsyncMock(return_value=ostk_servers)
        resp = await client.get("/api/settings/mcp-servers")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["ostk_servers"]) == 1
    assert data["manual_servers"] == []

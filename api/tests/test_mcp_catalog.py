import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_get_catalog_returns_all_entries(client):
    resp = await client.get("/api/mcp/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "catalog" in data
    assert len(data["catalog"]) >= 16
    names = {e["name"] for e in data["catalog"]}
    assert "Filesystem" in names
    assert "GitHub" in names
    assert "Notion" in names
    assert "Airtable" in names


@pytest.mark.asyncio
async def test_get_catalog_entry_shape(client):
    resp = await client.get("/api/mcp/catalog")
    entry = resp.json()["catalog"][0]
    assert "name" in entry
    assert "description" in entry
    assert "npm_package" in entry
    assert "requires_auth" in entry


@pytest.mark.asyncio
async def test_install_mcp_success(client):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("routers.mcp_catalog.subprocess.run", return_value=mock_result):
        resp = await client.post("/api/mcp/install", json={"name": "GitHub"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "successfully" in data["message"]


@pytest.mark.asyncio
async def test_install_mcp_failure(client):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "not found"

    with patch("routers.mcp_catalog.subprocess.run", return_value=mock_result):
        resp = await client.post("/api/mcp/install", json={"name": "BadTool"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "went wrong" in data["message"]


@pytest.mark.asyncio
async def test_install_mcp_missing_name(client):
    resp = await client.post("/api/mcp/install", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_get_installed(client):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "GitHub\nSlack\nNotion\n"
    mock_result.stderr = ""

    with patch("routers.mcp_catalog.subprocess.run", return_value=mock_result):
        resp = await client.get("/api/mcp/installed")

    assert resp.status_code == 200
    data = resp.json()
    assert "installed" in data
    assert "GitHub" in data["installed"]
    assert "Slack" in data["installed"]


@pytest.mark.asyncio
async def test_get_installed_cli_failure(client):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "command not found"

    with patch("routers.mcp_catalog.subprocess.run", return_value=mock_result):
        resp = await client.get("/api/mcp/installed")

    assert resp.status_code == 200
    assert resp.json()["installed"] == []

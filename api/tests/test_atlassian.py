"""Tests for Atlassian (Jira + Confluence) integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- GET /api/atlassian/defaults ---

@pytest.mark.asyncio
async def test_atlassian_defaults_no_env(client):
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("ATLASSIAN_SITE", None)
        resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    assert resp.json() == {"site": "", "email": ""}


@pytest.mark.asyncio
async def test_atlassian_defaults_with_env(client):
    with patch.dict("os.environ", {"ATLASSIAN_SITE": "https://company.atlassian.net"}):
        resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    assert resp.json() == {"site": "https://company.atlassian.net", "email": ""}


@pytest.mark.asyncio
async def test_atlassian_defaults_saved_config_provides_email(client):
    """Saved config email is exposed by /defaults even when ATLASSIAN_SITE env is absent."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "ATLASSIAN_SITE"}
    with patch.dict("os.environ", env, clear=True):
        with patch("routers.atlassian.atlassian_service") as mock_svc:
            mock_svc.get_config.return_value = {
                "site": "https://saved.atlassian.net",
                "email": "saved@example.com",
            }
            resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site"] == "https://saved.atlassian.net"
    assert data["email"] == "saved@example.com"


@pytest.mark.asyncio
async def test_atlassian_defaults_env_overrides_saved_site_but_email_preserved(client):
    """When ATLASSIAN_SITE env is set, it wins over saved site; saved email is still returned."""
    with patch.dict("os.environ", {"ATLASSIAN_SITE": "https://env.atlassian.net"}):
        with patch("routers.atlassian.atlassian_service") as mock_svc:
            mock_svc.get_config.return_value = {
                "site": "https://saved.atlassian.net",
                "email": "saved@example.com",
            }
            resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site"] == "https://env.atlassian.net"   # env wins
    assert data["email"] == "saved@example.com"           # email from saved config


# --- GET /api/atlassian/status ---

@pytest.mark.asyncio
async def test_atlassian_status_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["email"] == ""
    assert data["site"] == ""


@pytest.mark.asyncio
async def test_atlassian_status_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_config.return_value = {"email": "user@example.com", "site": "example.atlassian.net"}
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["email"] == "user@example.com"
    assert data["site"] == "example.atlassian.net"


# --- POST /api/atlassian/connect ---

@pytest.mark.asyncio
async def test_atlassian_connect_missing_email(client):
    resp = await client.post("/api/atlassian/connect", json={
        "email": "", "api_token": "token", "site": "example.atlassian.net"
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_atlassian_connect_missing_token(client):
    resp = await client.post("/api/atlassian/connect", json={
        "email": "user@example.com", "api_token": "", "site": "example.atlassian.net"
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_atlassian_connect_success(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(return_value={
            "account_id": "abc123",
            "display_name": "Tori Meyer",
            "email": "user@example.com",
        })
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "user@example.com",
            "api_token": "ATATT3x...",
            "site": "example.atlassian.net",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["user"]["display_name"] == "Tori Meyer"


@pytest.mark.asyncio
async def test_atlassian_connect_bad_creds(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(
            side_effect=RuntimeError("Invalid email or API token. Check your credentials and try again.")
        )
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "wrong@example.com",
            "api_token": "bad-token",
            "site": "example.atlassian.net",
        })

    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


# --- DELETE /api/atlassian/disconnect ---

@pytest.mark.asyncio
async def test_atlassian_disconnect(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.disconnect = AsyncMock()
        resp = await client.request("DELETE", "/api/atlassian/disconnect")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --- GET /api/atlassian/jira/issues ---

@pytest.mark.asyncio
async def test_jira_issues_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/jira/issues")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jira_issues_success(client):
    issues = [
        {
            "key": "JIRA-123",
            "summary": "Fix login bug",
            "status": "In Progress",
            "priority": "High",
            "type": "Bug",
            "updated": "2026-04-30T00:00:00Z",
            "url": "https://example.atlassian.net/browse/JIRA-123",
        }
    ]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_assigned_issues = AsyncMock(return_value=issues)
        resp = await client.get("/api/atlassian/jira/issues")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["key"] == "JIRA-123"


# --- GET /api/atlassian/jira/issue/{key} ---

@pytest.mark.asyncio
async def test_jira_get_issue_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/jira/issue/JIRA-123")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jira_get_issue_success(client):
    detail = {
        "key": "JIRA-123",
        "summary": "Fix login bug",
        "description_html": "<p>Details here</p>",
        "status": "In Progress",
        "priority": "High",
        "type": "Bug",
        "assignee": "Tori Meyer",
        "reporter": "Alice",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-04-30T00:00:00Z",
        "url": "https://example.atlassian.net/browse/JIRA-123",
        "comments": [],
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_issue = AsyncMock(return_value=detail)
        resp = await client.get("/api/atlassian/jira/issue/JIRA-123")

    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "JIRA-123"
    assert "<p>Details here</p>" in data["description_html"]


# --- GET /api/atlassian/confluence/pages ---

@pytest.mark.asyncio
async def test_confluence_pages_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/pages")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confluence_pages_success(client):
    pages = [
        {
            "id": "12345",
            "title": "IAM Strategy Doc",
            "space_id": "~space1",
            "updated": "2026-04-29T00:00:00Z",
            "url": "https://example.atlassian.net/wiki/spaces/~space1/pages/12345",
        }
    ]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_recent_pages = AsyncMock(return_value=pages)
        resp = await client.get("/api/atlassian/confluence/pages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pages"]) == 1
    assert data["pages"][0]["title"] == "IAM Strategy Doc"


# --- GET /api/atlassian/confluence/page/{page_id} ---

@pytest.mark.asyncio
async def test_confluence_get_page_success(client):
    page_detail = {
        "id": "12345",
        "title": "IAM Strategy Doc",
        "space_id": "~space1",
        "body_html": "<h1>Strategy</h1><p>Content here.</p>",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-04-29T00:00:00Z",
        "url": "https://example.atlassian.net/wiki/spaces/~space1/pages/12345",
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_page = AsyncMock(return_value=page_detail)
        resp = await client.get("/api/atlassian/confluence/page/12345")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "12345"
    assert "Strategy" in data["body_html"]


@pytest.mark.asyncio
async def test_confluence_get_page_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/page/12345")

    assert resp.status_code == 401


# --- Service unit tests ---

class TestAtlassianService:
    def test_is_connected_false(self, tmp_path):
        with patch("services.atlassian.CONFIG_PATH", tmp_path / "no_file.json"):
            from services.atlassian import is_connected
            assert is_connected() is False

    def test_is_connected_true(self, tmp_path):
        config_path = tmp_path / "atlassian.json"
        config_path.write_text('{"email": "u@e.com", "site": "x.atlassian.net"}')
        with patch("services.atlassian.CONFIG_PATH", config_path):
            from services.atlassian import is_connected
            assert is_connected() is True

    def test_get_config_not_connected(self, tmp_path):
        with patch("services.atlassian.CONFIG_PATH", tmp_path / "no_file.json"):
            from services import atlassian as svc
            svc._config_cache = None
            svc._config_cache_mtime = 0.0
            with pytest.raises(RuntimeError, match="Not connected"):
                svc.get_config()

    def test_get_config_env_fallback_for_missing_site(self, tmp_path):
        config_path = tmp_path / "atlassian.json"
        config_path.write_text('{"email": "u@e.com", "site": ""}')
        with patch("services.atlassian.CONFIG_PATH", config_path):
            with patch.dict("os.environ", {"ATLASSIAN_SITE": "https://company.atlassian.net"}):
                from services import atlassian as svc
                svc._config_cache = None
                svc._config_cache_mtime = 0.0
                config = svc.get_config()
                assert config["site"] == "https://company.atlassian.net"

    @pytest.mark.asyncio
    async def test_verify_creds_401(self, tmp_path):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from services.atlassian import verify_creds

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Invalid email or API token"):
                await verify_creds("u@e.com", "bad", "test.atlassian.net")

    @pytest.mark.asyncio
    async def test_verify_creds_404(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from services.atlassian import verify_creds

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Site not found"):
                await verify_creds("u@e.com", "tok", "notexist.atlassian.net")


# --- 2-way actions: comment, transitions, transition, assign ---

@pytest.mark.asyncio
async def test_add_comment_success(client):
    comment_result = {
        "id": "10001",
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Looks good"}]}],
        },
        "author": {"displayName": "Tori Meyer"},
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.add_comment = AsyncMock(return_value=comment_result)
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/comment",
            json={"body": "Looks good"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["comment"]["id"] == "10001"
    body = data["comment"]["body"]
    assert body["type"] == "doc"
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_add_comment_empty_body(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/comment",
            json={"body": "   "},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_add_comment_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/comment",
            json={"body": "hello"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_transitions_success(client):
    transitions = [
        {"id": "11", "name": "To Do", "to_status": "To Do"},
        {"id": "21", "name": "In Progress", "to_status": "In Progress"},
        {"id": "31", "name": "Done", "to_status": "Done"},
    ]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_transitions = AsyncMock(return_value=transitions)
        resp = await client.get("/api/atlassian/jira/issue/JIRA-1/transitions")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transitions"]) == 3
    assert data["transitions"][0]["id"] == "11"
    assert data["transitions"][2]["to_status"] == "Done"


@pytest.mark.asyncio
async def test_transition_issue_success(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.transition_issue = AsyncMock(return_value=None)
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/transition",
            json={"transition_id": "31"},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_svc.transition_issue.assert_awaited_once_with("JIRA-1", "31")


@pytest.mark.asyncio
async def test_assign_issue_success(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.assign_issue = AsyncMock(return_value=None)
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/assign",
            json={"account_id": "abc123"},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_svc.assign_issue.assert_awaited_once_with("JIRA-1", "abc123")


@pytest.mark.asyncio
async def test_assign_issue_unassign(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.assign_issue = AsyncMock(return_value=None)
        resp = await client.post(
            "/api/atlassian/jira/issue/JIRA-1/assign",
            json={"account_id": None},
        )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_svc.assign_issue.assert_awaited_once_with("JIRA-1", None)


# --- Service unit tests for 2-way actions ---

class TestAtlassianServiceActions:
    @pytest.mark.asyncio
    async def test_add_comment_adf_shape(self):
        """add_comment sends correct Atlassian Document Format body."""
        from services.atlassian import add_comment

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "10001"}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                result = await add_comment("JIRA-1", "Hello world")

        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs.kwargs["json"]
        assert sent_json["body"]["type"] == "doc"
        assert sent_json["body"]["version"] == 1
        content = sent_json["body"]["content"]
        assert content[0]["type"] == "paragraph"
        assert content[0]["content"][0]["text"] == "Hello world"
        assert result == {"id": "10001"}

    @pytest.mark.asyncio
    async def test_add_comment_4xx_raises(self):
        from services.atlassian import add_comment

        mock_resp = MagicMock()
        mock_resp.status_code = 403

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(RuntimeError, match="Jira API error"):
                    await add_comment("JIRA-1", "oops")

    @pytest.mark.asyncio
    async def test_list_transitions_success(self):
        from services.atlassian import list_transitions

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "transitions": [
                {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
                {"id": "21", "name": "Done", "to": {"name": "Done"}},
            ]
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                result = await list_transitions("JIRA-1")

        assert len(result) == 2
        assert result[0] == {"id": "11", "name": "To Do", "to_status": "To Do"}
        assert result[1] == {"id": "21", "name": "Done", "to_status": "Done"}

    @pytest.mark.asyncio
    async def test_transition_issue_204(self):
        from services.atlassian import transition_issue

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await transition_issue("JIRA-1", "31")

        sent_json = mock_client.post.call_args.kwargs["json"]
        assert sent_json == {"transition": {"id": "31"}}

    @pytest.mark.asyncio
    async def test_assign_issue_success(self):
        from services.atlassian import assign_issue

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await assign_issue("JIRA-1", "user123")

        sent_json = mock_client.put.call_args.kwargs["json"]
        assert sent_json == {"accountId": "user123"}

    @pytest.mark.asyncio
    async def test_assign_issue_unassign(self):
        from services.atlassian import assign_issue

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=(MagicMock(), "https://example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await assign_issue("JIRA-1", None)

        sent_json = mock_client.put.call_args.kwargs["json"]
        assert sent_json == {"accountId": None}

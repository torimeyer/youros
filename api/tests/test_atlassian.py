"""Tests for Atlassian (Jira + Confluence) integration."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- GET /api/atlassian/defaults ---

@pytest.mark.asyncio
async def test_atlassian_defaults_no_env(client, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    monkeypatch.delenv("ATLASSIAN_USER_EMAIL", raising=False)
    # Also patch atlassian_service to ensure no saved config is read (→2042)
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.get_config.side_effect = RuntimeError("Not connected")
        resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site"] == ""
    assert data["email"] == ""
    assert "oauth_available" in data


@pytest.mark.asyncio
async def test_atlassian_defaults_with_env(client):
    env = {k: v for k, v in os.environ.items() if k not in ("ATLASSIAN_SITE", "ATLASSIAN_CLIENT_ID")}
    env["ATLASSIAN_SITE"] = "https://company.atlassian.net"
    with patch.dict("os.environ", env, clear=True):
        # Also patch atlassian_service to ensure no saved config is read (→2042)
        with patch("routers.atlassian.atlassian_service") as mock_svc:
            mock_svc.get_config.side_effect = RuntimeError("Not connected")
            resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["site"] == "https://company.atlassian.net"
    assert data["email"] == ""
    assert data["oauth_available"] is False


@pytest.mark.asyncio
async def test_atlassian_defaults_oauth_available_when_client_id_set(client):
    env = {k: v for k, v in os.environ.items() if k not in ("ATLASSIAN_SITE",)}
    env["ATLASSIAN_CLIENT_ID"] = "test-client-id"
    with patch.dict("os.environ", env, clear=True):
        resp = await client.get("/api/atlassian/defaults")
    assert resp.status_code == 200
    assert resp.json()["oauth_available"] is True


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
        mock_svc.probe_token_validity = AsyncMock(return_value=True)
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
            "display_name": "Test User",
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
    assert data["user"]["display_name"] == "Test User"


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
        "assignee": "Test User",
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
                # ATLASSIAN_SITE env override now populates jira_site (and confluence_site).
                assert config["jira_site"] == "https://company.atlassian.net"

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
        "author": {"displayName": "Test User"},
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
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
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await assign_issue("JIRA-1", None)

        sent_json = mock_client.put.call_args.kwargs["json"]
        assert sent_json == {"accountId": None}

    @pytest.mark.asyncio
    async def test_list_assigned_issues_uses_search_jql_post(self):
        from services.atlassian import list_assigned_issues

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issues": []}

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian._get_auth_and_base", AsyncMock(
            return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
        )):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                with patch("services.atlassian._cache_get", return_value=None):
                    with patch("services.atlassian._cache_set"):
                        await list_assigned_issues()

        call_args = mock_client.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert url.endswith("/rest/api/3/search/jql"), f"Expected POST to /rest/api/3/search/jql, got: {url}"
        body = call_args.kwargs["json"]
        assert "jql" in body, "Body must contain 'jql'"
        assert isinstance(body["fields"], list), "fields must be a list, not a comma-separated string"


# --- Widget query readers (spec jira-and-confluence-dashboard-widgets, →2651) ---


def _http_client_mock(resp, method="post"):
    """Build an httpx.AsyncClient stand-in whose ``method`` returns ``resp``."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    setattr(mock_client, method, AsyncMock(return_value=resp))
    return mock_client


def _json_resp(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _auth_ok():
    return patch("services.atlassian._get_auth_and_base", AsyncMock(
        return_value=({"auth": MagicMock()}, "https://example.atlassian.net", "example.atlassian.net")
    ))


def _auth_disconnected():
    return patch("services.atlassian._get_auth_and_base", AsyncMock(
        side_effect=RuntimeError("Not connected to Atlassian.")
    ))


@pytest.fixture()
def fresh_atlassian_cache():
    from services import atlassian as svc
    svc._response_cache.clear()
    yield
    svc._response_cache.clear()


class TestAssignedIssuesDueDate:
    """→2651a: list_assigned_issues carries the due date through."""

    @pytest.mark.asyncio
    async def test_due_date_passthrough(self, fresh_atlassian_cache):
        from services.atlassian import list_assigned_issues

        payload = {"issues": [{
            "key": "JIRA-1",
            "fields": {"summary": "Fix it", "duedate": "2026-07-15",
                       "updated": "2026-07-01T00:00:00Z"},
        }]}
        mock_client = _http_client_mock(_json_resp(payload))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                rows = await list_assigned_issues()

        assert rows[0]["due"] == "2026-07-15"
        sent = mock_client.post.call_args.kwargs["json"]
        assert "duedate" in sent["fields"]

    @pytest.mark.asyncio
    async def test_due_empty_when_missing(self, fresh_atlassian_cache):
        from services.atlassian import list_assigned_issues

        payload = {"issues": [{"key": "JIRA-2", "fields": {"summary": "No due"}}]}
        mock_client = _http_client_mock(_json_resp(payload))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                rows = await list_assigned_issues()

        assert rows[0]["due"] == ""


class TestRunJql:
    """→2651b: generic capped JQL reader for dashboard widgets."""

    @pytest.mark.asyncio
    async def test_happy_row_shape(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        payload = {"issues": [{
            "key": "JIRA-9",
            "fields": {
                "summary": "Widget row",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "issuetype": {"name": "Bug"},
                "updated": "2026-07-01T00:00:00Z",
                "duedate": "2026-07-12",
            },
        }]}
        mock_client = _http_client_mock(_json_resp(payload))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                rows = await run_jql("assignee = currentUser()")

        assert rows == [{
            "key": "JIRA-9",
            "summary": "Widget row",
            "status": "In Progress",
            "priority": "High",
            "type": "Bug",
            "updated": "2026-07-01T00:00:00Z",
            "due": "2026-07-12",
            "url": "https://example.atlassian.net/browse/JIRA-9",
        }]
        sent = mock_client.post.call_args.kwargs["json"]
        assert sent["jql"] == "assignee = currentUser()"
        assert sent["maxResults"] == 10
        assert "duedate" in sent["fields"]

    @pytest.mark.asyncio
    async def test_limit_capped_at_25(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        mock_client = _http_client_mock(_json_resp({"issues": []}))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await run_jql("order by updated", limit=100)

        assert mock_client.post.call_args.kwargs["json"]["maxResults"] == 25

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        mock_client = _http_client_mock(_json_resp({}, status_code=500))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await run_jql("q1") == []

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self, fresh_atlassian_cache):
        import httpx
        from services.atlassian import run_jql

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await run_jql("q2") == []

    @pytest.mark.asyncio
    async def test_disconnected_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        with _auth_disconnected():
            assert await run_jql("q3") == []

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        mock_client = _http_client_mock(_json_resp({"issues": []}))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await run_jql("q4") == []

    @pytest.mark.asyncio
    async def test_result_cached_by_query_and_limit(self, fresh_atlassian_cache):
        from services.atlassian import run_jql

        mock_client = _http_client_mock(_json_resp({"issues": []}))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await run_jql("q5", limit=5)
                await run_jql("q5", limit=5)

        assert mock_client.post.await_count == 1


class TestRunCql:
    """→2651c: generic capped CQL reader for dashboard widgets."""

    @pytest.mark.asyncio
    async def test_happy_row_shape(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        payload = {"results": [{
            "id": 111,
            "title": "Team doc",
            "type": "page",
            "space": {"key": "ENG"},
            "version": {"when": "2026-07-02T00:00:00Z"},
        }]}
        mock_client = _http_client_mock(_json_resp(payload), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                rows = await run_cql("mention = currentUser()")

        assert rows == [{
            "id": "111",
            "title": "Team doc",
            "type": "page",
            "updated": "2026-07-02T00:00:00Z",
            "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/111",
        }]
        sent = mock_client.get.call_args.kwargs["params"]
        assert sent["cql"] == "mention = currentUser()"
        assert sent["limit"] == 10

    @pytest.mark.asyncio
    async def test_limit_capped_at_25(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        mock_client = _http_client_mock(_json_resp({"results": []}), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await run_cql("type=page", limit=99)

        assert mock_client.get.call_args.kwargs["params"]["limit"] == 25

    @pytest.mark.asyncio
    async def test_missing_space_and_version(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        payload = {"results": [{"id": 222, "title": "Bare", "type": "page"}]}
        mock_client = _http_client_mock(_json_resp(payload), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                rows = await run_cql("c1")

        assert rows[0]["updated"] == ""
        assert rows[0]["url"] == ""

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        mock_client = _http_client_mock(_json_resp({}, status_code=403), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await run_cql("c2") == []

    @pytest.mark.asyncio
    async def test_disconnected_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        with _auth_disconnected():
            assert await run_cql("c3") == []

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import run_cql

        mock_client = _http_client_mock(_json_resp({"results": []}), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await run_cql("c4") == []


class TestConfluenceAccountId:
    """→2651d: account id lookup, cached for an hour, empty on failure."""

    @pytest.mark.asyncio
    async def test_happy_returns_and_caches(self, fresh_atlassian_cache):
        from services.atlassian import _get_confluence_account_id

        mock_client = _http_client_mock(_json_resp({"accountId": "acc-1"}), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                with patch("services.atlassian._cache_set") as mock_set:
                    result = await _get_confluence_account_id()

        assert result == "acc-1"
        mock_set.assert_called_once_with(("confluence_account_id",), "acc-1", ttl=3600)
        url = mock_client.get.call_args.args[0]
        assert url.endswith("/wiki/rest/api/user/current")

    @pytest.mark.asyncio
    async def test_cache_hit_skips_http(self, fresh_atlassian_cache):
        from services import atlassian as svc

        svc._cache_set(("confluence_account_id",), "cached-acc", ttl=3600)
        # No auth or HTTP patches: a cache miss would degrade to "" here.
        assert await svc._get_confluence_account_id() == "cached-acc"

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import _get_confluence_account_id

        mock_client = _http_client_mock(_json_resp({}, status_code=500), method="get")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                assert await _get_confluence_account_id() == ""

    @pytest.mark.asyncio
    async def test_disconnected_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import _get_confluence_account_id

        with _auth_disconnected():
            assert await _get_confluence_account_id() == ""


class TestMyConfluenceTasks:
    """→2651e: my incomplete Confluence action items."""

    @pytest.mark.asyncio
    async def test_happy_storage_body(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        payload = {"results": [{
            "id": 42,
            "body": {"storage": {"value": "<p>Review <b>the</b> doc</p>",
                                 "representation": "storage"}},
            "dueAt": "2026-07-20",
            "pageId": 777,
        }]}
        mock_client = _http_client_mock(_json_resp(payload), method="get")
        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="acc-1")):
            with _auth_ok():
                with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                    rows = await list_my_confluence_tasks()

        assert rows == [{
            "id": "42",
            "text": "Review the doc",
            "due": "2026-07-20",
            "page_id": "777",
            "url": "https://example.atlassian.net/wiki/pages/viewpage.action?pageId=777",
        }]
        sent = mock_client.get.call_args.kwargs["params"]
        assert sent == {"assigned-to": "acc-1", "status": "incomplete", "limit": 10}

    @pytest.mark.asyncio
    async def test_adf_body_uses_plain_text(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        payload = {"results": [{
            "id": 43,
            "body": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Ship it"}]},
            ]},
            "dueAt": None,
            "pageId": 778,
        }]}
        mock_client = _http_client_mock(_json_resp(payload), method="get")
        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="acc-1")):
            with _auth_ok():
                with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                    rows = await list_my_confluence_tasks()

        assert rows[0]["text"] == "Ship it"
        assert rows[0]["due"] == ""

    @pytest.mark.asyncio
    async def test_no_account_id_returns_empty_without_http(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="")):
            with patch("services.atlassian.httpx.AsyncClient") as mock_client_cls:
                assert await list_my_confluence_tasks() == []

        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        mock_client = _http_client_mock(_json_resp({}, status_code=500), method="get")
        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="acc-1")):
            with _auth_ok():
                with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                    assert await list_my_confluence_tasks() == []

    @pytest.mark.asyncio
    async def test_disconnected_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="acc-1")):
            with _auth_disconnected():
                assert await list_my_confluence_tasks() == []

    @pytest.mark.asyncio
    async def test_no_tasks_returns_empty(self, fresh_atlassian_cache):
        from services.atlassian import list_my_confluence_tasks

        mock_client = _http_client_mock(_json_resp({"results": []}), method="get")
        with patch("services.atlassian._get_confluence_account_id",
                   AsyncMock(return_value="acc-1")):
            with _auth_ok():
                with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                    assert await list_my_confluence_tasks() == []


class TestCompleteConfluenceTask:
    """→2651f: checking off a Confluence action item."""

    @pytest.mark.asyncio
    async def test_success_puts_complete_status(self, fresh_atlassian_cache):
        from services.atlassian import complete_confluence_task

        mock_client = _http_client_mock(_json_resp({"id": "42", "status": "complete"}),
                                        method="put")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                await complete_confluence_task("42")

        url = mock_client.put.call_args.args[0]
        assert url.endswith("/wiki/api/v2/tasks/42")
        assert mock_client.put.call_args.kwargs["json"]["status"] == "complete"

    @pytest.mark.asyncio
    async def test_non_2xx_raises_plain_message(self, fresh_atlassian_cache):
        from services.atlassian import complete_confluence_task

        mock_client = _http_client_mock(_json_resp({}, status_code=409), method="put")
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(RuntimeError, match="Could not check off the item"):
                    await complete_confluence_task("42")

    @pytest.mark.asyncio
    async def test_network_error_raises_plain_message(self, fresh_atlassian_cache):
        import httpx
        from services.atlassian import complete_confluence_task

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock(side_effect=httpx.ConnectError("down"))
        with _auth_ok():
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(RuntimeError, match="Could not check off the item"):
                    await complete_confluence_task("42")


# --- Widget query endpoints (spec jira-and-confluence-dashboard-widgets, →2652) ---


@pytest.mark.asyncio
async def test_jira_query_success(client):
    rows = [{
        "key": "JIRA-9", "summary": "Widget row", "status": "In Progress",
        "priority": "High", "type": "Bug", "updated": "2026-07-01T00:00:00Z",
        "due": "2026-07-12", "url": "https://example.atlassian.net/browse/JIRA-9",
    }]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.run_jql = AsyncMock(return_value=rows)
        resp = await client.get(
            "/api/atlassian/jira/query",
            params={"jql": "assignee = currentUser()", "limit": 5},
        )

    assert resp.status_code == 200
    assert resp.json() == {"rows": rows}
    mock_svc.run_jql.assert_awaited_once_with("assignee = currentUser()", limit=5)


@pytest.mark.asyncio
async def test_jira_query_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/jira/query", params={"jql": "x"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jira_query_degrades_to_empty_rows(client):
    """The reader returns [] on errors; the endpoint stays a quiet 200."""
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.run_jql = AsyncMock(return_value=[])
        resp = await client.get("/api/atlassian/jira/query", params={"jql": "x"})

    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


@pytest.mark.asyncio
async def test_confluence_query_success(client):
    rows = [{
        "id": "111", "title": "Team doc", "type": "page",
        "updated": "2026-07-02T00:00:00Z",
        "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/111",
    }]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.run_cql = AsyncMock(return_value=rows)
        resp = await client.get(
            "/api/atlassian/confluence/query",
            params={"cql": "mention = currentUser()"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"rows": rows}
    mock_svc.run_cql.assert_awaited_once_with("mention = currentUser()", limit=10)


@pytest.mark.asyncio
async def test_confluence_query_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/query", params={"cql": "x"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confluence_query_degrades_to_empty_rows(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.run_cql = AsyncMock(return_value=[])
        resp = await client.get("/api/atlassian/confluence/query", params={"cql": "x"})

    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


@pytest.mark.asyncio
async def test_confluence_my_tasks_success(client):
    tasks = [{
        "id": "42", "text": "Review the doc", "due": "2026-07-20",
        "page_id": "777",
        "url": "https://example.atlassian.net/wiki/pages/viewpage.action?pageId=777",
    }]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_my_confluence_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/atlassian/confluence/my-tasks")

    assert resp.status_code == 200
    assert resp.json() == {"tasks": tasks}
    mock_svc.list_my_confluence_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_confluence_my_tasks_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/my-tasks")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confluence_my_tasks_degrades_to_empty(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_my_confluence_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/atlassian/confluence/my-tasks")

    assert resp.status_code == 200
    assert resp.json() == {"tasks": []}


@pytest.mark.asyncio
async def test_confluence_complete_task_success(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.complete_confluence_task = AsyncMock(return_value=None)
        resp = await client.post("/api/atlassian/confluence/task/42/complete")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_svc.complete_confluence_task.assert_awaited_once_with("42")


@pytest.mark.asyncio
async def test_confluence_complete_task_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/atlassian/confluence/task/42/complete")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confluence_complete_task_failure_returns_502(client):
    message = "Could not check off the item. It may have changed in Confluence."
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.complete_confluence_task = AsyncMock(side_effect=RuntimeError(message))
        resp = await client.post("/api/atlassian/confluence/task/42/complete")

    assert resp.status_code == 502
    assert resp.json()["detail"] == message

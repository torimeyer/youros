"""Atlassian (Jira + Confluence) integration service.

Supports two auth paths:
  - PAT: email + API token in keychain, base URL = https://<site>
  - OAuth: access + refresh tokens in keychain, base URL =
    https://api.atlassian.com/ex/{product}/<cloud_id>

When an ATLASSIAN_ACCESS_TOKEN secret is present we prefer OAuth and route
calls through api.atlassian.com using the Atlassian Connect-style cloud_id
path. Otherwise we fall back to the original PAT BasicAuth path so existing
users do not need to reconnect.

Tokens live in the system keychain via ostk secret_set. Site, email, and
cloud_id (OAuth-only) live in ~/.myos/atlassian.json.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from services.atomic_io import atomic_write_json
from services.ostk import ostk

MYOS_DIR = Path.home() / ".myos"
CONFIG_PATH = MYOS_DIR / "atlassian.json"
ATLASSIAN_TOKEN_KEY = "ATLASSIAN_API_TOKEN"
ATLASSIAN_ACCESS_TOKEN_KEY = "ATLASSIAN_ACCESS_TOKEN"
ATLASSIAN_REFRESH_TOKEN_KEY = "ATLASSIAN_REFRESH_TOKEN"

_CACHE_TTL_SECONDS = 60.0
_response_cache: dict[tuple, tuple[float, object]] = {}

_config_cache: dict | None = None
_config_cache_mtime: float = 0.0


def _cache_get(key: tuple):
    entry = _response_cache.get(key)
    if not entry:
        return None
    expires_at, data = entry
    if time.time() >= expires_at:
        _response_cache.pop(key, None)
        return None
    return data


def _cache_set(key: tuple, data: object, ttl: float = _CACHE_TTL_SECONDS) -> None:
    _response_cache[key] = (time.time() + ttl, data)


def _cache_clear() -> None:
    _response_cache.clear()


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)


def is_connected() -> bool:
    """Return True if we have a saved config file (quick sync check)."""
    return CONFIG_PATH.exists()


def get_config() -> dict:
    """Return saved config {email, site}. Raises RuntimeError if not connected."""
    global _config_cache, _config_cache_mtime
    if not CONFIG_PATH.exists():
        _config_cache = None
        _config_cache_mtime = 0.0
        raise RuntimeError("Not connected to Atlassian.")
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _config_cache is None or mtime != _config_cache_mtime:
        _config_cache = json.loads(CONFIG_PATH.read_text())
        _config_cache_mtime = mtime
    config = _config_cache
    site = config.get("site") or os.environ.get("ATLASSIAN_SITE", "")
    if site != config.get("site"):
        return {**config, "site": site}
    return config


def _site_host(config: dict) -> str:
    """Return the user-facing host for building external links (no scheme)."""
    site = config.get("site", "") or ""
    return site.replace("https://", "").replace("http://", "").rstrip("/")


async def _get_auth_and_base(product: str = "jira") -> tuple[dict, str, str]:
    """Return (httpx-kwargs, api_base_url, site_host) for the connected account.

    The kwargs dict is splatted into client.get/post calls so the caller
    does not need to know whether we are using OAuth bearer headers or
    PAT BasicAuth. ``product`` is one of "jira" or "confluence" and is
    only consulted on the OAuth path because the cloud_id-based base
    URL differs per product.

    site_host is the user-facing hostname (e.g. ``acme.atlassian.net``)
    used to build links the user clicks on. It is the same regardless
    of which auth path produced api_base_url.
    """
    config = get_config()
    site_host = _site_host(config)
    access_token = await ostk.secret_get(ATLASSIAN_ACCESS_TOKEN_KEY)
    if access_token:
        cloud_id = config.get("cloud_id", "")
        if not cloud_id:
            raise RuntimeError(
                "Atlassian connected via OAuth but cloud_id is missing. Please reconnect."
            )
        base = f"https://api.atlassian.com/ex/{product}/{cloud_id}"
        return (
            {"headers": {"Authorization": f"Bearer {access_token}"}},
            base,
            site_host,
        )

    email = config["email"]
    token = await ostk.secret_get(ATLASSIAN_TOKEN_KEY)
    if not token:
        raise RuntimeError("Atlassian token not found in keychain. Please reconnect.")
    return ({"auth": httpx.BasicAuth(email, token)}, f"https://{site_host}", site_host)


async def _refresh_atlassian_token() -> bool:
    """Use the saved refresh token to mint a new access token.

    Returns True on success, False on any failure. Callers can then
    decide whether to retry the original request or surface the error.
    """
    refresh_token = await ostk.secret_get(ATLASSIAN_REFRESH_TOKEN_KEY)
    if not refresh_token:
        return False
    client_id = os.environ.get("ATLASSIAN_CLIENT_ID", "")
    client_secret = os.environ.get("ATLASSIAN_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://auth.atlassian.com/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
        if resp.status_code != 200:
            return False
        data = resp.json()
        new_access = data.get("access_token", "")
        new_refresh = data.get("refresh_token", "")
        if not new_access:
            return False
        await ostk.secret_set(ATLASSIAN_ACCESS_TOKEN_KEY, new_access)
        if new_refresh:
            await ostk.secret_set(ATLASSIAN_REFRESH_TOKEN_KEY, new_refresh)
        return True
    except httpx.HTTPError:
        return False


async def verify_creds(email: str, api_token: str, site: str) -> dict:
    """Verify credentials by calling /rest/api/3/myself.

    Returns user dict on success. Raises RuntimeError with actionable message
    on 401 (bad creds) or 404 (site not found).
    """
    site = site.replace("https://", "").replace("http://", "").rstrip("/")
    base_url = f"https://{site}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/rest/api/3/myself",
                auth=httpx.BasicAuth(email, api_token),
            )
            if resp.status_code == 401:
                raise RuntimeError("Invalid email or API token. Check your credentials and try again.")
            if resp.status_code == 404:
                raise RuntimeError(f"Site not found: {site}. Check the site URL and try again.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Could not connect ({resp.status_code}). Check your credentials and site URL.")
            data = resp.json()
            return {
                "account_id": data.get("accountId", ""),
                "display_name": data.get("displayName", ""),
                "email": data.get("emailAddress", email),
            }
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc


async def save_config(email: str, api_token: str, site: str) -> None:
    """Persist email + site to disk. Store token in keychain."""
    global _config_cache, _config_cache_mtime
    _ensure_dirs()
    site = site.replace("https://", "").replace("http://", "").rstrip("/")
    atomic_write_json(CONFIG_PATH, {"email": email, "site": site})
    await ostk.secret_set(ATLASSIAN_TOKEN_KEY, api_token)
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


async def save_oauth_config(
    email: str, site: str, cloud_id: str, access_token: str, refresh_token: str
) -> None:
    """Persist OAuth-flow connection details. site/email/cloud_id on disk; tokens in keychain."""
    global _config_cache, _config_cache_mtime
    _ensure_dirs()
    site = site.replace("https://", "").replace("http://", "").rstrip("/")
    atomic_write_json(
        CONFIG_PATH,
        {"email": email, "site": site, "cloud_id": cloud_id, "auth_method": "oauth"},
    )
    await ostk.secret_set(ATLASSIAN_ACCESS_TOKEN_KEY, access_token)
    if refresh_token:
        await ostk.secret_set(ATLASSIAN_REFRESH_TOKEN_KEY, refresh_token)
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


async def disconnect() -> None:
    """Remove config file and clear keychain entries (PAT and OAuth)."""
    global _config_cache, _config_cache_mtime
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink(missing_ok=True)
    for key in (ATLASSIAN_TOKEN_KEY, ATLASSIAN_ACCESS_TOKEN_KEY, ATLASSIAN_REFRESH_TOKEN_KEY):
        try:
            await ostk.secret_set(key, "")
        except Exception:
            pass
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


async def list_assigned_issues() -> list[dict]:
    """Return issues assigned to the current user that are not done."""
    auth_kwargs, base_url, site = await _get_auth_and_base(product="jira")
    cache_key = ("list_assigned_issues", base_url)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    jql = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
    fields = "summary,status,priority,issuetype,updated,assignee,reporter"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/rest/api/3/search",
                **auth_kwargs,
                params={"jql": jql, "fields": fields, "maxResults": 50},
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 403:
                raise RuntimeError("Access denied. Check your API token permissions.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    issues = []
    for item in data.get("issues", []):
        fields_data = item.get("fields", {})
        status_obj = fields_data.get("status") or {}
        priority_obj = fields_data.get("priority") or {}
        issuetype_obj = fields_data.get("issuetype") or {}
        issues.append({
            "key": item.get("key", ""),
            "summary": fields_data.get("summary", ""),
            "status": status_obj.get("name", ""),
            "priority": priority_obj.get("name", ""),
            "type": issuetype_obj.get("name", ""),
            "updated": fields_data.get("updated", ""),
            "url": f"https://{site}/browse/{item.get('key', '')}",
        })

    _cache_set(cache_key, issues)
    return issues


async def get_issue(key: str) -> dict:
    """Return full issue detail including rendered description and comments."""
    auth_kwargs, base_url, site = await _get_auth_and_base(product="jira")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/rest/api/3/issue/{key}",
                **auth_kwargs,
                params={"fields": "*all", "expand": "renderedFields"},
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Issue {key} not found.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
            issue_data = resp.json()

            comment_resp = await client.get(
                f"{base_url}/rest/api/3/issue/{key}/comment",
                **auth_kwargs,
                params={"maxResults": 50, "orderBy": "created"},
            )
            comment_data = comment_resp.json() if comment_resp.status_code == 200 else {"comments": []}
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    fields_data = issue_data.get("fields", {})
    rendered = issue_data.get("renderedFields", {})
    status_obj = fields_data.get("status") or {}
    priority_obj = fields_data.get("priority") or {}
    issuetype_obj = fields_data.get("issuetype") or {}
    assignee_obj = fields_data.get("assignee") or {}
    reporter_obj = fields_data.get("reporter") or {}

    comments = []
    for c in comment_data.get("comments", []):
        rendered_c = c.get("renderedBody", "")
        if not rendered_c:
            body = c.get("body", {})
            rendered_c = str(body) if body else ""
        author_obj = c.get("author") or {}
        comments.append({
            "author": author_obj.get("displayName", ""),
            "body_html": rendered_c,
            "created": c.get("created", ""),
        })

    return {
        "key": key,
        "summary": fields_data.get("summary", ""),
        "description_html": rendered.get("description", "") or "",
        "status": status_obj.get("name", ""),
        "priority": priority_obj.get("name", ""),
        "type": issuetype_obj.get("name", ""),
        "assignee": assignee_obj.get("displayName", ""),
        "reporter": reporter_obj.get("displayName", ""),
        "created": fields_data.get("created", ""),
        "updated": fields_data.get("updated", ""),
        "url": f"https://{site}/browse/{key}",
        "comments": comments,
    }


async def list_recent_pages(limit: int = 25) -> list[dict]:
    """Return recently-updated Confluence pages via the v2 API."""
    auth_kwargs, base_url, site = await _get_auth_and_base(product="confluence")
    cache_key = ("list_recent_pages", base_url, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/wiki/api/v2/pages",
                **auth_kwargs,
                params={"sort": "-modified-date", "limit": limit},
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 403:
                raise RuntimeError("Access denied. Check your API token Confluence permissions.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Confluence API error ({resp.status_code}).")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    pages = []
    for page in data.get("results", []):
        page_id = str(page.get("id", ""))
        pages.append({
            "id": page_id,
            "title": page.get("title", ""),
            "space_id": str(page.get("spaceId", "")),
            "updated": page.get("version", {}).get("createdAt", ""),
            "url": f"https://{site}/wiki/spaces/{page.get('spaceId', '')}/pages/{page_id}",
        })

    _cache_set(cache_key, pages)
    return pages


async def get_page(page_id: str) -> dict:
    """Return Confluence page detail with body HTML."""
    auth_kwargs, base_url, site = await _get_auth_and_base(product="confluence")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/wiki/api/v2/pages/{page_id}",
                **auth_kwargs,
                params={"body-format": "view"},
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Page {page_id} not found.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Confluence API error ({resp.status_code}).")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    page_id_str = str(data.get("id", page_id))
    space_id = str(data.get("spaceId", ""))
    body_obj = data.get("body", {})
    body_html = ""
    if isinstance(body_obj, dict):
        view = body_obj.get("view") or body_obj.get("storage") or {}
        body_html = view.get("value", "") if isinstance(view, dict) else ""

    return {
        "id": page_id_str,
        "title": data.get("title", ""),
        "space_id": space_id,
        "body_html": body_html,
        "created": data.get("createdAt", ""),
        "updated": data.get("version", {}).get("createdAt", ""),
        "url": f"https://{site}/wiki/spaces/{space_id}/pages/{page_id_str}",
    }


async def add_comment(issue_key: str, body: str) -> dict:
    """Post a comment on a Jira issue. Returns the created comment dict."""
    auth_kwargs, base_url, _ = await _get_auth_and_base(product="jira")
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body}],
                }
            ],
        }
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/comment",
                **auth_kwargs,
                json=payload,
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Issue {issue_key} not found.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
            return resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc


async def list_transitions(issue_key: str) -> list[dict]:
    """Return available transitions for a Jira issue."""
    auth_kwargs, base_url, _ = await _get_auth_and_base(product="jira")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
                **auth_kwargs,
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Issue {issue_key} not found.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    return [
        {
            "id": t.get("id", ""),
            "name": t.get("name", ""),
            "to_status": (t.get("to") or {}).get("name", ""),
        }
        for t in data.get("transitions", [])
    ]


async def transition_issue(issue_key: str, transition_id: str) -> None:
    """Move a Jira issue to a new status via a transition ID."""
    auth_kwargs, base_url, _ = await _get_auth_and_base(product="jira")
    payload = {"transition": {"id": transition_id}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
                **auth_kwargs,
                json=payload,
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Issue {issue_key} not found.")
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc


async def assign_issue(issue_key: str, account_id: Optional[str]) -> None:
    """Assign a Jira issue to a user by account ID. Pass None to unassign."""
    auth_kwargs, base_url, _ = await _get_auth_and_base(product="jira")
    payload = {"accountId": account_id}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{base_url}/rest/api/3/issue/{issue_key}/assignee",
                **auth_kwargs,
                json=payload,
            )
            if resp.status_code == 401:
                raise RuntimeError("Atlassian credentials expired. Please reconnect.")
            if resp.status_code == 404:
                raise RuntimeError(f"Issue {issue_key} not found.")
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"Jira API error ({resp.status_code}).")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

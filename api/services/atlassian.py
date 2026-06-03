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


def _adf_to_plain(adf: dict | str | None) -> str:
    """Convert an Atlassian Document Format (ADF) dict to plain text.

    Handles the common node types:
      - text        → the text value
      - mention     → attrs.text (e.g. "@Tori Meyer")
      - hardBreak   → newline
      - inlineCard  → omitted (URL cards add no readable value)
      - all others  → recurse into 'content' children

    If *adf* is already a string it is returned as-is (already rendered).
    If *adf* is None or empty, returns "".
    """
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf

    node_type = adf.get("type", "")

    if node_type == "text":
        return adf.get("text", "")

    if node_type == "mention":
        return adf.get("attrs", {}).get("text", "")

    if node_type == "hardBreak":
        return "\n"

    if node_type == "inlineCard":
        return ""

    # For all container nodes (doc, paragraph, bulletList, listItem, …)
    # recurse into children and join their text.
    parts: list[str] = []
    for child in adf.get("content", []):
        child_text = _adf_to_plain(child)
        if child_text:
            parts.append(child_text)

    # Paragraphs and list items are separated by newlines; inline nodes join
    # directly so that text + mention run together without extra spaces.
    if node_type in ("paragraph", "listItem", "bulletList", "orderedList",
                     "blockquote", "heading"):
        return "".join(parts)

    return "".join(parts)


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


def _migrate_atlassian_config(cfg: dict) -> dict:
    """Map old {site} config to {jira_site, confluence_site}. Idempotent.

    When only the legacy ``site`` key is present, populate both new keys with
    that value. If ``jira_site`` already exists the file is already migrated;
    return as-is so running twice is a no-op.
    """
    if "site" in cfg and "jira_site" not in cfg:
        site = cfg["site"]
        cfg = {**cfg, "jira_site": site, "confluence_site": site}
    return cfg


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)


def is_connected() -> bool:
    """Return True if we have a saved config file (quick sync check)."""
    return CONFIG_PATH.exists()


def get_config() -> dict:
    """Return saved config {email, jira_site, confluence_site}. Raises RuntimeError if not connected.

    Transparently migrates old {site} files to the new split format on first
    read and writes the migrated form back so future reads skip migration.
    """
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
        raw = json.loads(CONFIG_PATH.read_text())
        migrated = _migrate_atlassian_config(raw)
        if migrated is not raw:
            atomic_write_json(CONFIG_PATH, migrated)
        _config_cache = migrated
        _config_cache_mtime = mtime
    config = _config_cache
    # Allow env override for the Jira site only (legacy ATLASSIAN_SITE env var).
    env_site = os.environ.get("ATLASSIAN_SITE", "")
    if env_site and not config.get("jira_site"):
        return {**config, "jira_site": env_site, "confluence_site": config.get("confluence_site") or env_site}
    return config


def _site_host(config: dict, product: str = "jira") -> str:
    """Return the user-facing host for building external links (no scheme).

    Uses ``jira_site`` for Jira calls and ``confluence_site`` for Confluence.
    Falls back to the legacy ``site`` key so old config files keep working
    before migration runs.
    """
    if product == "confluence":
        site = config.get("confluence_site") or config.get("site", "") or ""
    else:
        site = config.get("jira_site") or config.get("site", "") or ""
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
    site_host = _site_host(config, product=product)
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


async def _request_with_refresh(product: str, fn):
    """Execute fn(client, auth_kwargs, base_url, site); retry once on 401 after refresh.

    fn must be an async callable that performs ONE httpx request and returns the response.
    On 401 we attempt _refresh_atlassian_token() once; on success we re-resolve auth and redo the call.
    Returns (resp, base_url, site).
    """
    auth_kwargs, base_url, site = await _get_auth_and_base(product=product)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await fn(client, auth_kwargs, base_url, site)
        if resp.status_code != 401:
            return resp, base_url, site
        ok = await _refresh_atlassian_token()
        if not ok:
            return resp, base_url, site
        auth_kwargs, base_url, site = await _get_auth_and_base(product=product)
        resp = await fn(client, auth_kwargs, base_url, site)
        return resp, base_url, site


_STATUS_PROBE_TTL = 60.0  # 60s: fast enough for UI polling, slow enough not to hammer Atlassian
_status_probe_cache: dict[str, tuple[float, bool]] = {}


async def probe_token_validity() -> bool:
    """Return True if the OAuth token is valid (after one refresh attempt).

    Tries Jira first (GET /rest/api/3/myself). If Jira is not accessible,
    falls back to Confluence (GET /wiki/rest/api/user/current). Returns True
    if either endpoint responds 200. Only reports expired when both fail —
    this keeps Confluence-only setups from showing as expired.
    PAT auth (no saved access_token) returns True (treated as valid).
    Cache TTL is 60s.
    """
    import logging
    _log = logging.getLogger(__name__)

    access_token = await ostk.secret_get(ATLASSIAN_ACCESS_TOKEN_KEY)
    if not access_token:
        return True  # PAT path; caller treats this as valid
    cache_key = "probe_token_validity"
    entry = _status_probe_cache.get(cache_key)
    if entry is not None:
        expires_at, result = entry
        if time.time() < expires_at:
            return result

    # Try Jira first.
    async def call_jira(client, auth_kwargs, base_url, site):
        return await client.get(f"{base_url}/rest/api/3/myself", **auth_kwargs)

    jira_ok = False
    try:
        resp, _, _ = await _request_with_refresh("jira", call_jira)
        jira_ok = resp.status_code == 200
    except Exception as exc:
        _log.warning("atlassian probe_token_validity jira probe failed: %s", exc, exc_info=True)

    if jira_ok:
        _status_probe_cache[cache_key] = (time.time() + _STATUS_PROBE_TTL, True)
        return True

    # Jira unavailable — try Confluence so Confluence-only setups stay valid.
    async def call_confluence(client, auth_kwargs, base_url, site):
        return await client.get(f"{base_url}/wiki/rest/api/user/current", **auth_kwargs)

    confluence_ok = False
    try:
        resp, _, _ = await _request_with_refresh("confluence", call_confluence)
        confluence_ok = resp.status_code == 200
    except Exception as exc:
        _log.warning(
            "atlassian probe_token_validity confluence fallback failed: %s", exc, exc_info=True
        )

    result = confluence_ok
    _status_probe_cache[cache_key] = (time.time() + _STATUS_PROBE_TTL, result)
    return result


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


async def save_config(
    email: str,
    api_token: str,
    jira_site: str,
    confluence_site: Optional[str] = None,
) -> None:
    """Persist email + per-product sites to disk. Store token in keychain.

    When ``confluence_site`` is omitted, it defaults to ``jira_site`` (single-
    tenant case). Writes the new {jira_site, confluence_site} schema; the
    legacy ``site`` key is not written so reads always get the migrated form.
    """
    global _config_cache, _config_cache_mtime
    _ensure_dirs()
    jira_site = jira_site.replace("https://", "").replace("http://", "").rstrip("/")
    if confluence_site is None:
        confluence_site = jira_site
    else:
        confluence_site = confluence_site.replace("https://", "").replace("http://", "").rstrip("/")
    atomic_write_json(CONFIG_PATH, {"email": email, "jira_site": jira_site, "confluence_site": confluence_site})
    await ostk.secret_set(ATLASSIAN_TOKEN_KEY, api_token)
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


async def save_oauth_config(
    email: str,
    site: str,
    cloud_id: str,
    access_token: str,
    refresh_token: str,
    jira_site: Optional[str] = None,
    confluence_site: Optional[str] = None,
) -> None:
    """Persist OAuth-flow connection details. site/email/cloud_id on disk; tokens in keychain.

    ``jira_site`` and ``confluence_site`` default to ``site`` when omitted so
    OAuth-flow callers that have not been updated yet keep working unchanged.
    """
    global _config_cache, _config_cache_mtime
    _ensure_dirs()
    site = site.replace("https://", "").replace("http://", "").rstrip("/")
    jira_host = (jira_site or site).replace("https://", "").replace("http://", "").rstrip("/")
    conf_host = (confluence_site or site).replace("https://", "").replace("http://", "").rstrip("/")
    atomic_write_json(
        CONFIG_PATH,
        {"email": email, "jira_site": jira_host, "confluence_site": conf_host, "cloud_id": cloud_id, "auth_method": "oauth"},
    )
    await ostk.secret_set(ATLASSIAN_ACCESS_TOKEN_KEY, access_token)
    if refresh_token:
        await ostk.secret_set(ATLASSIAN_REFRESH_TOKEN_KEY, refresh_token)
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


async def verify_confluence_creds(email: str, api_token: str, site: str) -> bool:
    """Verify Confluence access by calling /wiki/rest/api/space.

    Returns True on success. Raises RuntimeError with an actionable message
    on 401 (bad creds) or 404 (site not found).
    """
    site = site.replace("https://", "").replace("http://", "").rstrip("/")
    base_url = f"https://{site}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/wiki/rest/api/space",
                auth=httpx.BasicAuth(email, api_token),
                params={"limit": 1},
            )
            if resp.status_code == 401:
                raise RuntimeError("Invalid email or API token for Confluence. Check your credentials and try again.")
            if resp.status_code == 404:
                raise RuntimeError(f"Confluence not found at {site}. Check the site URL and try again.")
            if resp.status_code >= 400:
                raise RuntimeError(f"Could not reach Confluence ({resp.status_code}). Check your site URL.")
            return True
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Confluence: {exc}") from exc


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
    cache_key = ("list_assigned_issues",)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    jql = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
    fields = ["summary", "status", "priority", "issuetype", "updated", "assignee", "reporter"]

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/search/jql",
            **auth_kwargs,
            json={"jql": jql, "fields": fields, "maxResults": 50},
        )

    try:
        resp, base_url, site = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 403:
        raise RuntimeError("Access denied. Check your API token permissions.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")
    data = resp.json()

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


async def get_issue_links(key: str) -> dict:
    """Return an issue with its issuelinks for dependency mapping.

    Returns dict with: key, summary, status, assignee, issuelinks (raw Jira list).
    Raises RuntimeError("<key> not found.") on 404.
    Raises RuntimeError on auth/network failures.
    """
    async def call(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/rest/api/3/issue/{key}",
            **auth_kwargs,
            params={"fields": "summary,status,assignee,issuelinks"},
        )

    try:
        resp, base_url, site = await _request_with_refresh("jira", call)
    except Exception as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc

    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {key} not found.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")

    data = resp.json()
    f = data.get("fields", {})
    status_obj = f.get("status") or {}
    assignee_obj = f.get("assignee") or {}

    return {
        "key": data.get("key", key),
        "summary": f.get("summary", ""),
        "status": status_obj.get("name", ""),
        "assignee": assignee_obj.get("displayName", ""),
        "issuelinks": f.get("issuelinks", []),
    }


    """Return full issue detail including rendered description and comments."""
    async def call_issue(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/rest/api/3/issue/{key}",
            **auth_kwargs,
            params={"fields": "*all", "expand": "renderedFields"},
        )

    try:
        resp, base_url, site = await _request_with_refresh("jira", call_issue)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {key} not found.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")
    issue_data = resp.json()

    async def call_comments(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/rest/api/3/issue/{key}/comment",
            **auth_kwargs,
            params={"maxResults": 50, "orderBy": "created"},
        )

    try:
        comment_resp, _, _ = await _request_with_refresh("jira", call_comments)
        comment_data = comment_resp.json() if comment_resp.status_code == 200 else {"comments": []}
    except httpx.HTTPError:
        comment_data = {"comments": []}

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
            rendered_c = _adf_to_plain(body) if body else ""
        author_obj = c.get("author") or {}
        comments.append({
            "author": author_obj.get("displayName", ""),
            "body_html": rendered_c,
            "created": c.get("created", ""),
        })

    return {
        "key": key,
        "summary": fields_data.get("summary", ""),
        "description_html": rendered.get("description", "")
        or _adf_to_plain(fields_data.get("description") or {}),
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


async def list_blocked_issues() -> list[dict]:
    """Return Jira issues that are blocked or cross-team flagged.

    Criteria: status=Blocked OR labels="cross-team" OR flagged=true.
    Returns list of dicts with key, summary, status, priority, updated, url,
    assignee (display name), reporter (display name).
    """
    cache_key = ("list_blocked_issues",)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    jql = (
        '(status = Blocked OR labels = "cross-team" OR flagged = true) '
        "AND statusCategory != Done ORDER BY updated DESC"
    )
    fields = ["summary", "status", "priority", "issuetype", "updated", "assignee", "reporter", "labels"]

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/search/jql",
            **auth_kwargs,
            json={"jql": jql, "fields": fields, "maxResults": 50},
        )

    try:
        resp, base_url, site = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 403:
        raise RuntimeError("Access denied. Check your API token permissions.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")
    data = resp.json()

    issues = []
    for item in data.get("issues", []):
        fields_data = item.get("fields", {})
        status_obj = fields_data.get("status") or {}
        priority_obj = fields_data.get("priority") or {}
        assignee_obj = fields_data.get("assignee") or {}
        reporter_obj = fields_data.get("reporter") or {}
        issues.append({
            "key": item.get("key", ""),
            "summary": fields_data.get("summary", ""),
            "status": status_obj.get("name", ""),
            "priority": priority_obj.get("name", ""),
            "updated": fields_data.get("updated", ""),
            "url": f"https://{site}/browse/{item.get('key', '')}",
            "assignee": assignee_obj.get("displayName", ""),
            "reporter": reporter_obj.get("displayName", ""),
        })

    _cache_set(cache_key, issues)
    return issues


async def list_recent_pages(limit: int = 25, space_key: str = "") -> list[dict]:
    """Return recently-updated Confluence pages.

    When ``space_key`` is non-empty, uses the v1 REST API which supports
    filtering by space key directly. Otherwise uses the v2 API for global
    recent pages.
    """
    space_key = space_key.strip().upper() if space_key else ""
    cache_key = ("list_recent_pages", limit, space_key)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    if space_key:
        async def call(client, auth_kwargs, base_url, site):
            return await client.get(
                f"{base_url}/wiki/rest/api/content",
                **auth_kwargs,
                params={
                    "type": "page",
                    "spaceKey": space_key,
                    "limit": limit,
                    "orderby": "modified",
                    "status": "current",
                    "expand": "version,space",
                },
            )
    else:
        async def call(client, auth_kwargs, base_url, site):
            return await client.get(
                f"{base_url}/wiki/api/v2/pages",
                **auth_kwargs,
                params={"sort": "-modified-date", "limit": limit},
            )

    try:
        resp, base_url, site = await _request_with_refresh("confluence", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 403:
        raise RuntimeError("Access denied. Check your API token Confluence permissions.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence API error ({resp.status_code}).")
    data = resp.json()

    pages = []
    if space_key:
        # v1 API response shape
        for page in data.get("results", []):
            page_id = str(page.get("id", ""))
            space_obj = page.get("space") or {}
            space_id = str(space_obj.get("id", ""))
            version_obj = page.get("version") or {}
            pages.append({
                "id": page_id,
                "title": page.get("title", ""),
                "space_id": space_id,
                "updated": version_obj.get("when", ""),
                "url": f"https://{site}/wiki/spaces/{space_key}/pages/{page_id}",
            })
    else:
        # v2 API response shape
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
    async def call(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/wiki/api/v2/pages/{page_id}",
            **auth_kwargs,
            params={"body-format": "view"},
        )

    try:
        resp, base_url, site = await _request_with_refresh("confluence", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Page {page_id} not found.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence API error ({resp.status_code}).")
    data = resp.json()

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

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/issue/{issue_key}/comment",
            **auth_kwargs,
            json=payload,
        )

    try:
        resp, _, _ = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {issue_key} not found.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")
    return resp.json()


async def list_transitions(issue_key: str) -> list[dict]:
    """Return available transitions for a Jira issue."""
    async def call(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
            **auth_kwargs,
        )

    try:
        resp, _, _ = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {issue_key} not found.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira API error ({resp.status_code}).")
    data = resp.json()

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
    payload = {"transition": {"id": transition_id}}

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
            **auth_kwargs,
            json=payload,
        )

    try:
        resp, _, _ = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {issue_key} not found.")
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Jira API error ({resp.status_code}).")


async def assign_issue(issue_key: str, account_id: Optional[str]) -> None:
    """Assign a Jira issue to a user by account ID. Pass None to unassign."""
    payload = {"accountId": account_id}

    async def call(client, auth_kwargs, base_url, site):
        return await client.put(
            f"{base_url}/rest/api/3/issue/{issue_key}/assignee",
            **auth_kwargs,
            json=payload,
        )

    try:
        resp, _, _ = await _request_with_refresh("jira", call)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Atlassian: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code == 404:
        raise RuntimeError(f"Issue {issue_key} not found.")
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Jira API error ({resp.status_code}).")


async def list_blocked_issues() -> list[dict]:
    """Return Jira issues that are blocked, flagged, or labeled cross-team.

    JQL: status = "Blocked" OR labels = "cross-team" OR flagged = impediment
    Returns each issue with: key, summary, status, priority, updated, url,
    assignee (display name or ""), reporter (display name or "").
    Returns empty list on any API error so callers never get 500.
    """
    cache_key = ("list_blocked_issues",)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    jql = (
        'status = "Blocked" OR labels = "cross-team" OR flagged = impediment '
        "ORDER BY updated ASC"
    )
    fields = [
        "summary", "status", "priority", "issuetype",
        "updated", "assignee", "reporter", "labels",
    ]

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/search/jql",
            **auth_kwargs,
            json={"jql": jql, "fields": fields, "maxResults": 50},
        )

    try:
        resp, base_url, site = await _request_with_refresh("jira", call)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []

    data = resp.json()
    issues = []
    for item in data.get("issues", []):
        f = item.get("fields", {})
        status_obj = f.get("status") or {}
        priority_obj = f.get("priority") or {}
        assignee_obj = f.get("assignee") or {}
        reporter_obj = f.get("reporter") or {}
        issues.append({
            "key": item.get("key", ""),
            "summary": f.get("summary", ""),
            "status": status_obj.get("name", ""),
            "priority": priority_obj.get("name", ""),
            "updated": f.get("updated", ""),
            "url": f"https://{site}/browse/{item.get('key', '')}",
            "assignee": assignee_obj.get("displayName", ""),
            "reporter": reporter_obj.get("displayName", ""),
        })

    _cache_set(cache_key, issues)
    return issues


# ---------------------------------------------------------------------------
# P3a: cross-source search — Excerpt-returning search functions
# ---------------------------------------------------------------------------

async def search_jira(query: str, limit: int = 10) -> list:
    """Search Jira via JQL text search; returns list[Excerpt] with deep links."""
    from services.excerpts import Excerpt

    async def call(client, auth_kwargs, base_url, site):
        return await client.post(
            f"{base_url}/rest/api/3/search/jql",
            **auth_kwargs,
            json={"jql": f'text ~ "{query}"', "fields": ["summary", "issuetype"], "maxResults": limit},
        )

    resp, base_url, site = await _request_with_refresh("jira", call)
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Jira search error ({resp.status_code}).")

    results = []
    for item in resp.json().get("issues", []):
        key = item.get("key", "")
        summary = (item.get("fields") or {}).get("summary", "")
        results.append(Excerpt(
            text=f"{key}: {summary}",
            source_id=key,
            source_title=key,
            deep_link=f"https://{site}/browse/{key}",
            score=1.0,
            access_denied=False,
            provider="jira",
        ))
    return results


async def search_confluence(query: str, limit: int = 10) -> list:
    """Search Confluence via CQL; returns list[Excerpt] with deep links. 404 -> []."""
    from services.excerpts import Excerpt

    async def call(client, auth_kwargs, base_url, site):
        return await client.get(
            f"{base_url}/wiki/rest/api/content/search",
            **auth_kwargs,
            params={"cql": f'text ~ "{query}"', "limit": limit},
        )

    resp, base_url, site = await _request_with_refresh("confluence", call)
    if resp.status_code == 404:
        return []
    if resp.status_code == 401:
        raise RuntimeError("Atlassian credentials expired. Please reconnect.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Confluence search error ({resp.status_code}).")

    results = []
    for item in resp.json().get("results", []):
        page_id = str(item.get("id", ""))
        title = item.get("title", "")
        space_key = (item.get("space") or {}).get("key", "")
        deep_link = (
            f"https://{site}/wiki/spaces/{space_key}/pages/{page_id}"
            if space_key else None
        )
        results.append(Excerpt(
            text=title,
            source_id=page_id,
            source_title=title,
            deep_link=deep_link,
            score=1.0,
            access_denied=False,
            provider="confluence",
        ))
    return results


async def search(query: str, limit: int = 10) -> list:
    """Fan out across Jira and Confluence; returns merged list[Excerpt].

    A failing source is silently skipped so the other's results still arrive.
    """
    import asyncio

    async def _safe(coro):
        try:
            return await coro
        except Exception:
            return []

    jira_results, conf_results = await asyncio.gather(
        _safe(search_jira(query, limit)),
        _safe(search_confluence(query, limit)),
    )
    return list(jira_results) + list(conf_results)

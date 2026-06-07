"""GitHub integration service.

Personal access token auth for v1. Stores token in ~/.youros/github_token.json.
Supports listing repos, fetching issues, creating issues, and syncing with tasks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from services.atomic_io import atomic_write_json

MYOS_DIR = Path.home() / ".youros"
TOKEN_PATH = MYOS_DIR / "github_token.json"

GITHUB_API_BASE = "https://api.github.com"

# Circuit breaker
_BREAKER_THRESHOLD = 2
_BREAKER_COOLDOWN = 300
_breaker_failures: int = 0
_breaker_tripped_at: float = 0.0

# In-process response cache for read endpoints.
# Key: (method, path, frozen_params). Value: (expires_at, data).
_CACHE_TTL_SECONDS = 60.0
_response_cache: dict[tuple, tuple[float, object]] = {}

# Config cache: avoid re-reading the token JSON on every request.
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


def _breaker_is_open() -> bool:
    if _breaker_failures < _BREAKER_THRESHOLD:
        return False
    if _breaker_tripped_at and (time.time() - _breaker_tripped_at) > _BREAKER_COOLDOWN:
        return False
    return True


def _breaker_record_failure() -> None:
    global _breaker_failures, _breaker_tripped_at
    _breaker_failures += 1
    if _breaker_failures >= _BREAKER_THRESHOLD:
        _breaker_tripped_at = time.time()


def _breaker_record_success() -> None:
    global _breaker_failures, _breaker_tripped_at
    _breaker_failures = 0
    _breaker_tripped_at = 0.0


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)


def is_connected() -> bool:
    """Return True if we have a saved GitHub token."""
    return TOKEN_PATH.exists()


def get_config() -> dict:
    """Return saved config (token, repo). Raises RuntimeError if not connected.

    Reads token JSON once and reuses it across requests. Reloads only when
    the file's mtime changes, so rotating the token still takes effect.
    """
    global _config_cache, _config_cache_mtime
    if not TOKEN_PATH.exists():
        _config_cache = None
        _config_cache_mtime = 0.0
        raise RuntimeError("Not connected to GitHub.")
    try:
        mtime = TOKEN_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _config_cache is None or mtime != _config_cache_mtime:
        _config_cache = json.loads(TOKEN_PATH.read_text())
        _config_cache_mtime = mtime
    return _config_cache


def save_config(token: str, repo: str) -> None:
    """Persist PAT and repo to disk."""
    global _config_cache, _config_cache_mtime
    _ensure_dirs()
    atomic_write_json(TOKEN_PATH, {"token": token, "repo": repo})
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


def disconnect() -> None:
    """Remove saved token."""
    global _config_cache, _config_cache_mtime
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink(missing_ok=True)
    _config_cache = None
    _config_cache_mtime = 0.0
    _cache_clear()


def _headers() -> dict:
    config = get_config()
    return {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }


def _repo() -> str:
    config = get_config()
    repo = config.get("repo", "")
    if not repo:
        raise RuntimeError("No GitHub repository configured.")
    # Strip leading github.com URL parts if pasted as full URL
    repo = repo.replace("https://github.com/", "").strip("/")
    return repo


async def _github_get(path: str, params: Optional[dict] = None) -> dict | list:
    """Make an authenticated GET request to the GitHub API."""
    if _breaker_is_open():
        raise RuntimeError("GitHub API is temporarily unavailable. Try again in a few minutes.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{GITHUB_API_BASE}{path}",
                headers=_headers(),
                params=params or {},
            )
            if resp.status_code >= 400:
                _breaker_record_failure()
                detail = resp.json().get("message", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                raise RuntimeError(f"GitHub API error ({resp.status_code}): {detail}")
            _breaker_record_success()
            return resp.json()
    except httpx.HTTPError as exc:
        _breaker_record_failure()
        raise RuntimeError(f"Could not reach GitHub: {exc}") from exc


async def _github_post(path: str, body: dict) -> dict:
    """Make an authenticated POST request to the GitHub API."""
    if _breaker_is_open():
        raise RuntimeError("GitHub API is temporarily unavailable. Try again in a few minutes.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GITHUB_API_BASE}{path}",
                headers=_headers(),
                json=body,
            )
            if resp.status_code >= 400:
                _breaker_record_failure()
                detail = resp.json().get("message", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                raise RuntimeError(f"GitHub API error ({resp.status_code}): {detail}")
            _breaker_record_success()
            return resp.json()
    except httpx.HTTPError as exc:
        _breaker_record_failure()
        raise RuntimeError(f"Could not reach GitHub: {exc}") from exc


async def verify_token() -> dict:
    """Verify the token works and return user info."""
    data = await _github_get("/user")
    if isinstance(data, dict):
        return {"login": data.get("login", ""), "name": data.get("name", "")}
    return {}


async def list_issues(state: str = "open", per_page: int = 50, use_cache: bool = True) -> list[dict]:
    """Fetch issues from the connected repo.

    Results are cached in-process for 60 seconds keyed by (repo, state, per_page).
    Pass use_cache=False to force a refresh.
    """
    repo = _repo()
    cache_key = ("list_issues", repo, state, per_page)
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

    data = await _github_get(f"/repos/{repo}/issues", {
        "state": state,
        "per_page": str(per_page),
        "sort": "updated",
        "direction": "desc",
    })
    if not isinstance(data, list):
        result: list[dict] = []
    else:
        result = [
            {
                "number": issue["number"],
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "body": issue.get("body", "") or "",
                "labels": [l.get("name", "") for l in issue.get("labels", [])],
                "assignee": (issue.get("assignee") or {}).get("login", ""),
                "created_at": issue.get("created_at", ""),
                "updated_at": issue.get("updated_at", ""),
                "html_url": issue.get("html_url", ""),
            }
            for issue in data
            if "pull_request" not in issue  # Exclude PRs
        ]
    _cache_set(cache_key, result)
    return result


async def create_issue(title: str, body: str = "", labels: Optional[list[str]] = None) -> dict:
    """Create a new issue in the connected repo."""
    repo = _repo()
    payload: dict = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    data = await _github_post(f"/repos/{repo}/issues", payload)
    return {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "html_url": data.get("html_url", ""),
    }


async def update_issue(issue_number: int, state: Optional[str] = None, labels: Optional[list[str]] = None) -> dict:
    """Update an existing issue."""
    repo = _repo()
    if _breaker_is_open():
        raise RuntimeError("GitHub API is temporarily unavailable.")

    payload: dict = {}
    if state:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{GITHUB_API_BASE}/repos/{repo}/issues/{issue_number}",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                _breaker_record_failure()
                raise RuntimeError(f"GitHub API error: {resp.status_code}")
            _breaker_record_success()
            return resp.json()
    except httpx.HTTPError as exc:
        _breaker_record_failure()
        raise RuntimeError(f"Could not reach GitHub: {exc}") from exc


# Label mapping: GitHub labels to myOS label equivalents
GITHUB_TO_MYOS_LABEL = {
    "bug": "Bug",
    "enhancement": "Feature",
    "feature": "Feature",
    "documentation": "Docs",
    "good first issue": "Easy",
    "help wanted": "Help Wanted",
    "question": "Question",
    "wontfix": "Won't Fix",
    "duplicate": "Duplicate",
    "invalid": "Invalid",
}


def map_github_labels(github_labels: list[str]) -> list[str]:
    """Map GitHub label names to myOS label equivalents."""
    mapped = []
    for label in github_labels:
        myos_label = GITHUB_TO_MYOS_LABEL.get(label.lower())
        if myos_label:
            mapped.append(myos_label)
        else:
            mapped.append(label)
    return mapped

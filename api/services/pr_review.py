"""PR review service: fetch a pull request, chunk the diff, and ask the model
for a structured review (summary, per-file walkthrough, risk flags).

Public API
----------
review_pr(owner, repo, number) -> dict
    Returns a structured review dict or raises RuntimeError with a
    plain-language message.

Data shape returned::

    {
        "summary": str,                  # plain-language summary
        "file_count": int,
        "additions": int,
        "deletions": int,
        "truncated": bool,               # True when diff was too large to include fully
        "walkthrough": [                 # per-file entries
            {"file": str, "change_type": str, "description": str},
            ...
        ],
        "flags": [                       # risk flags
            {"title": str, "severity": "high"|"medium"|"low",
             "description": str, "file": str|None},
            ...
        ],
    }
"""
from __future__ import annotations

import json
import re
from typing import Optional

from services import github as github_service
from services.ai_backend import get_ai_client

# Diff is chunked when the raw text exceeds this many characters.
_DIFF_CHAR_LIMIT = 60_000
# When a diff has more than this many files we only include the first N.
_MAX_FILES_IN_DIFF = 80

_SYSTEM_PROMPT = (
    "You are a senior software engineer performing a pull request review. "
    "You receive a PR's metadata and code diff, and you produce a structured "
    "review in valid JSON. Focus on clarity and correctness. Be specific about "
    "files. Write in plain language — no jargon. Never use em-dashes."
)

_RISKY_PATTERNS = [
    (r"\bauth\b|\btoken\b|\bpassword\b|\boauth\b|\bcredential\b|\bsecret\b|\bpermission\b",
     "auth_credential", "Auth or credential change"),
    (r"def test_.*deleted|removed test|delete.*test|test.*removed",
     "deleted_test", "Test deletion"),
    (r'["\'](?:[A-Za-z0-9+/]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9]{20,})["\']',
     "hardcoded_secret", "Hardcoded secret or key"),
    (r"\*\s*$|\ball\s+\w+\b|\bpermissions\s*=\s*[\"\']\*",
     "broad_permission", "Broad permission grant"),
]


def _detect_risky_patterns(diff_text: str, file_list: list[str]) -> list[dict]:
    """Scan diff text for known risky patterns and return flag hints."""
    flags: list[dict] = []
    seen: set[str] = set()

    for pattern, kind, label in _RISKY_PATTERNS:
        if re.search(pattern, diff_text, re.IGNORECASE):
            if kind not in seen:
                seen.add(kind)
                flags.append({"kind": kind, "label": label})

    # Heuristic: large untested surface = many added files with no test added
    added_non_test = [f for f in file_list if not re.search(r"test|spec", f, re.IGNORECASE)]
    added_test = [f for f in file_list if re.search(r"test|spec", f, re.IGNORECASE)]
    if len(added_non_test) >= 10 and not added_test:
        flags.append({"kind": "large_untested", "label": "Large untested surface"})

    return flags


def _chunk_diff(raw_diff: str, files_meta: list[dict]) -> tuple[str, bool]:
    """Return (diff_text_to_send, was_truncated).

    If raw_diff is short enough, return it as-is.
    Otherwise, include only the first _MAX_FILES_IN_DIFF file sections,
    each capped so the total stays under _DIFF_CHAR_LIMIT.
    """
    if len(raw_diff) <= _DIFF_CHAR_LIMIT and len(files_meta) <= _MAX_FILES_IN_DIFF:
        return raw_diff, False

    # Split on "diff --git" boundaries
    sections = re.split(r"(?=^diff --git)", raw_diff, flags=re.MULTILINE)
    if not sections or sections[0] == "":
        sections = sections[1:]

    budget = _DIFF_CHAR_LIMIT
    kept: list[str] = []
    for section in sections[: _MAX_FILES_IN_DIFF]:
        if budget <= 0:
            break
        chunk = section[:budget]
        kept.append(chunk)
        budget -= len(chunk)

    return "\n".join(kept), True


def _build_prompt(pr_meta: dict, diff_text: str, truncated: bool,
                  hint_flags: list[dict]) -> str:
    title = pr_meta.get("title", "")
    body = pr_meta.get("body", "") or ""
    additions = pr_meta.get("additions", 0)
    deletions = pr_meta.get("deletions", 0)
    changed_files = pr_meta.get("changed_files", 0)
    state = pr_meta.get("state", "")
    html_url = pr_meta.get("html_url", "")

    trunc_note = ""
    if truncated:
        trunc_note = (
            "\n[NOTE: This PR is very large. Only a portion of the diff is shown below. "
            "Your review must explicitly state that the diff was truncated.]\n"
        )

    hint_text = ""
    if hint_flags:
        labels = ", ".join(f["label"] for f in hint_flags)
        hint_text = (
            f"\n[HINT: Static scan detected possible risky patterns: {labels}. "
            "Confirm in the diff whether each applies.]\n"
        )

    return (
        f"Review this pull request and return ONLY a JSON object, no other text.\n\n"
        f"PR: {html_url}\n"
        f"Title: {title}\n"
        f"State: {state}\n"
        f"Changes: +{additions} -{deletions} across {changed_files} files\n"
        f"Description:\n{body[:1000] if body else '(none)'}\n"
        f"{trunc_note}{hint_text}\n"
        f"Diff:\n```diff\n{diff_text}\n```\n\n"
        "Return exactly this JSON structure (no markdown fences, no extra keys):\n"
        "{\n"
        '  "summary": "<2-4 sentence plain-language summary of what the PR does>",\n'
        '  "walkthrough": [\n'
        '    {"file": "<path>", "change_type": "added|modified|deleted|renamed", '
        '"description": "<one sentence>"}\n'
        "  ],\n"
        '  "flags": [\n'
        '    {"title": "<short title>", "severity": "high|medium|low", '
        '"description": "<specific detail>", "file": "<path or null>"}\n'
        "  ]\n"
        "}\n"
        "Flags severity guide: high = auth/security/data-loss risk; "
        "medium = test deletion, broad permissions, large untested surface; "
        "low = style/naming concerns.\n"
        "If no flags apply, return flags as [].\n"
        "walkthrough should have at most 20 entries; omit trivial or auto-generated files.\n"
        "If the diff was truncated, include a high-severity flag titled "
        "\"Large PR — partial review\" explaining that only part was reviewed.\n"
    )


async def review_pr(owner: str, repo: str, number: int) -> dict:
    """Fetch PR metadata + diff and return a structured review.

    Raises RuntimeError with a plain-language message on any failure
    (auth, not found, model unavailable, etc.).
    """
    if not github_service.is_connected():
        raise RuntimeError("GitHub is not connected. Connect in Settings first.")

    # Fetch PR metadata (extended) and diff in parallel using httpx
    try:
        pr_meta = await _get_pr_extended(owner, repo, number)
    except RuntimeError as exc:
        msg = str(exc)
        if "404" in msg or "Not Found" in msg:
            raise RuntimeError(
                f"PR #{number} was not found in {owner}/{repo}. "
                "Make sure the PR exists and your GitHub token has access to this repo."
            ) from exc
        if "401" in msg or "403" in msg or "Bad credentials" in msg:
            raise RuntimeError(
                "Your GitHub token does not have permission to read this PR. "
                "Check that it has 'repo' scope for private repos."
            ) from exc
        raise RuntimeError(f"Could not fetch PR #{number}: {msg}") from exc

    try:
        raw_diff = await _get_pr_diff(owner, repo, number)
    except RuntimeError:
        raw_diff = ""

    files_meta = pr_meta.get("files", [])
    file_paths = [f.get("filename", "") for f in files_meta]

    hint_flags = _detect_risky_patterns(raw_diff, file_paths)
    diff_text, truncated = _chunk_diff(raw_diff, files_meta)

    client = await get_ai_client()
    if client is None:
        raise RuntimeError(
            "No AI backend is configured. Add an Anthropic API key in Settings "
            "or connect the Claude subscription."
        )

    prompt = _build_prompt(pr_meta, diff_text, truncated, hint_flags)
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()
    except Exception as exc:
        raise RuntimeError(f"Model call failed: {exc}") from exc

    try:
        # Strip markdown fences if the model wrapped it anyway
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
        review = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        review = {
            "summary": raw_text[:1000],
            "walkthrough": [],
            "flags": [],
        }

    return {
        "summary": review.get("summary", ""),
        "file_count": pr_meta.get("changed_files", len(files_meta)),
        "additions": pr_meta.get("additions", 0),
        "deletions": pr_meta.get("deletions", 0),
        "truncated": truncated,
        "walkthrough": review.get("walkthrough", [])[:20],
        "flags": review.get("flags", []),
        "pr_url": pr_meta.get("html_url", ""),
        "pr_title": pr_meta.get("title", ""),
        "pr_number": number,
        "owner": owner,
        "repo": repo,
    }


async def _get_pr_extended(owner: str, repo: str, number: int) -> dict:
    """Fetch extended PR metadata including files list, additions/deletions."""
    from services.github import _github_get
    data = await _github_get(f"/repos/{owner}/{repo}/pulls/{number}")
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected response from GitHub.")

    # Also fetch the files list
    try:
        files_data = await _github_get(
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            {"per_page": "100"},
        )
        files = files_data if isinstance(files_data, list) else []
    except RuntimeError:
        files = []

    merged_at = data.get("merged_at")
    if merged_at:
        state = "merged"
    elif data.get("draft"):
        state = "draft"
    elif data.get("state") == "closed":
        state = "closed"
    else:
        state = "open"

    return {
        "number": data.get("number", number),
        "title": data.get("title", ""),
        "state": state,
        "body": data.get("body", "") or "",
        "html_url": data.get("html_url", ""),
        "additions": data.get("additions", 0),
        "deletions": data.get("deletions", 0),
        "changed_files": data.get("changed_files", 0),
        "files": files,
    }


async def _get_pr_diff(owner: str, repo: str, number: int) -> str:
    """Fetch the raw unified diff for a PR."""
    from services.github import get_config
    import httpx

    try:
        config = get_config()
        token = config.get("token", "")
    except RuntimeError:
        return ""

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3.diff",
                },
            )
        if resp.status_code >= 400:
            return ""
        return resp.text
    except httpx.HTTPError:
        return ""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from services.ostk import OstkError, get_effective_root, ostk

router = APIRouter(tags=["search"])

_RECALL_TIMEOUT_SECONDS = 3.0

_CLI_SCOPE_MAP = {
    "all": "all",
    "code": "code",
    "file": "files",
    "files": "files",
    "work": "work",
    "needle": "work",
    "needles": "work",
    "task": "work",
    "tasks": "work",
    "history": "history",
    "decision": "decisions",
    "decisions": "decisions",
    "transcript": "transcripts",
    "transcripts": "transcripts",
}

_ADDR_LINE_RE = re.compile(
    r"^\s*addr:(?P<addr>\S+)(?:\s+score:(?P<score>[0-9.]+))?(?:\s+(?P<rest>.*))?$"
)


def _split_path_and_line(raw_path: str) -> tuple[str, Optional[int]]:
    if ":" in raw_path:
        head, tail = raw_path.rsplit(":", 1)
        if head and tail.isdigit():
            return head, int(tail)
    return raw_path, None


def _normalize_recall_scope(
    raw_scope: str, addr: str = "", path: Optional[str] = None
) -> str:
    lowered = (raw_scope or "").strip().lower()
    if lowered in ("decision", "decisions"):
        return "decision"
    if lowered in ("transcript", "transcripts", "tool", "session"):
        return "transcript"
    if lowered in ("work", "needle", "needles", "task", "tasks"):
        return "work"
    if lowered in ("file", "files"):
        return "file"
    if lowered == "code":
        return "code"

    if addr.startswith(("→", "->", "needle:", "work:")):
        return "work"
    if addr.startswith(("tool:", "transcript:", "session:")):
        return "transcript"
    if addr.startswith(("decision:", "dec:")):
        return "decision"
    if addr.startswith("file:"):
        return "file"
    if addr.startswith(("path:", "code:")) or path:
        return "code"
    if lowered == "history":
        return "work"
    return lowered or "recall"


def _normalize_structured_hit(
    item: dict[str, Any], default_scope: str, index: int
) -> dict[str, Any]:
    addr = str(item.get("addr") or item.get("id") or "")
    raw_path = item.get("path") or item.get("file")
    path: Optional[str] = str(raw_path) if raw_path else None
    line_raw = item.get("line") if item.get("line") is not None else item.get("line_number")
    line: Optional[int] = None
    if isinstance(line_raw, int):
        line = line_raw
    elif isinstance(line_raw, str) and line_raw.isdigit():
        line = int(line_raw)
    elif path:
        path, parsed_line = _split_path_and_line(path)
        line = parsed_line

    raw_scope = str(item.get("scope") or item.get("kind") or item.get("type") or default_scope)
    scope = _normalize_recall_scope(raw_scope, addr=addr, path=path)
    hit_id = str(item.get("id") or addr or path or item.get("title") or f"recall-{index}")
    default_title = Path(path).name if path else hit_id
    title = str(item.get("title") or item.get("subject") or item.get("name") or default_title)
    snippet = str(
        item.get("snippet")
        or item.get("text")
        or item.get("excerpt")
        or item.get("preview")
        or ""
    )
    return {
        "id": hit_id,
        "scope": scope,
        "title": title,
        "snippet": snippet,
        "path": path,
        "line": line,
        "source": "ostk_recall",
    }


def _parse_addr_text_output(
    text: str, default_scope: str = "all", limit: int = 10
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        match = _ADDR_LINE_RE.match(raw_line)
        if not match:
            continue
        addr = match.group("addr")
        score = match.group("score") or ""
        rest = (match.group("rest") or "").strip()

        path: Optional[str] = None
        line: Optional[int] = None
        hit_id = addr
        title = addr

        if addr.startswith(("path:", "file:", "code:")):
            _, raw_target = addr.split(":", 1)
            path, line = _split_path_and_line(raw_target)
            title = Path(path).name or path
        elif addr.startswith(("needle:", "work:")):
            _, hit_id = addr.split(":", 1)
            title = hit_id
        elif addr.startswith(("decision:", "dec:")):
            path = ".ostk/decisions.jsonl"

        scope = _normalize_recall_scope(default_scope, addr=addr, path=path)
        snippet = rest if rest else (f"score {score}" if score else "")

        hits.append(
            {
                "id": hit_id,
                "scope": scope,
                "title": title,
                "snippet": snippet,
                "path": path,
                "line": line,
                "source": "ostk_recall",
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _parse_ostk_recall_output(
    raw_stdout: str, default_scope: str = "all", limit: int = 10
) -> list[dict[str, Any]]:
    stripped = (raw_stdout or "").strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return _parse_addr_text_output(stripped, default_scope=default_scope, limit=limit)

    if isinstance(parsed, list):
        return [
            _normalize_structured_hit(item, default_scope, idx)
            for idx, item in enumerate(parsed[:limit])
            if isinstance(item, dict)
        ]

    if isinstance(parsed, dict):
        if parsed.get("ok") is False:
            return []
        for list_key in ("hits", "results", "items", "recall"):
            candidate = parsed.get(list_key)
            if isinstance(candidate, list):
                return [
                    _normalize_structured_hit(item, default_scope, idx)
                    for idx, item in enumerate(candidate[:limit])
                    if isinstance(item, dict)
                ]
        output_text = parsed.get("output")
        if isinstance(output_text, str):
            return _parse_addr_text_output(
                output_text, default_scope=default_scope, limit=limit
            )

    return []


async def _run_ostk_recall_search(
    query: str, scope: str = "all", limit: int = 10
) -> list[dict]:
    """Run ostk recall-search in the effective root with a 3.0s timeout.

    Returns normalized recall hits and falls back cleanly to [] on timeout,
    missing binary, or non-zero exit so /api/search never fails.
    """
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return []

    cli_scope = _CLI_SCOPE_MAP.get((scope or "all").strip().lower(), "all")
    ostk_bin = shutil.which("ostk") or "ostk"
    cwd = get_effective_root()

    try:
        proc = await asyncio.create_subprocess_exec(
            ostk_bin,
            "recall-search",
            cleaned_query,
            "--scope",
            cli_scope,
            "--limit",
            str(limit),
            "--json",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError, Exception):
        return []

    try:
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_RECALL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.5)
        except Exception:
            pass
        return []
    except Exception:
        return []

    if proc.returncode != 0:
        return []

    raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
    return _parse_ostk_recall_output(raw_stdout, default_scope=scope, limit=limit)


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search topic or keyword")
):
    """Search across tasks by topic and substrate-wide recall hits."""
    try:
        results, recall_hits = await asyncio.gather(
            ostk.search_near(q),
            _run_ostk_recall_search(q, scope="all", limit=10),
        )
        return {**results, "recall": recall_hits}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/deep")
async def deep_search(q: str = Query(..., min_length=1, description="Search query")):
    """Deep search across all kernel state: needles, audit events, and transcripts.

    Uses ostk pitchfork to search everything the kernel knows about,
    including closed tasks, decisions, threads, and audit history.
    """
    try:
        results = await ostk.pitchfork(q)
        return results
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/recall")
async def recall_search(
    q: str = Query(..., min_length=1, description="Search query"),
    scope: str = Query("all", description="Search scope"),
    limit: int = Query(15, ge=1, le=100, description="Maximum hits to return"),
):
    """Search substrate state via ostk recall-search across decisions, code, work, and transcripts."""
    hits = await _run_ostk_recall_search(q, scope=scope, limit=limit)
    return {"hits": hits, "recall": hits, "query": q, "scope": scope}

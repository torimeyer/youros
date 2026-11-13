"""Integration tests for ostk recall-search in /api/search and /api/search/recall."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode("utf-8"), stderr.encode("utf-8"))
    )
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.mark.asyncio
async def test_run_ostk_recall_search_parses_real_cli_json_envelope(tmp_path: Path):
    """Parses real ostk 7.7+ recall-search --json envelope into normalized hits."""
    from routers.search import _run_ostk_recall_search

    cli_payload = json.dumps(
        {
            "ok": True,
            "verb": "recall_search",
            "output": (
                "+ recall_search: hits:4 mode:literal scope:all fallback:true\n"
                "    addr:→3094 score:0.95\n"
                "    addr:tool:h2s9ud6f score:0.90\n"
                "    addr:path:app/src/lib/spawn.ts:42 score:0.75\n"
                "    addr:decision:dec-humanfile-sign score:0.88"
            ),
        }
    )
    proc = _make_fake_proc(stdout=cli_payload, returncode=0)

    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec,
    ):
        hits = await _run_ostk_recall_search("spawn", scope="all", limit=10)

    assert mock_exec.await_count == 1
    call_args = mock_exec.await_args.args
    assert "recall-search" in call_args
    assert "spawn" in call_args
    assert "--scope" in call_args
    assert "--limit" in call_args
    assert "--json" in call_args
    assert mock_exec.await_args.kwargs.get("cwd") == str(tmp_path)

    assert len(hits) == 4
    by_scope = {h["scope"]: h for h in hits}
    assert set(by_scope.keys()) == {"work", "transcript", "code", "decision"}

    assert by_scope["work"]["id"] == "→3094"
    assert by_scope["work"]["source"] == "ostk_recall"

    assert by_scope["transcript"]["id"] == "tool:h2s9ud6f"
    assert by_scope["transcript"]["source"] == "ostk_recall"

    assert by_scope["code"]["path"] == "app/src/lib/spawn.ts"
    assert by_scope["code"]["line"] == 42
    assert by_scope["code"]["title"] == "spawn.ts"
    assert by_scope["code"]["source"] == "ostk_recall"

    assert by_scope["decision"]["id"] == "decision:dec-humanfile-sign"
    assert by_scope["decision"]["source"] == "ostk_recall"


@pytest.mark.asyncio
async def test_run_ostk_recall_search_parses_structured_hits_json(tmp_path: Path):
    """Also normalizes structured JSON hit lists with title/snippet/path/line."""
    from routers.search import _run_ostk_recall_search

    structured_payload = json.dumps(
        {
            "hits": [
                {
                    "id": "dec-42",
                    "scope": "decisions",
                    "title": "Enforce HUMANFILE Ed25519 trust sign",
                    "snippet": "Run ostk trust sign before secret injection",
                    "path": ".ostk/decisions.jsonl",
                    "line": 12,
                },
                {
                    "id": "tr-99",
                    "scope": "transcripts",
                    "title": "Spawn isolation discussion",
                    "snippet": "Worktree isolation prevents lock collisions",
                    "path": "transcripts/2026-11-13.jsonl",
                    "line": 88,
                },
            ]
        }
    )
    proc = _make_fake_proc(stdout=structured_payload, returncode=0)

    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        hits = await _run_ostk_recall_search("trust", scope="all", limit=5)

    assert len(hits) == 2
    assert hits[0] == {
        "id": "dec-42",
        "scope": "decision",
        "title": "Enforce HUMANFILE Ed25519 trust sign",
        "snippet": "Run ostk trust sign before secret injection",
        "path": ".ostk/decisions.jsonl",
        "line": 12,
        "source": "ostk_recall",
    }
    assert hits[1] == {
        "id": "tr-99",
        "scope": "transcript",
        "title": "Spawn isolation discussion",
        "snippet": "Worktree isolation prevents lock collisions",
        "path": "transcripts/2026-11-13.jsonl",
        "line": 88,
        "source": "ostk_recall",
    }


@pytest.mark.asyncio
async def test_run_ostk_recall_search_graceful_fallback_on_nonzero_timeout_and_missing_binary(
    tmp_path: Path,
):
    """Non-zero exit, timeout, or missing ostk binary all return [] without raising."""
    from routers.search import _run_ostk_recall_search

    # 1. Non-zero exit code
    bad_proc = _make_fake_proc(stdout="", stderr="boom", returncode=2)
    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=bad_proc),
        ),
    ):
        assert await _run_ostk_recall_search("spawn") == []

    # 2. Subprocess timeout
    slow_proc = _make_fake_proc()
    slow_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=slow_proc),
        ),
    ):
        assert await _run_ostk_recall_search("spawn") == []
    slow_proc.kill.assert_called_once()

    # 3. Missing binary
    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("ostk not found")),
        ),
    ):
        assert await _run_ostk_recall_search("spawn") == []


@pytest.mark.asyncio
async def test_search_recall_endpoint_returns_normalized_hits(client, tmp_path: Path):
    """GET /api/search/recall?q=spawn returns normalized ostk recall-search hits."""
    cli_payload = json.dumps(
        {
            "ok": True,
            "verb": "recall_search",
            "output": (
                "+ recall_search: hits:2 mode:literal scope:all\n"
                "    addr:→3094 score:0.92\n"
                "    addr:tool:abc12345 score:0.85"
            ),
        }
    )
    proc = _make_fake_proc(stdout=cli_payload, returncode=0)

    with (
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        resp = await client.get("/api/search/recall?q=spawn&scope=all&limit=15")

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "spawn"
    assert len(body["hits"]) == 2
    assert body["recall"] == body["hits"]
    assert body["hits"][0]["id"] == "→3094"
    assert body["hits"][0]["scope"] == "work"
    assert body["hits"][0]["source"] == "ostk_recall"
    assert body["hits"][1]["scope"] == "transcript"


@pytest.mark.asyncio
async def test_search_endpoint_includes_recall_and_preserves_existing_keys(
    client, tmp_path: Path
):
    """GET /api/search?q=spawn preserves tasks/query keys and includes recall hits."""
    near_results = {
        "tasks": [{"id": "→3094", "priority": "P1", "title": "Unified Mission Control"}],
        "ideas": [],
        "query": "spawn",
    }
    structured_payload = json.dumps(
        {
            "hits": [
                {
                    "id": "dec-1",
                    "scope": "decision",
                    "title": "Mission Control consolidation",
                    "snippet": "Consolidate live seats onto Dashboard",
                    "path": ".ostk/decisions.jsonl",
                    "line": 7,
                }
            ]
        }
    )
    proc = _make_fake_proc(stdout=structured_payload, returncode=0)

    with (
        patch("routers.search.ostk") as mock_ostk,
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        mock_ostk.search_near = AsyncMock(return_value=near_results)
        resp = await client.get("/api/search?q=spawn")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == near_results["tasks"]
    assert data["ideas"] == []
    assert data["query"] == "spawn"
    assert len(data["recall"]) == 1
    assert data["recall"][0]["scope"] == "decision"
    assert data["recall"][0]["source"] == "ostk_recall"


@pytest.mark.asyncio
async def test_search_endpoint_succeeds_with_empty_recall_on_recall_timeout(
    client, tmp_path: Path
):
    """If ostk recall-search times out, /api/search still returns 200 with recall=[]."""
    near_results = {
        "tasks": [{"id": "→086", "priority": "P1", "title": "Add concept search"}],
        "query": "spawn",
    }
    slow_proc = _make_fake_proc()
    slow_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

    with (
        patch("routers.search.ostk") as mock_ostk,
        patch("routers.search.get_effective_root", return_value=tmp_path),
        patch(
            "routers.search.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=slow_proc),
        ),
    ):
        mock_ostk.search_near = AsyncMock(return_value=near_results)
        resp = await client.get("/api/search?q=spawn")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == near_results["tasks"]
    assert data["recall"] == []

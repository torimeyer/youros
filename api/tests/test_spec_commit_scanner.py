"""Tests for services.spec_commit_scanner (→1427, Phase 3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_commits(*messages: str) -> list[dict]:
    """Build a list of fake commit dicts."""
    return [
        {"hash": f"abc{i:03d}", "author": "Tori Meyer", "message": msg}
        for i, msg in enumerate(messages)
    ]


# ---------------------------------------------------------------------------
# _get_slug_map
# ---------------------------------------------------------------------------

def test_get_slug_map_returns_empty_when_dir_missing(tmp_path):
    from services.spec_commit_scanner import _get_slug_map

    result = _get_slug_map(specs_dir=tmp_path / "nonexistent")
    assert result == {}


def test_get_slug_map_returns_stems(tmp_path):
    from services.spec_commit_scanner import _get_slug_map

    (tmp_path / "spec-auto-status.md").touch()
    (tmp_path / "gemini-cli-integration.md").touch()

    result = _get_slug_map(specs_dir=tmp_path)
    assert "spec-auto-status" in result
    assert "gemini-cli-integration" in result
    assert all(v.endswith(".md") for v in result.values())


# ---------------------------------------------------------------------------
# _slugs_in_message
# ---------------------------------------------------------------------------

def test_slugs_in_message_whole_word_match():
    from services.spec_commit_scanner import _slugs_in_message

    slug_map = {"spec-auto-status": "/x/spec-auto-status.md"}
    assert _slugs_in_message("feat(spec-auto-status): add phase 3", slug_map) == ["/x/spec-auto-status.md"]


def test_slugs_in_message_case_insensitive():
    from services.spec_commit_scanner import _slugs_in_message

    slug_map = {"my-feature": "/x/my-feature.md"}
    assert _slugs_in_message("feat(My-Feature): something", slug_map) == ["/x/my-feature.md"]


def test_slugs_in_message_no_partial_match():
    from services.spec_commit_scanner import _slugs_in_message

    slug_map = {"feat": "/x/feat.md"}
    # "feature" contains "feat" but not as a whole word
    assert _slugs_in_message("add new feature here", slug_map) == []


def test_slugs_in_message_no_match():
    from services.spec_commit_scanner import _slugs_in_message

    slug_map = {"spec-auto-status": "/x/spec-auto-status.md"}
    assert _slugs_in_message("chore: unrelated commit", slug_map) == []


# ---------------------------------------------------------------------------
# _task_ids_in_message
# ---------------------------------------------------------------------------

def test_task_ids_in_message_arrow_unicode():
    from services.spec_commit_scanner import _task_ids_in_message

    assert _task_ids_in_message("feat(→1427): add watcher") == ["1427"]


def test_task_ids_in_message_arrow_ascii():
    from services.spec_commit_scanner import _task_ids_in_message

    assert _task_ids_in_message("fix(->273): something") == ["273"]


def test_task_ids_in_message_multiple():
    from services.spec_commit_scanner import _task_ids_in_message

    ids = _task_ids_in_message("feat(→1427, →1555): two needles")
    assert "1427" in ids
    assert "1555" in ids


def test_task_ids_in_message_no_match():
    from services.spec_commit_scanner import _task_ids_in_message

    assert _task_ids_in_message("chore: no task references") == []


# ---------------------------------------------------------------------------
# scan_and_claim — slug matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_and_claim_records_claim_for_slug_match(tmp_path):
    """A commit mentioning a spec slug should trigger a passive claim."""
    from services import spec_commit_scanner as scs

    spec_file = tmp_path / "spec-auto-status.md"
    spec_file.write_text("---\ntitle: test\n---\n")

    commits = _make_commits("feat(spec-auto-status): implement phase 3")
    seen: set[str] = set()
    claims_recorded: list[tuple] = []

    async def _fake_record(spec_path: str, author: str) -> None:
        claims_recorded.append((spec_path, author))

    with (
        patch.object(scs, "_recent_commits", return_value=commits),
        patch.object(scs, "_record_passive_claim", side_effect=_fake_record),
    ):
        result = await scs.scan_and_claim(
            repo_path=tmp_path,
            specs_dir=tmp_path,
            seen_commits=seen,
        )

    assert result["commits_scanned"] == 1
    assert result["claims_recorded"] == 1
    assert any("spec-auto-status" in sp for sp, _ in claims_recorded)


@pytest.mark.asyncio
async def test_scan_and_claim_skips_unrelated_commits(tmp_path):
    """A commit with no spec slug or task ID should not trigger a claim."""
    from services import spec_commit_scanner as scs

    (tmp_path / "spec-auto-status.md").touch()
    commits = _make_commits("chore: update readme")
    seen: set[str] = set()
    claims_recorded: list = []

    with (
        patch.object(scs, "_recent_commits", return_value=commits),
        patch.object(scs, "_record_passive_claim", side_effect=lambda *a: claims_recorded.append(a)),
    ):
        result = await scs.scan_and_claim(
            repo_path=tmp_path,
            specs_dir=tmp_path,
            seen_commits=seen,
        )

    assert result["claims_recorded"] == 0
    assert not claims_recorded


@pytest.mark.asyncio
async def test_scan_and_claim_deduplicates_seen_commits(tmp_path):
    """Commits already in seen_commits set must not be processed again."""
    from services import spec_commit_scanner as scs

    (tmp_path / "myfeature.md").touch()
    commits = _make_commits("feat(myfeature): something")
    already_seen = {commits[0]["hash"]}
    claims_recorded: list = []

    with (
        patch.object(scs, "_recent_commits", return_value=commits),
        patch.object(scs, "_record_passive_claim", side_effect=lambda *a: claims_recorded.append(a)),
    ):
        result = await scs.scan_and_claim(
            repo_path=tmp_path,
            specs_dir=tmp_path,
            seen_commits=already_seen,
        )

    assert result["commits_scanned"] == 0
    assert result["claims_recorded"] == 0


@pytest.mark.asyncio
async def test_scan_and_claim_task_id_match(tmp_path):
    """A commit with →NNNN matching a spec in _spec_task_origin triggers a claim."""
    from services import spec_commit_scanner as scs

    commits = _make_commits("fix(→1427): something")
    seen: set[str] = set()
    claims_recorded: list = []

    fake_origin = {"1427": "/abs/path/spec-auto-status.md"}

    with (
        patch.object(scs, "_recent_commits", return_value=commits),
        patch.object(scs, "_spec_path_for_task", side_effect=lambda tid: fake_origin.get(tid)),
        patch.object(scs, "_record_passive_claim", side_effect=lambda *a: claims_recorded.append(a)),
    ):
        result = await scs.scan_and_claim(
            repo_path=tmp_path,
            specs_dir=tmp_path,  # empty dir — no slug matches
            seen_commits=seen,
        )

    assert result["claims_recorded"] == 1
    assert claims_recorded[0][0] == "/abs/path/spec-auto-status.md"


@pytest.mark.asyncio
async def test_scan_and_claim_no_double_claim_when_slug_and_task_match_same_spec(tmp_path):
    """If both a slug match and a task ID match point to the same spec, claim once."""
    from services import spec_commit_scanner as scs

    spec_file = tmp_path / "myspec.md"
    spec_file.write_text("---\ntitle: test\n---\n")
    abs_path = str(spec_file.resolve())

    commits = _make_commits("feat(myspec →1234): implement")
    seen: set[str] = set()
    claims_recorded: list = []

    with (
        patch.object(scs, "_recent_commits", return_value=commits),
        patch.object(scs, "_spec_path_for_task", return_value=abs_path),
        patch.object(scs, "_record_passive_claim", side_effect=lambda *a: claims_recorded.append(a)),
    ):
        result = await scs.scan_and_claim(
            repo_path=tmp_path,
            specs_dir=tmp_path,
            seen_commits=seen,
        )

    # Should claim exactly once, not twice
    assert result["claims_recorded"] == 1
    assert len(claims_recorded) == 1

"""Regression test: specs with status:complete must not appear in the unfinished badge count.

Root cause (→2025): spec_counts mapped "complete" → "in_progress" in _status_to_stage,
so completed specs inflated the sidebar badge. Fixed to map "complete" → "complete" and
override the lagging stage field when raw status is "complete".
"""

import pytest


def _count_stages(docs: list[dict]) -> dict:
    """Inline port of the spec_counts stage-mapping logic from api/routers/specs.py."""
    _status_to_stage = {
        "draft": "draft",
        "ready": "ready",
        "spec": "ready",
        "in-progress": "in_progress",
        "building": "in_progress",
        "complete": "complete",
    }
    by_stage: dict[str, int] = {}
    for d in docs:
        raw_status = d.get("status", "draft")
        stage = d.get("stage") or _status_to_stage.get(raw_status, "draft")
        if stage not in ("draft", "ready", "in_progress", "complete"):
            stage = "draft"
        # Always honour explicit status:complete even if stage field lags
        if raw_status == "complete":
            stage = "complete"
        by_stage[stage] = by_stage.get(stage, 0) + 1
    unfinished = by_stage.get("ready", 0) + by_stage.get("in_progress", 0)
    return {"by_stage": by_stage, "unfinished": unfinished, "total": len(docs)}


def test_complete_status_not_counted_as_unfinished():
    """A doc with status:complete must never appear in unfinished count."""
    docs = [{"status": "complete", "stage": "ready"}]
    result = _count_stages(docs)
    assert result["unfinished"] == 0, "complete spec must not be unfinished"
    assert result["by_stage"].get("complete") == 1
    assert result["by_stage"].get("in_progress", 0) == 0


def test_complete_overrides_lagging_stage_field():
    """Even if list_docs returns stage:ready for a completed spec, complete wins."""
    docs = [
        {"status": "complete", "stage": "ready"},
        {"status": "complete", "stage": "in-progress"},
        {"status": "complete"},
    ]
    result = _count_stages(docs)
    assert result["unfinished"] == 0
    assert result["by_stage"].get("complete") == 3


def test_spec_status_counts_as_ready():
    docs = [{"status": "spec"}]
    result = _count_stages(docs)
    assert result["unfinished"] == 1
    assert result["by_stage"].get("ready") == 1


def test_building_status_counts_as_in_progress():
    docs = [{"status": "building"}]
    result = _count_stages(docs)
    assert result["unfinished"] == 1
    assert result["by_stage"].get("in_progress") == 1


def test_mixed_set_counts_correctly():
    docs = [
        {"status": "complete", "stage": "ready"},
        {"status": "spec"},
        {"status": "building"},
        {"status": "draft"},
        {"status": "in-progress"},
    ]
    result = _count_stages(docs)
    # complete is excluded from unfinished
    assert result["unfinished"] == 3  # spec + building + in-progress
    assert result["by_stage"]["complete"] == 1
    assert result["by_stage"]["ready"] == 1
    assert result["by_stage"]["in_progress"] == 2
    assert result["by_stage"]["draft"] == 1
    assert result["total"] == 5

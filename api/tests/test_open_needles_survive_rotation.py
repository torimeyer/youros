"""Regression: open needles must survive a store rotation that left them in the
rotated archive (issues.jsonl.1) instead of the active issues.jsonl.

Root cause (2026-06-01): an overnight rotation truncated the active
issues.jsonl to 2 closed entries and pushed all ~140 open needles into the
rotated archive. The →1694 reconcile filter intersected the daemon's results
against active-store IDs, so it dropped every open needle and GET /api/tasks
returned {"tasks": []} while `ostk work list` still showed 138 open.

Fix: the reconcile filter suppresses ONLY closed/shelved archive entries. A
needle the daemon reports as live (open/in_progress/ready/...) is always kept,
even when its id is absent from the active issues.jsonl. The daemon is
authoritative for what is open; an id missing from the active file means the
store was rotated, not that the needle is gone.
"""
from services.ostk import _reconcile_active


def _t(needle_id, status):
    return {"id": needle_id, "status": status, "title": needle_id}


def test_open_needle_not_in_active_store_is_kept():
    # The exact rotation bug: daemon serves an open needle whose id is NOT in
    # the active issues.jsonl (which only holds stale closed entries).
    seen = {"→100": _t("→100", "open")}
    active_ids = {"→1932", "→1951"}
    out = {t["id"] for t in _reconcile_active(seen, active_ids)}
    assert "→100" in out


def test_closed_archive_entry_not_in_active_store_is_dropped():
    # The original →1694 goal: hide rotated-archive closed noise.
    seen = {"→9": _t("→9", "closed")}
    active_ids = {"→1932"}
    out = {t["id"] for t in _reconcile_active(seen, active_ids)}
    assert "→9" not in out


def test_closed_entry_in_active_store_is_kept():
    seen = {"→1932": _t("→1932", "closed")}
    active_ids = {"→1932"}
    out = {t["id"] for t in _reconcile_active(seen, active_ids)}
    assert "→1932" in out


def test_shelved_archive_entry_is_dropped():
    seen = {"→7": _t("→7", "shelved")}
    active_ids = {"→1932"}
    out = {t["id"] for t in _reconcile_active(seen, active_ids)}
    assert "→7" not in out


def test_none_active_ids_is_no_filter():
    seen = {"→1": _t("→1", "open"), "→2": _t("→2", "closed")}
    out = {t["id"] for t in _reconcile_active(seen, None)}
    assert out == {"→1", "→2"}


def test_in_progress_and_ready_survive_empty_active_store():
    # Worst-case rotation: active store empty. Live work must still show.
    seen = {"→a": _t("→a", "in_progress"), "→b": _t("→b", "ready")}
    out = {t["id"] for t in _reconcile_active(seen, set())}
    assert out == {"→a", "→b"}


def test_open_rotated_needle_rescued_even_when_active_store_is_healthy():
    # →2200: mid-day rotation edge case. A few fresh tasks were created AFTER
    # the rotation (so they appear in the active store as live), but older open
    # needles that were pushed into the archive must STILL be rescued.
    # "active_healthy" was True because fresh_task is live in active_ids, but
    # that must NOT cause old_open to be dropped.
    seen = {
        "→fresh": _t("→fresh", "in_progress"),  # present in active store
        "→old_open": _t("→old_open", "open"),   # only in rotated archive
    }
    active_ids = {"→fresh"}  # active store is "healthy" — has a live needle
    out = {t["id"] for t in _reconcile_active(seen, active_ids)}
    assert "→old_open" in out, (
        "open needle absent from active store must be rescued even when "
        "the active store holds other live needles (→2200)"
    )

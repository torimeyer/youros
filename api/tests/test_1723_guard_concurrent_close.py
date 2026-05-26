"""Regression tests for →1723: _guard_real_store_writes false-positives.

When ostk agents close needles concurrently, issues.jsonl is rewritten with
existing entries removed or status-changed but no new IDs added.  The guard
must tolerate these external changes and only fire when a test itself leaks
a new needle ID into the real store.
"""

from __future__ import annotations

import json


# Pull the helpers directly from conftest (they are module-level functions).
from tests.conftest import _guard_jsonl_ids, _issues_only_external_activity


def _enc(*entries: dict) -> bytes:
    """Encode a list of dicts as JSONL bytes."""
    return b"\n".join(json.dumps(e).encode() for e in entries) + b"\n"


# ---------------------------------------------------------------------------
# _guard_jsonl_ids
# ---------------------------------------------------------------------------


def test_jsonl_ids_extracts_ids():
    content = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→101", "status": "open"},
    )
    assert _guard_jsonl_ids(content) == {"→100", "→101"}


def test_jsonl_ids_empty_content():
    assert _guard_jsonl_ids(b"") == set()


def test_jsonl_ids_ignores_malformed_lines():
    content = '{"id": "→100"}\nnot-json\n{"id": "→101"}\n'.encode()
    assert _guard_jsonl_ids(content) == {"→100", "→101"}


def test_jsonl_ids_skips_entries_without_id():
    content = _enc({"title": "no id here", "status": "open"}, {"id": "→200"})
    assert _guard_jsonl_ids(content) == {"→200"}


# ---------------------------------------------------------------------------
# _issues_only_external_activity
# ---------------------------------------------------------------------------


def test_external_activity_needle_closed_removed():
    """Ostk removes a closed needle from the file → external, guard should pass."""
    before = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→101", "status": "open"},
    )
    after = _enc({"id": "→100", "status": "open"})  # →101 was closed and removed
    assert _issues_only_external_activity(before, after) is True


def test_external_activity_needle_status_changed():
    """Ostk rewrites a needle entry with status=closed → external, guard should pass."""
    before = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→101", "status": "open"},
    )
    after = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→101", "status": "closed", "closed_at": "2026-05-25T00:00:00Z"},
    )
    # →101 still present but changed — IDs in after (→100, →101) ⊆ before (→100, →101)
    assert _issues_only_external_activity(before, after) is True


def test_external_activity_all_needles_closed():
    """All existing needles removed → still external (pure deletion)."""
    before = _enc({"id": "→100", "status": "open"})
    after = b""
    assert _issues_only_external_activity(before, after) is True


def test_not_external_new_id_added():
    """Test snuck a new needle into the real store → NOT external, guard must fire."""
    before = _enc({"id": "→100", "status": "open"})
    after = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→999", "status": "open"},  # NEW — test wrote this
    )
    assert _issues_only_external_activity(before, after) is False


def test_not_external_when_unchanged():
    """No change at all → function returns False (guard simply passes via ==)."""
    content = _enc({"id": "→100", "status": "open"})
    assert _issues_only_external_activity(content, content) is False


def test_not_external_when_before_is_none():
    after = _enc({"id": "→100", "status": "open"})
    assert _issues_only_external_activity(None, after) is False


def test_not_external_when_after_is_none():
    before = _enc({"id": "→100", "status": "open"})
    assert _issues_only_external_activity(before, None) is False


def test_external_activity_mixed_close_and_remaining_open():
    """One needle closed (removed), another stays open → external."""
    before = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→101", "status": "open"},
        {"id": "→102", "status": "open"},
    )
    after = _enc(
        {"id": "→100", "status": "open"},
        {"id": "→102", "status": "open"},
    )
    assert _issues_only_external_activity(before, after) is True

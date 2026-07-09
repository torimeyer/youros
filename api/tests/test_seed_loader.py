"""Tests for the data-only seed loader (spec S009 Track 0.3).

The loader reads seed JSON files from a seeds directory, applies them
through existing CRUD code paths, tracks what it created in a .applied
file keyed on (target, version), and can un-apply exactly those items.

All tests run against a tmp_path seeds dir and an isolated labels
store, never the real ~/.myos or ~/.youros.
"""

import json
from unittest.mock import patch

import pytest

from services.labels_store import LabelsStore
from services.seed_loader import apply_seeds, unapply


@pytest.fixture()
def label_env(tmp_path):
    """Isolated seeds dir plus labels store. Yields (seeds_dir, store)."""
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    store = LabelsStore(path=tmp_path / "labels.json")
    with patch("services.labels_store.labels_store", store):
        yield seeds_dir, store


def _write_seed(seeds_dir, name="starter.json", target="labels", version="1", payload=None):
    if payload is None:
        payload = [
            {"op": "create", "data": {"name": "Launch", "color": "#3b82f6"}},
            {"op": "create", "data": {"name": "Website", "color": "#22c55e"}},
        ]
    seed = {"target": target, "version": version, "payload": payload}
    (seeds_dir / name).write_text(json.dumps(seed))


def test_apply_creates_items_and_records_them(label_env):
    seeds_dir, store = label_env
    _write_seed(seeds_dir)

    summary = apply_seeds(seeds_dir=seeds_dir)

    assert summary["applied"] == ["starter.json"]
    names = {l["name"] for l in store.list_labels()}
    assert names == {"Launch", "Website"}

    applied = json.loads((seeds_dir / ".applied").read_text())
    assert applied["labels"]["version"] == "1"
    assert len(applied["labels"]["created_ids"]) == 2


def test_reapply_same_version_does_not_duplicate(label_env):
    seeds_dir, store = label_env
    _write_seed(seeds_dir)

    apply_seeds(seeds_dir=seeds_dir)
    summary = apply_seeds(seeds_dir=seeds_dir)

    assert summary["applied"] == []
    assert len(store.list_labels()) == 2


def test_version_bump_reapplies_as_update(label_env):
    seeds_dir, store = label_env
    _write_seed(seeds_dir)
    apply_seeds(seeds_dir=seeds_dir)

    # Downstream ships version 2 with different content.
    _write_seed(
        seeds_dir,
        version="2",
        payload=[{"op": "create", "data": {"name": "Rollout", "color": "#ef4444"}}],
    )
    summary = apply_seeds(seeds_dir=seeds_dir)

    assert summary["applied"] == ["starter.json"]
    names = {l["name"] for l in store.list_labels()}
    assert names == {"Rollout"}
    applied = json.loads((seeds_dir / ".applied").read_text())
    assert applied["labels"]["version"] == "2"


def test_unapply_round_trip_leaves_store_as_it_began(label_env):
    seeds_dir, store = label_env
    assert store.list_labels() == []
    _write_seed(seeds_dir)
    apply_seeds(seeds_dir=seeds_dir)
    assert len(store.list_labels()) == 2

    result = unapply("labels", "1", seeds_dir=seeds_dir)

    assert result["removed"] == 2
    assert store.list_labels() == []
    applied = json.loads((seeds_dir / ".applied").read_text())
    assert "labels" not in applied


def test_unapply_wrong_version_is_a_noop(label_env):
    seeds_dir, store = label_env
    _write_seed(seeds_dir)
    apply_seeds(seeds_dir=seeds_dir)

    result = unapply("labels", "99", seeds_dir=seeds_dir)

    assert result["removed"] == 0
    assert len(store.list_labels()) == 2


def test_malformed_seeds_are_skipped_and_valid_ones_still_apply(label_env):
    seeds_dir, store = label_env
    # Broken JSON.
    (seeds_dir / "a-broken.json").write_text("{not json")
    # Unknown target.
    _write_seed(seeds_dir, name="b-unknown.json", target="nonsense")
    # Payload is not a list.
    (seeds_dir / "c-badpayload.json").write_text(
        json.dumps({"target": "labels", "version": "1", "payload": {"op": "create"}})
    )
    # Valid seed alongside the bad ones.
    _write_seed(seeds_dir, name="d-valid.json")

    summary = apply_seeds(seeds_dir=seeds_dir)

    assert summary["applied"] == ["d-valid.json"]
    assert set(summary["skipped"]) == {"a-broken.json", "b-unknown.json", "c-badpayload.json"}
    assert len(store.list_labels()) == 2


def test_missing_seeds_dir_is_a_quiet_noop(tmp_path):
    summary = apply_seeds(seeds_dir=tmp_path / "does-not-exist")
    assert summary == {"applied": [], "skipped": [], "unchanged": []}


def test_unsupported_op_in_payload_is_skipped_not_fatal(label_env):
    seeds_dir, store = label_env
    _write_seed(
        seeds_dir,
        payload=[
            {"op": "delete", "data": {"name": "Nope"}},
            {"op": "create", "data": {"name": "Kept", "color": "#3b82f6"}},
        ],
    )

    apply_seeds(seeds_dir=seeds_dir)

    names = {l["name"] for l in store.list_labels()}
    assert names == {"Kept"}

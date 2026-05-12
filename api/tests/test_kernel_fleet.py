"""Tests for →1160-wave-a: api/services/kernel_fleet.py

Covers:
  - read_kernel_fleet: missing file, empty file, valid rows, partial/malformed rows
  - _normalise_row: field mapping and status translation
  - merge_kernel_fleet: kernel rows populate agents_map; existing rows not clobbered
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.kernel_fleet import read_kernel_fleet, merge_kernel_fleet, _normalise_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# _normalise_row
# ---------------------------------------------------------------------------

class TestNormaliseRow:
    def test_active_becomes_running(self):
        row = _normalise_row({"alias": "a1", "status": "active",
                               "registered_at": "2026-05-12T00:00:00Z",
                               "last_seen": "2026-05-12T00:01:00Z", "pid": 42})
        assert row is not None
        assert row["status"] == "running"
        assert row["name"] == "a1"
        assert row["spawned_at"] == "2026-05-12T00:00:00Z"
        assert row["last_heartbeat_at"] == "2026-05-12T00:01:00Z"
        assert row["pid"] == 42
        assert row["source"] == "kernel"

    def test_inactive_becomes_stopped(self):
        row = _normalise_row({"alias": "a2", "status": "inactive"})
        assert row is not None
        assert row["status"] == "stopped"

    def test_unknown_status_passed_through(self):
        row = _normalise_row({"alias": "a3", "status": "zombie"})
        assert row is not None
        assert row["status"] == "zombie"

    def test_missing_alias_returns_none(self):
        assert _normalise_row({"status": "active", "pid": 1}) is None

    def test_empty_alias_returns_none(self):
        assert _normalise_row({"alias": "", "status": "active"}) is None

    def test_optional_fields_absent(self):
        row = _normalise_row({"alias": "a4", "status": "active"})
        assert row is not None
        assert "spawned_at" not in row
        assert "last_heartbeat_at" not in row
        assert "pid" not in row

    def test_trust_tier_preserved(self):
        row = _normalise_row({"alias": "a5", "status": "active", "trust_tier": "t2"})
        assert row is not None
        assert row["trust_tier"] == "t2"


# ---------------------------------------------------------------------------
# read_kernel_fleet
# ---------------------------------------------------------------------------

class TestReadKernelFleet:
    def test_missing_file_returns_empty(self, tmp_path):
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path):
        (tmp_path / "agents.jsonl").write_text("", encoding="utf-8")
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert result == []

    def test_blank_lines_skipped(self, tmp_path):
        (tmp_path / "agents.jsonl").write_text("\n\n\n", encoding="utf-8")
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert result == []

    def test_valid_rows_parsed(self, tmp_path):
        rows = [
            {"alias": "myos-api", "pid": 100, "status": "active",
             "registered_at": "2026-05-12T00:00:00Z",
             "last_seen": "2026-05-12T00:01:00Z", "trust_tier": "t2"},
            {"alias": "scheduler", "pid": 101, "status": "inactive",
             "registered_at": "2026-05-12T00:00:01Z",
             "last_seen": "2026-05-12T00:00:30Z", "trust_tier": "t2"},
        ]
        _write_jsonl(tmp_path / "agents.jsonl", rows)
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"myos-api", "scheduler"}

    def test_malformed_json_line_skipped(self, tmp_path):
        path = tmp_path / "agents.jsonl"
        path.write_text('{"alias":"good","status":"active"}\nnot-json\n{"alias":"also-good","status":"inactive"}\n',
                        encoding="utf-8")
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert len(result) == 2

    def test_non_dict_json_line_skipped(self, tmp_path):
        path = tmp_path / "agents.jsonl"
        path.write_text('{"alias":"ok","status":"active"}\n["list","value"]\n', encoding="utf-8")
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert len(result) == 1

    def test_row_without_alias_skipped(self, tmp_path):
        rows = [
            {"pid": 99, "status": "active"},
            {"alias": "legit", "status": "active"},
        ]
        _write_jsonl(tmp_path / "agents.jsonl", rows)
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert len(result) == 1
        assert result[0]["name"] == "legit"

    def test_mtime_cache_hit(self, tmp_path):
        path = tmp_path / "agents.jsonl"
        _write_jsonl(path, [{"alias": "a", "status": "active"}])
        r1 = read_kernel_fleet(ostk_dir=tmp_path)
        r2 = read_kernel_fleet(ostk_dir=tmp_path)
        assert r1 == r2

    def test_status_mapping_applied(self, tmp_path):
        _write_jsonl(tmp_path / "agents.jsonl", [{"alias": "x", "status": "active"}])
        result = read_kernel_fleet(ostk_dir=tmp_path)
        assert result[0]["status"] == "running"


# ---------------------------------------------------------------------------
# merge_kernel_fleet
# ---------------------------------------------------------------------------

class TestMergeKernelFleet:
    def test_new_names_inserted(self, tmp_path):
        _write_jsonl(tmp_path / "agents.jsonl", [
            {"alias": "kernel-agent", "status": "active",
             "registered_at": "2026-05-12T00:00:00Z",
             "last_seen": "2026-05-12T00:01:00Z"},
        ])
        agents_map: dict = {}
        merge_kernel_fleet(agents_map, ostk_dir=tmp_path)
        assert "kernel-agent" in agents_map
        assert agents_map["kernel-agent"]["status"] == "running"

    def test_existing_names_not_clobbered(self, tmp_path):
        _write_jsonl(tmp_path / "agents.jsonl", [
            {"alias": "existing", "status": "active"},
        ])
        agents_map = {"existing": {"name": "existing", "status": "completed", "source": "api"}}
        merge_kernel_fleet(agents_map, ostk_dir=tmp_path)
        # The pre-existing "completed" status must survive.
        assert agents_map["existing"]["status"] == "completed"
        assert agents_map["existing"]["source"] == "api"

    def test_missing_file_leaves_map_unchanged(self, tmp_path):
        agents_map: dict = {"pre": {"name": "pre", "status": "running"}}
        merge_kernel_fleet(agents_map, ostk_dir=tmp_path)
        assert list(agents_map.keys()) == ["pre"]

    def test_source_is_kernel(self, tmp_path):
        _write_jsonl(tmp_path / "agents.jsonl", [{"alias": "kk", "status": "inactive"}])
        agents_map: dict = {}
        merge_kernel_fleet(agents_map, ostk_dir=tmp_path)
        assert agents_map["kk"]["source"] == "kernel"

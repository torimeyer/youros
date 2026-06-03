"""Tests for the /api/rules endpoints (→1288 wave 4/7).

Covers:
  GET /api/rules            — returns merged default + user config
  PUT /api/rules/{key}      — atomic write; rejects unknown keys
  POST /api/rules/reset     — removes per-key or all overrides
  GET /api/rules/fires      — tail of rule-fires.jsonl with filter + limit

All tests redirect USER_RULES_PATH and RULE_FIRES_LOG onto tmp_path so the
user's real ~/.myos/rules.json and ~/.claude/logs/rule-fires.jsonl are
never read or written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Make sure api/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.rules as rules_router  # noqa: E402
from main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect USER_RULES_PATH and RULE_FIRES_LOG onto tmp_path.

    Leaves DEFAULT_RULES_PATH pointing at the real in-repo file so we test
    against the actual shipped schema.
    """
    user_path = tmp_path / "rules.json"
    log_path = tmp_path / "rule-fires.jsonl"
    monkeypatch.setattr(rules_router, "USER_RULES_PATH", user_path)
    monkeypatch.setattr(rules_router, "RULE_FIRES_LOG", log_path)
    return {
        "user": user_path,
        "log": log_path,
        "defaults": rules_router.DEFAULT_RULES_PATH,
    }


@pytest_asyncio.fixture
async def http(isolated_paths):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# GET /api/rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rules_returns_all_keys_from_defaults(http, isolated_paths):
    """Every key present in default-rules.json must come back in GET."""
    with open(isolated_paths["defaults"], "r") as f:
        defaults = json.load(f)
    expected_keys = set(defaults["rules"].keys())

    resp = await http.get("/api/rules")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 10, "default-rules.json should ship at least 10 rules"
    got_keys = {r["key"] for r in rows}
    assert got_keys == expected_keys


@pytest.mark.asyncio
async def test_get_rules_each_row_has_required_fields(http):
    resp = await http.get("/api/rules")
    rows = resp.json()
    for row in rows:
        assert "key" in row
        assert "enabled" in row
        assert "params" in row
        assert "meta" in row
        assert "default_enabled" in row
        assert "source" in row
        assert row["source"] in ("default", "user")
        # _meta should not leak into params — it lives in `meta` instead.
        assert "_meta" not in row["params"]


def test_default_rules_drops_dead_task_state_hygiene():
    """task_state_hygiene shipped with no hook implementation and was off by
    default; it duplicated the task-quality concern of task_hygiene and the
    state-accuracy guidance that already lives in memory. It is removed as a
    dedup (17 -> 16 rules). task_hygiene, which has a real implementation,
    remains.
    """
    from routers.rules import DEFAULT_RULES_PATH

    data = json.loads(DEFAULT_RULES_PATH.read_text())
    rules = data["rules"]
    assert "task_state_hygiene" not in rules
    assert "task_hygiene" in rules


@pytest.mark.asyncio
async def test_get_rules_marks_default_source_when_no_user_file(http, isolated_paths):
    assert not isolated_paths["user"].exists()
    resp = await http.get("/api/rules")
    rows = resp.json()
    for row in rows:
        assert row["source"] == "default"
        assert row["enabled"] == row["default_enabled"]


@pytest.mark.asyncio
async def test_get_rules_overlays_user_enabled(http, isolated_paths):
    """A user file that flips `enabled: true` for one key must surface."""
    user_path = isolated_paths["user"]
    user_path.write_text(json.dumps({
        "schema_version": 1,
        "rules": {
            "saa_must_spawn": {"enabled": True},
        },
    }))

    resp = await http.get("/api/rules")
    rows = {r["key"]: r for r in resp.json()}
    assert rows["saa_must_spawn"]["enabled"] is True
    assert rows["saa_must_spawn"]["source"] == "user"
    # default_enabled stays at the in-repo value (True for saa_must_spawn after →2047).
    assert rows["saa_must_spawn"]["default_enabled"] is True

    # Other rules should still be default-sourced.
    assert rows["adhd_monitor_pairing"]["source"] == "default"


@pytest.mark.asyncio
async def test_get_rules_overlays_user_params(http, isolated_paths):
    """User overrides on params merge into the row's params dict."""
    isolated_paths["user"].write_text(json.dumps({
        "schema_version": 1,
        "rules": {
            "incremental_commit_warning": {"max_uncommitted_files": 12},
        },
    }))
    resp = await http.get("/api/rules")
    rows = {r["key"]: r for r in resp.json()}
    assert rows["incremental_commit_warning"]["params"]["max_uncommitted_files"] == 12
    assert rows["incremental_commit_warning"]["source"] == "user"


# ---------------------------------------------------------------------------
# PUT /api/rules/{key}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_writes_enabled_to_user_file(http, isolated_paths):
    resp = await http.put(
        "/api/rules/saa_must_spawn",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "saa_must_spawn"
    assert body["enabled"] is True

    # File should now exist with our override.
    on_disk = json.loads(isolated_paths["user"].read_text())
    assert on_disk["rules"]["saa_must_spawn"]["enabled"] is True


@pytest.mark.asyncio
async def test_put_merges_params(http, isolated_paths):
    resp = await http.put(
        "/api/rules/incremental_commit_warning",
        json={"enabled": True, "params": {"max_uncommitted_files": 7}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["params"]["max_uncommitted_files"] == 7

    on_disk = json.loads(isolated_paths["user"].read_text())
    assert on_disk["rules"]["incremental_commit_warning"]["enabled"] is True
    assert on_disk["rules"]["incremental_commit_warning"]["max_uncommitted_files"] == 7


@pytest.mark.asyncio
async def test_put_rejects_unknown_key(http):
    resp = await http.put("/api/rules/this_rule_does_not_exist", json={"enabled": True})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_preserves_other_user_overrides(http, isolated_paths):
    """Writing one key must not nuke other keys in rules.json."""
    isolated_paths["user"].write_text(json.dumps({
        "schema_version": 1,
        "rules": {
            "adhd_monitor_pairing": {"enabled": True},
        },
    }))

    resp = await http.put("/api/rules/saa_must_spawn", json={"enabled": True})
    assert resp.status_code == 200

    on_disk = json.loads(isolated_paths["user"].read_text())
    assert on_disk["rules"]["adhd_monitor_pairing"]["enabled"] is True
    assert on_disk["rules"]["saa_must_spawn"]["enabled"] is True


@pytest.mark.asyncio
async def test_put_is_atomic_on_filesystem_failure(http, isolated_paths, monkeypatch):
    """If os.rename fails mid-write, the existing rules.json stays intact.

    The atomic-write pattern is `write to .tmp + os.rename`. If rename
    raises, the original file is untouched and the request must surface
    the failure (500) so the UI can show an error.
    """
    # Seed an existing file.
    original = {"schema_version": 1, "rules": {"adhd_monitor_pairing": {"enabled": True}}}
    isolated_paths["user"].write_text(json.dumps(original))
    original_bytes = isolated_paths["user"].read_bytes()

    # Make os.rename inside the router raise.
    def _boom(*args, **kwargs):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(rules_router.os, "rename", _boom)

    resp = await http.put("/api/rules/saa_must_spawn", json={"enabled": True})
    assert resp.status_code == 500

    # Original content untouched.
    assert isolated_paths["user"].read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# POST /api/rules/reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_with_no_key_empties_user_file(http, isolated_paths):
    isolated_paths["user"].write_text(json.dumps({
        "schema_version": 1,
        "rules": {
            "saa_must_spawn": {"enabled": True},
            "adhd_monitor_pairing": {"enabled": True},
        },
    }))
    resp = await http.post("/api/rules/reset", json={})
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    for row in rows:
        assert row["source"] == "default"

    on_disk = json.loads(isolated_paths["user"].read_text())
    assert on_disk == {"schema_version": 1, "rules": {}}


@pytest.mark.asyncio
async def test_reset_with_key_removes_just_that_override(http, isolated_paths):
    isolated_paths["user"].write_text(json.dumps({
        "schema_version": 1,
        "rules": {
            "saa_must_spawn": {"enabled": True},
            "adhd_monitor_pairing": {"enabled": True},
        },
    }))
    resp = await http.post("/api/rules/reset", json={"key": "saa_must_spawn"})
    assert resp.status_code == 200

    on_disk = json.loads(isolated_paths["user"].read_text())
    assert "saa_must_spawn" not in on_disk["rules"]
    assert on_disk["rules"]["adhd_monitor_pairing"]["enabled"] is True


@pytest.mark.asyncio
async def test_reset_with_unknown_key_returns_400(http):
    resp = await http.post("/api/rules/reset", json={"key": "not_a_real_rule"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reset_with_key_no_existing_override_is_noop(http, isolated_paths):
    """Reset on a key that wasn't overridden returns 200 and leaves file empty."""
    assert not isolated_paths["user"].exists()
    resp = await http.post("/api/rules/reset", json={"key": "saa_must_spawn"})
    assert resp.status_code == 200
    # No write happens when there was no override and no user file.
    assert not isolated_paths["user"].exists()


# ---------------------------------------------------------------------------
# GET /api/rules/fires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fires_returns_empty_when_log_missing(http, isolated_paths):
    assert not isolated_paths["log"].exists()
    resp = await http.get("/api/rules/fires")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_fires_filters_by_rule(http, isolated_paths):
    log = isolated_paths["log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join([
        json.dumps({"ts": "2026-05-13T10:00:00Z", "rule": "saa_must_spawn", "decision": "block"}),
        json.dumps({"ts": "2026-05-13T10:01:00Z", "rule": "bash_guards", "decision": "allow"}),
        json.dumps({"ts": "2026-05-13T10:02:00Z", "rule": "saa_must_spawn", "decision": "allow"}),
    ]))
    resp = await http.get("/api/rules/fires", params={"rule": "saa_must_spawn"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    for r in rows:
        assert r["rule"] == "saa_must_spawn"
    # Sorted desc by ts.
    assert rows[0]["ts"] == "2026-05-13T10:02:00Z"


@pytest.mark.asyncio
async def test_fires_respects_limit(http, isolated_paths):
    log = isolated_paths["log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"ts": f"2026-05-13T10:{i:02d}:00Z", "rule": "bash_guards", "decision": "allow"})
        for i in range(30)
    ]
    log.write_text("\n".join(lines))
    resp = await http.get("/api/rules/fires", params={"limit": 5})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 5
    # Newest first.
    assert rows[0]["ts"] == "2026-05-13T10:29:00Z"


@pytest.mark.asyncio
async def test_fires_filters_by_since(http, isolated_paths):
    log = isolated_paths["log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join([
        json.dumps({"ts": "2026-05-13T09:00:00Z", "rule": "x"}),
        json.dumps({"ts": "2026-05-13T10:00:00Z", "rule": "x"}),
        json.dumps({"ts": "2026-05-13T11:00:00Z", "rule": "x"}),
    ]))
    resp = await http.get("/api/rules/fires", params={"since": "2026-05-13T10:00:00Z"})
    assert resp.status_code == 200
    rows = resp.json()
    # since is inclusive: the 10:00 row qualifies.
    assert len(rows) == 2
    timestamps = [r["ts"] for r in rows]
    assert "2026-05-13T09:00:00Z" not in timestamps


@pytest.mark.asyncio
async def test_fires_tolerates_malformed_lines(http, isolated_paths):
    log = isolated_paths["log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join([
        json.dumps({"ts": "2026-05-13T10:00:00Z", "rule": "a"}),
        "not-json-at-all",
        "",
        json.dumps({"ts": "2026-05-13T10:01:00Z", "rule": "a"}),
    ]))
    resp = await http.get("/api/rules/fires")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2

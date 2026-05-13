"""Tests for →1303: registry-jsonl reader and MYOS_AGENTS_USE_REGISTRY flag."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_CHAT_NOTIFICATIONS", "1")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")

from services.registry_reader import (
    read_registry_jsonl,
    read_registry_for_snapshot,
    registry_to_kernel_ps_format,
)


class TestReadRegistryJsonl:
    def test_returns_empty_for_missing_file(self, tmp_path):
        result = read_registry_jsonl(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_parses_valid_rows(self, tmp_path):
        path = tmp_path / "agents.jsonl"
        rows = [
            {"alias": "agent-a", "pid": 1234, "status": "active",
             "registered_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:01:00Z", "trust_tier": "t2"},
            {"alias": "agent-b", "pid": 5678, "status": "inactive",
             "registered_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:01:00Z", "trust_tier": "t2"},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows))
        result = read_registry_jsonl(path)
        assert len(result) == 2
        assert result[0]["alias"] == "agent-a"
        assert result[1]["alias"] == "agent-b"

    def test_skips_blank_lines_and_bad_json(self, tmp_path):
        path = tmp_path / "agents.jsonl"
        path.write_text(
            '{"alias":"ok","status":"active"}\n\nnot-json\n{"alias":"ok2","status":"inactive"}\n'
        )
        result = read_registry_jsonl(path)
        assert len(result) == 2
        assert result[0]["alias"] == "ok"
        assert result[1]["alias"] == "ok2"


class TestRegistryToKernelPsFormat:
    def test_active_row_maps_to_running(self):
        rows = [{"alias": "agent-a", "pid": 1234, "status": "active",
                 "registered_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:01:00Z"}]
        result = registry_to_kernel_ps_format(rows)
        assert result["daemon_running"] is True
        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert agent["name"] == "agent-a"
        assert agent["status"] == "running"
        assert agent["source"] == "registry"
        assert agent["pid"] == 1234
        assert agent["spawned_at"] == "2026-01-01T00:00:00Z"
        assert agent["last_heartbeat_at"] == "2026-01-01T00:01:00Z"

    def test_inactive_row_maps_to_stopped(self):
        rows = [{"alias": "agent-b", "status": "inactive"}]
        result = registry_to_kernel_ps_format(rows)
        assert result["agents"][0]["status"] == "stopped"

    def test_daemon_running_false_when_no_active(self):
        rows = [{"alias": "agent-b", "status": "inactive"}]
        result = registry_to_kernel_ps_format(rows)
        assert result["daemon_running"] is False

    def test_empty_rows_returns_empty_agents(self):
        result = registry_to_kernel_ps_format([])
        assert result["agents"] == []
        assert result["daemon_running"] is False
        assert result["raw"] == ""

    def test_skips_rows_with_no_alias(self):
        rows = [{"pid": 123, "status": "active"}]
        result = registry_to_kernel_ps_format(rows)
        assert result["agents"] == []

    def test_trust_tier_passed_through(self):
        rows = [{"alias": "a", "status": "active", "trust_tier": "t2"}]
        result = registry_to_kernel_ps_format(rows)
        assert result["agents"][0]["trust_tier"] == "t2"


class TestReadRegistryForSnapshot:
    def test_uses_provided_ostk_dir(self, tmp_path):
        row = {"alias": "worker-1", "pid": 99, "status": "active",
               "registered_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:01:00Z"}
        (tmp_path / "agents.jsonl").write_text(json.dumps(row))
        result = read_registry_for_snapshot(ostk_dir=tmp_path)
        assert result["daemon_running"] is True
        assert result["agents"][0]["name"] == "worker-1"
        assert result["agents"][0]["pid"] == 99

    def test_missing_file_returns_empty(self, tmp_path):
        result = read_registry_for_snapshot(ostk_dir=tmp_path)
        assert result == {"raw": "", "daemon_running": False, "agents": []}


class TestComputeSnapshotFlag:
    """_compute_agents_snapshot_async uses registry when flag is set."""

    @pytest.mark.asyncio
    async def test_flag_off_calls_kernel_ps(self, monkeypatch):
        """Without the flag, kernel_ps is called (existing path unchanged)."""
        monkeypatch.delenv("MYOS_AGENTS_USE_REGISTRY", raising=False)
        fake_ps = AsyncMock(return_value={"raw": "", "daemon_running": False, "agents": []})
        fake_audit = AsyncMock(return_value=[])
        with patch("routers.agents.ostk.kernel_ps", fake_ps), \
             patch("routers.agents.ostk.audit_agents", fake_audit):
            import importlib
            import routers.agents as agents_mod
            importlib.reload(agents_mod)  # reset module-level state
            await agents_mod._compute_agents_snapshot_async()
        fake_ps.assert_awaited()

    @pytest.mark.asyncio
    async def test_flag_on_skips_kernel_ps(self, monkeypatch):
        """With MYOS_AGENTS_USE_REGISTRY=1, kernel_ps is NOT called."""
        monkeypatch.setenv("MYOS_AGENTS_USE_REGISTRY", "1")
        fake_ps = AsyncMock(return_value={"raw": "", "daemon_running": False, "agents": []})
        fake_audit = AsyncMock(return_value=[])
        fake_registry_result = {"raw": "", "daemon_running": False, "agents": []}
        with patch("routers.agents.ostk.kernel_ps", fake_ps), \
             patch("routers.agents.ostk.audit_agents", fake_audit), \
             patch("services.registry_reader.read_registry_for_snapshot",
                   return_value=fake_registry_result):
            import routers.agents as agents_mod
            await agents_mod._compute_agents_snapshot_async()
        fake_ps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_on_registry_agents_appear_in_snapshot(self, monkeypatch, tmp_path):
        """Agents from registry surface in the snapshot result."""
        monkeypatch.setenv("MYOS_AGENTS_USE_REGISTRY", "1")
        fake_audit = AsyncMock(return_value=[])
        registry_result = {
            "raw": "",
            "daemon_running": True,
            "agents": [
                {"name": "reg-agent-xyz", "status": "running",
                 "source": "registry", "pid": 42}
            ],
        }
        with patch("routers.agents.ostk.kernel_ps", AsyncMock(return_value={})), \
             patch("routers.agents.ostk.audit_agents", fake_audit), \
             patch("services.registry_reader.read_registry_for_snapshot",
                   return_value=registry_result):
            import routers.agents as agents_mod
            result = await agents_mod._compute_agents_snapshot_async()
        names = {a["name"] for a in result.get("agents", [])}
        assert "reg-agent-xyz" in names

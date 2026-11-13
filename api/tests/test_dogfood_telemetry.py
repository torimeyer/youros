"""Tests for 14-day dogfood fleet vs raw terminal telemetry and chat latency endpoints (->3097)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else "")
    path.write_text(text, encoding="utf-8")


def _seed_session(repo: Path, session_id: str, ts: datetime, tool_input: str = "{}") -> None:
    events_path = repo / ".ostk" / "sessions" / session_id / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            {
                "seq": 0,
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "type": "event",
                "kind": "tool_call",
                "data": {"tool": "bash", "input": tool_input, "success": True},
            }
        ],
    )


def test_compute_dogfood_telemetry_multi_repo_counts_and_ratios(tmp_path: Path):
    """Verify tracked spawns, handoffs, verified merges, untracked sessions, and ratios across repos."""
    from services.dogfood_telemetry import compute_dogfood_telemetry

    now = datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc)
    day_0 = now - timedelta(hours=2)
    day_1 = now - timedelta(days=1, hours=1)
    day_2 = now - timedelta(days=2, hours=3)
    ancient = now - timedelta(days=25)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    # repo_a: 2 valid tracked spawns (1 Claude, 1 Gemini), 2 verified merges,
    # 1 handoff on agent state, 1 stale spawn outside 14d, and 1 non-fleet audit row.
    _write_json(
        repo_a / ".ostk" / "agent_state.json",
        {
            "claude-feat-1": {
                "source": "ui",
                "model": "claude-sonnet-4-6",
                "spawned_at": day_2.isoformat(),
                "completed_at": day_1.isoformat(),
                "merged_at": day_1.isoformat(),
                "status": "completed",
            },
            "gemini-handoff-1": {
                "source": "handoff",
                "runtime": "gemini",
                "model": "gemini-3.1-pro",
                "handed_off_from": "claude-feat-1",
                "spawned_at": day_1.isoformat(),
                "completed_at": day_0.isoformat(),
                "merged_at": day_0.isoformat(),
                "status": "completed",
            },
            "claude-stale-old": {
                "source": "task-bridge",
                "model": "claude-opus-4-7",
                "spawned_at": ancient.isoformat(),
                "status": "completed",
            },
            "audit-noise": {
                "source": "audit",
                "model": "claude-sonnet-4-6",
                "spawned_at": day_0.isoformat(),
                "status": "completed",
            },
        },
    )

    # repo_a sessions:
    # - myos-api-1 and myos-api-p99999: backend self-sessions -> ignored
    # - claude-feat-1: matches tracked agent name -> NOT untracked
    # - raw-term-1 and raw-term-2: 2 untracked raw terminal sessions
    # - raw-term-old: outside 14d window -> ignored
    _seed_session(repo_a, "myos-api-1", day_0, "ostk clock")
    _seed_session(repo_a, "myos-api-p99999", day_0, "ostk clock")
    _seed_session(repo_a, "claude-feat-1", day_1, "pytest")
    _seed_session(repo_a, "raw-term-1", day_2, "git status")
    _seed_session(repo_a, "raw-term-2", day_0, "ls -la")
    _seed_session(repo_a, "raw-term-old", ancient, "pwd")

    # repo_b: 1 tracked Claude spawn with PID + worktree, 1 audit handoff,
    # 1 PID-matched tracked session, and 1 untracked raw terminal session.
    _write_json(
        repo_b / ".ostk" / "agent_state.json",
        {
            "claude-bridge-b1": {
                "source": "task-bridge",
                "model": "claude-opus-4-7",
                "pid": 4242,
                "worktree_path": str(repo_b / ".youros" / "worktrees" / "agent-b1"),
                "spawned_at": day_0.isoformat(),
                "status": "running",
            }
        },
    )
    _write_jsonl(
        repo_b / ".ostk" / "audit.jsonl",
        [
            {
                "timestamp": day_0.isoformat(),
                "verb": "handoff",
                "from_agent": "claude-bridge-b1",
                "to_agent": "gemini-critic-b1",
            }
        ],
    )
    _seed_session(repo_b, "claude-code-p4242", day_0, "pytest api/tests")
    _seed_session(repo_b, "raw-b-term-1", day_1, "vim main.py")

    # Durable handoffs.jsonl: one duplicate of gemini-handoff-1 (must deduplicate)
    # plus one distinct handoff in repo_a.
    handoffs_file = tmp_path / "handoffs.jsonl"
    _write_jsonl(
        handoffs_file,
        [
            {
                "timestamp": day_1.isoformat(),
                "from_agent": "claude-feat-1",
                "to_agent": "gemini-handoff-1",
                "repo_path": str(repo_a),
            },
            {
                "timestamp": day_0.isoformat(),
                "from_agent": "claude-feat-1",
                "to_agent": "gemini-review-2",
                "repo_path": str(repo_a),
            },
        ],
    )

    result = compute_dogfood_telemetry(
        window_days=14,
        repo_roots=[repo_a, repo_b],
        now=now,
        handoffs_log_path=handoffs_file,
    )

    assert result["window_days"] == 14
    assert result["freeze_target_days"] == 14
    assert result["tracked_spawns"] == 3
    assert result["handoffs_count"] == 3
    assert result["verified_merges"] == 2
    assert result["untracked_terminal_sessions"] == 3
    assert result["supervised_ratio"] == 0.5
    assert result["by_runtime"] == {"claude": 2, "gemini": 1}

    assert "repo_a" in result["by_repo"]
    assert "repo_b" in result["by_repo"]

    ra = result["by_repo"]["repo_a"]
    assert ra["tracked_spawns"] == 2
    assert ra["handoffs_count"] == 2
    assert ra["verified_merges"] == 2
    assert ra["untracked_terminal_sessions"] == 2
    assert ra["supervised_ratio"] == 0.5
    assert ra["by_runtime"] == {"claude": 1, "gemini": 1}

    rb = result["by_repo"]["repo_b"]
    assert rb["tracked_spawns"] == 1
    assert rb["handoffs_count"] == 1
    assert rb["verified_merges"] == 0
    assert rb["untracked_terminal_sessions"] == 1
    assert rb["supervised_ratio"] == 0.5
    assert rb["by_runtime"] == {"claude": 1, "gemini": 0}

    assert len(result["daily"]) == 14
    assert sum(d["tracked_spawns"] for d in result["daily"]) == 3
    assert sum(d["untracked_terminal_sessions"] for d in result["daily"]) == 3
    assert sum(d["verified_merges"] for d in result["daily"]) == 2
    assert sum(d["handoffs_count"] for d in result["daily"]) == 3


def test_session_registration_event_matches_tracked_subagent(tmp_path: Path):
    """CLI session whose events.jsonl posts to /api/agents/<tracked_name> is treated as supervised."""
    from services.dogfood_telemetry import compute_dogfood_telemetry

    now = datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc)
    repo = tmp_path / "repo_subagent"
    repo.mkdir()

    _write_json(
        repo / ".ostk" / "agent_state.json",
        {
            "w2-subagent-worker": {
                "source": "claude-code",
                "model": "gemini-3.1-pro",
                "spawned_at": now.isoformat(),
                "status": "running",
            }
        },
    )
    _seed_session(
        repo,
        "claude-code-p77777",
        now,
        "curl -sSk -X POST https://127.0.0.1:8000/api/agents/w2-subagent-worker/heartbeat",
    )
    _seed_session(repo, "claude-code-p88888", now, "echo raw_unsupervised_shell")

    res = compute_dogfood_telemetry(window_days=14, repo_roots=[repo], now=now)
    assert res["tracked_spawns"] == 1
    assert res["untracked_terminal_sessions"] == 1
    assert res["supervised_ratio"] == 0.5
    assert res["by_runtime"] == {"claude": 0, "gemini": 1}


def test_empty_repo_returns_zero_metrics(tmp_path: Path):
    """Empty repository without .ostk returns safe zero counts and 0.0 supervised_ratio."""
    from services.dogfood_telemetry import compute_dogfood_telemetry

    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()

    res = compute_dogfood_telemetry(window_days=7, repo_roots=[empty_repo])
    assert res["window_days"] == 7
    assert res["freeze_target_days"] == 14
    assert res["tracked_spawns"] == 0
    assert res["handoffs_count"] == 0
    assert res["verified_merges"] == 0
    assert res["untracked_terminal_sessions"] == 0
    assert res["supervised_ratio"] == 0.0
    assert res["by_runtime"] == {"claude": 0, "gemini": 0}
    assert len(res["daily"]) == 7


def test_live_agent_metadata_and_handoff_count_integration(tmp_path: Path, monkeypatch):
    """In-memory agent_metadata rows scoped via repo_path merge cleanly without double counting."""
    import routers.agents as agents_mod
    from services.dogfood_telemetry import compute_dogfood_telemetry

    now = datetime(2026, 11, 13, 16, 0, 0, tzinfo=timezone.utc)
    repo = tmp_path / "live_repo"
    repo.mkdir()

    # Persisted state has initial spawn without merged_at yet.
    _write_json(
        repo / ".ostk" / "agent_state.json",
        {
            "persisted-and-live": {
                "source": "api",
                "model": "claude-opus-4-7",
                "spawned_at": now.isoformat(),
                "status": "running",
            }
        },
    )

    fake_live = {
        "persisted-and-live": {
            "source": "api",
            "model": "claude-opus-4-7",
            "repo_path": str(repo),
            "spawned_at": now.isoformat(),
            "merged_at": now.isoformat(),
            "handoff_count": 2,
            "status": "completed",
        },
        "only-in-memory-gemini": {
            "source": "ui",
            "runtime": "gemini",
            "model": "gemini-3.1-pro",
            "repo_path": str(repo),
            "spawned_at": now.isoformat(),
            "status": "running",
        },
    }
    monkeypatch.setattr(agents_mod, "agent_metadata", fake_live)

    res = compute_dogfood_telemetry(window_days=14, repo_roots=[repo], now=now)
    assert res["tracked_spawns"] == 2
    assert res["verified_merges"] == 1
    assert res["handoffs_count"] == 2
    assert res["by_runtime"] == {"claude": 1, "gemini": 1}


@pytest.mark.asyncio
async def test_dogfood_telemetry_http_endpoint(client, tmp_path: Path, monkeypatch):
    """GET /api/dashboard/dogfood-telemetry returns 200 with computed metrics and validates days."""
    import services.dogfood_telemetry as dt_mod

    now = datetime.now(timezone.utc)
    repo = tmp_path / "http_repo"
    repo.mkdir()
    _write_json(
        repo / ".ostk" / "agent_state.json",
        {
            "ui-agent-1": {
                "source": "ui",
                "model": "claude-sonnet-4-6",
                "spawned_at": now.isoformat(),
                "merged_at": now.isoformat(),
                "status": "completed",
            },
            "gemini-agent-2": {
                "source": "spec-build",
                "model": "gemini-3.1-pro",
                "handed_off_from": "ui-agent-1",
                "spawned_at": now.isoformat(),
                "status": "running",
            },
        },
    )
    _seed_session(repo, "myos-api-1", now, "ostk clock")
    _seed_session(repo, "ui-agent-1", now, "pytest")
    _seed_session(repo, "raw-cli-1", now, "git diff")

    monkeypatch.setattr(dt_mod, "_default_repo_roots", lambda: [repo])
    monkeypatch.setattr(dt_mod, "_default_handoffs_log_path", lambda: tmp_path / "none.jsonl")

    resp = await client.get("/api/dashboard/dogfood-telemetry?days=14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 14
    assert body["freeze_target_days"] == 14
    assert body["tracked_spawns"] == 2
    assert body["handoffs_count"] == 1
    assert body["verified_merges"] == 1
    assert body["untracked_terminal_sessions"] == 1
    assert body["supervised_ratio"] == round(2 / 3, 3)
    assert body["by_runtime"] == {"claude": 1, "gemini": 1}
    assert "http_repo" in body["by_repo"]

    bad_low = await client.get("/api/dashboard/dogfood-telemetry?days=0")
    assert bad_low.status_code == 422
    bad_high = await client.get("/api/dashboard/dogfood-telemetry?days=91")
    assert bad_high.status_code == 422


@pytest.mark.asyncio
async def test_chat_latency_http_endpoint(client):
    """GET /api/dashboard/chat-latency exposes get_latency_summary() over HTTP."""
    from services.chat_latency import (
        TurnLatencySample,
        clear_latency_samples,
        record_turn_latency,
    )

    clear_latency_samples()
    try:
        record_turn_latency(
            TurnLatencySample(
                provider="claude_code",
                model="claude-sonnet-4-6",
                tab_id="tab-1",
                warm_session=False,
                spawn_ms=120.0,
                init_ms=85.0,
                first_token_ms=410.0,
                total_ms=1250.0,
            )
        )
        record_turn_latency(
            TurnLatencySample(
                provider="gemini_cli",
                model="gemini-3.1-pro",
                tab_id="tab-2",
                warm_session=True,
                spawn_ms=0.0,
                init_ms=15.0,
                first_token_ms=260.0,
                total_ms=780.0,
            )
        )

        resp = await client.get("/api/dashboard/chat-latency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sample_count"] == 2
        assert "claude_code" in data["by_provider"]
        assert "gemini_cli" in data["by_provider"]
        assert data["by_provider"]["claude_code"]["cold_count"] == 1
        assert data["by_provider"]["gemini_cli"]["warm_count"] == 1
        assert "claude-sonnet-4-6" in data["by_model"]
        assert "gemini-3.1-pro" in data["by_model"]
    finally:
        clear_latency_samples()

"""2983: tests for the coverage gaps the review agent ranked.

Backend gaps covered here (reviewer's rank numbers):

Gap 4  - PATCH /api/tasks/{task_id} with a commit_ref runs ``git
         rev-parse`` in a thread with a 5 second cap. When that lookup
         times out the endpoint must answer 504 with the plain-language
         message, never hang, never leak a stack trace, and leave the
         stored row untouched.
Gap 10 - _write_commit_ref answers 500 with a plain-language message
         when the task store file (.ostk/needles/issues.jsonl) is
         missing.
Gap 5  - the savings window on a day with BOTH chat turns and agent
         runs reports the combined numbers: available:true, the agent
         token totals, and the chat cache totals side by side. The
         payload keys asserted here (agent_run_tokens, agent_run_count,
         conversation_cache_tokens) also pin the backend half of the
         gap-7 contract.

Frontend gaps 1-3 and 8 live in
app/src/pages/{Upgrade,InviteAccept,ShareView,BreakRoom}.test.tsx.
Fixture and helper shapes mirror test_2972_commit_ref_correction.py and
test_2961_agent_savings.py so the three files stay easy to read together.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ostk import ostk
from services import token_metrics

WRONG_HASH = "e8ca1367710f6f2539836b8d762b0d246af7edd9"


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _seed_task(issues_path, task_id="→900", commit_ref=WRONG_HASH):
    row = {
        "id": task_id,
        "title": "Declutter the Settings page",
        "status": "closed",
        "priority": "P1",
        "commit_ref": commit_ref,
        "closed_at": "2026-07-19T18:26:23Z",
        "closed_reason": "completed",
    }
    issues_path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
    return row


@pytest.fixture
def issues_path():
    """The per-test task store created by the autouse _isolate_tasks_ostk."""
    return Path(ostk.cwd) / ".ostk" / "needles" / "issues.jsonl"


@pytest.fixture
def repo_with_commit(issues_path):
    """Real git repo in the per-test ostk cwd with one commit.

    Mirrors test_2972_commit_ref_correction.repo_with_commit so the
    commit-ref tests here validate against a real save point.
    Returns (issues_path, full_hash).
    """
    root = Path(ostk.cwd)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("2983 test repo")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "the real fix commit", "--no-verify")
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root), check=True, capture_output=True, text=True,
    )
    return issues_path, out.stdout.strip()


# ---------------------------------------------------------------------------
# Gap 4: the git-timeout branch of commit-ref validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commit_ref_git_timeout_returns_504_plain_language(client, issues_path):
    """When git rev-parse exceeds its 5s cap the endpoint answers 504 with
    the plain-language message instead of hanging or raising, and the
    stored row keeps its old commit_ref."""
    _seed_task(issues_path)

    with patch(
        "asyncio.to_thread",
        new=AsyncMock(
            side_effect=subprocess.TimeoutExpired(cmd="git rev-parse", timeout=5)
        ),
    ):
        resp = await client.patch(
            "/api/tasks/→900", json={"commit_ref": "abcdef1234"}
        )

    assert resp.status_code == 504
    assert resp.json()["detail"] == (
        "Checking the code save point took too long. Try again."
    )

    row = json.loads(issues_path.read_text().strip())
    assert row["commit_ref"] == WRONG_HASH  # nothing was written


@pytest.mark.asyncio
async def test_commit_ref_still_works_after_a_timeout(client, repo_with_commit):
    """A timeout is transient: the very next PATCH with a real hash must
    succeed (no lingering state from the 504)."""
    issues_path, full_hash = repo_with_commit
    _seed_task(issues_path)

    with patch(
        "asyncio.to_thread",
        new=AsyncMock(
            side_effect=subprocess.TimeoutExpired(cmd="git rev-parse", timeout=5)
        ),
    ):
        first = await client.patch(
            "/api/tasks/→900", json={"commit_ref": full_hash}
        )
    assert first.status_code == 504

    second = await client.patch(
        "/api/tasks/→900", json={"commit_ref": full_hash}
    )
    assert second.status_code == 200, second.text

    row = json.loads(issues_path.read_text().strip())
    assert row["commit_ref"] == full_hash


# ---------------------------------------------------------------------------
# Gap 10: the task store file is missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_task_store_returns_500_plain_language(client, repo_with_commit):
    """A valid hash against a repo whose task store file is gone answers
    500 with the plain-language message, not a traceback."""
    issues_path, full_hash = repo_with_commit
    issues_path.unlink()

    resp = await client.patch(
        "/api/tasks/→900", json={"commit_ref": full_hash}
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "The task store file is missing."


# ---------------------------------------------------------------------------
# Gap 5: mixed agent + chat savings window
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_savings_caches():
    """Invalidate savings/metrics caches before and after every test
    (same guard test_2961_agent_savings.py uses)."""
    from routers import costs as costs_router
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    costs_router._savings_cache.clear()
    costs_router._savings_refresh_in_flight.clear()
    costs_router.invalidate_metrics_parse_cache()
    yield
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    costs_router._savings_cache.clear()
    costs_router._savings_refresh_in_flight.clear()
    costs_router.invalidate_metrics_parse_cache()


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


OSTK_PAYLOAD = {
    "prompt_cache": {"cache_savings_usd": 0.05, "efficiency_pct": 55.0,
                     "cost_usd": 0.04, "no_cache_cost_usd": 0.09},
    "squash": {"compression_pct": 30.0, "est_saved_usd": 0.02},
}


def _ts(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_run_line(tokens: int, ts: str, name: str = "saa-test-agent") -> str:
    return json.dumps({
        "event": "agent_run",
        "agent": name,
        "model": "claude-sonnet-4",
        "tokens_used": tokens,
        "source": "agent",
        "ts": ts,
    })


def _chat_turn_line(ts: str, input_tokens: int = 500, cache_read: int = 400) -> str:
    return json.dumps({
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": input_tokens,
        "output_tokens": 100,
        "has_ostk_boot": True,
        "boot_context_bytes": 500,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
        "ts": ts,
    })


@pytest.mark.asyncio
async def test_savings_mixed_agent_and_chat_day(tmp_path, client):
    """A day with BOTH a chat turn and an agent run reports the combined
    totals in one payload: available:true, the agent run tokens, and the
    chat cache reads. Every real productive day is mixed; before this
    test only agent-only and chat-only days were pinned."""
    metrics_path = tmp_path / "metrics.jsonl"
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _agent_run_line(80_000, now_ts) + "\n"
        + _chat_turn_line(now_ts, input_tokens=500, cache_read=400) + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp = await client.get("/api/costs/savings?period=today")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True, (
        f"a mixed day must never show the empty state: {data}"
    )
    # Agent side of the mixed day
    assert data["agent_run_tokens"] == 80_000
    assert data["agent_run_count"] == 1
    # Chat side of the same window: cache reads flow through untouched,
    # never double-counted into the agent bucket (and vice versa).
    assert data["conversation_cache_tokens"] == 400
    assert data["conversation_cache_read_tokens"] == 400
    assert data["conversation_cache_pct"] == 80.0  # 400 cache reads / 500 input


@pytest.mark.asyncio
async def test_savings_mixed_day_window_excludes_old_events_of_both_kinds(
    tmp_path, client
):
    """The week window drops BOTH old agent runs and old chat turns while
    all-time keeps them; neither kind leaks into the other's bucket."""
    metrics_path = tmp_path / "metrics.jsonl"
    old_ts = _ts(datetime.now(timezone.utc) - timedelta(days=60))
    now_ts = _ts(datetime.now(timezone.utc))
    metrics_path.write_text(
        _agent_run_line(70_000, old_ts) + "\n"
        + _chat_turn_line(old_ts, input_tokens=1000, cache_read=900) + "\n"
        + _agent_run_line(50_000, now_ts) + "\n"
        + _chat_turn_line(now_ts, input_tokens=500, cache_read=400) + "\n"
    )

    from routers import costs as costs_router
    with patch("services.token_metrics.subprocess.run") as mock_run, \
         patch("services.token_metrics._METRICS_PATH", metrics_path):
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp_week = await client.get("/api/costs/savings?period=week")
        costs_router._savings_cache.clear()
        token_metrics.invalidate_savings_cache()
        mock_run.return_value = _fake_completed(json.dumps(OSTK_PAYLOAD))
        resp_all = await client.get("/api/costs/savings?period=all")

    week = resp_week.json()
    assert week["available"] is True
    assert week["agent_run_tokens"] == 50_000
    assert week["agent_run_count"] == 1
    assert week["conversation_cache_tokens"] == 400

    allp = resp_all.json()
    assert allp["available"] is True
    assert allp["agent_run_tokens"] == 120_000
    assert allp["agent_run_count"] == 2
    assert allp["conversation_cache_tokens"] == 1300

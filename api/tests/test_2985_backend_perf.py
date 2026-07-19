"""->2985: the backend stops re-reading files it already has.

Five confirmed slow spots from the read-only performance review
(review-backend-performance-bb4e05), verified at current line positions:

1. routers/agents.py _compute_agents_snapshot_async read
   deleted_agents.json synchronously on the event loop, every 500 ms
   (plus sync transcript exists/stat calls in the same hot loop).
2. routers/tasks.py _enrich_task re-read task_source.json once per task
   per GET /api/tasks request (100 tasks = 100 disk reads).
3. routers/tasks.py _enrich_task rebuilt the session inversion dict per
   task (O(N x M)) and re-read session_task_map.json for every task not
   owned by a session.
4. routers/agents.py ran _recover_stale_agents() at module import,
   blocking cold start with up to N x 9 s of git subprocesses.
5. routers/agents.py mark_agent_complete read deleted_agents.json
   synchronously on the event loop.
"""

import ast
import asyncio
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(task_id, title=None, status="open"):
    return {
        "id": task_id,
        "title": title or f"Real work item {task_id}",
        "priority": "P1",
        "status": status,
        "tags": [],
    }


@contextmanager
def _tasks_request_env(mock_tasks):
    """Patch the ostk daemon and label store the way test_tasks.py does."""
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        yield


def _stub_snapshot_deps(monkeypatch, agents_mod, audit_agents):
    """Silence every expensive stage of _compute_agents_snapshot_async except
    the ones under test, so the snapshot runs fast and touches no real state."""
    import services.registry_reader as registry_reader
    from services.ostk import ostk

    monkeypatch.setattr(agents_mod, "_prune_stale_completed_agents", lambda: None)
    monkeypatch.setattr(agents_mod, "_prune_reaped_worktree_agents", lambda: None)
    monkeypatch.setattr(
        registry_reader,
        "read_registry_for_snapshot",
        lambda: {"daemon_running": False, "agents": [], "raw": "test"},
    )
    monkeypatch.setattr(ostk, "audit_agents", AsyncMock(return_value=audit_agents))
    monkeypatch.setattr(
        agents_mod, "_infer_cc_sessions", lambda agents_map, cutoff: None
    )
    monkeypatch.setattr(agents_mod, "_run_enrich_pipeline", lambda *a, **k: [])
    monkeypatch.setattr(agents_mod, "_enrich_async_lock", asyncio.Lock())
    monkeypatch.setattr(agents_mod, "_pending_needle_closes", [])


# ---------------------------------------------------------------------------
# Finding 1: deleted_agents.json read must leave the event loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_reads_deleted_agents_off_the_event_loop(monkeypatch):
    """The 500 ms snapshot path must never read deleted_agents.json on the
    event loop thread. The neighboring registry read (agents.py ~:4754) is
    already dispatched to an executor; the deleted-names read must follow."""
    from routers import agents as agents_mod

    loop_thread = threading.current_thread()
    load_threads = []

    def fake_load_deleted():
        load_threads.append(threading.current_thread())
        return set()

    monkeypatch.setattr(agents_mod, "_load_deleted_agents", fake_load_deleted)
    _stub_snapshot_deps(monkeypatch, agents_mod, audit_agents=[])

    result = await agents_mod._compute_agents_snapshot_async(run_autocomplete=False)

    assert result["agents"] == []
    assert load_threads, "snapshot never read deleted_agents.json"
    assert all(t is not loop_thread for t in load_threads), (
        "deleted_agents.json was read on the event loop thread inside the "
        "500 ms snapshot path; it must run in an executor thread"
    )


@pytest.mark.asyncio
async def test_snapshot_transcript_stat_runs_off_the_event_loop(monkeypatch):
    """The audit-agent reconcile branch stats transcript files. Those sync
    exists()/stat() calls sit in the same 500 ms hot loop and must be
    dispatched through the _transcript_nonempty helper in an executor."""
    from routers import agents as agents_mod

    loop_thread = threading.current_thread()
    stat_threads = []

    def fake_nonempty(path):
        stat_threads.append(threading.current_thread())
        return False

    # RED before the fix: the helper does not exist yet.
    monkeypatch.setattr(agents_mod, "_transcript_nonempty", fake_nonempty)
    monkeypatch.setattr(agents_mod, "_load_deleted_agents", lambda: set())
    _stub_snapshot_deps(
        monkeypatch,
        agents_mod,
        audit_agents=[{"name": "perf-audit-agent", "status": "running"}],
    )

    await agents_mod._compute_agents_snapshot_async(run_autocomplete=False)

    assert stat_threads, "audit-agent branch never checked the transcript file"
    assert all(t is not loop_thread for t in stat_threads), (
        "transcript exists()/stat() ran on the event loop thread inside the "
        "500 ms snapshot path; it must run in an executor thread"
    )


# ---------------------------------------------------------------------------
# Finding 2: task_source.json is loaded once per request, not once per task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_loads_task_source_file_once(client, monkeypatch):
    from services.task_source_store import TaskSourceStore

    load_calls = []

    def counting_load(self):
        load_calls.append(1)
        return {"t-1": {"source": "slack", "source_ref": "slack:C1/p1"}}

    monkeypatch.setattr(TaskSourceStore, "_load", counting_load)

    mock_tasks = [_make_task(f"t-{i}") for i in range(5)]
    with _tasks_request_env(mock_tasks):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["tasks"]}
    assert by_id["t-1"]["source"] == "slack"
    assert by_id["t-1"]["source_ref"] == "slack:C1/p1"
    assert by_id["t-0"]["source"] is None
    assert by_id["t-0"]["source_ref"] is None
    assert len(load_calls) == 1, (
        f"task_source.json was loaded {len(load_calls)} times for a single "
        "GET /api/tasks request with 5 tasks; it must be loaded exactly once "
        "and passed into _enrich_task as a source_map"
    )


# ---------------------------------------------------------------------------
# Finding 3: session lookups are batched, no per-task file reads or rebuilds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_session_lookups_are_batched(client, monkeypatch, tmp_path):
    from services import session_task_map

    store = tmp_path / "session_task_map.json"
    store.write_text(json.dumps({
        "session_to_task": {"sess-A": "t-1"},
        "task_to_session": {"t-2": "sess-B"},
    }))
    monkeypatch.setenv("MYOS_SESSION_TASK_MAP_PATH", str(store))

    per_task_calls = []
    real_get = session_task_map.get_session_for_task

    def counting_get(task_id):
        per_task_calls.append(task_id)
        return real_get(task_id)

    monkeypatch.setattr(session_task_map, "get_session_for_task", counting_get)

    load_calls = []
    real_load = session_task_map._load

    def counting_load():
        load_calls.append(1)
        return real_load()

    monkeypatch.setattr(session_task_map, "_load", counting_load)

    mock_tasks = [_make_task("t-1"), _make_task("t-2"), _make_task("t-3")]
    with _tasks_request_env(mock_tasks):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["tasks"]}
    # Behavior is unchanged: owned session, child session, and no session.
    assert by_id["t-1"]["session_id"] == "sess-A"
    assert by_id["t-1"]["child_task_count"] == 0
    assert by_id["t-2"]["session_id"] == "sess-B"
    assert by_id["t-2"]["child_task_count"] == 0
    assert by_id["t-3"]["session_id"] is None
    assert by_id["t-3"]["child_task_count"] == 0
    # Cost is bounded: no per-task fallback reads, and at most three
    # whole-file loads per request (pairs, children counts, child map)
    # regardless of the number of tasks.
    assert per_task_calls == [], (
        "get_session_for_task was called per task inside the enrich loop "
        f"(tasks: {per_task_calls}); the child map must be loaded once and "
        "passed into _enrich_task"
    )
    assert len(load_calls) <= 3, (
        f"session_task_map.json was loaded {len(load_calls)} times for a "
        "single GET /api/tasks request with 3 tasks; expected at most 3 "
        "loads regardless of task count"
    )


# ---------------------------------------------------------------------------
# Finding 4: no blocking git recovery at module import
# ---------------------------------------------------------------------------


def test_recover_stale_agents_not_called_at_import_time():
    """_recover_stale_agents runs git subprocesses (up to ~9 s per worktree
    agent). It must not be invoked at module import; the lifespan handler
    schedules it off-thread via schedule_startup_recovery instead."""
    import routers.agents as agents_mod

    src = Path(agents_mod.__file__.rstrip("c")).read_text()
    tree = ast.parse(src)

    offenders = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Name) and fn.id == "_recover_stale_agents":
                    offenders.append(sub.lineno)
    assert not offenders, (
        "routers/agents.py calls _recover_stale_agents() at module import "
        f"(lines {offenders}); the git subprocess sweep must be scheduled "
        "from the startup lifespan handler instead"
    )


@pytest.mark.asyncio
async def test_schedule_startup_recovery_runs_off_the_event_loop(monkeypatch):
    from routers import agents as agents_mod

    loop_thread = threading.current_thread()
    calls = []

    def fake_recover():
        calls.append(threading.current_thread())

    monkeypatch.setattr(agents_mod, "_recover_stale_agents", fake_recover)

    # RED before the fix: schedule_startup_recovery does not exist yet.
    await agents_mod.schedule_startup_recovery()
    task = agents_mod._startup_recovery_task
    assert task is not None, "schedule_startup_recovery did not create a task"
    await asyncio.wait_for(task, timeout=5)

    assert calls, "schedule_startup_recovery never ran _recover_stale_agents"
    assert all(t is not loop_thread for t in calls), (
        "_recover_stale_agents ran on the event loop thread; it must run "
        "in a worker thread via asyncio.to_thread"
    )


def test_lifespan_schedules_startup_recovery():
    """The lifespan handler must actually call the new scheduler, otherwise
    the sweep never runs and phantom RUNNING rows survive restarts."""
    import main as main_mod

    src = Path(main_mod.__file__.rstrip("c")).read_text()
    assert "schedule_startup_recovery" in src, (
        "api/main.py lifespan never calls agents.schedule_startup_recovery(); "
        "the startup sweep would silently stop running"
    )


# ---------------------------------------------------------------------------
# Finding 5: mark_agent_complete reads deleted_agents.json off the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_agent_complete_reads_deleted_agents_off_the_event_loop(monkeypatch):
    from routers import agents as agents_mod
    from routers.agents import AgentComplete

    loop_thread = threading.current_thread()
    load_threads = []

    def fake_load_deleted():
        load_threads.append(threading.current_thread())
        return {"perf-deleted-agent"}

    monkeypatch.setattr(agents_mod, "_load_deleted_agents", fake_load_deleted)

    result = await agents_mod.mark_agent_complete(
        "perf-deleted-agent", AgentComplete(summary="perf test")
    )

    # Behavior is unchanged: a deleted agent with no live row is refused.
    assert result["status"] == "deleted"
    assert load_threads, "mark_agent_complete never checked deleted_agents.json"
    assert all(t is not loop_thread for t in load_threads), (
        "deleted_agents.json was read on the event loop thread inside "
        "mark_agent_complete; it must run via asyncio.to_thread"
    )

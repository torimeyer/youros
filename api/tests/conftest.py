import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import httpx

# Ensure api/ is on sys.path so imports like `from main import app` work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The agents router runs a one-shot retroactive sweep at import time that
# scans ~/.myos/agent_memory/ and writes summaries into ~/.myos/files/.
# That must NEVER fire during tests because it would pollute the user's
# real home directory with test run artifacts.
os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")

# services.workflows.run_workflow writes a rollup .md to ~/.myos/files/
# at terminal state so workflow runs surface on the Files tab. Block
# that write during tests so exercising the lifecycle does not pollute
# the user's real home. Individual tests that want to inspect the
# artifact path override the env var or call _write_workflow_artifact
# directly with a patched MYOS_FILES_DIR.
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")

# Roadmap /complete runs now post a torichat system message via
# services.chat_notifications. That helper writes to the real
# ~/.myos/chat_history.json by default. Opt out during tests so the
# user's actual chat panel never inherits a stray pytest entry.
# Tests that specifically want to exercise the helper unset this in
# their own scope (see test_roadmap_chat_flow.py).
os.environ.setdefault("MYOS_SKIP_CHAT_NOTIFICATIONS", "1")

# Rate limiter is opt-out during tests. Tests that exercise the limiter
# explicitly clear this and call services.rate_limit._reset_all().
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")

# Disable the periodic worktree reaper so its 5-second boot sweep does not
# run subprocess.run() against the real repo during tests and slow down
# (or fail) event-loop latency assertions like test_websocket_round_trip.
os.environ.setdefault("MYOS_REAPER_ENABLED", "0")

from main import app


@pytest.fixture(autouse=True)
def _redirect_async_state_save(tmp_path):
    """Redirect _save_agent_state_async writes to a temp path so tests never
    pollute the real .ostk/agent_state.json.

    The sync _save_agent_state has long been patched in individual tests.
    Since →1086 the hot async handlers call _save_agent_state_async, which
    writes via _write_state_content. Redirecting AGENT_STATE_PATH here keeps
    those writes harmless. Tests that need a specific path patch it themselves
    in their own ``with patch("routers.agents.AGENT_STATE_PATH", ...)`` block,
    which takes precedence over this fixture's patch for that scope.
    """
    tmp_state = tmp_path / "test_agent_state.json"
    tmp_state.write_text("{}")
    with patch("routers.agents.AGENT_STATE_PATH", tmp_state):
        yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def live_task_tracker(client):
    """Records task IDs created during a test and deletes them on teardown.

    Use this in any test that calls POST /api/tasks against a real or
    in-process backend without mocking ostk. Without the fixture, a
    failing assertion leaves the created task behind where it surfaces
    on the Tasks page until somebody notices and hand-deletes it. With
    the fixture, teardown runs even when the test raises, so the repo
    never carries a smoke-run ghost forward.

    Contract:
      tracker.track(task_id)       — record a single ID for cleanup
      tracker.track_all([id, id])  — bulk record
      tracker.create(title, **kw)  — POST /api/tasks and auto-track

    The fixture always attempts DELETE /api/tasks/{id} for every tracked
    ID, ignoring 404s (already gone) and logging any other failure so
    the test output is never silently swallowing leaks.
    """
    created: list[str] = []

    class _Tracker:
        def track(self, task_id: str) -> None:
            if task_id and task_id not in created:
                created.append(task_id)

        def track_all(self, ids) -> None:
            for tid in ids or []:
                self.track(tid)

        async def create(self, title: str, **kwargs) -> str:
            payload = {"title": title, **kwargs}
            resp = await client.post(
                "/api/tasks",
                params={"include_test_data": "true"},
                json=payload,
            )
            resp.raise_for_status()
            body = resp.json()
            tid = body.get("task_id") or (body.get("task") or {}).get("id") or body.get("id")
            if not tid:
                raise RuntimeError(f"POST /api/tasks did not return a task id: {body}")
            self.track(tid)
            return tid

        @property
        def tracked(self) -> list[str]:
            return list(created)

    tracker = _Tracker()
    try:
        yield tracker
    finally:
        # Teardown runs even on test failure. Delete every tracked ID.
        # A 404 means the task was already removed (by the test itself
        # or by a downstream cleanup) and is not a leak. Other errors
        # are logged but not raised, so one stuck ID does not mask the
        # rest of the suite's results.
        for tid in list(created):
            try:
                await client.delete(f"/api/tasks/{tid}")
            except Exception as exc:  # noqa: BLE001
                import sys
                print(
                    f"[live_task_tracker] failed to delete {tid}: {exc}",
                    file=sys.stderr,
                )


@pytest.fixture(autouse=True)
def _reset_worktree_mutex():
    """Reset the module-level asyncio.Lock in spawn_isolation between tests.

    asyncio.Lock binds to the first event loop that acquires it. pytest-asyncio
    creates a fresh loop per test, so any test that calls create_worktree or
    remove_worktree (directly or via the HTTP endpoint) will leave the lock
    bound to a dead event loop. The next test then fails with:
      '<lock> is bound to a different event loop'
    Replacing the lock object before and after each test keeps it unbound.
    """
    import asyncio
    _siso = None
    try:
        import services.spawn_isolation as _siso
        _siso._worktree_git_mutex = asyncio.Lock()
    except Exception:
        pass
    yield
    try:
        if _siso is not None:
            _siso._worktree_git_mutex = asyncio.Lock()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_spawn_throttle():
    """Reset services.spawn_throttle between tests (→2130).

    services.spawn_throttle._throttle is a module-level SpawnBurstThrottle
    holding two pieces of state that cannot survive a pytest-asyncio test
    boundary:

      1. asyncio.Lock — binds to the first event loop that acquires it.
         pytest-asyncio creates a fresh loop per test, so the lock is left
         pointing at a dead loop for every subsequent test. Awaiting it
         from a new loop blocks the loop forever in kqueue.control(None)
         instead of raising a useful error.
      2. Deque[float] of spawn timestamps — the 3 spawns per 30s burst
         limit. With persistence across tests, ~4 routes calling
         /api/agents/spawn within a 30s wall-clock window leave the
         bucket full and the next test queues for up to
         MYOS_SPAWN_MAX_WAIT_S (default 90s).

    Together these wedged the full backend suite for 10+ minutes whenever
    test ordering put 4+ spawn-route tests close together. Replacing the
    singleton before and after each test keeps the lock unbound and the
    bucket empty.
    """
    from services import spawn_throttle
    spawn_throttle.reset_for_testing()
    yield
    spawn_throttle.reset_for_testing()


@pytest.fixture(autouse=True)
def _reset_connections_cache():
    """Drop every cached connection-status payload before and after each test.

    The four connection-status endpoints (Gmail, Calendar, Drive, Slack)
    serve their payloads from a short TTL in-memory cache. Tests that
    patch ``TOKEN_PATH`` or the service layer expect a fresh computation
    every request. Without this reset, the second /api/gmail/auth/status
    call in a run would return the first test's cached dict.
    """
    from services import connections_cache
    connections_cache.invalidate_all()
    yield
    connections_cache.invalidate_all()


@pytest.fixture(autouse=True)
def _reset_template_persona_cache():
    """Drop the persona-templates in-memory cache before and after each test.

    The cache is class-level state on AgentTemplatesStore. Tests that
    monkeypatch AGENT_TEMPLATES_PATH would otherwise see the previous
    test's cached payload because the signature uses the path string.
    """
    try:
        from services.agent_templates_store import AgentTemplatesStore
        AgentTemplatesStore._invalidate_persona_cache()
        yield
        AgentTemplatesStore._invalidate_persona_cache()
    except Exception:
        # If the store cannot be imported in a stripped-down test env,
        # do not block the rest of the suite.
        yield


@pytest.fixture(autouse=True)
def clear_costs_caches():
    """Reset the costs aggregation cache and savings TTL cache before each test.

    Both caches are module-level singletons that persist across tests.
    Without this reset, a test that warms the cache can cause a subsequent
    test (which patches subprocess or the audit path) to see stale data
    instead of the fresh result it expects.
    """
    import tempfile
    from pathlib import Path
    from routers import costs as costs_router
    from services.token_metrics import invalidate_savings_cache
    costs_router._agg_cache.clear()
    costs_router._savings_cache.clear()
    # Redirect the savings disk snapshot to a per-test tmp file so cold-path
    # tests that patch _compute_savings_for_period still see a cache miss,
    # and so we never clobber the real user snapshot under ~/.myos/.
    tmp_snapshot = Path(tempfile.mkdtemp()) / "savings_snapshot.json"
    original_path_fn = costs_router._savings_snapshot_path
    costs_router._savings_snapshot_path = lambda: tmp_snapshot
    invalidate_savings_cache()
    try:
        yield
    finally:
        costs_router._savings_snapshot_path = original_path_fn
        costs_router._agg_cache.clear()
        costs_router._savings_cache.clear()
        invalidate_savings_cache()
        # Clean up per-test tmpdir. shutil.rmtree handles the case where a
        # background _refresh_savings_async thread raced teardown and wrote
        # additional files into the directory. The daemon thread + join(3)
        # ensures a stuck OS-level stat/rmdir syscall (seen in pytest-timeout
        # tracebacks as pathlib.exists→os.stat blocking) cannot stall the
        # entire test suite — the main thread moves on after 3 seconds and
        # the daemon thread is killed at process exit.
        def _cleanup_tmpdir() -> None:
            shutil.rmtree(str(tmp_snapshot.parent), ignore_errors=True)

        t = threading.Thread(target=_cleanup_tmpdir, daemon=True)
        t.start()
        t.join(timeout=3.0)


@pytest.fixture(autouse=True)
def _reset_gemini_client_cache():
    """Clear the module-level Gemini client cache before and after each test.

    _GEMINI_CLIENT_CACHE stores (api_key, model_name) -> (model_name, model)
    entries that survive between tests. When an earlier test mocks out
    google.generativeai and calls stream_gemini, it populates the cache
    with a mock model instance. A later test with the same api_key and
    model_name skips _genai.GenerativeModel() entirely (cache hit), so
    its fresh mock is never used and assertions on mock_model.start_chat
    fail with "Called 0 times".
    """
    from services.chat_providers import _clear_gemini_client_cache
    _clear_gemini_client_cache()
    yield
    _clear_gemini_client_cache()


@pytest.fixture(autouse=True)
def _reset_anthropic_client_cache():
    """Clear the module-level Anthropic client cache before and after each test.

    _ANTHROPIC_CLIENT_CACHE stores api_key -> AsyncAnthropic client. When
    an earlier test patches ``anthropic.AsyncAnthropic`` with a fake
    factory and calls stream_anthropic or agent_anthropic, the fake
    client gets stored in the cache. A later test with the same api_key
    ("test-key" is common) picks up the stale fake instead of invoking
    its own patched factory, so assertions on the new mock fail.
    """
    from services.chat_providers import _clear_anthropic_client_cache
    _clear_anthropic_client_cache()
    yield
    _clear_anthropic_client_cache()


@pytest.fixture(autouse=True)
def _reset_activity_context_cache():
    """Clear the module-level recent-activity cache before and after each test.

    _ACTIVITY_CONTEXT_CACHE stores the formatted recent-activity block
    for ``_ACTIVITY_CONTEXT_TTL_S`` seconds to save an audit.jsonl read
    on every chat turn. Tests that patch ``read_audit_entries`` to
    return different data must clear this cache or they will see stale
    output from a previous test's run.
    """
    from services.chat_providers import _clear_activity_context_cache
    _clear_activity_context_cache()
    yield
    _clear_activity_context_cache()


@pytest.fixture(autouse=True)
def _isolate_agent_state(tmp_path, monkeypatch):
    """Isolate the module-level ``agent_metadata`` dict and its on-disk
    persistence file from every test run.

    ``routers.agents`` keeps a process-wide ``agent_metadata`` dict that is
    rehydrated from ``.ostk/agent_state.json`` at import time. Three
    ``test_correct_agent_*`` tests call ``_save_agent_state()`` directly
    (unmocked), which writes whatever the dict currently holds back to the
    real file. That means:

      1. A previous pytest session, or a live myOS process, can leave
         rows like ``triage-wip-and-restore-demo`` or a stale
         ``cc-new-agent`` on disk. Module import pulls them into memory.
      2. Any test that asserts a specific agent's metadata shape
         (notably ``test_register_endpoint_stores_metadata``) sees the
         polluted row and fails intermittently depending on which tests
         ran before it in the same process.
      3. The correct-agent tests then checkpoint the already-polluted
         dict back to disk, persisting the leak across sessions.

    This fixture snapshots ``agent_metadata`` and ``active_agents`` at the
    start of each test, redirects the persistence path to
    ``tmp_path/agent_state.json`` so no real writes happen, clears the
    dicts, and restores the snapshots afterwards. Tests that add their own
    rows still see them via the module dicts. Tests that call
    ``_save_agent_state`` write to the tmp path and never touch the shared
    file.

    Also redirects OSTK_DIR so that _emit_audit_event (which constructs
    its audit.jsonl path from OSTK_DIR at call time) writes to a tmp
    directory rather than the production .ostk/audit.jsonl. Without this,
    every POST /register call during tests appends a real agent.registered
    event to the live audit log, causing test-named agents to appear in
    the production GET /api/agents response.

    Also disables the _RESPONSE_STALE_SECONDS filter (set to 10 years) so
    tests with hardcoded historical timestamps are not silently dropped
    from GET /api/agents responses.
    """
    from routers import agents as agents_mod

    tmp_ostk = tmp_path / "ostk"
    tmp_ostk.mkdir(parents=True, exist_ok=True)
    tmp_state = tmp_path / "agent_state.json"
    snapshot = dict(agents_mod.agent_metadata)
    nudge_snapshot = {k: list(v) for k, v in agents_mod.nudge_history.items()}
    active_snapshot = dict(agents_mod.active_agents)

    monkeypatch.setattr(agents_mod, "OSTK_DIR", tmp_ostk)
    monkeypatch.setattr(agents_mod, "AGENT_STATE_PATH", tmp_state)
    monkeypatch.setattr(agents_mod, "DELETED_AGENTS_PATH", tmp_ostk / "deleted_agents.json")
    monkeypatch.setattr(agents_mod, "DURATION_STATS_PATH", tmp_ostk / "agent_durations.json")
    monkeypatch.setattr(agents_mod, "_RESPONSE_STALE_SECONDS", 10 * 365 * 24 * 3600)
    agents_mod.agent_metadata.clear()
    agents_mod.nudge_history.clear()
    agents_mod.active_agents.clear()

    yield

    agents_mod.agent_metadata.clear()
    agents_mod.agent_metadata.update(snapshot)
    agents_mod.nudge_history.clear()
    agents_mod.nudge_history.update(nudge_snapshot)
    agents_mod.active_agents.clear()
    agents_mod.active_agents.update(active_snapshot)


@pytest.fixture(autouse=True)
def _guard_audit_writes(tmp_path):
    """Redirect all write_audit_entry calls to a temp file during tests.

    Without this, tests that exercise chat providers (stream_gemini,
    stream_anthropic) write real entries to .ostk/audit.jsonl, polluting
    the Cost Tracking page with phantom models like "Gemini Custom Test".

    Patches both the definition site (services.ostk) and every module
    that imports the function with ``from services.ostk import
    write_audit_entry``, so the already-bound local name is also
    redirected.
    """
    tmp_audit = tmp_path / "audit.jsonl"

    import services.ostk as ostk_mod
    real_fn = ostk_mod.write_audit_entry

    def _safe_write(entry, audit_path=None):
        real_fn(entry, audit_path=tmp_audit)

    with patch("services.ostk.write_audit_entry", side_effect=_safe_write), \
         patch("services.chat_providers.write_audit_entry", side_effect=_safe_write):
        yield


@pytest.fixture(autouse=True)
def _reset_ostk_singleton():
    """Reset the services.ostk.ostk singleton state between tests.

    The singleton carries mutable state that leaks across tests:

    1. ``_socket_available`` (None/True/False): once a test drives it to False
       (socket unavailable), all subsequent tests skip the socket path even when
       they patch ostk_socket.

    2. ``_audit_cache`` and ``_audit_tail`` (module-level dicts): keyed by
       audit.jsonl path.  A test that reads the real audit.jsonl warms the
       cache; the next test sees stale data even if it patches the audit path.

    3. Instance-attribute bleed from ``monkeypatch.setattr(ostk, "method",
       fake)``: monkeypatch restores by calling ``setattr(ostk, "method",
       original_bound_method)``, which puts the original bound method in the
       INSTANCE DICT.  Any subsequent ``patch("services.ostk.OstkService.method",
       ...)`` then misses because Python's MRO finds the instance-level attr
       first, bypassing the class-level patch.  Stripping unexpected instance
       attrs prevents this ordering sensitivity.
    """
    import services.ostk as ostk_mod

    _EXPECTED_INSTANCE_ATTRS = frozenset({"cwd", "_socket_available"})

    def _clean():
        for k in list(vars(ostk_mod.ostk)):
            if k not in _EXPECTED_INSTANCE_ATTRS:
                try:
                    delattr(ostk_mod.ostk, k)
                except AttributeError:
                    pass
        ostk_mod.ostk._socket_available = None
        ostk_mod._audit_cache.clear()
        ostk_mod._audit_tail.clear()

    _clean()
    yield
    _clean()


@pytest.fixture(autouse=True)
def _pin_myos_files_dir_to_tmp(tmp_path, monkeypatch):
    """Redirect ``MYOS_FILES_DIR`` to a tmp path for every test.

    The real dir is ``~/.myos/files/`` and is where user-facing artifacts
    live (roadmap.md, fleet outputs, etc.). Any test that exercises a
    spawn path which calls ``_save_agent_output_to_files`` would
    otherwise overwrite the user's real roadmap.md with the test's fake
    content.

    Individual tests that specifically want to inspect a write can still
    re-patch the attribute themselves (patterns like
    ``with patch.object(agents_module, "MYOS_FILES_DIR", ...)`` in
    test_agents.py already do this locally). The autouse default just
    guarantees the user's real home is never the write target.
    """
    try:
        from routers import agents as _agents_mod
    except Exception:
        yield
        return
    fake_files = tmp_path / "myos-files"
    fake_files.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_agents_mod, "MYOS_FILES_DIR", fake_files)
    yield


@pytest.fixture(autouse=True)
def _isolate_notifications_store(tmp_path, monkeypatch):
    """Redirect NOTIFICATIONS_FILE to a tmp path for every test.

    Without this, any code path that calls notifications_service.add()
    (e.g. _save_agent_output_to_files for roadmap completions) writes a
    real entry to ~/.myos/notifications.json. That causes stale
    'Roadmap ready' toasts to appear in the app after every test run,
    even though the user never created a roadmap.
    """
    try:
        import services.notifications as _notif_mod
    except Exception:
        yield
        return
    fake_notifications = tmp_path / "notifications.json"
    monkeypatch.setattr(_notif_mod, "NOTIFICATIONS_FILE", fake_notifications)
    monkeypatch.setattr(_notif_mod, "MYOS_DIR", tmp_path)
    yield


@pytest.fixture(autouse=True)
def _isolate_external_data_sources():
    """Prevent tests from reading real Claude Code transcripts or live agent state.

    The costs router calls _claude_code_usage_events() which reads
    ~/.claude/projects transcript files, injecting real usage data into
    tests that only intend to test specific audit entries.

    The sessions router calls _claude_code_transcript_sessions() and
    _agent_sessions() which read ~/.claude/projects and live agent
    metadata, injecting real session data into tests that patch
    SESSIONS_DIR to a temp dir.

    Patching these to return [] isolates each test to only the data it
    explicitly sets up.
    """
    with patch("routers.costs._claude_code_usage_events", return_value=[]), \
         patch("routers.sessions._claude_code_transcript_sessions", return_value=[]), \
         patch("routers.sessions._agent_sessions", return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _reset_api_key_cache():
    """Clear the module-level API key cache before and after each test.

    _API_KEY_CACHE stores resolved Anthropic/Gemini API keys for 60s.
    Without this reset, a test that resolves a real key from os.environ
    pollutes later tests that patch settings_store and ostk to return no
    key -- those patches are bypassed by the warm cache, causing the
    patched tests to make real API calls instead of hitting the error path.
    """
    from services.chat_providers import _clear_api_key_cache
    _clear_api_key_cache()
    yield
    _clear_api_key_cache()


# ---------------------------------------------------------------------------
# Pre-flight guard: block registry tests when live claude-code agents run
# ---------------------------------------------------------------------------


def _check_no_live_agents(curl_stdout: str) -> None:
    """Parse curl output from GET /api/agents and raise UsageError if any
    claude-code agent with status 'running' is present.

    Accepts malformed or non-JSON input silently so the fixture degrades
    gracefully when the backend is not reachable.
    """
    try:
        agents = json.loads(curl_stdout)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(agents, list):
        return
    live = [
        a["name"]
        for a in agents
        if isinstance(a, dict)
        and a.get("source") == "claude-code"
        and a.get("status") == "running"
    ]
    if live:
        names = ", ".join(live)
        raise pytest.UsageError(
            f"Cannot run registry tests while live claude-code agents are running: "
            f"{names}. Stop them first via "
            f"curl --connect-timeout 3 -m 5 -sSk -X POST "
            f"https://127.0.0.1:8000/api/agents/<name>/cancel"
        )


@pytest.fixture(autouse=True)
def _isolate_tasks_ostk(tmp_path, monkeypatch):
    """Redirect routers.tasks.ostk to a tmp OstkService so no test writes
    to the real .ostk/needles/issues.jsonl.

    Tests that already patch routers.tasks.ostk with their own ``with patch(...)``
    are unaffected — their inner patch wins for that scope. This fixture is a
    safety net for any test that hits the tasks router via the ASGI client
    without its own ostk mock.

    Root cause of →1323: the real ostk singleton's cwd pointed at the project
    root, so any POST /api/tasks call (including those from e2e_smoke.sh phase 4)
    wrote real needles into .ostk/needles/issues.jsonl.
    """
    from services.ostk import OstkService
    import routers.tasks as tasks_mod

    # Use a dedicated subdirectory so we don't collide with tests that also
    # create .ostk/needles/ inside tmp_path for their own fixtures.
    tmp_root = tmp_path / "_ostk_isolation"
    tmp_needles = tmp_root / ".ostk" / "needles"
    tmp_needles.mkdir(parents=True, exist_ok=True)
    (tmp_needles / "issues.jsonl").write_text("")

    tmp_svc = OstkService(cwd=str(tmp_root))
    monkeypatch.setattr(tasks_mod, "ostk", tmp_svc)
    yield


@pytest.fixture(autouse=True)
def _isolate_threads_store(tmp_path, monkeypatch):
    """Redirect threads_store to a per-test tmp file so no test writes to
    the real ~/.myos/threads.json.

    Patches both routers.threads and routers.tasks, which each hold a
    module-level reference imported from services.threads_store.

    Root cause of →1323: the ThreadsStore singleton was initialised at import
    time with THREADS_PATH = ~/.myos/threads.json. Any POST /api/threads call
    from the test suite wrote directly to the live file, surfacing ghost groups
    in the sidebar.
    """
    from services.threads_store import ThreadsStore
    import routers.tasks as tasks_mod

    tmp_store = ThreadsStore(path=tmp_path / "threads.json")
    # routers.threads no longer imports threads_store (→1330: routes return 410)
    monkeypatch.setattr(tasks_mod, "threads_store", tmp_store)
    yield


def _guard_jsonl_ids(content: bytes) -> set[str]:
    """Return the set of 'id' values parsed from a JSONL byte string."""
    import json as _json
    ids: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
            needle_id = obj.get("id")
            if needle_id:
                ids.add(str(needle_id))
        except Exception:
            pass
    return ids


def _issues_only_external_activity(before: bytes | None, after: bytes | None) -> bool:
    """True when issues.jsonl changed but no new needle IDs were introduced.

    Concurrent ostk agents closing needles rewrite issues.jsonl: existing
    entries change status or are removed, but no new IDs appear.  When
    after_ids ⊆ before_ids the change is entirely external — no test-
    originated needle leaked into the real store (→1723).

    Returns False when before == after (no change) or when a new ID appears
    (potential test leak, keep the guard loud).
    """
    if before is None or after is None or before == after:
        return False
    return _guard_jsonl_ids(after) <= _guard_jsonl_ids(before)


@pytest.fixture(autouse=True)
def _guard_real_store_writes():
    """Fail any test that silently writes to the real ostk or myOS data stores.

    Snapshots the content of issues.jsonl and threads.json before the test,
    then asserts both are unchanged after. Writes that sneak past the isolation
    fixtures (e.g. via a raw subprocess call to the real ostk binary) are caught
    here instead of persisting silently into the live store.

    Uses content comparison (bytes), not (mtime_ns, size). Concurrent ostk
    commands that atomically rewrite the file with identical content only bump
    mtime — a content-based snapshot ignores those and avoids the false-positive
    that made test_complete_agent_hook_queues_on_transport_failure flaky (→1460).

    For issues.jsonl, changes where no new needle IDs appeared are tolerated —
    they indicate concurrent ostk agents closing/modifying existing needles,
    not the test leaking new data into the real store (→1723).

    Defined last so its teardown assertion runs while the isolation fixtures are
    still patched — it can see the real on-disk files, not the tmp copies.
    """
    from pathlib import Path
    from config import PROJECT_ROOT

    # Sentinel: file exists but the read blocked (e.g. ostk kernel holds an
    # exclusive lock on issues.jsonl while rewriting it).  When either snap
    # times out we skip the comparison rather than hanging for 30 s per test.
    # Root cause: in worktree contexts PROJECT_ROOT/.ostk/needles/ resolves
    # through a symlink to the live repo store which the kernel locks on write.
    _TIMEOUT = object()

    def _snap(path: Path):
        if not path.exists():
            return None
        result: list = [_TIMEOUT]

        def _read() -> None:
            try:
                result[0] = path.read_bytes()
            except OSError:
                result[0] = None

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=2.0)
        return result[0]

    issues_path = PROJECT_ROOT / ".ostk" / "needles" / "issues.jsonl"
    threads_path = Path.home() / ".myos" / "threads.json"

    snap_issues = _snap(issues_path)
    snap_threads = _snap(threads_path)

    yield

    after_issues = _snap(issues_path)
    if (
        after_issues is not _TIMEOUT
        and snap_issues is not _TIMEOUT
        and after_issues != snap_issues
        and not _issues_only_external_activity(snap_issues, after_issues)
    ):
        raise AssertionError(
            f"Real issues.jsonl was modified during test — "
            f"a code path bypassed _isolate_tasks_ostk. "
            f"Content before: {len(snap_issues or b'')} bytes, "
            f"after: {len(after_issues or b'')} bytes"
        )

    after_threads = _snap(threads_path)
    if after_threads is not _TIMEOUT and snap_threads is not _TIMEOUT:
        assert after_threads == snap_threads, (
            f"Real threads.json was modified during test — "
            f"a code path bypassed _isolate_threads_store. "
            f"Content before: {len(snap_threads or b'')} bytes, "
            f"after: {len(after_threads or b'')} bytes"
        )


@pytest.fixture(autouse=True)
def _preflight_no_live_agents(request):
    """Block test execution if live claude-code agents are running.

    Only activates for tests marked with @pytest.mark.touches_agent_registry.
    When the backend is unreachable the check is skipped rather than
    blocking the whole test session.
    """
    if not request.node.get_closest_marker("touches_agent_registry"):
        return
    result = subprocess.run(
        [
            "curl",
            "--connect-timeout", "1",
            "-m", "2",
            "-sSk",
            "https://127.0.0.1:8000/api/agents",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    _check_no_live_agents(result.stdout)


# ---------------------------------------------------------------------------
# Assertion-free test guard (→2067)
# A test with no assert/raises/warns always passes even when the code under
# test is broken.  The hook below fails any such test before it runs.
# ---------------------------------------------------------------------------
import ast as _ast
import inspect as _inspect
import os as _os
import textwrap as _textwrap
import warnings as _warnings


def _has_assertion(func) -> bool:
    """Return True if *func* source contains at least one meaningful assertion."""
    try:
        src = _textwrap.dedent(_inspect.getsource(func))
        tree = _ast.parse(src)
    except Exception:
        return True  # can't inspect — benefit of the doubt
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assert):
            return True
        if isinstance(node, _ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, _ast.Call):
                    fn = call.func
                    if isinstance(fn, _ast.Attribute) and fn.attr in ("raises", "warns"):
                        return True
        if isinstance(node, _ast.Call):
            fn = node.func
            if isinstance(fn, _ast.Attribute):
                if fn.attr in ("raises", "warns", "fail"):
                    return True
                # unittest.mock assertions: assert_called_with, assert_not_called,
                # assert_called_once, assert_any_call, assert_has_calls, etc.
                # These are real assertions even though they don't use the `assert` keyword.
                if fn.attr.startswith("assert_"):
                    return True
            if isinstance(fn, _ast.Name) and fn.id == "fail":
                return True
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Flag assertion-free test functions (→2067).

    Warn by default so the signal is visible without breaking the suite.
    Set MYOS_ENFORCE_ASSERTION_GUARD=1 to hard-fail — flip that on once the
    existing assertion-free tests are triaged (given real assertions or
    marked @pytest.mark.no_assert).
    """
    func = getattr(item, "function", None)
    if func is not None and not _has_assertion(func):
        skip_markers = {"skip", "xfail", "no_assert"}
        if not any(item.get_closest_marker(m) for m in skip_markers):
            msg = (
                f"Assertion-free test: '{item.nodeid}' contains no assert, "
                "pytest.raises, or pytest.warns — it will always pass even "
                "when the code is broken.  Add at least one assertion, or "
                "mark with @pytest.mark.no_assert if the test legitimately "
                "relies on a fixture that asserts internally."
            )
            if _os.environ.get("MYOS_ENFORCE_ASSERTION_GUARD") == "1":
                pytest.fail(msg, pytrace=False)
            else:
                _warnings.warn(msg, stacklevel=2)
    yield

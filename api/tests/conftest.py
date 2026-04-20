import os
import sys
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

from main import app


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
        try:
            if tmp_snapshot.exists():
                tmp_snapshot.unlink()
            tmp_snapshot.parent.rmdir()
        except OSError:
            pass


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

    This fixture snapshots ``agent_metadata`` at the start of each test,
    redirects the persistence path to ``tmp_path/agent_state.json`` so no
    real writes happen, clears the dict, and restores the snapshot
    afterwards. Tests that add their own rows still see them via the
    module dict. Tests that call ``_save_agent_state`` write to the tmp
    path and never touch the shared file. Deliberately a separate
    fixture so it composes cleanly with ``live_task_tracker`` (added in
    a parallel branch) rather than nesting inside it.
    """
    from routers import agents as agents_mod

    tmp_state = tmp_path / "agent_state.json"
    snapshot = dict(agents_mod.agent_metadata)
    nudge_snapshot = {k: list(v) for k, v in agents_mod.nudge_history.items()}

    monkeypatch.setattr(agents_mod, "AGENT_STATE_PATH", tmp_state)
    agents_mod.agent_metadata.clear()
    agents_mod.nudge_history.clear()

    yield

    agents_mod.agent_metadata.clear()
    agents_mod.agent_metadata.update(snapshot)
    agents_mod.nudge_history.clear()
    agents_mod.nudge_history.update(nudge_snapshot)


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
def _pin_prewarm_roadmap_to_tmp(tmp_path, monkeypatch):
    """Point the Roadmap prewarm file at a missing tmp path by default.

    The real asset lives at ``~/.myos/prewarm/roadmap.md``. Existing
    tests that exercise the demo Roadmap spawn path assert the real
    subprocess is invoked; they would break if a developer happens to
    have seeded the prewarm file locally. Individual tests that want
    to exercise the replay path override the attribute themselves
    (see ``test_roadmap_prewarm_replay_when_file_exists_and_demo_mode``).
    """
    from routers import agents as _agents_mod

    missing = tmp_path / "prewarm" / "no-roadmap.md"
    monkeypatch.setattr(_agents_mod, "PREWARM_ROADMAP_PATH", missing)
    yield


@pytest.fixture(autouse=True)
def _pin_myos_files_dir_to_tmp(tmp_path, monkeypatch):
    """Redirect ``MYOS_FILES_DIR`` to a tmp path for every test.

    The real dir is ``~/.myos/files/`` and is where user-facing artifacts
    live (roadmap.md, fleet outputs, etc.). Any test that exercises a
    spawn path which calls ``_save_agent_output_to_files`` (for example
    ``test_roadmap_prewarm_replay_when_file_exists_and_demo_mode``) would
    otherwise overwrite the user's real roadmap.md with the test's fake
    prewarm content. That is exactly what clobbered Tori's 4229-byte
    roadmap.md with a 160-byte "ship the thing" placeholder the morning
    of the demo.

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

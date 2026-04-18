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
    from routers.costs import _agg_cache
    from services.token_metrics import invalidate_savings_cache
    _agg_cache.clear()
    invalidate_savings_cache()
    yield
    _agg_cache.clear()
    invalidate_savings_cache()


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

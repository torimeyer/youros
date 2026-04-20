import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import httpx

# Ensure api/ is on sys.path so imports like `from main import app` work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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

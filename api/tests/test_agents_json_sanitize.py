"""Regression tests for /api/agents JSON corruption (->1137).

Control characters in agent metadata fields (current_step, task, etc.)
produced literal bytes in the HTTP response that failed json.loads(strict=True).
These tests verify:
1. sanitize_for_json strips characters that would break strict JSON parsing.
2. GET /api/agents always returns a response that parses with strict=True,
   even when agent metadata contains raw control bytes in text fields.
3. The sanitization preserves tab, newline, and carriage return (valid
   JSON whitespace) while escaping everything else below U+0020.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import routers.agents as agents_mod
from routers.agents import sanitize_for_json

# Helper: build a single-char string from a code point without embedding
# literal control chars in the source file.
_NUL = chr(0)   # U+0000
_SOH = chr(1)   # U+0001
_STX = chr(2)   # U+0002
_BEL = chr(7)   # U+0007
_BS  = chr(8)   # U+0008
_VT  = chr(11)  # U+000B (vertical tab)


# ---------------------------------------------------------------------------
# Unit tests for sanitize_for_json
# ---------------------------------------------------------------------------


def test_sanitize_nul_byte():
    result = sanitize_for_json("before" + _NUL + "after")
    assert _NUL not in result
    # The NUL should be replaced by its \uXXXX form (6 printable chars)
    assert "\\u0000" in result


def test_sanitize_soh_byte():
    result = sanitize_for_json("step" + _SOH + "done")
    assert _SOH not in result
    assert "\\u0001" in result


def test_sanitize_preserves_tab():
    assert sanitize_for_json("col1\tcol2") == "col1\tcol2"


def test_sanitize_preserves_newline():
    assert sanitize_for_json("line1\nline2") == "line1\nline2"


def test_sanitize_preserves_carriage_return():
    assert sanitize_for_json("line\r\n") == "line\r\n"


def test_sanitize_preserves_normal_text():
    text = "Agent finished. Cost: $0.03. All 12 tests passed."
    assert sanitize_for_json(text) == text


def test_sanitize_all_low_control_chars():
    """Every char 0x00-0x1F is either kept (tab/lf/cr) or escaped."""
    for i in range(0x20):
        c = chr(i)
        result = sanitize_for_json(c)
        if c in "\t\n\r":
            assert result == c, f"Expected 0x{i:02x} to be kept, got {repr(result)}"
        else:
            assert c not in result, (
                f"Control char 0x{i:02x} survived sanitization: {repr(result)}"
            )


def test_sanitize_empty_string():
    assert sanitize_for_json("") == ""


def test_sanitize_high_unicode_untouched():
    text = "emoji: \U0001F600 and arrow: →"
    assert sanitize_for_json(text) == text


def test_sanitize_escapes_produce_valid_strict_json():
    """Sanitized strings must survive json.loads(strict=True)."""
    dirty = "step" + "".join(chr(i) for i in range(0x20) if chr(i) not in "\t\n\r") + "done"
    clean = sanitize_for_json(dirty)
    payload = json.dumps({"step": clean})
    parsed = json.loads(payload, strict=True)
    assert parsed["step"] == clean


# ---------------------------------------------------------------------------
# Integration test: GET /api/agents with control bytes in metadata
# ---------------------------------------------------------------------------


def _mock_ostk():
    """Return a mock ostk service that never hits the real daemon."""
    mock = MagicMock()
    mock.kernel_ps = AsyncMock(return_value={
        "raw": "no daemon running",
        "daemon_running": False,
        "agents": [],
    })
    mock.audit_agents = AsyncMock(return_value=[])
    return mock


@pytest.mark.asyncio
async def test_list_agents_parses_cleanly_with_control_bytes_in_metadata():
    """GET /api/agents response must be strict=True parseable even when an
    agent's current_step or task field contains raw control characters.

    Root cause (->1137): agents whose heartbeat step contained a NUL byte
    (or other U+0000-U+001F chars outside tab/LF/CR) produced a JSON
    response that failed json.loads(strict=True) at the position of the
    raw byte.  sanitize_for_json is now applied in _run_enrich_pipeline
    before the filtered list is serialized.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dirty_step = "running pytest" + _NUL + _SOH + "output follows"
        dirty_task = "diagnose" + _NUL + "the" + _SOH + "bug"
        dirty_desc = "Agent with" + _STX + "control" + chr(3) + "chars"

        with patch("routers.agents.ostk", _mock_ostk()), \
             patch("routers.agents.agent_metadata", {
                 "ctrl-byte-agent": {
                     "name": "ctrl-byte-agent",
                     "source": "claude-code",
                     "status": "running",
                     "spawned_at": "2026-05-11T00:00:00+00:00",
                     "last_heartbeat_at": "2026-05-11T00:00:00+00:00",
                     "model": "claude-sonnet-4-6",
                     "budget": "2.0",
                     "current_step": dirty_step,
                     "task": dirty_task,
                     "description": dirty_desc,
                     "tokens_used": 0,
                 }
             }), \
             patch("routers.agents.active_agents", {}), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._prune_stale_completed_agents"), \
             patch("routers.agents._recover_bulk_cancelled_agents", return_value=False), \
             patch("routers.agents._reconcile_workflow_step_agents", return_value=False), \
             patch("routers.agents._autocomplete_exited_subagents", return_value=False), \
             patch("routers.agents._avg_minutes_per_dollar", return_value={}):

            resp = await client.get("/api/agents")
            assert resp.status_code == 200

            raw_body = resp.content
            # MUST parse with strict=True -- no literal control bytes allowed
            try:
                data = json.loads(raw_body, strict=True)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"GET /api/agents produced invalid strict JSON: {exc}\n"
                    f"Raw body (first 500 bytes): {raw_body[:500]!r}"
                ) from exc

            agents = data.get("agents", [])
            found = next((a for a in agents if a.get("name") == "ctrl-byte-agent"), None)
            assert found is not None, "ctrl-byte-agent not in response"

            # The control bytes must be gone from the serialized fields
            step = found.get("current_step", "")
            assert _NUL not in step, f"NUL survived in current_step: {step!r}"
            assert _SOH not in step, f"SOH survived in current_step: {step!r}"

            task_val = found.get("task", "")
            assert _NUL not in task_val, f"NUL survived in task: {task_val!r}"

            desc = found.get("description", "")
            assert _STX not in desc, f"STX survived in description: {desc!r}"


@pytest.mark.asyncio
async def test_list_agents_parses_cleanly_with_control_bytes_in_ps_status():
    """The 'status' field (raw ostk kernel ps output) is also sanitized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk", _mock_ostk()) as mock_ostk, \
             patch("routers.agents.agent_metadata", {}), \
             patch("routers.agents.active_agents", {}), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._prune_stale_completed_agents"), \
             patch("routers.agents._recover_bulk_cancelled_agents", return_value=False), \
             patch("routers.agents._reconcile_workflow_step_agents", return_value=False), \
             patch("routers.agents._autocomplete_exited_subagents", return_value=False), \
             patch("routers.agents._avg_minutes_per_dollar", return_value={}):

            # Simulate kernel ps returning output with control chars
            mock_ostk.kernel_ps = AsyncMock(return_value={
                "raw": "daemon running" + _BEL + _BS + _VT + " cols follow",
                "daemon_running": True,
                "agents": [],
            })

            resp = await client.get("/api/agents")
            assert resp.status_code == 200

            raw_body = resp.content
            try:
                data = json.loads(raw_body, strict=True)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"GET /api/agents produced invalid strict JSON with ps control chars: {exc}\n"
                    f"Raw body (first 300): {raw_body[:300]!r}"
                ) from exc

            # Verify the raw control bytes were escaped/removed from the status field
            status_val = data.get("status", "")
            assert _BEL not in status_val, "BEL survived in status field"
            assert _BS not in status_val, "BS survived in status field"
            assert _VT not in status_val, "VT survived in status field"

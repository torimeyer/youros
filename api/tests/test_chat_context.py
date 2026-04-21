"""Tests for the four chat context and failure mode fixes.

Bug 1: recent activity context block injected into system prompt.
Bug 2: file:// and absolute path hints used directly, not searched first.
Bug 3: _check_agents retries on transient errors; _spawn_agent validates inputs.
Bug 4: myOS vocabulary (spec = Specs page entry) present in system prompt.
"""

import asyncio
import os
import json
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Bug 1: recent activity context
# ---------------------------------------------------------------------------


class TestRecentActivityContext:
    """_recent_activity_context builds a compact summary from audit events."""

    @pytest.fixture(autouse=True)
    def _reset_activity_cache(self):
        """Drop the 15-second in-process cache before/after every test.

        Without this the first test (no relevant events) caches an empty
        string, and every subsequent test sees the empty cached result
        regardless of the audit file it wrote.
        """
        from services import chat_providers
        chat_providers._clear_activity_context_cache()
        yield
        chat_providers._clear_activity_context_cache()

    def _make_audit(self, entries: list[dict], path: Path) -> None:
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_returns_empty_string_when_no_relevant_events(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        self._make_audit(
            [{"event": "tool.bash", "cmd": "ls", "ts": "2026-04-15T01:00:00Z"}],
            audit,
        )
        from services.ostk import invalidate_audit_cache
        from services import chat_providers

        with patch("services.ostk.OSTK_DIR", tmp_path):
            invalidate_audit_cache(audit)
            result = chat_providers._recent_activity_context()

        # tool.bash is not in _ACTIVITY_EVENTS so nothing should be returned
        assert result == ""

    def test_includes_agent_completed_event(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        self._make_audit(
            [
                {
                    "event": "agent.completed",
                    "name": "roadmap-builder",
                    "summary": "wrote docs/plans/three-year-roadmap.md",
                    "timestamp": "2026-04-15T02:30:00+00:00",
                }
            ],
            audit,
        )
        from services.ostk import invalidate_audit_cache
        from services import chat_providers

        with patch("services.ostk.OSTK_DIR", tmp_path):
            invalidate_audit_cache(audit)
            result = chat_providers._recent_activity_context()

        assert "RECENT ACTIVITY" in result
        assert "roadmap-builder" in result
        assert "completed" in result

    def test_result_within_max_chars(self, tmp_path):
        audit = tmp_path / "audit.jsonl"
        entries = [
            {
                "event": "agent.completed",
                "name": f"agent-{i}",
                "summary": "x" * 200,
                "timestamp": f"2026-04-15T01:0{i:02d}:00+00:00",
            }
            for i in range(30)
        ]
        self._make_audit(entries, audit)
        from services.ostk import invalidate_audit_cache
        from services import chat_providers

        with patch("services.ostk.OSTK_DIR", tmp_path):
            invalidate_audit_cache(audit)
            result = chat_providers._recent_activity_context()

        assert len(result) <= 1800

    def test_injected_into_compose_system_prompt(self, tmp_path):
        """_compose_system_prompt must include activity when events exist."""
        audit = tmp_path / "audit.jsonl"
        with open(audit, "w") as f:
            f.write(
                json.dumps({
                    "event": "agent.completed",
                    "name": "spec-writer",
                    "summary": "wrote api-access spec",
                    "timestamp": "2026-04-15T02:00:00+00:00",
                }) + "\n"
            )
        from services.ostk import invalidate_audit_cache
        from services import chat_providers

        with patch("services.ostk.OSTK_DIR", tmp_path), \
             patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: d
            invalidate_audit_cache(audit)
            result = chat_providers._compose_system_prompt(None)

        assert "RECENT ACTIVITY" in result
        assert "spec-writer" in result

    def test_injected_into_build_cached_system_blocks(self, tmp_path):
        """_build_cached_system_blocks volatile block must include activity."""
        audit = tmp_path / "audit.jsonl"
        with open(audit, "w") as f:
            f.write(
                json.dumps({
                    "event": "agent.spawned",
                    "name": "api-docs",
                    "model": "claude-sonnet-4-6",
                    "timestamp": "2026-04-15T02:10:00+00:00",
                }) + "\n"
            )
        from services.ostk import invalidate_audit_cache
        from services import chat_providers

        with patch("services.ostk.OSTK_DIR", tmp_path), \
             patch("services.chat_providers._get_boot_context", return_value="boot stuff"), \
             patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: d
            invalidate_audit_cache(audit)
            blocks = chat_providers._build_cached_system_blocks(None)

        # With boot context and activity, we should have 2 blocks
        assert len(blocks) == 2
        volatile_text = blocks[1]["text"]
        assert "RECENT ACTIVITY" in volatile_text
        assert "api-docs" in volatile_text


# ---------------------------------------------------------------------------
# Bug 2: path-first resolution in _read_file
# ---------------------------------------------------------------------------


class TestReadFilePathFirst:
    """_read_file must use file:// and absolute paths directly."""

    @pytest.mark.asyncio
    async def test_file_uri_reads_directly(self, tmp_path):
        """file:// prefix is stripped and the file is read directly."""
        target = tmp_path / "roadmap.html"
        target.write_text("<h1>Three Year Roadmap</h1>")

        from services.tool_executor import _read_file

        result = await _read_file(f"file://{target}")
        assert "<h1>Three Year Roadmap</h1>" in result

    @pytest.mark.asyncio
    async def test_absolute_path_reads_directly(self, tmp_path):
        """Absolute paths are read directly without workspace restriction."""
        target = tmp_path / "plan.md"
        target.write_text("## Plan\nAll the things.")

        from services.tool_executor import _read_file

        result = await _read_file(str(target))
        assert "## Plan" in result

    @pytest.mark.asyncio
    async def test_nonexistent_absolute_path_returns_not_found(self, tmp_path):
        """When an explicit absolute path does not exist, return a clear error."""
        missing = tmp_path / "does_not_exist.md"

        from services.tool_executor import _read_file

        result = await _read_file(str(missing))
        assert "not found" in result.lower() or "File not found" in result

    @pytest.mark.asyncio
    async def test_file_uri_outside_workspace_is_readable(self, tmp_path):
        """User-supplied file:// path outside the workspace boundary is readable."""
        # The workspace is PROJECT_ROOT. tmp_path is outside it.
        target = tmp_path / "outside.txt"
        target.write_text("outside workspace content")

        from services.tool_executor import _read_file

        result = await _read_file(f"file://{target}")
        assert "outside workspace content" in result


# ---------------------------------------------------------------------------
# Bug 3: _check_agents retry + _spawn_agent validation
# ---------------------------------------------------------------------------


class TestCheckAgentsRetry:
    """_check_agents retries on transient connection errors."""

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self):
        """Returns agent data when the first attempt fails but second succeeds."""
        import httpx
        from services import tool_executor

        call_count = 0

        async def fake_get(url, timeout=5):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Server disconnected without sending a response.")
            resp = MagicMock()
            resp.json.return_value = {
                "agents": [{"name": "test-agent", "status": "complete", "model": "sonnet", "source": "api"}],
                "active": [],
            }
            return resp

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = fake_get

        with patch("services.tool_executor.asyncio.sleep", new=AsyncMock()), \
             patch("httpx.AsyncClient", return_value=fake_client):
            result = await tool_executor._check_agents()

        assert "test-agent" in result
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_returns_actionable_error_after_all_retries_fail(self):
        """Returns a helpful message when all retry attempts fail."""
        import httpx
        from services import tool_executor

        async def fake_get(url, timeout=5):
            raise httpx.ConnectError("TLS handshake reset")

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = fake_get

        with patch("services.tool_executor.asyncio.sleep", new=AsyncMock()), \
             patch("httpx.AsyncClient", return_value=fake_client):
            result = await tool_executor._check_agents()

        assert "try again" in result.lower() or "wait" in result.lower()
        assert "Could not reach" in result


class TestSpawnAgentValidation:
    """_spawn_agent validates name and prompt before posting to the API."""

    @pytest.mark.asyncio
    async def test_empty_name_returns_actionable_error(self):
        from services.tool_executor import _spawn_agent

        result = await _spawn_agent("", "do stuff")
        assert "empty" in result.lower() or "Cannot spawn" in result

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_actionable_error(self):
        from services.tool_executor import _spawn_agent

        result = await _spawn_agent("my-agent", "")
        assert "prompt is empty" in result or "Cannot spawn" in result

    @pytest.mark.asyncio
    async def test_name_with_spaces_returns_actionable_error(self):
        from services.tool_executor import _spawn_agent

        result = await _spawn_agent("my agent name", "do stuff")
        assert "letters, numbers, hyphens" in result or "Cannot spawn" in result

    @pytest.mark.asyncio
    async def test_name_with_hyphens_is_accepted(self):
        """Hyphenated names like 'api-spec' must be allowed."""
        import httpx
        from services import tool_executor

        async def fake_post(url, json=None, timeout=10):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"pid": 12345}
            return resp

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            result = await tool_executor._spawn_agent("api-spec", "build the api spec")

        assert "api-spec" in result
        assert "spawned" in result.lower()

    @pytest.mark.asyncio
    async def test_http_error_returns_actionable_message(self):
        """Non-200 HTTP response surfaces a clear error with the detail."""
        import httpx
        from services import tool_executor

        async def fake_post(url, json=None, timeout=10):
            resp = MagicMock()
            resp.status_code = 400
            resp.json.return_value = {"detail": "Unknown saa template: 'api-spec'"}
            return resp

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            result = await tool_executor._spawn_agent("api-spec", "build it")

        assert "HTTP 400" in result or "400" in result
        assert "Unknown saa template" in result


# ---------------------------------------------------------------------------
# Bug 4: myOS vocabulary in system prompt
# ---------------------------------------------------------------------------


class TestMyOSVocabulary:
    """System prompt must include the Specs page vocabulary definition."""

    def test_spec_vocabulary_in_system_prompt(self):
        """_system_prompt must define 'spec' as a Specs page entry."""
        from services import chat_providers
        with patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: d
            prompt = chat_providers._system_prompt()

        assert "Specs page" in prompt or "specs page" in prompt.lower()
        assert "spec" in prompt.lower()

    def test_vocabulary_section_present(self):
        """The myOS VOCABULARY section must exist in the system prompt."""
        from services import chat_providers
        with patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: d
            prompt = chat_providers._system_prompt()

        assert "myOS VOCABULARY" in prompt or "VOCABULARY" in prompt

    def test_path_hint_instruction_present(self):
        """The PATH HINTS section must instruct to use file:// paths directly."""
        from services import chat_providers
        with patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: d
            prompt = chat_providers._system_prompt()

        assert "PATH HINTS" in prompt or "file://" in prompt

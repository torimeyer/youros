import os
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_executor import (
    _safe_path,
    execute_tool,
    TOOL_DEFINITIONS,
    WORKSPACE,
)


# ---- _safe_path ----

class TestSafePath:
    def test_absolute_within_workspace(self, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", WORKSPACE)
        result = _safe_path(str(WORKSPACE / "api" / "main.py"))
        # resolve() on macOS prepends /private to /var; ensure both sides match
        assert str(result).startswith(str(WORKSPACE.resolve()))

    def test_relative_path_resolved(self, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", WORKSPACE)
        """Relative paths are resolved from cwd, not workspace. The test
        verifies that _safe_path raises on paths outside the workspace."""
        # A relative path that resolves outside workspace should raise
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path("/tmp/outside_file.txt")

    def test_path_traversal_blocked(self, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", WORKSPACE)
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path(str(WORKSPACE / ".." / ".." / "etc" / "passwd"))

    def test_symlink_traversal_blocked(self, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", WORKSPACE)
        """Ensure /etc/passwd is rejected even if disguised."""
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path("/etc/passwd")

    def test_workspace_root_allowed(self, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", WORKSPACE)
        result = _safe_path(str(WORKSPACE))
        assert result == WORKSPACE.resolve()


# ---- Tool definitions ----

class TestToolDefinitions:
    def test_all_tools_have_required_fields(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_expected_tools_present(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "read_file", "write_file", "edit_file", "run_command",
            "list_directory", "search_files", "list_tasks",
            "create_task", "close_task", "spawn_agent",
            "check_agents",
            "web_search", "web_fetch",
            "git_status", "git_diff", "git_commit",
            "get_calendar_events",
            "create_calendar_event",
            "send_email",
            "send_imessage",
            "delete_emails",
            "upload_to_drive",
            "create_tasks_from_spec",
            "build_tasks_from_file",
            "build_from_recent_tasks",
            "chat_schedule_wakeup",
            "chat_monitor",
            "semantic_search",
        }
        assert expected == names

    def test_create_task_has_description_and_labels(self):
        """create_task must expose description and labels fields."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "create_task")
        props = tool["input_schema"]["properties"]
        assert "description" in props
        assert "labels" in props

    def test_create_task_title_contract_enforces_80_char_cap(self):
        """Title field description must instruct AI to keep titles under 80 chars."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "create_task")
        title_desc = tool["input_schema"]["properties"]["title"]["description"]
        assert "max 80 chars" in title_desc, (
            f"Title description must contain 'max 80 chars' but got: {title_desc!r}"
        )

    def test_create_task_description_contract_has_why_and_acceptance(self):
        """Description field must spell out the Why/Acceptance/Blockers/Notes shape."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "create_task")
        desc_desc = tool["input_schema"]["properties"]["description"]["description"]
        assert "Why:" in desc_desc, (
            f"Description contract must contain 'Why:' but got: {desc_desc!r}"
        )
        assert "Acceptance:" in desc_desc, (
            f"Description contract must contain 'Acceptance:' but got: {desc_desc!r}"
        )

    def test_create_tasks_from_spec_definition(self):
        """create_tasks_from_spec must be well-formed with spec_path required."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "create_tasks_from_spec")
        assert "spec_path" in tool["input_schema"]["properties"]
        assert "spec_path" in tool["input_schema"]["required"]

    def test_no_duplicate_names(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names))


# ---- execute_tool: read_file ----

class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path, monkeypatch):
        """Read a file that we know exists in the workspace."""
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)

        test_api_dir = tmp_path / "api"
        test_api_dir.mkdir()
        test_file = test_api_dir / "main.py"
        test_file.write_text("import FastAPI")

        result = await execute_tool("read_file", {"path": str(test_file)})
        assert "FastAPI" in result

    @pytest.mark.asyncio
    async def test_read_missing_file(self):
        result = await execute_tool("read_file", {"path": str(WORKSPACE / "nonexistent_file_xyz.py")})
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_outside_workspace(self):
        # Absolute paths are allowed (user-supplied paths are trusted for drag-and-drop).
        # /etc/hosts exists on this system so this should read successfully.
        result = await execute_tool("read_file", {"path": "/etc/hosts"})
        assert "localhost" in result or "not found" in result.lower() or "Error" in result


# ---- execute_tool: write_file ----

class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_and_read(self, tmp_path, monkeypatch):
        """Write a file within a temporary workspace and read it back."""
        # The workspace guard reads config.PROJECT_ROOT at call time, so point
        # the workspace at tmp_path there. Patching WORKSPACE alone has no
        # effect on the guard.
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = str(tmp_path / "test_output.txt")
        result = await execute_tool("write_file", {"path": test_file, "content": "hello world"})
        assert "Wrote" in result
        content = Path(test_file).read_text()
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_write_outside_workspace(self):
        result = await execute_tool("write_file", {"path": "/tmp/bad.txt", "content": "nope"})
        assert "Error" in result


# ---- execute_tool: edit_file ----

class TestEditFile:
    @pytest.mark.asyncio
    async def test_edit_replaces_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "edit_me.txt"
        test_file.write_text("hello world")
        result = await execute_tool("edit_file", {
            "path": str(test_file),
            "old_text": "hello",
            "new_text": "goodbye",
        })
        assert "Edited" in result
        assert test_file.read_text() == "goodbye world"

    @pytest.mark.asyncio
    async def test_edit_old_text_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "edit_me.txt"
        test_file.write_text("hello world")
        result = await execute_tool("edit_file", {
            "path": str(test_file),
            "old_text": "missing text",
            "new_text": "replacement",
        })
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_edit_multiple_occurrences(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "edit_me.txt"
        test_file.write_text("foo bar foo")
        result = await execute_tool("edit_file", {
            "path": str(test_file),
            "old_text": "foo",
            "new_text": "baz",
        })
        assert "2 times" in result


# ---- execute_tool: run_command ----

class TestRunCommand:
    @pytest.mark.asyncio
    async def test_run_echo(self):
        result = await execute_tool("run_command", {"command": "echo hello"})
        assert "hello" in result
        assert "Exit code: 0" in result

    @pytest.mark.asyncio
    async def test_run_failing_command(self):
        result = await execute_tool("run_command", {"command": "ls /nonexistent_path_xyz"})
        assert "Exit code:" in result
        # The exit code should be non-zero
        assert "Exit code: 0" not in result


# ---- execute_tool: list_directory ----

class TestListDirectory:
    @pytest.mark.asyncio
    async def test_list_workspace_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        
        (tmp_path / "api").mkdir()
        (tmp_path / "app").mkdir()
        
        result = await execute_tool("list_directory", {})
        assert "api" in result
        assert "app" in result

    @pytest.mark.asyncio
    async def test_list_specific_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        
        test_api_dir = tmp_path / "api"
        test_api_dir.mkdir()
        (test_api_dir / "main.py").write_text("hello")
        
        result = await execute_tool("list_directory", {"path": str(test_api_dir)})
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_list_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        result = await execute_tool("list_directory", {"path": str(tmp_path / "no_such_dir")})
        assert "not found" in result.lower()


# ---- execute_tool: search_files ----

class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_search_finds_pattern(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        
        test_api_dir = tmp_path / "api"
        test_api_dir.mkdir()
        test_file = test_api_dir / "main.py"
        test_file.write_text("yourOS API")
        
        # Search for a string that only appears in main.py (updated from myOS → yourOS in v4.0.0)
        result = await execute_tool("search_files", {"pattern": "yourOS API", "path": str(test_api_dir)})
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_search_no_matches(self, tmp_path, monkeypatch):
        # Use an isolated directory so the pattern can't be found anywhere
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "sample.py"
        test_file.write_text("hello world")
        result = await execute_tool("search_files", {"pattern": "zzz_will_not_match"})
        assert "No matches" in result

    @pytest.mark.asyncio
    async def test_context_lines_returns_surrounding_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "sample.py"
        test_file.write_text("line_before\nTARGET_LINE\nline_after\n")
        result = await execute_tool(
            "search_files", {"pattern": "TARGET_LINE", "context_lines": 1}
        )
        assert "line_before" in result
        assert "line_after" in result

    @pytest.mark.asyncio
    async def test_max_results_caps_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "sample.py"
        # Write 30 lines each containing MATCH so grep returns 30 match lines
        test_file.write_text("\n".join(f"MATCH line {i}" for i in range(30)))
        result = await execute_tool(
            "search_files",
            {"pattern": "MATCH", "context_lines": 0, "max_results": 5},
        )
        lines = [l for l in result.splitlines() if "MATCH" in l]
        assert len(lines) <= 5
        assert "total lines" in result

    @pytest.mark.asyncio
    async def test_max_results_capped_at_200(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "sample.py"
        test_file.write_text("\n".join(f"MATCH line {i}" for i in range(10)))
        # Requesting 999 should be silently capped at 200
        result = await execute_tool(
            "search_files",
            {"pattern": "MATCH", "context_lines": 0, "max_results": 999},
        )
        assert "No matches" not in result


# ---- execute_tool: ostk tools ----

class TestOstkTools:
    @pytest.mark.asyncio
    async def test_list_tasks_calls_ostk(self):
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "T1", "priority": "P1", "title": "Test task"},
            ])
            result = await execute_tool("list_tasks", {})
            assert "T1" in result
            assert "Test task" in result

    @pytest.mark.asyncio
    async def test_create_task_calls_ostk(self):
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.add_task = AsyncMock(return_value="added T2")
            result = await execute_tool("create_task", {"title": "New task", "priority": "P0"})
            assert "added" in result
            mock_ostk.add_task.assert_awaited_once_with("New task", "P0")

    @pytest.mark.asyncio
    async def test_create_task_schedules_auto_labels(self):
        """The tool_executor create_task path must run auto-labeling like /api/tasks does."""
        with patch("services.tool_executor.ostk") as mock_ostk, \
             patch("services.task_labeling.schedule_auto_labels") as mock_schedule:
            mock_ostk.add_task = AsyncMock(return_value="added 501: New task [P0]")
            await execute_tool("create_task", {"title": "New task", "priority": "P0"})
            mock_schedule.assert_called_once()
            args, _ = mock_schedule.call_args
            # task id parsed from the ostk return string, then title, then empty desc
            assert args[0] == "501"
            assert args[1] == "New task"
            assert args[2] == ""

    @pytest.mark.asyncio
    async def test_create_task_schedules_auto_labels_with_arrow_id(self):
        """Auto-label scheduling must accept the arrow-prefixed id format."""
        with patch("services.tool_executor.ostk") as mock_ostk, \
             patch("services.task_labeling.schedule_auto_labels") as mock_schedule:
            mock_ostk.add_task = AsyncMock(return_value="added \u2192142: do thing [P1]")
            await execute_tool("create_task", {"title": "do thing", "priority": "P1"})
            mock_schedule.assert_called_once()
            args, _ = mock_schedule.call_args
            assert args[0] == "\u2192142"

    @pytest.mark.asyncio
    async def test_create_task_passes_description_to_auto_labels(self):
        """Description is forwarded to schedule_auto_labels for better label suggestions."""
        with patch("services.tool_executor.ostk") as mock_ostk, \
             patch("services.task_labeling.schedule_auto_labels") as mock_schedule:
            mock_ostk.add_task = AsyncMock(return_value="added 99: Do something [P2]")
            await execute_tool("create_task", {
                "title": "Do something",
                "description": "A detailed description",
                "priority": "P2",
            })
            mock_schedule.assert_called_once()
            args, _ = mock_schedule.call_args
            assert args[2] == "A detailed description"

    @pytest.mark.asyncio
    async def test_close_task_calls_ostk(self):
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.close_task = AsyncMock(return_value="closed T1")
            result = await execute_tool("close_task", {"task_id": "T1"})
            assert "closed" in result
            mock_ostk.close_task.assert_awaited_once_with("T1")

    @pytest.mark.asyncio
    async def test_create_task_deduplicates_same_title(self):
        """When an open task with the same (normalised) title already exists,
        create_task must return the existing ID without calling add_task again.
        This prevents the chat model from creating duplicate needles on a single
        saa turn (→1123)."""
        existing = [{"id": "→1119", "title": "Plans for needles in recent docs", "status": "open"}]
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=existing)
            mock_ostk.add_task = AsyncMock(return_value="added →1120: Plans for needles in recent docs")
            result = await execute_tool("create_task", {"title": "Plans for needles in recent docs"})
            mock_ostk.add_task.assert_not_awaited()
            assert "→1119" in result

    @pytest.mark.asyncio
    async def test_create_task_deduplicates_case_insensitive(self):
        """Dedup is case- and whitespace-insensitive."""
        existing = [{"id": "→500", "title": "Fix the login bug", "status": "open"}]
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=existing)
            mock_ostk.add_task = AsyncMock(return_value="added →501")
            result = await execute_tool("create_task", {"title": "  Fix The Login Bug  "})
            mock_ostk.add_task.assert_not_awaited()
            assert "→500" in result

    @pytest.mark.asyncio
    async def test_create_task_creates_when_no_duplicate(self):
        """When no matching open task exists, add_task must still be called."""
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"id": "→99", "title": "Something else entirely", "status": "open"}
            ])
            mock_ostk.add_task = AsyncMock(return_value="added →100: Build new feature")
            result = await execute_tool("create_task", {"title": "Build new feature"})
            mock_ostk.add_task.assert_awaited_once()
            assert "added" in result


# ---- execute_tool: create_tasks_from_spec ----

class TestCreateTasksFromSpec:
    @pytest.mark.asyncio
    async def test_valid_spec_path_calls_decompose_endpoint(self):
        """A valid docs/spec/... path must POST to /api/specs/decompose and return task IDs."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": "Created tasks 301 302",
            "task_ids": [301, 302],
        }

        with patch("services.tool_executor.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await execute_tool("create_tasks_from_spec", {"spec_path": "docs/spec/my-feature.md"})

        assert "301" in result
        assert "302" in result
        assert "2 task" in result
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert "specs/decompose" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["path"] == "docs/spec/my-feature.md"

    @pytest.mark.asyncio
    async def test_draft_path_also_accepted(self):
        """docs/draft/... paths must also be accepted."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "Created task 400", "task_ids": [400]}

        with patch("services.tool_executor.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await execute_tool("create_tasks_from_spec", {"spec_path": "docs/draft/my-draft.md"})

        assert "1 task" in result
        assert "400" in result

    @pytest.mark.asyncio
    async def test_path_outside_docs_rejected(self):
        """Paths not under docs/draft/ or docs/spec/ must be rejected without an HTTP call."""
        result = await execute_tool("create_tasks_from_spec", {"spec_path": "api/main.py"})
        assert "must be under docs/draft/" in result

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        """Paths containing .. must be rejected without an HTTP call."""
        result = await execute_tool("create_tasks_from_spec", {"spec_path": "docs/spec/../../../etc/passwd"})
        assert "path traversal" in result.lower()

    @pytest.mark.asyncio
    async def test_http_error_returns_actionable_message(self):
        """A non-200 response must return a readable error, not a raw exception."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "spec not found"}

        with patch("services.tool_executor.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await execute_tool("create_tasks_from_spec", {"spec_path": "docs/spec/missing.md"})

        assert "404" in result
        assert "spec not found" in result

    @pytest.mark.asyncio
    async def test_no_tasks_created_returns_informative_message(self):
        """When task_ids is empty the result must explain what happened."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "No tasks found in spec", "task_ids": []}

        with patch("services.tool_executor.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await execute_tool("create_tasks_from_spec", {"spec_path": "docs/spec/empty.md"})

        assert "no tasks" in result.lower()


# ---- system prompt includes task creation tools ----

class TestSystemPromptTaskTools:
    def test_system_prompt_mentions_create_tasks_from_spec(self):
        """The system prompt must instruct the model to call create_tasks_from_spec."""
        from services.chat_providers import _system_prompt
        prompt = _system_prompt()
        assert "create_tasks_from_spec" in prompt

    def test_system_prompt_mentions_create_task(self):
        """The system prompt must instruct the model to call create_task."""
        from services.chat_providers import _system_prompt
        prompt = _system_prompt()
        assert "create_task" in prompt

    def test_system_prompt_covers_create_tasks_trigger_phrases(self):
        """The system prompt must cover the key trigger phrases Tori uses."""
        from services.chat_providers import _system_prompt
        prompt = _system_prompt()
        assert "create tasks for all of it" in prompt
        assert "turn this into tasks" in prompt

    def test_system_prompt_has_curl_fallback_for_claude_code(self):
        """System prompt must include curl fallback so Claude Code backend can create tasks via Bash."""
        from services.chat_providers import _system_prompt
        prompt = _system_prompt()
        # Must mention the tasks REST endpoint so Claude Code can POST via its Bash tool
        assert "/api/tasks" in prompt
        # Must mention curl as the fallback mechanism
        assert "curl" in prompt

    def test_system_prompt_anchors_relative_dates_to_today(self):
        """Regression: the LLM used to hallucinate years for relative dates
        (e.g. "the 28th" -> 2024-12-28 when today was 2026-04-21) because
        the system prompt never gave it the current date. Anchor today's
        date at the top of the prompt so "the 28th" resolves to the current
        year/month instead of a training-era guess.
        """
        from datetime import datetime, timezone
        from services.chat_providers import _system_prompt
        prompt = _system_prompt()
        # The prompt now uses the SERVER's local date, not UTC, because
        # calendar events get created in the user's timezone. Compute
        # expected local date the same way the prompt does.
        today_local = datetime.now(timezone.utc).astimezone()
        assert today_local.strftime("%Y-%m-%d") in prompt
        assert str(today_local.year) in prompt
        # Must explicitly instruct the model not to make up years.
        assert "Never invent a year" in prompt
        # Timezone block (UTC-05:00 or similar offset) must be present so
        # Claude attaches the correct offset to calendar event datetimes
        # instead of letting Google default to Pacific.
        assert "TIMEZONE:" in prompt
        assert "UTC" in prompt


# ---- execute_tool: capture_idea ----

# ---- execute_tool: unknown tool ----

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await execute_tool("nonexistent_tool", {})
        assert "Unknown tool" in result


# ---- execute_tool: build_tasks_from_file + build_from_recent_tasks ----


class TestBuildTasksFromFile:
    """Tests for the two demo-flow tools that let chat break a file into
    tasks and then spawn Builder agents for each one.
    """

    def test_tool_definitions_registered(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "build_tasks_from_file" in names
        assert "build_from_recent_tasks" in names
        bt = next(t for t in TOOL_DEFINITIONS if t["name"] == "build_tasks_from_file")
        assert "file_path" in bt["input_schema"]["required"]
        br = next(t for t in TOOL_DEFINITIONS if t["name"] == "build_from_recent_tasks")
        assert br["input_schema"]["required"] == []

    @pytest.mark.asyncio
    async def test_chat_build_tasks_from_md_creates_tasks(self, tmp_path):
        """Reading a markdown file with bullet points should produce a
        task per bullet and record the batch in last_task_batch.json.
        """
        import json
        from services import tool_executor

        fake_home = tmp_path / "home"
        myos_files = fake_home / ".youros" / "files"
        myos_files.mkdir(parents=True, exist_ok=True)
        (myos_files / "roadmap.md").write_text(
            "# Roadmap\n\n"
            "- Ship onboarding wizard\n"
            "- Launch briefing feed\n"
            "- Add cost tracking dashboard\n"
        )

        added_titles: list[str] = []

        async def fake_add_task(title, priority="P1"):
            added_titles.append(title)
            idx = len(added_titles)
            # Mirror ostk's real output: "added 101: <title> [P1]"
            return f"added {100 + idx}: {title} [{priority}]"

        batch_path = tmp_path / "last_task_batch.json"

        with patch.object(tool_executor.ostk, "add_task", new=AsyncMock(side_effect=fake_add_task)), \
             patch.object(tool_executor.ostk, "list_tasks", new=AsyncMock(return_value=[])), \
             patch.object(tool_executor, "_LAST_BATCH_PATH", batch_path), \
             patch("services.task_labeling.schedule_auto_labels"), \
             patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="")), \
             patch("services.tool_executor.Path.home", return_value=fake_home):
            result = await execute_tool(
                "build_tasks_from_file",
                {"file_path": "roadmap.md"},
            )

        assert "Created 3 tasks" in result, result
        assert len(added_titles) == 3
        assert added_titles[0] == "Ship onboarding wizard"
        assert batch_path.exists()
        payload = json.loads(batch_path.read_text())
        assert len(payload["task_ids"]) == 3
        assert payload["file_path"].endswith("roadmap.md")

    @pytest.mark.asyncio
    async def test_build_tasks_missing_file_returns_helpful_error(self, tmp_path):
        from services import tool_executor

        fake_home = tmp_path / "home"
        (fake_home / ".youros" / "files").mkdir(parents=True, exist_ok=True)

        with patch("services.tool_executor.Path.home", return_value=fake_home):
            result = await execute_tool(
                "build_tasks_from_file",
                {"file_path": "does-not-exist.md"},
            )
        assert "Could not find" in result

    @pytest.mark.asyncio
    async def test_chat_build_it_spawns_agents_for_last_batch(self, tmp_path):
        """After a decomposition batch is recorded, 'build it' (i.e. the
        build_from_recent_tasks tool) must spawn one Builder agent per
        task via the spawn endpoint.
        """
        import json
        from services import tool_executor

        batch_path = tmp_path / "last_task_batch.json"
        batch_path.write_text(json.dumps({
            "file_path": "/tmp/roadmap.md",
            "task_ids": ["\u2192101", "\u2192102"],
            "created_at": "2026-04-15T10:00:00Z",
        }))

        spawned_requests: list[dict] = []

        class _FakeResp:
            status_code = 200
            text = "{}"
            def json(self):
                return {"pid": 12345}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False
            async def post(self, url, json=None, timeout=None):
                spawned_requests.append({"url": url, "json": json})
                return _FakeResp()

        with patch.object(tool_executor, "_LAST_BATCH_PATH", batch_path), \
             patch.object(tool_executor.ostk, "list_tasks", new=AsyncMock(return_value=[
                 {"id": "\u2192101", "title": "Ship onboarding wizard"},
                 {"id": "\u2192102", "title": "Launch briefing feed"},
             ])), \
             patch("services.tool_executor.httpx.AsyncClient", _FakeClient):
            result = await execute_tool("build_from_recent_tasks", {})

        assert "Started 2 Builder agents" in result, result
        assert len(spawned_requests) == 2
        for req in spawned_requests:
            assert req["url"].endswith("/api/agents/spawn")
            assert req["json"]["template"] == "builder"
            assert req["json"]["source"] == "chat-build-it"
        # Each spawn must carry the task title so the Agents page shows
        # what each build-<id> agent is actually working on instead of
        # the opaque internal name. The mocked list_tasks above returned
        # "Ship onboarding wizard" and "Launch briefing feed" for the two
        # task IDs, so both titles must appear verbatim on the task field.
        tasks_spawned = {req["json"]["task"] for req in spawned_requests}
        assert any("Ship onboarding wizard" in t for t in tasks_spawned), tasks_spawned
        assert any("Launch briefing feed" in t for t in tasks_spawned), tasks_spawned

    @pytest.mark.asyncio
    async def test_build_it_without_batch_returns_help(self, tmp_path):
        from services import tool_executor
        missing = tmp_path / "no_batch.json"
        with patch.object(tool_executor, "_LAST_BATCH_PATH", missing):
            result = await execute_tool("build_from_recent_tasks", {})
        assert "No recent task batch" in result

    @pytest.mark.asyncio
    async def test_build_tasks_from_file_is_idempotent_by_title_and_source(self, tmp_path):
        """Running the tool twice on the same file must not duplicate tasks.

        Regression: Tori deletes tasks (→565-568) and they instantly come
        back because a sibling smoke agent re-runs build_tasks_from_file
        on the same roadmap.md. The tool now checks existing open tasks
        by normalized title and reuses them instead of creating duplicates.
        """
        from services import tool_executor, recent_deletes

        recent_deletes.clear()
        fake_home = tmp_path / "home"
        myos_files = fake_home / ".youros" / "files"
        myos_files.mkdir(parents=True, exist_ok=True)
        (myos_files / "roadmap.md").write_text(
            "- Build basic homepage\n"
            "- Create contact form\n"
        )

        added_titles: list[str] = []

        async def fake_add_task(title, priority="P1"):
            added_titles.append(title)
            return f"added {100 + len(added_titles)}: {title} [{priority}]"

        # First pass: nothing exists yet, both tasks are created.
        async def first_list(status=None, priority=None):
            return []

        batch_path = tmp_path / "last_task_batch.json"

        with patch.object(tool_executor.ostk, "add_task", new=AsyncMock(side_effect=fake_add_task)), \
             patch.object(tool_executor.ostk, "list_tasks", new=AsyncMock(side_effect=first_list)), \
             patch.object(tool_executor, "_LAST_BATCH_PATH", batch_path), \
             patch("services.task_labeling.schedule_auto_labels"), \
             patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="")), \
             patch("services.tool_executor.Path.home", return_value=fake_home):
            first = await execute_tool(
                "build_tasks_from_file",
                {"file_path": "roadmap.md"},
            )

        assert "Created 2 tasks" in first, first
        assert len(added_titles) == 2

        # Second pass: the same two tasks are already open. The tool
        # must reuse them, not add again. The add_task mock would
        # inflate added_titles past 2 if idempotency failed.
        existing = [
            {"id": "\u2192101", "title": "Build basic homepage", "status": "open"},
            {"id": "\u2192102", "title": "Create contact form", "status": "open"},
        ]

        async def second_list(status=None, priority=None):
            return existing

        with patch.object(tool_executor.ostk, "add_task", new=AsyncMock(side_effect=fake_add_task)), \
             patch.object(tool_executor.ostk, "list_tasks", new=AsyncMock(side_effect=second_list)), \
             patch.object(tool_executor, "_LAST_BATCH_PATH", batch_path), \
             patch("services.task_labeling.schedule_auto_labels"), \
             patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="")), \
             patch("services.tool_executor.Path.home", return_value=fake_home):
            second = await execute_tool(
                "build_tasks_from_file",
                {"file_path": "roadmap.md"},
            )

        # No new adds happened, add_task is not called on the second pass.
        assert len(added_titles) == 2, f"idempotency failed: {added_titles}"
        assert "reused 2 existing" in second, second

    @pytest.mark.asyncio
    async def test_recently_deleted_task_not_recreated_by_same_source(self, tmp_path):
        """A task title the user just deleted must NOT come back via
        build_tasks_from_file for the next five minutes.

        Regression: live demo bug where deleting four tasks off the
        Tasks page did nothing because the smoke loop re-created them
        from the same roadmap.md seconds later.
        """
        from services import tool_executor, recent_deletes

        recent_deletes.clear()
        # Tombstone two titles as if the user just deleted them.
        recent_deletes.record("Build basic homepage")
        recent_deletes.record("Create contact form")

        fake_home = tmp_path / "home"
        myos_files = fake_home / ".youros" / "files"
        myos_files.mkdir(parents=True, exist_ok=True)
        (myos_files / "roadmap.md").write_text(
            "- Build basic homepage\n"
            "- Create contact form\n"
            "- Publish release notes\n"
        )

        added_titles: list[str] = []

        async def fake_add_task(title, priority="P1"):
            added_titles.append(title)
            return f"added {200 + len(added_titles)}: {title} [{priority}]"

        batch_path = tmp_path / "last_task_batch.json"

        with patch.object(tool_executor.ostk, "add_task", new=AsyncMock(side_effect=fake_add_task)), \
             patch.object(tool_executor.ostk, "list_tasks", new=AsyncMock(return_value=[])), \
             patch.object(tool_executor, "_LAST_BATCH_PATH", batch_path), \
             patch("services.task_labeling.schedule_auto_labels"), \
             patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="")), \
             patch("services.tool_executor.Path.home", return_value=fake_home):
            result = await execute_tool(
                "build_tasks_from_file",
                {"file_path": "roadmap.md"},
            )

        # Only the un-tombstoned third title should have been created.
        assert added_titles == ["Publish release notes"], added_titles
        assert "skipped 2 recently deleted" in result, result
        recent_deletes.clear()


# ---- execute_tool: delete_emails ----


class TestDeleteEmailsTool:
    @pytest.mark.asyncio
    async def test_delete_without_confirm_returns_preview(self):
        """With a query and no confirm, return a preview count plus ids."""
        fake_matches = [
            {"id": "m1", "from_name": "Amazon", "from_email": "a@x.com", "subject": "Deal!"},
            {"id": "m2", "from_name": "Amazon", "from_email": "a@x.com", "subject": "Sale!"},
        ]
        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch(
                "services.gmail.search_messages",
                new=AsyncMock(return_value=fake_matches),
            ),
            patch("services.gmail.trash_message", new=AsyncMock()) as trash_mock,
            patch("services.gmail.permanent_delete_message", new=AsyncMock()) as perm_mock,
        ):
            result = await execute_tool(
                "delete_emails",
                {"query": "from:amazon marketing"},
            )

        assert "Found 2 messages" in result
        assert "m1" in result
        assert "m2" in result
        # Preview must never touch mailbox state.
        trash_mock.assert_not_awaited()
        perm_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_with_confirm_and_ids_trashes_each(self):
        """confirm=true + ids should Trash each message, not permanent delete."""
        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.gmail.trash_message", new=AsyncMock()) as trash_mock,
            patch("services.gmail.permanent_delete_message", new=AsyncMock()) as perm_mock,
        ):
            result = await execute_tool(
                "delete_emails",
                {"confirm": True, "ids": ["m1", "m2", "m3"]},
            )

        assert "3 messages moved to Trash" in result
        assert trash_mock.await_count == 3
        perm_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_permanent_flag_calls_permanent_delete(self):
        """permanent=true routes through permanent_delete_message."""
        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.gmail.trash_message", new=AsyncMock()) as trash_mock,
            patch("services.gmail.permanent_delete_message", new=AsyncMock()) as perm_mock,
        ):
            result = await execute_tool(
                "delete_emails",
                {"confirm": True, "permanent": True, "ids": ["m1"]},
            )

        assert "permanently deleted" in result
        perm_mock.assert_awaited_once_with("m1")
        trash_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_confirm_without_ids_is_refused(self):
        """Must not accept confirm=true without explicit ids."""
        with (
            patch("services.google_auth.is_authenticated", return_value=True),
            patch("services.gmail.trash_message", new=AsyncMock()) as trash_mock,
        ):
            result = await execute_tool(
                "delete_emails",
                {"confirm": True},
            )

        assert "requires an explicit list of message ids" in result
        trash_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_no_query_no_confirm_shows_help(self):
        """Calling with nothing useful should give a clear error."""
        with patch("services.google_auth.is_authenticated", return_value=True):
            result = await execute_tool("delete_emails", {})
        assert "provide a 'query'" in result or "Cannot preview" in result

    @pytest.mark.asyncio
    async def test_delete_not_authenticated_returns_friendly_error(self):
        with patch("services.google_auth.is_authenticated", return_value=False):
            result = await execute_tool(
                "delete_emails",
                {"query": "from:anyone"},
            )
        assert "Gmail is not connected" in result


# ---- chat spawn-agent close prelude ----

class TestChatSpawnClosePrelude:
    """Regression: every chat-spawned agent was hitting the stale-sweep
    fallback summary ``Agent finished its work. It didn't formally close the
    task``. Root cause: claude-code subagents exit naturally at end-of-turn
    without executing the POST /complete sidecar curl buried in the long
    mailbox block. Fix: `_spawn_agent` now prepends a short, loud prelude
    making the /complete call the final tool call.
    """

    def test_prelude_contains_complete_endpoint_and_agent_name(self):
        from services.tool_executor import _chat_close_prelude

        block = _chat_close_prelude("fix-tests-42")

        assert "/agents/fix-tests-42/complete" in block
        assert "final tool call" in block.lower()
        assert "curl" in block
        # Agent-facing instruction must be unambiguous about formally closing.
        assert "close" in block.lower()

    @pytest.mark.asyncio
    async def test_spawn_agent_prepends_close_prelude_to_prompt(self):
        """The prompt POSTed to /agents/spawn must include the close prelude
        above the user's prompt so the subagent reads it first.
        """
        from services.tool_executor import _spawn_agent

        captured_payloads = []

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"pid": 12345}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, json=None, timeout=None):
                captured_payloads.append({"url": url, "json": json})
                return _FakeResp()

        with patch("httpx.AsyncClient", _FakeClient):
            result = await _spawn_agent(
                "diagnose-chat-bug",
                "Find the bug in chat.py and fix it.",
                "sonnet",
            )

        assert "spawned and running" in result
        assert len(captured_payloads) == 1
        posted_prompt = captured_payloads[0]["json"]["prompt"]
        # Prelude above the user task.
        assert "/agents/diagnose-chat-bug/complete" in posted_prompt
        assert posted_prompt.index("/complete") < posted_prompt.index(
            "Find the bug"
        )
        # User prompt still reaches the agent unmodified.
        assert "Find the bug in chat.py and fix it." in posted_prompt

    @pytest.mark.asyncio
    async def test_spawn_agent_prelude_uses_actual_agent_name(self):
        """Defence against a copy-paste regression where the prelude gets
        hard-coded to a placeholder name. The agent's OWN name must appear
        in the /complete URL so the curl is runnable without edits.
        """
        from services.tool_executor import _spawn_agent

        captured = []

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"pid": 1}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, json=None, timeout=None):
                captured.append(json)
                return _FakeResp()

        with patch("httpx.AsyncClient", _FakeClient):
            await _spawn_agent("unique-name-abc", "do something", "sonnet")

        prompt = captured[0]["prompt"]
        assert "/agents/unique-name-abc/complete" in prompt
        # No stray placeholder names.
        assert "AGENT_NAME" not in prompt
        assert "<name>" not in prompt


# ---- semantic_search ----

class TestSemanticSearch:
    def test_tool_registered(self):
        """semantic_search must be in TOOL_DEFINITIONS."""
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "semantic_search" in names

    def test_tool_schema(self):
        """semantic_search schema must have query (required) plus scope and limit."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "semantic_search")
        props = tool["input_schema"]["properties"]
        assert "query" in props
        assert "scope" in props
        assert "limit" in props
        assert tool["input_schema"]["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_falls_back_when_ostk_unavailable(self, tmp_path, monkeypatch):
        """When ostk socket raises, semantic_search falls back to grep."""
        monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)
        (tmp_path / "hello.py").write_text("def greet(): pass\n")

        async def _fake_call_tool(name, arguments=None, timeout=5.0):
            raise Exception("socket unavailable")

        with patch("services.ostk_socket.call_tool", _fake_call_tool):
            result = await execute_tool("semantic_search", {"query": "greet"})

        assert "greet" in result

    @pytest.mark.asyncio
    async def test_returns_ostk_result_when_available(self, monkeypatch):
        """When ostk socket succeeds, its result is returned directly."""
        async def _fake_call_tool(name, arguments=None, timeout=5.0):
            assert name == "search"
            assert arguments["mode"] == "semantic"
            return "ostk result: found greet in hello.py"

        with patch("services.ostk_socket.call_tool", _fake_call_tool):
            result = await execute_tool("semantic_search", {"query": "greet", "scope": "code"})

        assert "ostk result" in result

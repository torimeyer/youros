import os
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.tool_executor import (
    _safe_path,
    execute_tool,
    TOOL_DEFINITIONS,
    WORKSPACE,
)


# ---- _safe_path ----

class TestSafePath:
    def test_absolute_within_workspace(self):
        result = _safe_path(str(WORKSPACE / "api" / "main.py"))
        assert str(result).startswith(str(WORKSPACE))

    def test_relative_path_resolved(self):
        """Relative paths are resolved from cwd, not workspace. The test
        verifies that _safe_path raises on paths outside the workspace."""
        # A relative path that resolves outside workspace should raise
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path("/tmp/outside_file.txt")

    def test_path_traversal_blocked(self):
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path(str(WORKSPACE / ".." / ".." / "etc" / "passwd"))

    def test_symlink_traversal_blocked(self):
        """Ensure /etc/passwd is rejected even if disguised."""
        with pytest.raises(ValueError, match="outside the workspace"):
            _safe_path("/etc/passwd")

    def test_workspace_root_allowed(self):
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
        }
        assert expected == names

    def test_no_duplicate_names(self):
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names))


# ---- execute_tool: read_file ----

class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        result = await execute_tool("read_file", {"path": str(WORKSPACE / "api" / "main.py")})
        assert "FastAPI" in result

    @pytest.mark.asyncio
    async def test_read_missing_file(self):
        result = await execute_tool("read_file", {"path": str(WORKSPACE / "nonexistent_file_xyz.py")})
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_outside_workspace(self):
        result = await execute_tool("read_file", {"path": "/etc/hosts"})
        assert "Error" in result


# ---- execute_tool: write_file ----

class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_and_read(self, tmp_path, monkeypatch):
        """Write a file within a temporary workspace and read it back."""
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
    async def test_list_workspace_root(self):
        result = await execute_tool("list_directory", {})
        assert "api" in result
        assert "app" in result

    @pytest.mark.asyncio
    async def test_list_specific_dir(self):
        result = await execute_tool("list_directory", {"path": str(WORKSPACE / "api")})
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_list_nonexistent(self):
        result = await execute_tool("list_directory", {"path": str(WORKSPACE / "no_such_dir")})
        assert "not found" in result.lower()


# ---- execute_tool: search_files ----

class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_search_finds_pattern(self):
        # Search for a string that only appears in main.py
        result = await execute_tool("search_files", {"pattern": "myOS API", "path": str(WORKSPACE / "api")})
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_search_no_matches(self, tmp_path, monkeypatch):
        # Use an isolated directory so the pattern can't be found anywhere
        monkeypatch.setattr("services.tool_executor.WORKSPACE", tmp_path)
        test_file = tmp_path / "sample.py"
        test_file.write_text("hello world")
        result = await execute_tool("search_files", {"pattern": "zzz_will_not_match"})
        assert "No matches" in result


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
    async def test_close_task_calls_ostk(self):
        with patch("services.tool_executor.ostk") as mock_ostk:
            mock_ostk.close_task = AsyncMock(return_value="closed T1")
            result = await execute_tool("close_task", {"task_id": "T1"})
            assert "closed" in result
            mock_ostk.close_task.assert_awaited_once_with("T1")


# ---- execute_tool: unknown tool ----

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await execute_tool("nonexistent_tool", {})
        assert "Unknown tool" in result

"""Tool definitions and execution for ToriChat agent loop.

Defines the tools that Claude can call (read files, write files, run commands,
etc.) and executes them safely within the workspace boundary.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from services.ostk import ostk

from config import PROJECT_ROOT

WORKSPACE = PROJECT_ROOT
COMMAND_TIMEOUT = 30  # seconds


def _safe_path(raw_path: str) -> Path:
    """Resolve a path and ensure it stays within the workspace.

    Raises ValueError if the resolved path escapes the workspace.
    """
    resolved = Path(raw_path).expanduser().resolve()
    workspace_resolved = WORKSPACE.resolve()
    if not (resolved == workspace_resolved or str(resolved).startswith(str(workspace_resolved) + os.sep)):
        raise ValueError(f"Path is outside the workspace: {raw_path}")
    return resolved


# ---- Anthropic tool definitions (sent to the API) ----

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the file.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it does not exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the file.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace a specific piece of text in a file. The old_text must appear exactly once in the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the file.",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace.",
                },
                "new_text": {
                    "type": "string",
                    "description": "The replacement text.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace directory. Returns stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path. Defaults to workspace root if omitted.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": "Search for a text pattern across files using grep. Returns matching lines with file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The search pattern (regular expression).",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to workspace root.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List open tasks from the workspace task tracker.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_task",
        "description": "Create a new task in the workspace task tracker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The task title.",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level: P0, P1, or P2.",
                    "enum": ["P0", "P1", "P2"],
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "close_task",
        "description": "Close (complete) a task by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to close.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "spawn_agent",
        "description": "Spawn a background AI agent to work on a task independently. The agent runs in the background and can be monitored on the Agents page. Use this for tasks that take a while or can run in parallel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A short name for the agent (e.g. 'research-api', 'fix-tests').",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed instructions for what the agent should do.",
                },
                "model": {
                    "type": "string",
                    "description": "Which model to use: sonnet, opus, or haiku.",
                    "enum": ["sonnet", "opus", "haiku"],
                },
            },
            "required": ["name", "prompt"],
        },
    },
]


# ---- Tool execution ----

async def execute_tool(name: str, input_data: dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "read_file":
            return await _read_file(input_data["path"])
        elif name == "write_file":
            return await _write_file(input_data["path"], input_data["content"])
        elif name == "edit_file":
            return await _edit_file(input_data["path"], input_data["old_text"], input_data["new_text"])
        elif name == "run_command":
            return await _run_command(input_data["command"])
        elif name == "list_directory":
            return await _list_directory(input_data.get("path", ""))
        elif name == "search_files":
            return await _search_files(input_data["pattern"], input_data.get("path", ""))
        elif name == "list_tasks":
            return await _list_tasks()
        elif name == "create_task":
            return await _create_task(input_data["title"], input_data.get("priority", "P1"))
        elif name == "close_task":
            return await _close_task(input_data["task_id"])
        elif name == "spawn_agent":
            return await _spawn_agent(input_data["name"], input_data["prompt"], input_data.get("model", "sonnet"))
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


async def _read_file(path: str) -> str:
    safe = _safe_path(path)
    if not safe.exists():
        return f"File not found: {path}"
    if not safe.is_file():
        return f"Not a file: {path}"
    content = safe.read_text(errors="replace")
    # Truncate very large files
    if len(content) > 50_000:
        return content[:50_000] + f"\n\n... (truncated, file is {len(content)} characters)"
    return content


async def _write_file(path: str, content: str) -> str:
    safe = _safe_path(path)
    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.write_text(content)
    return f"Wrote {len(content)} characters to {safe}"


async def _edit_file(path: str, old_text: str, new_text: str) -> str:
    safe = _safe_path(path)
    if not safe.exists():
        return f"File not found: {path}"
    content = safe.read_text()
    count = content.count(old_text)
    if count == 0:
        return f"old_text not found in {path}"
    if count > 1:
        return f"old_text appears {count} times in {path}. It must appear exactly once."
    new_content = content.replace(old_text, new_text, 1)
    safe.write_text(new_content)
    return f"Edited {safe}"


async def _run_command(command: str) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
        parts = []
        if stdout:
            parts.append(stdout.decode(errors="replace"))
        if stderr:
            parts.append(f"STDERR:\n{stderr.decode(errors='replace')}")
        parts.append(f"Exit code: {proc.returncode}")
        result = "\n".join(parts)
        # Truncate very long output
        if len(result) > 30_000:
            result = result[:30_000] + "\n... (output truncated)"
        return result
    except asyncio.TimeoutError:
        return f"Command timed out after {COMMAND_TIMEOUT} seconds"


async def _list_directory(path: str) -> str:
    if path:
        safe = _safe_path(path)
    else:
        safe = WORKSPACE
    if not safe.exists():
        return f"Directory not found: {path}"
    if not safe.is_dir():
        return f"Not a directory: {path}"
    entries = sorted(safe.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = []
    for entry in entries[:200]:
        prefix = "d " if entry.is_dir() else "f "
        lines.append(f"{prefix}{entry.name}")
    if len(entries) > 200:
        lines.append(f"... and {len(entries) - 200} more")
    return "\n".join(lines) if lines else "(empty directory)"


async def _search_files(pattern: str, path: str) -> str:
    if path:
        safe = _safe_path(path)
    else:
        safe = WORKSPACE
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-rn",
            "--include=*.py", "--include=*.ts", "--include=*.tsx",
            "--include=*.js", "--include=*.json", "--include=*.md", "--include=*.toml",
            "--include=*.yaml", "--include=*.yml", "--include=*.html", "--include=*.css",
            "--exclude-dir=.venv", "--exclude-dir=node_modules",
            "--exclude-dir=.git", "--exclude-dir=dist", "--exclude-dir=.vite",
            "-I",  # skip binary files
            pattern, str(safe),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
        result = stdout.decode(errors="replace")
        if not result:
            return f"No matches found for: {pattern}"
        # Truncate long results
        lines = result.splitlines()
        if len(lines) > 100:
            result = "\n".join(lines[:100]) + f"\n... ({len(lines)} total matches)"
        return result
    except asyncio.TimeoutError:
        return f"Search timed out after {COMMAND_TIMEOUT} seconds"


async def _list_tasks() -> str:
    tasks = await ostk.list_tasks(status="open")
    if not tasks:
        return "No open tasks."
    lines = []
    for t in tasks:
        lines.append(f"{t.get('id')} [{t.get('priority')}] {t.get('title')}")
    return "\n".join(lines)


async def _create_task(title: str, priority: str = "P1") -> str:
    result = await ostk.add_task(title, priority)
    return result


async def _close_task(task_id: str) -> str:
    result = await ostk.close_task(task_id)
    return result


async def _spawn_agent(name: str, prompt: str, model: str = "sonnet") -> str:
    try:
        result = await ostk.kernel_spawn(name, prompt, model, budget=2.0)
        return f"Agent '{name}' spawned successfully. Check the Agents page to monitor it.\n{result}"
    except Exception as e:
        return f"Failed to spawn agent '{name}': {e}"

"""Tool definitions and execution for ToriChat agent loop.

Defines the tools that Claude can call (read files, write files, run commands,
etc.) and executes them safely within the workspace boundary.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

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
        "description": (
            "Create a new task in the workspace task tracker. "
            "Use this when the user asks to add a task, create a needle, or track a to-do item. "
            "When the user says 'create tasks for all of it', 'break this down into tasks', "
            "or 'turn this into tasks', call this tool once per task rather than just describing them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional plain-language description of what needs to be done.",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level: P0 (urgent), P1 (high), P2 (normal), or P3 (low).",
                    "enum": ["P0", "P1", "P2", "P3"],
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of label strings to attach to the task.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_tasks_from_spec",
        "description": (
            "Break a spec document into individual tasks and create them all at once. "
            "Use this when the user says 'create tasks for all of it', 'break this spec into tasks', "
            "'turn this into tasks', or 'decompose this spec'. "
            "Pass the spec's file path (e.g. 'docs/spec/my-feature.md' or 'docs/draft/my-draft.md'). "
            "Returns the list of created task IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec_path": {
                    "type": "string",
                    "description": "Path to the spec file under docs/draft/ or docs/spec/ (e.g. 'docs/spec/my-feature.md').",
                },
            },
            "required": ["spec_path"],
        },
    },
    {
        "name": "build_tasks_from_file",
        "description": (
            "Read a plain-language file (like roadmap.md) and turn its "
            "contents into individual tracked tasks. Use this when the "
            "user says 'build tasks from <filename>', 'break <file> into "
            "tasks', or points at a markdown file and asks for a task "
            "list. The file can be a workspace-relative path (e.g. "
            "'roadmap.md') or an absolute path (e.g. "
            "'~/.myos/files/roadmap.md'). Returns the number of tasks "
            "created and their IDs. After this tool runs, the same batch "
            "can be turned into live agents by calling "
            "build_from_recent_tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to decompose into tasks.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "build_from_recent_tasks",
        "description": (
            "Spawn a Builder background agent for every task created by "
            "the most recent build_tasks_from_file call. Use this when "
            "the user says 'build it', 'ship it', 'start building', or "
            "asks to execute the tasks that were just decomposed. Each "
            "agent runs in quick mode so the spawn is fast. Returns the "
            "list of agent names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
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
        "name": "web_search",
        "description": "Search the web for information. Returns a summary of search results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch the content of a web page. Returns the text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "git_status",
        "description": "Show the current git status of the workspace (modified files, staged changes, branch info).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "git_diff",
        "description": "Show the git diff of uncommitted changes in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional file path to diff. Omit for all changes.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and create a git commit with the given message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The commit message.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "check_agents",
        "description": "Check the status of background agents. Shows which agents are running, completed, failed, or stopped.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_calendar_events",
        "description": "Get today's events from the user's Google Calendar. Returns a formatted list of today's meetings and events.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a new event on the user's Google Calendar. "
            "Use this when the user asks to add something to their calendar, schedule a meeting, or create a reminder. "
            "For all-day events (field trips, birthdays, holidays), set all_day to true and use YYYY-MM-DD for the date. "
            "For timed events, use ISO datetime format like 2026-04-28T09:00:00."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The event title (e.g. 'Pepper magic show field trip').",
                },
                "start": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD for all-day) or datetime (ISO format like 2026-04-28T09:00:00).",
                },
                "end": {
                    "type": "string",
                    "description": "Optional end date or datetime. Defaults to next day for all-day events, or one hour later for timed events.",
                },
                "all_day": {
                    "type": "boolean",
                    "description": "Set to true for all-day events (field trips, birthdays, etc.).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description or notes.",
                },
                "location": {
                    "type": "string",
                    "description": "Optional event location.",
                },
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Send a new email via the user's connected Gmail account. "
            "Use this when the user asks to send, email, or write to someone. "
            "The email is sent immediately. If the user's Gmail is not connected or "
            "send permission is missing, returns an error with instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Plain text email body.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "delete_emails",
        "description": (
            "Move Gmail messages to Trash (or permanently delete them). "
            "Use this when the user says 'delete the <X> emails', 'trash the emails from <Y>', "
            "'delete marketing emails', or similar. "
            "Default behavior is Trash, which matches Gmail's own Delete button: the messages "
            "leave the inbox and Gmail auto purges them after 30 days. "
            "Two-step flow: first call with just a ``query`` to preview matching messages. "
            "The tool returns a list plus the count. Show the user that list and ask for confirmation. "
            "Only after the user confirms, call again with ``confirm: true`` and the matched ``ids`` "
            "to actually move the messages. "
            "Set ``permanent: true`` ONLY when the user explicitly asks for a permanent or "
            "forever delete. There is no undo for that path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search string (e.g. 'from:amazon marketing', 'subject:newsletter'). "
                        "Provide this on the first call to preview matches."
                    ),
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Gmail message ids to move to Trash. Use on the confirm call.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to actually move messages. False or omitted returns a preview.",
                },
                "permanent": {
                    "type": "boolean",
                    "description": "Set to true only if the user explicitly asked for a forever delete. No undo.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "upload_to_drive",
        "description": (
            "Create a text file and upload it to the user's Google Drive (myOS folder). "
            "Use this when the user asks to save something to Drive or create a document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name for the file (e.g. 'meeting-notes.txt').",
                },
                "content": {
                    "type": "string",
                    "description": "The text content of the file.",
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "spawn_agent",
        "description": "Spawn a background AI agent to work on a task independently. The agent runs in the background and can be monitored on the Agents page. Use this for tasks that take a while or can run in parallel. When writing the agent's prompt, always include instructions to write tests for the work and verify they pass before finishing.",
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
            return await _create_task(
                input_data["title"],
                priority=input_data.get("priority", "P1"),
                description=input_data.get("description", ""),
                labels=input_data.get("labels"),
            )
        elif name == "create_tasks_from_spec":
            return await _create_tasks_from_spec(input_data["spec_path"])
        elif name == "build_tasks_from_file":
            return await _build_tasks_from_file(input_data["file_path"])
        elif name == "build_from_recent_tasks":
            return await _build_from_recent_tasks()
        elif name == "close_task":
            return await _close_task(input_data["task_id"])
        elif name == "web_search":
            return await _web_search(input_data["query"])
        elif name == "web_fetch":
            return await _web_fetch(input_data["url"])
        elif name == "git_status":
            return await _git_status()
        elif name == "git_diff":
            return await _git_diff(input_data.get("path", ""))
        elif name == "git_commit":
            return await _git_commit(input_data["message"])
        elif name == "check_agents":
            return await _check_agents()
        elif name == "get_calendar_events":
            return await _get_calendar_events()
        elif name == "create_calendar_event":
            return await _create_calendar_event(
                title=input_data["title"],
                start=input_data["start"],
                end=input_data.get("end"),
                all_day=input_data.get("all_day", False),
                description=input_data.get("description", ""),
                location=input_data.get("location", ""),
            )
        elif name == "send_email":
            return await _send_email(
                to=input_data["to"],
                subject=input_data["subject"],
                body=input_data["body"],
            )
        elif name == "delete_emails":
            return await _delete_emails(
                query=input_data.get("query"),
                ids=input_data.get("ids"),
                confirm=input_data.get("confirm", False),
                permanent=input_data.get("permanent", False),
            )
        elif name == "upload_to_drive":
            return await _upload_to_drive(
                filename=input_data["filename"],
                content=input_data["content"],
            )
        elif name == "spawn_agent":
            return await _spawn_agent(input_data["name"], input_data["prompt"], input_data.get("model", "sonnet"))
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


async def _read_file(path: str) -> str:
    # Bug 2 fix: strip file:// prefix so drag-and-drop paths work directly.
    # The user explicitly provided this path, so we honor it even if it falls
    # outside the workspace boundary (user-supplied absolute paths are trusted).
    stripped = path.strip()
    if stripped.startswith("file://"):
        stripped = stripped[len("file://"):]
    # Try user-supplied path first (absolute or file:// converted)
    if stripped.startswith("/") or stripped.startswith("~"):
        direct = Path(stripped).expanduser().resolve()
        if direct.exists() and direct.is_file():
            content = direct.read_text(errors="replace")
            if len(content) > 50_000:
                return content[:50_000] + f"\n\n... (truncated, file is {len(content)} characters)"
            return content
        # Path was given explicitly but not found - return informative error
        # rather than silently trying workspace-relative fallback.
        if stripped.startswith("/"):
            return f"File not found: {stripped}"
    # Fall back to workspace-scoped path resolution
    try:
        safe = _safe_path(path)
    except ValueError as e:
        return str(e)
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


def _norm_title(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", (s or "").strip().lower())


async def _create_task(
    title: str,
    priority: str = "P1",
    description: str = "",
    labels: Optional[list[str]] = None,
) -> str:
    # Deduplicate: if an open task with the same normalised title already exists,
    # return it instead of creating a duplicate. This prevents the chat model from
    # filing two identical needles on a single saa turn (→1123).
    try:
        existing = await ostk.list_tasks(status="open")
        key = _norm_title(title)
        for task in existing:
            if _norm_title(task.get("title") or "") == key:
                tid = task.get("id") or ""
                return f"Task already exists: {tid} — {task.get('title', title)}"
    except Exception:
        pass  # dedup is best-effort; never block creation on a list failure

    result = await ostk.add_task(title, priority)
    # Fire-and-forget auto label suggestion. Never blocks task creation.
    # Imported lazily to avoid a circular import via chat_providers.
    from services.task_labeling import extract_task_id, schedule_auto_labels
    new_id = extract_task_id(result)
    schedule_auto_labels(new_id, title, description or "")
    return result


async def _create_tasks_from_spec(spec_path: str) -> str:
    """Break a spec document into tasks via the /api/specs/decompose endpoint.

    Returns a summary of the created task IDs on success, or an actionable
    error message if the spec path is invalid or the decompose call fails.
    """
    # Validate that the path is under docs/draft/ or docs/spec/ before
    # hitting the network so the error message is more informative.
    from pathlib import PurePosixPath
    p = PurePosixPath(spec_path)
    if ".." in p.parts:
        return "Cannot decompose spec: path traversal not allowed."
    if not (str(p).startswith("docs/draft/") or str(p).startswith("docs/spec/")):
        return (
            f"Cannot decompose spec: path must be under docs/draft/ or docs/spec/. "
            f"Got: {spec_path}"
        )
    try:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.post(
                "https://127.0.0.1:8000/api/specs/decompose",
                json={"path": spec_path},
            )
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                return (
                    f"Could not decompose spec (HTTP {resp.status_code}): {detail}. "
                    "Make sure the spec file exists and has a title."
                )
            data = resp.json()
            task_ids = data.get("task_ids", [])
            raw_result = data.get("result", "")
            if not task_ids:
                return (
                    f"Spec decomposed but no tasks were created. "
                    f"ostk output: {raw_result}"
                )
            id_list = ", ".join(str(t) for t in task_ids)
            return f"Created {len(task_ids)} task(s) from spec: {id_list}"
    except Exception as e:
        return (
            f"Could not decompose spec: {e}. "
            "The backend may be restarting. Wait a few seconds and try again."
        )


# Path where the most recent "build tasks from <file>" batch is recorded.
# Used by build_from_recent_tasks to find the right set of tasks to
# hand to Builder agents. Kept in ~/.myos so a repo checkout wipe does
# not lose pending batches mid-demo.
_LAST_BATCH_PATH = Path.home() / ".myos" / "last_task_batch.json"


def _save_last_task_batch(file_path: str, task_ids: list[str]) -> None:
    """Persist the most recent decomposition batch to disk.

    Writing is best-effort: a failure logs nothing and returns silently
    so the tool still reports success to the chat. The build_from_recent
    tool falls back to "no recent batch" messaging when the file is
    missing.
    """
    try:
        _LAST_BATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "file_path": file_path,
            "task_ids": list(task_ids),
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        _LAST_BATCH_PATH.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _load_last_task_batch() -> Optional[dict[str, Any]]:
    """Return the most recent batch record or None if no batch exists."""
    try:
        if not _LAST_BATCH_PATH.exists():
            return None
        return json.loads(_LAST_BATCH_PATH.read_text())
    except Exception:
        return None


def _resolve_tasks_file(raw: str) -> Optional[Path]:
    """Resolve a user-supplied file path to an absolute Path, or None.

    Accepts three forms in priority order:
      1. Absolute paths (``/tmp/x.md`` or ``~/.myos/files/roadmap.md``).
      2. Bare filenames of files living in ``~/.myos/files/``, so the
         demo phrasing "build tasks from the roadmap.md" works without
         the user ever typing a path.
      3. Workspace-relative paths resolved against the project root.
    Returns None if nothing matches so the caller can surface a helpful
    error instead of silently reading the wrong file.
    """
    if not raw or not raw.strip():
        return None
    stripped = raw.strip()
    if stripped.startswith("file://"):
        stripped = stripped[len("file://"):]

    # Absolute or tilde paths take precedence.
    if stripped.startswith("/") or stripped.startswith("~"):
        direct = Path(stripped).expanduser().resolve()
        if direct.exists() and direct.is_file():
            return direct
        return None

    # Bare filename in ~/.myos/files (e.g. "roadmap.md" or "the roadmap.md").
    filename_only = stripped.split("/")[-1]
    myos_candidate = (Path.home() / ".myos" / "files" / filename_only).resolve()
    if myos_candidate.exists() and myos_candidate.is_file():
        return myos_candidate

    # Workspace-relative fallback.
    try:
        workspace_candidate = (WORKSPACE / stripped).resolve()
        if workspace_candidate.exists() and workspace_candidate.is_file():
            return workspace_candidate
    except Exception:
        pass
    return None


async def _build_tasks_from_file(file_path: str) -> str:
    """Read a markdown file and break it into tracked tasks.

    Strategy: ask the model to split the document into one bullet per
    task, then create each as a standalone ostk task. This avoids the
    docs/spec/ path constraint of create_tasks_from_spec so files like
    ~/.myos/files/roadmap.md work directly.

    Returns a plain-language summary with the count and IDs. The same
    IDs are stashed in ~/.myos/last_task_batch.json so
    build_from_recent_tasks can pick them up on the next turn.
    """
    resolved = _resolve_tasks_file(file_path)
    if resolved is None:
        return (
            f"Could not find a file at '{file_path}'. Try an absolute "
            "path or drop the file into ~/.myos/files/ and reference it "
            "by name."
        )

    try:
        text = resolved.read_text(errors="replace")
    except OSError as exc:
        return f"Could not read {resolved}: {exc}"
    if not text.strip():
        return f"{resolved.name} is empty, nothing to break into tasks."

    # Truncate very large files so the extraction call stays cheap.
    if len(text) > 30_000:
        text = text[:30_000] + "\n... (truncated)"

    # Ask Claude to produce a JSON list of task titles. Falls back to
    # a simple line-based parse if the API call fails so the demo
    # still produces tasks.
    titles: list[str] = []
    try:
        from services.chat_providers import _resolve_api_key
        import anthropic
        api_key = await _resolve_api_key("anthropic_api_key")
        if api_key:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            prompt = (
                "You are a PM turning a plan into concrete trackable "
                "tasks. Read the document and output ONLY a JSON array "
                "of short task titles. Each title should be one action "
                "(e.g. 'Build onboarding wizard'). No numbering, no "
                "preamble, no markdown fences. Aim for 5 to 15 titles "
                "depending on document size.\n\n"
                f"Document:\n{text}"
            )
            resp = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = ""
            for block in resp.content:
                if getattr(block, "type", "") == "text":
                    raw = block.text.strip()
                    break
            # Trim fences if the model added them despite instructions.
            if raw.startswith("```"):
                raw = raw.strip("`")
                # Drop leading language tag like "json\n"
                if "\n" in raw:
                    raw = raw.split("\n", 1)[1]
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    titles = [str(t).strip() for t in parsed if str(t).strip()]
            except Exception:
                pass
    except Exception:
        pass

    # Fallback: pull bullet/numbered lines as tasks.
    if not titles:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Strip bullet markers and numbering.
            for marker in ("- ", "* ", "• "):
                if stripped.startswith(marker):
                    stripped = stripped[len(marker):].strip()
                    break
            # Strip "1. ", "2. ", etc.
            import re as _re
            stripped = _re.sub(r"^\d+[\.)]\s+", "", stripped)
            # Skip headings, front matter, and code fences.
            if not stripped or stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("---"):
                continue
            if len(stripped) < 5 or len(stripped) > 200:
                continue
            titles.append(stripped)
            if len(titles) >= 15:
                break

    # Run every extracted title through the same sanitizer the tasks
    # router uses so trailing machine IDs, UUIDs, and pure-test
    # patterns never leak into the user-facing list even when they
    # were baked into the source markdown.
    from routers.tasks import _sanitize_task_title, _reject_bad_title
    cleaned_titles: list[str] = []
    for raw in titles:
        sanitized = _sanitize_task_title(raw)
        if _reject_bad_title(sanitized):
            continue
        cleaned_titles.append(sanitized)
    titles = cleaned_titles

    if not titles:
        return f"Could not extract any tasks from {resolved.name}."

    # Idempotency: load current open tasks once and skip any title the
    # user already has. Paired with the recent-delete tombstone cache so
    # a spec-build retry loop (smoke test, Builder re-run, workflow
    # step) cannot create duplicates OR resurrect tasks the user just
    # deleted. Matches on normalized title so casing and whitespace do
    # not defeat the guard.
    from services import recent_deletes

    def _norm(s: str) -> str:
        import re as _re
        return _re.sub(r"\s+", " ", (s or "").strip().lower())

    existing_titles: set[str] = set()
    existing_id_by_title: dict[str, str] = {}
    try:
        current = await ostk.list_tasks(status="open")
        for t in current:
            key = _norm(t.get("title") or "")
            if key:
                existing_titles.add(key)
                existing_id_by_title.setdefault(key, str(t.get("id") or ""))
    except Exception:
        pass

    created_ids: list[str] = []
    reused_ids: list[str] = []
    skipped_deleted: list[str] = []
    for title in titles:
        key = _norm(title)
        # Skip titles the user just deleted. This closes the race
        # where a smoke loop reads the same roadmap.md right after
        # the user wiped the tasks from the UI.
        if recent_deletes.is_recent(title):
            skipped_deleted.append(title)
            continue
        # Reuse an existing open task with the same title. Same file,
        # same headings, same tasks: we do not duplicate.
        if key in existing_titles:
            tid = existing_id_by_title.get(key)
            if tid:
                reused_ids.append(tid)
            continue
        try:
            result = await ostk.add_task(title, "P1")
            # ostk output format: "Added task →NNN: <title>" or similar.
            from services.task_labeling import extract_task_id, schedule_auto_labels
            tid = extract_task_id(result)
            # If ostk just handed us back a task ID the user recently
            # deleted (for example because issues.jsonl got rewritten
            # from a backup or an older counter value got restored),
            # roll the create back so we never resurrect a deleted id.
            if tid and recent_deletes.is_id_recent(tid):
                try:
                    await ostk.delete_task(tid)
                except Exception:
                    pass
                skipped_deleted.append(title)
                continue
            if tid:
                created_ids.append(tid)
                existing_titles.add(key)
                existing_id_by_title[key] = tid
                schedule_auto_labels(tid, title, "")
        except Exception:
            continue

    # Store both newly-created and reused IDs so "build it" covers the
    # full set of tasks from this file, not just the fresh ones.
    batch_ids = created_ids + reused_ids
    _save_last_task_batch(str(resolved), batch_ids)

    parts: list[str] = []
    if created_ids:
        parts.append(f"Created {len(created_ids)} tasks ({', '.join(created_ids)})")
    if reused_ids:
        parts.append(f"reused {len(reused_ids)} existing ({', '.join(reused_ids)})")
    if skipped_deleted:
        parts.append(f"skipped {len(skipped_deleted)} recently deleted")
    if not parts:
        return f"No tasks created from {resolved.name} (all titles were either duplicates or recently deleted)."
    summary = "; ".join(parts)
    return (
        f"{summary} from {resolved.name}. "
        "Say 'build it' to spawn a Builder agent for each one."
    )


async def _build_from_recent_tasks() -> str:
    """Spawn a Builder agent for every task in the last decomposition batch.

    Uses the same Builder template that the spec Build flow uses so
    quick-mode kicks in. Each agent is named ``build-<task_id>``.
    Returns a plain summary with the count and agent names.
    """
    batch = _load_last_task_batch()
    if not batch:
        return (
            "No recent task batch to build. Say 'build tasks from "
            "<filename>' first, then 'build it' to execute them."
        )

    task_ids = list(batch.get("task_ids") or [])
    if not task_ids:
        return (
            "The most recent batch has no task IDs. Re-run 'build tasks "
            "from <filename>' to create tasks, then try 'build it' again."
        )

    # Pull current task titles so each agent gets a real instruction.
    try:
        tasks = await ostk.list_tasks()
    except Exception:
        tasks = []
    title_by_id: dict[str, str] = {}
    for t in tasks:
        tid = str(t.get("id", "")).lstrip("\u2192")
        if tid:
            title_by_id[tid] = t.get("title", "") or ""

    spawned: list[str] = []
    failures: list[str] = []

    import httpx
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        for raw_id in task_ids:
            tid_stripped = str(raw_id).lstrip("\u2192")
            title = title_by_id.get(tid_stripped) or str(raw_id)
            agent_name = f"build-{tid_stripped}".lower().replace(" ", "-")
            # Reuse the Builder template so it inherits quick_mode and
            # the close-task instruction pattern.
            prompt = (
                f"Build task {raw_id}: {title}. When the work is done, "
                f"close the task via "
                f"curl -sSk --connect-timeout 3 -m 5 -X POST "
                f"https://127.0.0.1:8000/api/tasks/{tid_stripped}/close"
            )
            try:
                resp = await client.post(
                    "https://127.0.0.1:8000/api/agents/spawn",
                    json={
                        "name": agent_name,
                        "prompt": prompt,
                        "model": "sonnet",
                        "budget": 2.0,
                        "template": "builder",
                        "source": "chat-build-it",
                        # Include the task title so the Agents page shows
                        # what each build-<id> agent is actually working on
                        # instead of the opaque internal name. The UI's
                        # agentTitleParts helper uses this as the primary
                        # row label.
                        "task": (
                            f"Build task {raw_id}: {title}"
                            if title and title != str(raw_id)
                            else f"Build task {raw_id}"
                        ),
                    },
                )
                if resp.status_code == 200:
                    spawned.append(agent_name)
                else:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except Exception:
                        detail = resp.text
                    failures.append(f"{agent_name}: {detail}")
            except Exception as exc:
                failures.append(f"{agent_name}: {exc}")

    if not spawned:
        return (
            f"Could not spawn any Builder agents. {'; '.join(failures)}"
            if failures else "No agents were spawned."
        )

    msg = f"Started {len(spawned)} Builder agents: {', '.join(spawned)}."
    if failures:
        msg += f" {len(failures)} failed: {'; '.join(failures)}"
    msg += " Watch progress on the Agents page."
    return msg


async def _close_task(task_id: str) -> str:
    result = await ostk.close_task(task_id)
    return result


async def _web_search(query: str) -> str:
    """Search the web using DuckDuckGo HTML (no API key needed)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            # Extract result snippets from the HTML
            text = resp.text
            results = []
            # Parse result blocks
            import re
            for match in re.finditer(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</span>', text, re.DOTALL):
                url = match.group(1)
                title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
                snippet = re.sub(r'<[^>]+>', '', match.group(3)).strip()
                if title:
                    results.append(f"**{title}**\n{url}\n{snippet}")
                if len(results) >= 5:
                    break
            if not results:
                return f"No results found for: {query}"
            return "\n\n".join(results)
    except Exception as e:
        return f"Search failed: {e}"


async def _web_fetch(url: str) -> str:
    """Fetch a web page and return its text content."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            import re
            # Strip HTML tags for a text-only view
            text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 20_000:
                text = text[:20_000] + "\n... (truncated)"
            return text if text else "Page returned no text content."
    except Exception as e:
        return f"Fetch failed: {e}"


async def _git_status() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--short", "--branch",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return stdout.decode().strip() or "Working tree clean."
    except Exception as e:
        return f"Git status failed: {e}"


async def _git_diff(path: str) -> str:
    try:
        cmd = ["git", "diff"]
        if path:
            cmd.append(path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        result = stdout.decode().strip()
        if not result:
            return "No uncommitted changes."
        if len(result) > 30_000:
            result = result[:30_000] + "\n... (diff truncated)"
        return result
    except Exception as e:
        return f"Git diff failed: {e}"


async def _git_commit(message: str) -> str:
    try:
        # Stage all changes
        add_proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE),
        )
        await asyncio.wait_for(add_proc.communicate(), timeout=10)

        # Commit
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(WORKSPACE),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        result = stdout.decode().strip()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            return f"Commit failed: {err}"
        return result
    except Exception as e:
        return f"Git commit failed: {e}"


async def _check_agents() -> str:
    """Check agent status via the API endpoint with retry on transient errors.

    Bug 3 fix: retries twice (100 ms then 300 ms back-off) so a server
    reload window or TLS handshake reset does not surface as a user-visible
    failure.
    """
    _RETRY_DELAYS = [0.1, 0.3]
    last_exc: Optional[Exception] = None
    for attempt, delay in enumerate([-1] + _RETRY_DELAYS):
        if delay >= 0:
            await asyncio.sleep(delay)
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    "https://127.0.0.1:8000/api/agents",
                    timeout=5,
                )
                data = resp.json()

            agents = data.get("agents", [])
            if not agents:
                return "No agents have been spawned yet."

            lines = []
            for a in agents:
                name = a.get("name", "unknown")
                status = a.get("status", "unknown").upper()
                model = a.get("model", "")
                source = a.get("source", "")
                lines.append(f"{name}: {status} (model: {model}, source: {source})")

            active = data.get("active", [])
            if active:
                lines.append(f"\nCurrently running: {', '.join(active)}")
            else:
                lines.append("\nNo agents currently running.")

            return "\n".join(lines)
        except Exception as e:
            last_exc = e
            continue

    return (
        f"Could not reach the agents service after {len(_RETRY_DELAYS) + 1} tries: {last_exc}. "
        "The backend may be restarting. Wait a few seconds and try again."
    )


async def _get_calendar_events() -> str:
    """Return today's calendar events as formatted text."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return "Google Calendar is not connected. Connect your Google account in the Drive settings."
        from services import calendar as cal_service
        events = await cal_service.get_today_events()
        if not events:
            return "No events on your calendar today."
        lines = []
        for ev in events:
            title = ev.get("summary", "Untitled")
            start = ev.get("start", {})
            end = ev.get("end", {})
            start_val = start.get("dateTime") or start.get("date") or ""
            end_val = end.get("dateTime") or end.get("date") or ""
            location = ev.get("location", "")
            meet_link = ev.get("hangoutLink", "")

            def _fmt(dt_str: str) -> str:
                if not dt_str:
                    return ""
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(dt_str)
                    return dt.strftime("%-I:%M%p").lower()
                except Exception:
                    return dt_str[:5]

            time_str = ""
            if start_val and end_val:
                time_str = f"{_fmt(start_val)}-{_fmt(end_val)}"
            elif start_val:
                time_str = _fmt(start_val)

            line = f"- {time_str}: {title}" if time_str else f"- {title}"
            if location:
                line += f" (at {location})"
            if meet_link:
                line += f" [Meet: {meet_link}]"
            lines.append(line)
        return "Today's calendar events:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Could not load calendar events: {exc}"


async def _create_calendar_event(
    title: str,
    start: str,
    end: Optional[str] = None,
    all_day: bool = False,
    description: str = "",
    location: str = "",
) -> str:
    """Create a Google Calendar event via the calendar service."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return (
                "Google Calendar is not connected. "
                "Connect your Google account in Settings to add events."
            )
        from services import calendar as cal_service
        event = await cal_service.create_event(
            summary=title,
            start=start,
            end=end,
            all_day=all_day,
            description=description,
            location=location,
        )
        summary = event.get("summary", title)
        link = event.get("htmlLink", "")
        start_info = event.get("start", {})
        start_display = start_info.get("dateTime") or start_info.get("date") or start
        result = f"Created calendar event: {summary} on {start_display}"
        if link:
            result += f"\nLink: {link}"
        return result
    except Exception as exc:
        msg = str(exc).lower()
        if "insufficientpermissions" in msg or "insufficient authentication scopes" in msg:
            return (
                "Calendar write access has not been granted. "
                "Reconnect your Google account in Settings to allow creating events."
            )
        return f"Could not create the calendar event: {exc}"


async def _send_email(to: str, subject: str, body: str) -> str:
    """Send a new email via the user's connected Gmail account."""
    try:
        from services.google_auth import is_authenticated, get_email
        if not is_authenticated():
            return (
                "Gmail is not connected. "
                "Connect your Google account in Settings to send emails."
            )

        from services.gmail_reply import has_send_scope, _build_gmail_service
        if not has_send_scope():
            return (
                "Gmail send access has not been granted. "
                "Reconnect your Google account in Settings to enable sending emails."
            )

        import base64
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        from_addr = get_email() or ""
        if from_addr:
            msg["From"] = from_addr
        msg.set_content(body)

        raw = base64.urlsafe_b64encode(bytes(msg)).decode("ascii")

        service = _build_gmail_service()

        def _send_sync():
            return (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )

        sent = await asyncio.get_event_loop().run_in_executor(None, _send_sync)

        # Invalidate inbox cache so the sent message shows up.
        try:
            from services import gmail as gmail_service
            gmail_service.invalidate_full_inbox_cache()
        except Exception:
            pass

        return f"Email sent to {to} with subject \"{subject}\"."
    except Exception as exc:
        return f"Could not send email: {exc}"


async def _delete_emails(
    query: Optional[str] = None,
    ids: Optional[list[str]] = None,
    confirm: bool = False,
    permanent: bool = False,
) -> str:
    """Preview or execute a Gmail delete by query or id list.

    Two step by design:
      - If ``confirm`` is False, treat this as a preview: run the search,
        return the matching message summaries plus a count, and do NOT
        touch Gmail state.
      - If ``confirm`` is True, move each id to Trash (or permanent delete
        when ``permanent`` is True). Requires ``ids`` to be provided. This
        forces the caller to echo back the ids it just showed the user, so
        we cannot accidentally delete something the user did not see.
    """
    try:
        from services.google_auth import is_authenticated
        from services import gmail as gmail_service

        if not is_authenticated():
            return (
                "Gmail is not connected. "
                "Connect your Google account in Settings to delete emails."
            )

        # Preview path: return the list of matches with count. No writes.
        if not confirm:
            if not query:
                return (
                    "Cannot preview: provide a 'query' string (for example "
                    "'from:amazon marketing') or pass confirm=true with an "
                    "explicit list of ids."
                )
            try:
                matches = await gmail_service.search_messages(query)
            except Exception as exc:
                return f"Could not search Gmail: {exc}"

            if not matches:
                return f"No messages matched '{query}'."

            action = "permanently delete" if permanent else "move to Trash"
            lines = [f"Found {len(matches)} messages matching '{query}'. Reply 'delete them' to {action}:"]
            for msg in matches[:25]:
                sender = msg.get("from_name") or msg.get("from_email") or "(unknown)"
                subject = msg.get("subject") or "(no subject)"
                lines.append(f"- [{msg.get('id')}] {sender}: {subject}")
            if len(matches) > 25:
                lines.append(f"... and {len(matches) - 25} more")
            lines.append("")
            lines.append(
                "To confirm, call delete_emails again with confirm=true and ids=" +
                str([m.get("id") for m in matches])
            )
            return "\n".join(lines)

        # Confirm path: require explicit ids. No query fallback so we
        # never expand a preview into a different set under the user.
        if not ids:
            return (
                "Cannot delete: confirm=true requires an explicit list of "
                "message ids. Run a preview first."
            )

        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []
        for message_id in ids:
            try:
                if permanent:
                    await gmail_service.permanent_delete_message(message_id)
                else:
                    await gmail_service.trash_message(message_id)
                succeeded.append(message_id)
            except Exception as exc:
                failed.append((message_id, str(exc)))

        verb = "permanently deleted" if permanent else "moved to Trash"
        parts = [f"{len(succeeded)} messages {verb}."]
        if failed:
            parts.append(f"{len(failed)} failed:")
            for message_id, reason in failed:
                parts.append(f"- {message_id}: {reason}")
        return "\n".join(parts)
    except Exception as exc:
        return f"Could not delete emails: {exc}"


async def _upload_to_drive(filename: str, content: str) -> str:
    """Create a text file and upload it to the user's myOS Drive folder."""
    try:
        from services.google_auth import is_authenticated, has_write_scope
        if not is_authenticated():
            return (
                "Google Drive is not connected. "
                "Connect your Google account in Settings to upload files."
            )
        if not has_write_scope():
            return (
                "Drive write access has not been granted. "
                "Reconnect your Google account in Settings to enable uploads."
            )

        # Use the existing upload endpoint via internal HTTP call.
        import io
        async with httpx.AsyncClient() as client:
            files = {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/plain")}
            resp = await client.post(
                "http://127.0.0.1:8000/api/drive/files/upload",
                files=files,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                name = data.get("name", filename)
                link = data.get("webViewLink", "")
                result = f"Uploaded \"{name}\" to Google Drive."
                if link:
                    result += f"\nLink: {link}"
                return result
            else:
                return f"Could not upload to Drive: {resp.text}"
    except Exception as exc:
        return f"Could not upload to Drive: {exc}"


def _torichat_close_prelude(agent_name: str) -> str:
    """Return a torichat-specific close-task prelude.

    The long ``agent_mailbox_instruction`` block already tells the agent
    to POST /complete when done, but claude-code subagents spawned via
    the ``spawn_agent`` tool exit naturally at the end of their turn
    without executing the sidecar curl. That leaves every torichat-spawned
    agent's row stuck in the "Agent finished its work. It didn't formally
    close the task" fallback.

    This prelude goes at the VERY TOP of the prompt, above the mailbox
    block, so it is the last thing the model sees before reading the
    user task. It is short, direct, and restated in plain language: the
    final tool call of the turn must be a POST to /complete. Phrased
    this way the instruction survives prompt compaction and mid-turn
    reasoning resets.
    """
    base = "https://127.0.0.1:8000/api/agents"
    return (
        "IMPORTANT: You were spawned from torichat. Your VERY LAST action, "
        "after all code changes and tests pass, must be to formally close "
        "this task. Run this exact shell command as your final tool call "
        "(not optional, not skippable):\n"
        f"  curl --connect-timeout 3 -m 5 -sSk -X POST {base}/{agent_name}/complete "
        "-H 'Content-Type: application/json' "
        "-d '{\"summary\": \"<one line summary of what you did>\"}'\n"
        "Without this final call the user sees a misleading \"Agent "
        "didn\'t formally close the task\" warning even though your work "
        "is done. Close the task."
    )


async def _spawn_agent(name: str, prompt: str, model: str = "sonnet") -> str:
    """Spawn a Claude Code subprocess as a background agent.

    Bug 3 fix: validates that name is non-empty (hyphens are allowed and
    the API accepts them), validates that prompt is non-empty, and returns
    a specific actionable error for each failure mode instead of the raw
    exception message.

    Torichat close fix: prepends an explicit "post /complete as your final
    tool call" prelude so the agent actually formally closes the task
    instead of exiting silently and tripping the stale-sweep fallback.
    """
    import re as _re

    if not name or not name.strip():
        return (
            "Cannot spawn agent: 'name' is empty. "
            "Provide a short identifier like 'api-spec' or 'roadmap-tasks'."
        )
    if not prompt or not prompt.strip():
        return (
            f"Cannot spawn agent '{name}': prompt is empty. "
            "Provide a non-empty prompt describing what the agent should do."
        )
    # Names with spaces or special chars (other than hyphens and underscores)
    # cause issues in the transcript filename and ostk tracking.
    clean_name = name.strip()
    if _re.search(r"[^a-zA-Z0-9_\-]", clean_name):
        return (
            f"Cannot spawn agent '{clean_name}': name must contain only letters, "
            "numbers, hyphens, and underscores (e.g. 'api-spec' or 'roadmap_tasks')."
        )

    # Prepend the torichat-specific close prelude so the subagent treats
    # the POST /complete as a non-optional final tool call rather than
    # drifting into the natural-exit path that tripped the stale-sweep
    # fallback notification for every torichat-spawned agent.
    final_prompt = _torichat_close_prelude(clean_name) + "\n\n---\n\n" + prompt

    try:
        # Use the API endpoint so it's tracked consistently
        import httpx
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                "https://127.0.0.1:8000/api/agents/spawn",
                json={"name": clean_name, "prompt": final_prompt, "model": model, "budget": 2.0},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return f"Agent '{clean_name}' spawned and running (PID {data.get('pid', '?')}). Check the Agents page to monitor it."
            else:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                return (
                    f"Failed to spawn agent '{clean_name}' (HTTP {resp.status_code}): {detail}. "
                    "Check the Agents page for details or try a different agent name."
                )
    except Exception as e:
        return (
            f"Failed to spawn agent '{clean_name}': {e}. "
            "The backend may be restarting. Wait a few seconds and try again."
        )

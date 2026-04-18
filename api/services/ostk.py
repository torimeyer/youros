from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from config import PROJECT_ROOT, OSTK_DIR as _OSTK_DIR
from services.atomic_io import atomic_write_text

PROJECT_DIR = str(PROJECT_ROOT)
OSTK_DIR = _OSTK_DIR
NUDGES_DIR = OSTK_DIR / "nudges"


class OstkError(Exception):
    pass


# Shared parse cache for .ostk/audit.jsonl. The file is ~400 KB and was
# re-read by routers/dashboard.py and services/briefing.py on the async
# event loop on every request. Now every caller goes through
# :func:`read_audit_entries` which checks
# the file's size + mtime_ns and returns the cached parse if the file
# is unchanged. Callers get a shared list[dict] they must NOT mutate.
_audit_cache: dict[str, tuple[int, int, list[dict]]] = {}


def read_audit_entries(audit_path: Optional[Path] = None) -> list[dict]:
    """Return the parsed list of audit.jsonl entries, caching by (size, mtime_ns).

    Thread-safe enough for FastAPI's async event loop: a dict write of
    the cache slot is a single reference assignment. The cache is keyed
    on the string path so multiple OstkService instances pointed at the
    same file share one parse.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    if not audit_path.exists():
        return []
    try:
        stat = audit_path.stat()
    except OSError:
        return []
    key = str(audit_path)
    cached = _audit_cache.get(key)
    if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
        return cached[2]
    try:
        text = audit_path.read_text()
    except OSError:
        return []
    entries: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    _audit_cache[key] = (stat.st_size, stat.st_mtime_ns, entries)
    return entries


def invalidate_audit_cache(audit_path: Optional[Path] = None) -> None:
    """Drop the cached parse for an audit.jsonl file. Call this right
    after appending an entry so the next reader sees the new line.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    _audit_cache.pop(str(audit_path), None)


def write_audit_entry(entry: dict, audit_path: Optional[Path] = None) -> None:
    """Append a single JSON entry to audit.jsonl and invalidate the cache.

    Best-effort: any IO failure is swallowed so audit logging can never
    break the caller. Used by chat handlers and agent registration to
    record usage events without shelling out to ``ostk``.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        invalidate_audit_cache(audit_path)
    except OSError:
        pass


# In-flight coalescing for expensive read-only ostk calls. When two
# handlers ask for the same thing at the same time (for example
# /api/tasks and /api/dashboard/summary both call ``list_tasks()``
# during the same page load), they share ONE subprocess spawn instead
# of each spawning their own. Only applied to read-only calls keyed on
# (method, args). Never dedupe writes like ``add_task`` or ``close_task``
# because they would get silently merged.
_inflight_calls: dict[tuple, "asyncio.Future"] = {}


async def _coalesce_call(key: tuple, coro_factory):
    """Run ``coro_factory()`` once per ``key`` at a time. Other callers
    that enter before the first one finishes ``await`` its Future and
    receive the same result. Not a TTL cache: as soon as the first
    call resolves the key is freed, so serial callers always get fresh
    results.
    """
    existing = _inflight_calls.get(key)
    if existing is not None:
        # Another task is already running this call. Wait for its
        # result instead of spawning a second subprocess. shield()
        # prevents a canceled waiter from canceling the producer.
        return await asyncio.shield(existing)
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _inflight_calls[key] = fut
    try:
        result = await coro_factory()
        if not fut.done():
            fut.set_result(result)
        return result
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _inflight_calls.pop(key, None)


class OstkService:
    def __init__(self, cwd: str = PROJECT_DIR):
        self.cwd = cwd
        # Tri-state: None = unknown, True = working, False = unavailable
        self._socket_available: Optional[bool] = None

    def _resolve_socket_tool(self, args: tuple) -> Optional[tuple]:
        """Map CLI argument tuples to MCP tool names and arguments.

        Returns (tool_name, arguments_dict) or None if the command
        should go through subprocess instead.
        """
        if not args:
            return None

        head = args[0]

        if head == "os" and len(args) >= 2:
            sub = args[1]
            if sub == "status":
                return ("status", {})
            if sub == "diff":
                return ("diff", {})
            if sub == "clock":
                return ("clock", {})
            if sub == "history":
                params: dict = {}
                rest = args[2:]
                i = 0
                while i < len(rest):
                    if rest[i] == "--last" and i + 1 < len(rest):
                        params["last"] = rest[i + 1]
                        i += 2
                    else:
                        params["target"] = rest[i]
                        i += 1
                return ("session_history", params)
            return None

        if head == "kernel" and len(args) >= 2:
            sub = args[1]
            if sub == "ps":
                return ("ps", {})
            if sub == "reap":
                return ("reap", {})
            return None

        if head == "work" and len(args) >= 2:
            sub = args[1]
            # Only map read-only commands; mutations use subprocess
            if sub == "list":
                params = {}
                rest = args[2:]
                i = 0
                while i < len(rest):
                    if rest[i] == "--json":
                        params["format"] = "json"
                        i += 1
                    elif rest[i] == "--status" and i + 1 < len(rest):
                        params["status"] = rest[i + 1]
                        i += 2
                    elif rest[i] == "--priority" and i + 1 < len(rest):
                        params["priority"] = rest[i + 1]
                        i += 2
                    else:
                        i += 1
                return ("needle", params)
            if sub == "near" and len(args) >= 3:
                return ("near", {"query": args[2]})
            if sub == "hay":
                if len(args) >= 3:
                    return ("hay", {"thought": args[2]})
                return ("hay", {})
            if sub == "activate" and len(args) >= 3:
                return ("activate", {"needle": args[2]})
            if sub == "radiate":
                if len(args) >= 3:
                    return ("related", {"needle": args[2]})
                return ("related", {})
            return None

        if head == "compounds":
            return ("related", {"type": "compounds"})
        if head == "trace" and len(args) >= 2:
            return ("trace", {"needle": args[1]})
        if head == "boot":
            return ("boot", {})

        # Deep search verbs (MCP-only, no CLI equivalent)
        if head == "pitchfork" and len(args) >= 2:
            return ("pitchfork", {"query": " ".join(args[1:])})
        if head == "recall" and len(args) >= 2:
            return ("recall", {"query": " ".join(args[1:])})

        return None

    async def _run_socket(self, *args: str, timeout: int = 5) -> str:
        """Try to run an ostk command via the MCP socket transport."""
        from services.ostk_socket import call_tool, OstkSocketError

        mapping = self._resolve_socket_tool(args)
        if mapping is None:
            raise OstkSocketError(f"No socket mapping for: {args}")

        tool_name, tool_args = mapping
        return await call_tool(tool_name, tool_args, timeout=float(timeout))

    async def _run(self, *args: str, timeout: int = 5) -> str:
        # Try socket transport first (fast path)
        if self._socket_available is not False:
            try:
                result = await self._run_socket(*args, timeout=timeout)
                self._socket_available = True
                return result
            except Exception:
                self._socket_available = False

        # Fallback to subprocess
        import subprocess
        cmd = ["ostk", *args]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise OstkError(f"ostk command timed out: {' '.join(cmd)}")
        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip() or output
            raise OstkError(err)
        return output

    async def _run_json(self, *args: str) -> Union[list, dict]:
        output = await self._run(*args)
        return json.loads(output)

    # --- Tasks / Needles ---

    async def list_tasks(self, status: Optional[str] = None, priority: Optional[str] = None) -> list[dict]:
        args = ["work", "list", "--json"]
        if status:
            args += ["--status", status]
        if priority:
            args += ["--priority", priority]

        # Coalesce concurrent identical reads so a single page load that
        # hits /api/tasks + /api/dashboard/summary + /api/briefing all
        # at the same instant shares ONE ostk subprocess spawn instead
        # of racing three of them. Keyed on the full argv so filtered
        # variants do not collide with unfiltered calls. Returns a
        # defensive copy so the shared list is not accidentally
        # mutated by one caller in a way that leaks to the others.
        key = ("list_tasks", self.cwd, tuple(args))

        async def _do_call() -> list[dict]:
            raw: list[dict] = await self._run_json(*args)
            seen: dict[str, dict] = {}
            for entry in raw:
                task_id = entry.get("id")
                if task_id:
                    seen[task_id] = entry
            return list(seen.values())

        shared = await _coalesce_call(key, _do_call)
        return list(shared)

    async def add_task(
        self,
        title: str,
        priority: str = "P1",
        description: str = "",
        ac: str = "",
    ) -> str:
        # ostk requires --description and --ac. Fall back to sensible defaults
        # derived from the title so quick-add from the UI still works.
        desc = description.strip() or title
        acceptance = ac.strip() or "Task is complete and verified."
        return await self._run(
            "work", "add", title,
            "--priority", priority,
            "--description", desc,
            "--ac", acceptance,
        )

    async def close_task(self, task_id: str, closed_reason: Optional[str] = None) -> str:
        """Close a task and optionally tag it with a structured reason.

        ``closed_reason`` is a controlled vocabulary used by the Tasks audit
        feature to mark why a task was closed: ``"completed"`` when the work
        is already done, ``"duplicate"`` when another task covers it, or
        ``"archived"`` when it is no longer relevant. When omitted the task
        is closed as usual and no extra field is written. Values outside
        the allowed set are rejected so callers cannot invent ad-hoc tags.
        """
        result = await self._run("work", "close", task_id)
        if closed_reason is not None:
            allowed = {"completed", "duplicate", "archived"}
            if closed_reason not in allowed:
                raise OstkError(
                    f"invalid closed_reason '{closed_reason}', must be one of {sorted(allowed)}"
                )
            issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
            if issues_path.exists():
                lines = issues_path.read_text().strip().splitlines()
                updated: list[str] = []
                for line in lines:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        updated.append(line)
                        continue
                    if entry.get("id") == task_id:
                        entry["closed_reason"] = closed_reason
                    updated.append(json.dumps(entry, ensure_ascii=False))
                issues_path.write_text("\n".join(updated) + "\n")
        return result

    async def reopen_task(self, task_id: str) -> str:
        """Reopen a closed task by editing the JSONL file directly."""
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                entry["status"] = "open"
                entry.pop("close_reason", None)
                entry.pop("closed_at", None)
                entry.pop("closed_reason", None)
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"reopened {task_id}"

    async def delete_task(self, task_id: str) -> str:
        """Permanently remove a task from issues.jsonl."""
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        updated = []
        found = False
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                found = True
            else:
                updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + ("\n" if updated else ""))
        return f"deleted {task_id}"

    async def update_task_priority(
        self,
        task_id: str,
        priority: str,
        reason: Optional[str] = None,
    ) -> str:
        """Update a task's priority.

        When *reason* is provided, the change is logged via ``ostk work promote``
        so the audit trail captures why the priority was changed. Without a
        reason the JSONL file is edited directly (preserving the existing
        fast-path behaviour).
        """
        valid = {"P0", "P1", "P2", "P3"}
        if priority not in valid:
            raise OstkError(f"invalid priority '{priority}', must be one of {valid}")

        if reason:
            return await self._run(
                "work", "promote", task_id, priority, "--reason", reason
            )

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                entry["priority"] = priority
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"updated {task_id} priority to {priority}"

    async def update_task_fields(
        self,
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """Rename a task's title and/or description.

        ``ostk needle edit`` only supports ``--description`` today, so this
        edits ``issues.jsonl`` directly for title changes. Description changes
        use the same fast path for consistency. Both fields are optional and
        any unset field is left untouched.
        """
        if title is None and description is None:
            raise OstkError("update_task_fields requires title or description")

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                if title is not None:
                    entry["title"] = title
                if description is not None:
                    entry["description"] = description
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        fields = []
        if title is not None:
            fields.append("title")
        if description is not None:
            fields.append("description")
        return f"updated {task_id} {' and '.join(fields)}"

    async def shelve_task(self, task_id: str) -> str:
        """Pause a task via ``ostk work shelve``."""
        return await self._run("work", "shelve", task_id)

    async def unshelve_task(self, task_id: str) -> str:
        """Resume a shelved task via ``ostk work unshelve``."""
        return await self._run("work", "unshelve", task_id)

    async def link_tasks(self, source: str, relation: str, target: str) -> str:
        """Link two tasks with a relationship (blocks or depends-on)."""
        valid_relations = {"blocks", "depends-on"}
        if relation not in valid_relations:
            raise OstkError(
                f"invalid relation '{relation}', must be one of {valid_relations}"
            )
        return await self._run("work", "link", source, relation, target)

    async def get_dependencies(self, task_id: str) -> dict:
        """Get the dependency info for a task from the task list data.

        Rather than parsing ``ostk work depends`` text output, we read the
        blocks/depends_on arrays that ``ostk work list --json`` already
        provides. This is faster and more reliable.
        """
        tasks = await self.list_tasks()
        task = None
        for t in tasks:
            if t.get("id") == task_id:
                task = t
                break
        if task is None:
            raise OstkError(f"task \'{task_id}\' not found")

        blocks = task.get("blocks", [])
        depends_on = task.get("depends_on", [])

        # Build title lookup for referenced tasks
        titles: dict[str, str] = {}
        for t in tasks:
            tid = t.get("id", "")
            if tid in blocks or tid in depends_on:
                titles[tid] = t.get("title", "")

        return {
            "task_id": task_id,
            "blocks": [{"id": b, "title": titles.get(b, "")} for b in blocks],
            "depends_on": [
                {"id": d, "title": titles.get(d, "")} for d in depends_on
            ],
        }

    async def unlink_tasks(self, source: str, relation: str, target: str) -> str:
        """Remove a link between two tasks by editing issues.jsonl directly.

        ostk CLI does not have an unlink command, so we manipulate the
        data file directly, similar to reopen_task and update_task_priority.
        """
        valid_relations = {"blocks", "depends-on"}
        if relation not in valid_relations:
            raise OstkError(
                f"invalid relation \'{relation}\', must be one of {valid_relations}"
            )

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        field = "blocks" if relation == "blocks" else "depends_on"
        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == source and target in entry.get(field, []):
                entry[field] = [t for t in entry[field] if t != target]
                if not entry[field]:
                    del entry[field]
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"link not found: {source} {relation} {target}")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"unlinked {source} {relation} {target}"

    async def next_task(self) -> str:
        return await self._run("work", "next")

    async def claim_next_task(self) -> str:
        """Pull and claim the next task from the queue.

        Uses ostk's pull model: the agent declares readiness, ostk selects
        the highest-priority task (sphere-radius-aware), and atomically
        marks it as in_progress. Returns the task info or empty if no
        tasks available.
        """
        try:
            return await self._run("work", "next", "--claim")
        except OstkError:
            return ""

    async def activate_task(self, task_id: str) -> dict:
        """Run ``ostk work activate <id>`` and parse the briefing output.

        Returns a dict with structured briefing data: the task info,
        sphere details, neighbors, blockers, unblocks, and any hay
        (nearby ideas) mentioned.
        """
        raw = await self._run("work", "activate", task_id)
        return self._parse_activate(raw)

    def _parse_activate(self, output: str) -> dict:
        """Parse the text output of ``ostk work activate`` into a dict."""
        result: dict = {
            "task_id": "",
            "priority": "",
            "status": "",
            "title": "",
            "sphere": None,
            "neighbors": [],
            "blocked_by": [],
            "unblocks": [],
            "all_blockers_resolved": False,
            "raw": output,
        }

        lines = output.strip().splitlines()
        section: Optional[str] = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Header: ACTIVATE ->088 [P1|open]
            if stripped.startswith("\u2550") and "ACTIVATE" in stripped:
                m = re.search(r"ACTIVATE\s+(\S+)\s+\[(\w+)\|(\w+)\]", stripped)
                if m:
                    result["task_id"] = m.group(1)
                    result["priority"] = m.group(2)
                    result["status"] = m.group(3)
                continue

            # Footer line
            if stripped.startswith("\u2550") and "ready" in stripped.lower():
                continue

            # Section headers
            if stripped.startswith("SPHERE:"):
                result["sphere"] = stripped[len("SPHERE:"):].strip()
                section = "sphere"
                continue

            if stripped.startswith("NEIGHBORS"):
                section = "neighbors"
                continue

            if stripped.startswith("BLOCKED BY:"):
                section = "blocked_by"
                continue

            if stripped.startswith("UNBLOCKS:"):
                section = "unblocks"
                continue

            if stripped.startswith("HAY"):
                section = "hay"
                continue

            # Title line (first content before any section starts)
            if section is None and not result["title"]:
                result["title"] = stripped
                continue

            # Parse items in sections
            if section == "neighbors" and stripped.startswith("\u2192"):
                # Remove leading "→ " prefix (one arrow + space)
                text = re.sub(r"^\u2192\s*", "", stripped).strip()
                result["neighbors"].append(text)

            elif section == "blocked_by":
                if "all blockers resolved" in stripped.lower():
                    result["all_blockers_resolved"] = True
                    continue
                # ostk emits a trailing footer like "-> unresolved blockers, may not be ready"
                # when at least one blocker is still open. It is a status note, not an item.
                if stripped.startswith("\u2192") and "\u26a0" in stripped:
                    continue
                # Blocker items start with ✓ (done), ✗ (still open), or → (legacy/unknown).
                if (
                    stripped.startswith("\u2713")
                    or stripped.startswith("\u2717")
                    or stripped.startswith("\u2192")
                ):
                    resolved = stripped.startswith("\u2713")
                    # Remove one leading ✓, ✗, or → plus whitespace
                    text = re.sub(r"^[\u2713\u2717\u2192]\s*", "", stripped).strip()
                    result["blocked_by"].append({
                        "text": text,
                        "resolved": resolved,
                    })

            elif section == "unblocks" and stripped.startswith("\u2192"):
                # Remove leading "→ " prefix (one arrow + space)
                text = re.sub(r"^\u2192\s*", "", stripped).strip()
                result["unblocks"].append(text)

        return result

    # --- Hay / Ideas ---

    async def list_hay(self, exclude_converted: bool = True) -> dict:
        output = await self._run("work", "hay")
        result = self._parse_hay(output)
        if exclude_converted:
            converted_straws = {c["straw"] for c in await self.list_converted_hay()}
            if converted_straws:
                for cluster in result.get("clusters", []):
                    cluster["items"] = [
                        item for item in cluster["items"]
                        if item not in converted_straws
                    ]
                    cluster["count"] = len(cluster["items"])
                result["clusters"] = [c for c in result["clusters"] if c["items"]]
                result["unclustered"] = [
                    item for item in result.get("unclustered", [])
                    if item not in converted_straws
                ]
        return result

    async def add_hay(self, thought: str) -> str:
        return await self._run("work", "hay", thought)

    async def add_hay_from_chat(self, thought: str) -> str:
        """Save a hay entry that originated from a chat conversation.

        Calls the ostk CLI to file the hay (so it appears in the active list),
        then patches the last hay.filed event in audit.jsonl to mark
        source as 'chat'.
        """
        result = await self._run("work", "hay", thought)
        # Patch the source field on the hay.filed entry we just appended.
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if audit_path.exists():
            lines = audit_path.read_text().strip().splitlines()
            for i in range(len(lines) - 1, -1, -1):
                try:
                    entry = json.loads(lines[i])
                except json.JSONDecodeError:
                    continue
                if entry.get("event") == "hay.filed" and entry.get("straw") == thought:
                    entry["source"] = "chat"
                    lines[i] = json.dumps(entry, ensure_ascii=False)
                    audit_path.write_text("\n".join(lines) + "\n")
                    break
        return result

    async def compile_hay(self, dry_run: bool = False) -> str:
        args = ["work", "compile"]
        if dry_run:
            args.append("--dry-run")
        return await self._run(*args)

    async def delete_hay(self, straw: str, include_converted: bool = False) -> str:
        """Remove a hay entry from audit.jsonl by its straw text.

        When *include_converted* is True, also remove any matching
        hay.converted event so the idea disappears from both lists.
        """
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if not audit_path.exists():
            raise OstkError("audit.jsonl not found")

        lines = audit_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("event") == "hay.filed" and entry.get("straw") == straw:
                found = True
                continue
            if include_converted and entry.get("event") == "hay.converted" and entry.get("straw") == straw:
                continue
            updated.append(line)

        if not found:
            raise OstkError(f"hay item not found: {straw}")

        audit_path.write_text("\n".join(updated) + "\n")
        invalidate_audit_cache(audit_path)
        return f"deleted hay: {straw}"

    async def delete_converted_hay(self, straw: str) -> str:
        """Remove a hay.converted entry from audit.jsonl by its straw text."""
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if not audit_path.exists():
            raise OstkError("audit.jsonl not found")

        lines = audit_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("event") == "hay.converted" and entry.get("straw") == straw:
                found = True
                continue
            updated.append(line)

        if not found:
            raise OstkError(f"converted hay item not found: {straw}")

        audit_path.write_text("\n".join(updated) + "\n")
        invalidate_audit_cache(audit_path)
        return f"deleted converted hay: {straw}"

    async def convert_hay_to_task(self, straw: str, priority: str = "P1", delete_hay: bool = False) -> str:
        """Turn a hay entry into a task and mark it as converted.

        The hay item stays in the audit log but a ``hay.converted`` event
        is appended so the UI can filter it out of the active list and
        show it in a "Converted" tab instead.

        The legacy ``delete_hay`` flag is still accepted for backwards
        compatibility but defaults to False.
        """
        result = await self.add_task(straw, priority)
        # Mark the idea as converted in the audit log
        await self._mark_hay_converted(straw, result)
        if delete_hay:
            try:
                await self.delete_hay(straw)
            except OstkError:
                pass  # hay may already be gone; task was still created
        return result

    async def _mark_hay_converted(self, straw: str, task_result: str) -> None:
        """Append a hay.converted event to audit.jsonl."""
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        # Extract task ID from the result string if possible (e.g. "added →042")
        task_id = ""
        for word in task_result.split():
            if word.startswith("→") or word.startswith("->"):
                task_id = word
                break
        entry = {
            "event": "hay.converted",
            "straw": straw,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # Write on a worker thread so the append does not block the
        # async event loop, then invalidate the shared parse cache so
        # the next reader sees this entry.
        def _append() -> None:
            with open(audit_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        await asyncio.to_thread(_append)
        invalidate_audit_cache(audit_path)

    async def list_converted_hay(self) -> list[dict]:
        """Return hay items that have been converted into tasks.

        Reads audit.jsonl for hay.converted events and returns a list of
        dicts with the straw text and the task ID they became. Uses the
        shared :func:`read_audit_entries` cache so concurrent handlers
        do not re-parse the file on every request.
        """
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        converted: list[dict] = []
        for entry in read_audit_entries(audit_path):
            if entry.get("event") == "hay.converted":
                converted.append({
                    "straw": entry.get("straw", ""),
                    "task_id": entry.get("task_id", ""),
                    "converted_at": entry.get("timestamp", ""),
                })
        return converted

    def _parse_hay(self, output: str) -> dict:
        clusters: list[dict] = []
        unclustered: list[str] = []
        current_section = None
        current_cluster: Optional[dict] = None

        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "clusters (discovered)" in line.lower() or "clusters" in line.lower() and "──" in line:
                current_section = "clusters"
                continue
            if "unclustered" in line.lower() and "──" in line:
                current_section = "unclustered"
                if current_cluster:
                    clusters.append(current_cluster)
                    current_cluster = None
                continue

            if current_section == "clusters":
                cluster_match = re.match(r"(\w+)\s+\((\d+)\s+hay\)", line)
                if cluster_match:
                    if current_cluster:
                        clusters.append(current_cluster)
                    current_cluster = {
                        "name": cluster_match.group(1),
                        "count": int(cluster_match.group(2)),
                        "items": [],
                    }
                    continue
                if line.startswith("~"):
                    text = line.lstrip("~ ").strip()
                    if current_cluster:
                        current_cluster["items"].append(text)
                    continue

            if current_section == "unclustered" and line.startswith("~"):
                text = line.lstrip("~ ").strip()
                unclustered.append(text)

        if current_cluster:
            clusters.append(current_cluster)

        return {"clusters": clusters, "unclustered": unclustered}

    # --- Trace / Attribution ---

    async def trace(self, task_id: str) -> dict:
        """Run ``ostk trace <task_id>`` and parse the attribution chain.

        Returns a dict with the task headline plus lists for specs,
        drafts, agentfiles, depends_on, blocks, and commits.
        """
        raw = await self._run("trace", task_id)
        return self._parse_trace(raw)

    @staticmethod
    def _parse_trace(raw: str) -> dict:
        """Parse the text output of ``ostk trace`` into structured data.

        Example input::

            →093: Add attribution tracing [P2, open]
              specs: (none)
              drafts: (none)
              agentfiles: (none)
              depends_on: (none)
              blocks: →002
              commits: abc1234 Fix the thing
        """
        lines = raw.strip().splitlines()
        result: dict = {
            "headline": "",
            "specs": [],
            "drafts": [],
            "agentfiles": [],
            "depends_on": [],
            "blocks": [],
            "commits": [],
        }

        if not lines:
            return result

        result["headline"] = lines[0].strip()

        current_key: Optional[str] = None
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue

            # Check if line starts a new field ("key: value")
            if ":" in stripped:
                candidate_key, _, rest = stripped.partition(":")
                candidate_key = candidate_key.strip().lower().replace(" ", "_")
                if candidate_key in result and candidate_key != "headline":
                    current_key = candidate_key
                    rest = rest.strip()
                    if rest and rest != "(none)":
                        result[current_key].append(rest)
                    continue

            # Continuation line for the current field
            if current_key and stripped and stripped != "(none)":
                result[current_key].append(stripped)

        return result

    # --- Commits ---

    async def commit(
        self,
        message: str,
        needle: Optional[str] = None,
        spec: Optional[str] = None,
        section: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> str:
        """Run ostk commit with optional needle attribution.

        Links a git commit to a task so the work is tracked.
        """
        args = ["commit", "-m", message]
        if needle:
            args += ["--needle", needle]
        if spec:
            args += ["--spec", spec]
        if section:
            args += ["--section", section]
        if agent:
            args += ["--agent", agent]
        return await self._run(*args)

    # --- Search ---

    async def search_near(self, query: str) -> dict:
        """Search tasks by concept using ostk work near.

        Returns a dict with 'tasks' (list of matching needles).
        The CLI output is parsed into structured results.
        """
        tasks: list[dict] = []

        # Search tasks (needles) via ostk work near
        try:
            raw = await self._run("work", "near", query)
            tasks = self._parse_near_output(raw)
        except OstkError:
            # "no open needles matching ..." is not a real error
            pass

        return {"tasks": tasks, "query": query}

    def _parse_near_output(self, output: str) -> list[dict]:
        """Parse the output of ``ostk work near`` into a list of task dicts."""
        results: list[dict] = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Match lines like: →086 [P1] Add concept search across tasks and ideas
            match = re.match(r"(→\d+)\s+\[(\w+)\]\s+(.+)", line)
            if match:
                results.append({
                    "id": match.group(1),
                    "priority": match.group(2),
                    "title": match.group(3).strip(),
                })
        return results

    # --- Deep Search (pitchfork / recall) ---

    async def pitchfork(self, query: str) -> dict:
        """Search all kernel state via ostk pitchfork.

        Returns categorized results: needles, audit event summaries,
        and transcript snippets. Uses the MCP socket (no CLI equivalent).
        Timeout is longer because pitchfork scans transcripts.
        """
        try:
            raw = await self._run("pitchfork", query, timeout=15)
        except OstkError:
            return {"needles": [], "audit": [], "transcripts": [], "query": query}
        return self._parse_pitchfork(raw, query)

    async def recall(self, query: str) -> dict:
        """Search kernel state and past transcripts via ostk recall.

        Same output format as pitchfork. Kept as a separate method in
        case the two diverge in the future, but today the MCP tools
        return the same structure.
        """
        try:
            raw = await self._run("recall", query, timeout=15)
        except OstkError:
            return {"needles": [], "audit": [], "transcripts": [], "query": query}
        return self._parse_pitchfork(raw, query)

    @staticmethod
    def _parse_pitchfork(output: str, query: str) -> dict:
        """Parse the text output of pitchfork/recall into structured data.

        Output has three sections separated by blank lines:
          needles (N matches):
            - ->NNN [status] title
          audit (N matches, last 1000 events):
            - event_type (xN)
          transcripts (N matches in ~/.claude/projects/):
            - [date] session_id
              role: snippet...
        """
        needles: list[dict] = []
        audit: list[dict] = []
        transcripts: list[dict] = []

        section: Optional[str] = None
        current_transcript: Optional[dict] = None

        for line in output.splitlines():
            stripped = line.strip()

            # Detect section headers
            if stripped.startswith("needles ("):
                section = "needles"
                continue
            if stripped.startswith("audit ("):
                section = "audit"
                continue
            if stripped.startswith("transcripts ("):
                section = "transcripts"
                continue

            if not stripped:
                continue

            if section == "needles" and stripped.startswith("- "):
                item = stripped[2:]
                # Parse: ->NNN [status] title
                m = re.match(r"(→\d+)\s+\[(\w+)\]\s+(.+)", item)
                if m:
                    needles.append({
                        "id": m.group(1),
                        "status": m.group(2),
                        "title": m.group(3).strip(),
                    })

            elif section == "audit" and stripped.startswith("- "):
                item = stripped[2:]
                # Parse: event_type (xN)
                m = re.match(r"(.+?)\s+\(×(\d+)\)", item)
                if m:
                    audit.append({
                        "event": m.group(1).strip(),
                        "count": int(m.group(2)),
                    })

            elif section == "transcripts":
                if stripped.startswith("- ["):
                    # New transcript entry: - [date] session_id
                    if current_transcript is not None:
                        transcripts.append(current_transcript)
                    m = re.match(r"-\s+\[([^\]]+)\]\s+(\S+)", stripped)
                    if m:
                        current_transcript = {
                            "date": m.group(1),
                            "session_id": m.group(2),
                            "snippets": [],
                        }
                    else:
                        current_transcript = None
                elif current_transcript is not None:
                    # Snippet lines: "ai: ..." or "user: ..."
                    role_match = re.match(r"(ai|user):\s+(.*)", stripped)
                    if role_match:
                        current_transcript["snippets"].append({
                            "role": role_match.group(1),
                            "text": role_match.group(2).strip(),
                        })

        # Flush last transcript
        if current_transcript is not None:
            transcripts.append(current_transcript)

        return {
            "needles": needles,
            "audit": audit,
            "transcripts": transcripts,
            "query": query,
        }

    # --- Delegation / Radiate ---

    async def work_radiate(self, needle_id: Optional[str] = None) -> dict:
        """Run ``ostk work radiate`` and parse the output.

        Returns a dict with ``point`` (the center needle), ``rings`` (list of
        ring dicts with radius and needles), and ``delegation_targets`` (list
        of dicts ready for agent spawning).
        """
        args = ["work", "radiate"]
        if needle_id:
            args.append(needle_id)
        output = await self._run(*args)
        return self._parse_radiate(output)

    @staticmethod
    def _parse_radiate(output: str) -> dict:
        """Parse the text output of ``ostk work radiate``."""
        result: dict = {
            "point": None,
            "point_title": "",
            "rings": [],
            "delegation_targets": [],
        }

        section = "header"
        current_ring: Optional[dict] = None

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Header: extract point info
            header_match = re.match(
                r".*radiate from\s+(→\d+)\s+\[([^\]]+)\].*",
                stripped,
            )
            if header_match:
                result["point"] = header_match.group(1)
                continue

            # Point title (line after header, before "max radius")
            if section == "header" and result["point"] and not result["point_title"]:
                if not stripped.startswith("max radius"):
                    result["point_title"] = stripped
                    continue

            if stripped.startswith("max radius"):
                section = "rings"
                continue

            # Point line
            point_match = re.match(r".*POINT\s+\(radius\s+0\):\s+(→\d+)", stripped)
            if point_match:
                result["point"] = point_match.group(1)
                section = "rings"
                continue

            # Ring header
            ring_match = re.match(
                r".*RING\s+(\d+)\s+\((\d+)\s+needles?,\s*(\d+)\s+open\):",
                stripped,
            )
            if ring_match:
                if current_ring:
                    result["rings"].append(current_ring)
                current_ring = {
                    "radius": int(ring_match.group(1)),
                    "total": int(ring_match.group(2)),
                    "open": int(ring_match.group(3)),
                    "needles": [],
                }
                continue

            # Needle inside a ring
            if current_ring is not None and not stripped.startswith(("─", ":")):
                needle_match = re.match(
                    r"(→\d+)\s+\[([^\]]+)\]\s+(.*)",
                    stripped,
                )
                if needle_match:
                    current_ring["needles"].append({
                        "id": needle_match.group(1),
                        "priority": needle_match.group(2),
                        "title": needle_match.group(3).strip(),
                    })
                    continue

            # Delegation frontier header
            if "delegation frontier" in stripped.lower():
                if current_ring:
                    result["rings"].append(current_ring)
                    current_ring = None
                section = "delegation"
                continue

            # Delegation target lines
            if section == "delegation":
                spawn_match = re.match(
                    r":spawn\s+(→\d+)\s+→\s+(.*)",
                    stripped,
                )
                if spawn_match:
                    result["delegation_targets"].append({
                        "id": spawn_match.group(1),
                        "title": spawn_match.group(2).strip(),
                    })

        # Flush last ring
        if current_ring:
            result["rings"].append(current_ring)

        return result

    # --- Compounds ---

    async def get_compounds(self) -> list[dict]:
        """Run ``ostk compounds`` and parse the output.

        Returns a list of dicts, each with ``id``, ``title``, and
        ``blocks_count`` (how many other tasks it unblocks). The list is
        sorted so the highest-leverage task comes first.

        When there are no blocking dependencies the CLI prints
        "(no blocking dependencies found)" and we return an empty list.
        """
        output = await self._run("compounds")
        return self._parse_compounds(output)

    @staticmethod
    def _parse_compounds(output: str) -> list[dict]:
        """Parse the text output of ``ostk compounds``.

        Expected line formats (examples):
            →042  Build smart focus  (blocks 5)
            →017  Fix auth redirect  (blocks 2)

        If the output contains "no blocking dependencies" we return [].
        """
        if "no blocking dependencies" in output.lower():
            return []

        results: list[dict] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith(":") or line.startswith("─"):
                continue
            # Match lines like: →042  Some title  (blocks 5)
            match = re.match(
                r"(→\d+)\s+(.+?)\s+\(blocks?\s+(\d+)\)",
                line,
            )
            if match:
                results.append({
                    "id": match.group(1),
                    "title": match.group(2).strip(),
                    "blocks_count": int(match.group(3)),
                })
        # Sort descending by blocks_count (CLI may already do this, but be safe)
        results.sort(key=lambda r: r["blocks_count"], reverse=True)
        return results

    # --- Session Diff ---

    async def get_session_diff(self) -> dict:
        """Run ``ostk os diff`` and parse the output into structured data.

        Returns a dict with:
        - files_changed: list of file paths modified since boot
        - needles_filed: list of dicts with id, priority, title
        - audit_events: list of dicts with count and event type
        - audit_total: int total audit events
        """
        raw = await self._run("os", "diff")
        return self._parse_session_diff(raw)

    def _parse_session_diff(self, output: str) -> dict:
        files_changed: list[str] = []
        needles_filed: list[dict] = []
        audit_events: list[dict] = []
        audit_total = 0
        section: Optional[str] = None

        for line in output.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # Detect section headers
            if stripped.startswith("## Files changed"):
                section = "files"
                continue
            elif stripped.startswith("## Needles filed"):
                section = "needles"
                continue
            elif stripped.startswith("## Audit events"):
                section = "audit"
                continue
            elif stripped.startswith("## Active staging"):
                section = "staging"
                continue

            # Skip informational notes in parentheses
            if stripped.startswith("("):
                # Extract total from audit summary like "(22 total events)"
                if section == "audit":
                    total_match = re.match(r"\((\d+)\s+total", stripped)
                    if total_match:
                        audit_total = int(total_match.group(1))
                continue

            if section == "files":
                if stripped and not stripped.startswith("("):
                    files_changed.append(stripped)

            elif section == "needles":
                # Format: [P1] ->089  Title text
                needle_match = re.match(
                    r"\[([^\]]+)\]\s+([\u2192\->]+\d+)\s+(.*)", stripped
                )
                if needle_match:
                    needles_filed.append({
                        "priority": needle_match.group(1),
                        "id": needle_match.group(2),
                        "title": needle_match.group(3).strip(),
                    })

            elif section == "audit":
                # Format:  16  task.added
                event_match = re.match(r"(\d+)\s+(.+)", stripped)
                if event_match:
                    audit_events.append({
                        "count": int(event_match.group(1)),
                        "event": event_match.group(2).strip(),
                    })

        return {
            "files_changed": files_changed,
            "needles_filed": needles_filed,
            "audit_events": audit_events,
            "audit_total": audit_total,
        }

    # --- History ---

    async def get_history(self, last: Optional[int] = None, target: Optional[str] = None) -> list[dict]:
        """Fetch chronological activity events from ostk os history.

        Returns a list of dicts with keys: timestamp, event, detail.
        The CLI returns plain text lines like:
          [2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management
        """
        args = ["os", "history"]
        if last is not None:
            args += ["--last", str(last)]
        if target is not None:
            args.append(target)
        try:
            raw = await self._run(*args)
        except OstkError:
            return []

        events: list[dict] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = self._parse_history_line(line)
            if parsed:
                events.append(parsed)
        return events

    @staticmethod
    def _parse_history_line(line: str) -> Optional[dict]:
        """Parse a single ostk os history output line.

        Handles both legacy format (Z suffix, no microseconds):
          [2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management
        And the current format (microseconds + UTC offset):
          [2026-04-14T22:30:45.665455+00:00] agent.spawned     name="fix-activity-insights"
        """
        m = re.match(
            r"\[(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}))\]\s+(\S+)\s*(.*)",
            line,
        )
        if not m:
            return None
        return {
            "timestamp": m.group(1),
            "event": m.group(2),
            "detail": m.group(3).strip(),
        }

    # --- Refine / Health Check ---

    async def refine_tasks(self, task_ids: Optional[list[str]] = None) -> dict:
        """Run ostk work refine on the given task IDs (or all open tasks).

        Parses the CLI output into structured data with per-task info
        about spheres (clusters), joints (connections), and overall
        health signals like isolated tasks and duplicate titles.

        Returns a dict with:
          - tasks: list of parsed task results
          - issues: list of detected problems (duplicates, isolated, no description)
          - summary: counts of total, issues, connected, isolated
        """
        if not task_ids:
            all_open = await self.list_tasks(status="open")
            task_ids = [t["id"] for t in all_open]

        if not task_ids:
            return {"tasks": [], "issues": [], "summary": {"total": 0, "issues": 0, "connected": 0, "isolated": 0}}

        args = ["work", "refine"] + task_ids
        raw = await self._run(*args)

        refined = self._parse_refine(raw)

        # Fetch full task list for additional checks
        all_tasks = await self.list_tasks()
        open_tasks = [t for t in all_tasks if t.get("status") == "open"]

        issues = self._detect_issues(refined, open_tasks)

        connected = sum(1 for t in refined if t.get("degree", 0) > 0)
        isolated = sum(1 for t in refined if t.get("degree", 0) == 0)

        return {
            "tasks": refined,
            "issues": issues,
            "summary": {
                "total": len(refined),
                "issues": len(issues),
                "connected": connected,
                "isolated": isolated,
            },
        }

    def _parse_refine(self, output: str) -> list[dict]:
        """Parse the output of ostk work refine into structured data."""
        tasks: list[dict] = []
        current: Optional[dict] = None

        for line in output.split("\n"):
            line = line.rstrip()

            # Task header: ->NNN [P1|open] Title text
            task_match = re.match(r"^(→\d+)\s+\[(\w+)\|(\w+)\]\s+(.+)$", line)
            if task_match:
                if current:
                    tasks.append(current)
                current = {
                    "id": task_match.group(1),
                    "priority": task_match.group(2),
                    "status": task_match.group(3),
                    "title": task_match.group(4),
                    "sphere": None,
                    "degree": 0,
                    "joints": [],
                }
                continue

            if not current:
                continue

            stripped = line.strip()

            # Sphere: sphere: N (M needles, point=->NNN)
            sphere_match = re.match(r"sphere:\s+(\d+)\s+\((\d+)\s+needles?,\s+point=(→\d+)\)", stripped)
            if sphere_match:
                current["sphere"] = {
                    "id": int(sphere_match.group(1)),
                    "size": int(sphere_match.group(2)),
                    "point": sphere_match.group(3),
                }
                continue

            # Degree: degree: N joints
            degree_match = re.match(r"degree:\s+(\d+)\s+joints?", stripped)
            if degree_match:
                current["degree"] = int(degree_match.group(1))
                continue

            # Joint: <-> ->NNN Title text
            joint_match = re.match(r"↔\s+(→\d+)\s+(.+)", stripped)
            if joint_match:
                current["joints"].append({
                    "id": joint_match.group(1),
                    "title": joint_match.group(2),
                })
                continue

        if current:
            tasks.append(current)

        return tasks

    def _detect_issues(self, refined: list[dict], open_tasks: list[dict]) -> list[dict]:
        """Detect quality issues in the task list."""
        issues: list[dict] = []

        # 1. Duplicate titles
        titles_by_id: dict[str, str] = {}
        for t in open_tasks:
            titles_by_id[t["id"]] = t.get("title", "").strip().lower()

        seen_titles: dict[str, str] = {}
        for tid, title in titles_by_id.items():
            if title in seen_titles:
                issues.append({
                    "type": "duplicate",
                    "severity": "warning",
                    "message": f"Tasks {seen_titles[title]} and {tid} have the same title",
                    "task_ids": [seen_titles[title], tid],
                })
            else:
                seen_titles[title] = tid

        # 2. Missing descriptions
        for t in open_tasks:
            desc = (t.get("description") or "").strip()
            if not desc:
                issues.append({
                    "type": "no_description",
                    "severity": "info",
                    "message": f"Task {t['id']} has no description",
                    "task_ids": [t["id"]],
                })

        # 3. Isolated tasks (no connections)
        for t in refined:
            if t.get("degree", 0) == 0:
                issues.append({
                    "type": "isolated",
                    "severity": "info",
                    "message": f"Task {t['id']} is not linked to any other tasks",
                    "task_ids": [t["id"]],
                })

        return issues

    # --- OS Status ---

    async def os_clock(self) -> dict:
        """Run ``ostk os clock`` and parse the key-value output into a dict.

        Returns a dict with keys like wall, session, kernel, swap,
        last_gen, audit, and focus.
        """
        raw = await self._run("os", "clock")
        result: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.startswith("ostk"):
                continue
            # Lines look like:  "  wall      2026-04-20T17:46:04Z"
            # Keys may be multi-word like "last gen"
            m = re.match(r'^([\w][\w ]*?)\s{2,}(.+)$', line)
            if m:
                key = m.group(1).strip().replace(" ", "_")
                value = m.group(2).strip()
                result[key] = value
        return result

    async def os_status(self) -> str:
        try:
            return await self._run("os", "status")
        except OstkError:
            return "no daemon running"

    async def os_metrics(self) -> str:
        try:
            return await self._run("os", "metrics")
        except OstkError:
            return "no metrics available"

    # --- Kernel / Agents ---

    async def kernel_ps(self) -> dict:
        """Query ostk kernel ps for active agents.

        Returns a dict with 'raw' (the CLI output string), 'daemon_running'
        (bool), and 'agents' (list of parsed agent dicts when available).
        Concurrent identical calls are coalesced into one subprocess spawn.
        """
        key = ("kernel_ps", self.cwd)

        async def _do_call() -> dict:
            try:
                raw = await self._run("kernel", "ps")
            except OstkError:
                raw = "no daemon running"

            daemon_running = "no daemon" not in raw.lower()
            agents: list[dict] = []

            if daemon_running and raw.strip():
                for line in raw.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith("─") or line.lower().startswith("name"):
                        continue
                    parts = line.split()
                    if parts:
                        agent: dict = {"name": parts[0], "source": "daemon"}
                        if len(parts) > 1:
                            agent["status"] = parts[1]
                        if len(parts) > 2:
                            agent["model"] = parts[2]
                        agents.append(agent)

            return {"raw": raw, "daemon_running": daemon_running, "agents": agents}

        shared = await _coalesce_call(key, _do_call)
        # Shallow copy: the dict has an "agents" list which we also copy
        # so a downstream mutation does not bleed across coalesced callers.
        return {**shared, "agents": list(shared.get("agents", []))}

    async def audit_agents(self) -> list[dict]:
        """Read .ostk/audit.jsonl and return agent lifecycle events.

        Builds a picture of agents that have been spawned (and optionally
        completed/failed) so the UI can show them even without the daemon.

        Agents that were spawned before a session.shutdown event and never
        received an agent.completed or agent.failed event are marked as
        "stopped" rather than "spawned", since the session that ran them
        has ended.
        """
        audit_path = OSTK_DIR / "audit.jsonl"
        agents_by_name: dict[str, dict] = {}
        # Shared parse cache: the 400 KB audit.jsonl only reparses when
        # its size or mtime changes, so rapid /api/agents polls do not
        # re-scan the file on every hit.
        for entry in read_audit_entries(audit_path):
            event = entry.get("event", "")
            name = entry.get("name", "")

            if event == "agent.spawned" and name:
                agents_by_name[name] = {
                    "name": name,
                    "status": "spawned",
                    "model": entry.get("model", ""),
                    "budget": entry.get("budget", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "source": "audit",
                }
            elif event == "agent.completed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "completed"
                    agents_by_name[name]["completed_at"] = entry.get("timestamp", "")
            elif event == "agent.failed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "failed"
                    agents_by_name[name]["failed_at"] = entry.get("timestamp", "")
            elif event == "agent.killed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "killed"
                    agents_by_name[name]["killed_at"] = entry.get("timestamp", "")
            elif event == "session.shutdown":
                # A session ended. Any agent still marked "spawned" (i.e. no
                # completed/failed event arrived before this shutdown) should
                # be considered stopped. The process that ran them is gone.
                for agent in agents_by_name.values():
                    if agent["status"] == "spawned":
                        agent["status"] = "stopped"

        return list(agents_by_name.values())

    async def kernel_spawn(self, name: str, prompt: str = "", model: str = "sonnet", budget: float = 2.0) -> asyncio.subprocess.Process:
        # Pass prompt via stdin, never as a CLI argument, so it does not
        # appear in the process list (ps aux / pgrep output). Needle 342.
        args = ["ostk", "kernel", "spawn", name, "--model", model, "--budget", str(budget)]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        if prompt:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
        proc.stdin.close()
        return proc

    async def kernel_kill(self, name: str) -> dict:
        """Kill a running agent by name.

        Strategy:
        1. Use pgrep to find processes whose command line contains
           'ostk kernel spawn <name>'.
        2. Send SIGTERM to each matching process.
        3. Record an agent.killed event in the audit log.

        Returns a dict with 'killed' (bool) and 'pids' (list of killed PIDs).
        """
        # Find processes matching the spawn command for this agent name.
        # The spawn command looks like: ostk kernel spawn <name> ...
        pattern = f"ostk kernel spawn {name}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-f", pattern,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            # pgrep not available on this system
            return {"killed": False, "pids": [], "error": "pgrep not available"}

        pids_text = stdout.decode().strip()
        if not pids_text:
            return {"killed": False, "pids": [], "error": "no matching process found"}

        killed_pids = []
        for pid_str in pids_text.splitlines():
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
                os.kill(pid, signal.SIGTERM)
                killed_pids.append(pid)
            except (ValueError, ProcessLookupError, PermissionError):
                continue

        if killed_pids:
            await self._record_agent_killed(name)

        return {"killed": len(killed_pids) > 0, "pids": killed_pids}

    async def _record_agent_killed(self, name: str) -> None:
        """Append an agent.killed event to audit.jsonl."""
        audit_path = OSTK_DIR / "audit.jsonl"
        entry = {
            "event": "agent.killed",
            "name": name,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "ui",
        }
        try:
            with open(audit_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    async def kernel_reap(self) -> str:
        try:
            return await self._run("kernel", "reap")
        except OstkError as e:
            return str(e)


    # --- Docs ---

    async def doc_draft(self, title: str) -> str:
        """Create a new draft document. Returns the file path."""
        return await self._run("doc", "draft", title)

    async def doc_promote(self, path: str) -> str:
        """Promote a draft to a spec. Returns the new file path."""
        return await self._run("doc", "promote", path)

    async def doc_decompose(self, path: str) -> dict:
        """Break a spec into tasks. Returns result text and extracted task IDs.

        After ostk decomposes the spec, this method:
        1. Parses the output for created needle IDs (lines matching ->NNN)
        2. Writes those IDs back to the spec's front matter ``tasks:`` field
        3. Returns ``{"result": "...", "task_ids": ["NNN", ...]}``
        """
        result = await self._run("doc", "decompose", path, "--auto")

        # Extract needle IDs from output lines like "->407" or "→407 Some task title"
        # ostk may emit ASCII "->NNN" or Unicode "→NNN"; handle both.
        task_ids: list[str] = []
        for line in result.split("\n"):
            stripped = line.strip()
            match = re.match(r"(?:->|→)(\d+)", stripped)
            if match:
                task_ids.append(match.group(1))

        # Write task IDs back to spec front matter if any were created
        if task_ids:
            self._write_tasks_to_frontmatter(path, task_ids)

        return {"result": result, "task_ids": task_ids}

    async def list_docs(self) -> list[dict]:
        """Scan docs/draft and docs/spec directories for documents.

        Returns a list of dicts with path, title, status, timestamps,
        task_ids, task_summary, acceptance_criteria, and computed status
        parsed from the YAML front matter and body.
        """
        docs_dir = Path(self.cwd) / "docs"
        results: list[dict] = []

        for subdir, status in [("draft", "draft"), ("spec", "spec")]:
            target = docs_dir / subdir
            if not target.is_dir():
                continue
            for md in sorted(target.glob("*.md")):
                doc = self._parse_doc_frontmatter(md, status)
                results.append(doc)

        # Collect all task IDs referenced by any spec. Spec front matter
        # stores bare numeric IDs ("407") but ostk returns IDs prefixed with
        # an arrow ("→407"). Normalize both sides so the lookup matches.
        all_task_ids: set[str] = set()
        for doc in results:
            all_task_ids.update(
                self._normalize_task_id(t) for t in doc.get("task_ids", [])
            )

        # Fetch task statuses in one batch if any specs reference tasks
        task_status_map: dict[str, str] = {}
        if all_task_ids:
            try:
                all_tasks = await self.list_tasks()
                for t in all_tasks:
                    tid = self._normalize_task_id(t.get("id"))
                    if tid and tid in all_task_ids:
                        task_status_map[tid] = t.get("status", "open")
            except OstkError:
                pass  # graceful degradation if ostk is unavailable

        # Enrich each doc with task_summary and computed status
        for doc in results:
            raw_ids = doc.get("task_ids", [])
            norm_ids = [self._normalize_task_id(t) for t in raw_ids]
            total = len(norm_ids)
            closed = sum(
                1 for tid in norm_ids
                if task_status_map.get(tid, "open") == "closed"
            )
            doc["task_summary"] = {
                "total": total,
                "open": total - closed,
                "closed": closed,
            }
            # Include acceptance-criteria progress in the computation so a
            # spec with all tasks closed flips to "complete" once Verify has
            # checked every box. Without an explicit Verify run, ACs start
            # unchecked and the spec stays "in-progress" so the Verify step
            # is still required in the happy path.
            ac = doc.get("acceptance_criteria", [])
            ac_all_met = bool(ac) and all(c.get("checked") for c in ac)
            doc["status"] = self.compute_spec_status(
                doc["status"], norm_ids, task_status_map, ac_all_met=ac_all_met
            )

        return results

    @staticmethod
    def _normalize_task_id(tid: Optional[str]) -> str:
        """Strip the ``→`` / ``->`` prefix so IDs compare cleanly.

        Spec front matter stores bare numeric IDs ("407") while ostk's
        ``work list --json`` emits the arrow-prefixed form ("→407"). Both
        map to the same task; without this normalization the spec status
        never flipped to Complete because the lookup always missed.
        """
        if not tid:
            return ""
        s = str(tid).strip()
        if s.startswith("→"):
            return s[1:]
        if s.startswith("->"):
            return s[2:]
        return s

    def _parse_doc_frontmatter(self, path: Path, fallback_status: str) -> dict:
        """Read YAML front matter from a markdown file.

        Returns a dict with the standard fields plus Phase 2 additions:
        - ``task_ids``: list of needle IDs from the ``tasks:`` front matter
        - ``acceptance_criteria``: list of ``{text, checked}`` dicts parsed
          from ``- [ ]`` / ``- [x]`` checkboxes in the body
        """
        text = path.read_text()
        doc: dict = {
            "path": str(path.relative_to(self.cwd)),
            "filename": path.name,
            "title": path.stem.replace("-", " "),
            "status": fallback_status,
            "created_at": "",
            "promoted_at": "",
            "body": "",
            "task_ids": [],
            "acceptance_criteria": [],
        }

        lines = text.split("\n")
        if lines and lines[0].strip() == "---":
            end = None
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "---":
                    end = i
                    break
            if end:
                # Track whether we are inside the tasks: YAML list
                in_tasks_list = False
                for line in lines[1:end]:
                    stripped = line.strip()
                    # Detect YAML list items under tasks:
                    if in_tasks_list:
                        if stripped.startswith("- "):
                            val = stripped[2:].strip().strip('"').strip("'")
                            if val:
                                doc["task_ids"].append(val)
                            continue
                        else:
                            in_tasks_list = False
                    if ":" in line:
                        key, _, val = line.partition(":")
                        key = key.strip()
                        val = val.strip()
                        if key == "title":
                            doc["title"] = val
                        elif key == "status":
                            doc["status"] = val
                        elif key == "created_at":
                            doc["created_at"] = val
                        elif key == "promoted_at":
                            doc["promoted_at"] = val
                        elif key == "tasks":
                            # Inline format: tasks: ["407", "408"]
                            if val.startswith("["):
                                import ast
                                try:
                                    parsed = ast.literal_eval(val)
                                    doc["task_ids"] = [str(t) for t in parsed]
                                except (ValueError, SyntaxError):
                                    pass
                            else:
                                # Block format: subsequent lines are list items
                                in_tasks_list = True
                # Body is everything after the front matter
                doc["body"] = "\n".join(lines[end + 1:]).strip()
        else:
            doc["body"] = text.strip()

        # Parse acceptance criteria from markdown checkboxes in the body
        doc["acceptance_criteria"] = self._parse_acceptance_criteria(doc["body"])

        return doc

    @staticmethod
    def _parse_acceptance_criteria(body: str) -> list[dict]:
        """Extract ``- [ ]`` and ``- [x]`` checkboxes from markdown body."""
        criteria: list[dict] = []
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
                text = stripped[5:].strip()
                criteria.append({"text": text, "checked": True})
            elif stripped.startswith("- [ ]"):
                text = stripped[5:].strip()
                criteria.append({"text": text, "checked": False})
        return criteria

    def _write_tasks_to_frontmatter(self, spec_path: str, task_ids: list[str]) -> None:
        """Write task IDs into a spec's YAML front matter ``tasks:`` field.

        Merges with any existing task IDs to avoid duplicates.
        """
        full_path = Path(self.cwd) / spec_path
        if not full_path.exists():
            return

        text = full_path.read_text()
        lines = text.split("\n")

        if not (lines and lines[0].strip() == "---"):
            return

        end = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i
                break
        if end is None:
            return

        # Parse existing task IDs from front matter
        existing_ids: list[str] = []
        fm_lines = lines[1:end]
        tasks_start = None
        tasks_end = None
        in_tasks = False
        for idx, line in enumerate(fm_lines):
            stripped = line.strip()
            if in_tasks:
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip('"').strip("'")
                    if val:
                        existing_ids.append(val)
                    tasks_end = idx + 1
                    continue
                else:
                    in_tasks = False
            if stripped.startswith("tasks:"):
                _, _, val = stripped.partition(":")
                val = val.strip()
                tasks_start = idx
                tasks_end = idx + 1
                if val.startswith("["):
                    import ast
                    try:
                        parsed = ast.literal_eval(val)
                        existing_ids = [str(t) for t in parsed]
                    except (ValueError, SyntaxError):
                        pass
                elif not val:
                    in_tasks = True

        # Merge: add new IDs that are not already present
        merged = list(existing_ids)
        for tid in task_ids:
            if tid not in merged:
                merged.append(tid)

        # Build the tasks: YAML block
        tasks_yaml_lines = ["tasks:"]
        for tid in merged:
            tasks_yaml_lines.append(f'  - "{tid}"')

        # Rebuild front matter
        if tasks_start is not None:
            new_fm = fm_lines[:tasks_start] + tasks_yaml_lines + fm_lines[tasks_end:]
        else:
            new_fm = fm_lines + tasks_yaml_lines

        new_lines = ["---"] + new_fm + ["---"] + lines[end + 1:]
        full_path.write_text("\n".join(new_lines))

    def _resolve_spec_path(self, spec_path: str) -> Path:
        """Resolve a spec path to a full filesystem path, with validation."""
        full = Path(self.cwd) / spec_path
        if not full.exists():
            raise OstkError(f"Spec not found: {spec_path}")
        docs_dir = Path(self.cwd) / "docs"
        resolved = full.resolve()
        if not str(resolved).startswith(str(docs_dir.resolve())):
            raise OstkError("Spec path must be under docs/")
        return full

    async def spec_tasks(self, spec_path: str) -> list[dict]:
        """Read a spec's linked tasks and return their current status.

        Returns a list of dicts: ``[{id, title, status, priority}, ...]``
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        task_ids = doc.get("task_ids", [])
        if not task_ids:
            return []

        # Fetch all tasks and filter to the ones linked to this spec.
        # IDs on both sides may or may not carry the arrow prefix, so
        # normalize before comparing.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        matched: list[dict] = []
        for t in all_tasks:
            if self._normalize_task_id(t.get("id")) in id_set:
                matched.append({
                    "id": t["id"],
                    "title": t.get("title", ""),
                    "status": t.get("status", "open"),
                    "priority": t.get("priority", "P1"),
                })
        return matched

    async def spec_verify(self, spec_path: str) -> dict:
        """Verify a spec's acceptance criteria against linked task status.

        Returns::

            {
                "criteria": [{"text": "...", "met": bool}, ...],
                "all_met": bool,
                "task_summary": {"total": N, "open": N, "closed": N}
            }
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        ac = doc.get("acceptance_criteria", [])
        task_ids = doc.get("task_ids", [])

        # Get task statuses. Normalize IDs so arrow-prefixed tasks returned
        # by ostk match the bare numeric IDs stored in the spec front matter.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        linked_tasks = [
            t for t in all_tasks
            if self._normalize_task_id(t.get("id")) in id_set
        ]

        total = len(linked_tasks)
        closed = sum(1 for t in linked_tasks if t.get("status") == "closed")
        open_count = total - closed

        # Acceptance criteria are treated as met when either the checkbox is
        # already checked in the spec body, OR every linked task is closed
        # and there are tasks to begin with. This mirrors a real Verify run:
        # once the agents finish every task, the checklist is considered
        # satisfied. We also persist the checkmarks back to the spec body
        # so subsequent list_docs calls see the spec as complete.
        all_tasks_closed = total > 0 and closed == total
        criteria = [
            {
                "text": c["text"],
                "met": c["checked"] or all_tasks_closed,
            }
            for c in ac
        ]
        all_met = bool(criteria) and all(c["met"] for c in criteria)

        # If Verify confirms every box, write the checkmarks back to disk so
        # the spec's computed status flips to complete on the next list.
        if all_met and not all(c["checked"] for c in ac):
            try:
                self._check_all_acceptance_criteria(spec_path)
            except Exception:
                pass  # best effort; stale state is better than a 500

        return {
            "criteria": criteria,
            "all_met": all_met,
            "task_summary": {"total": total, "open": open_count, "closed": closed},
        }

    def _check_all_acceptance_criteria(self, spec_path: str) -> None:
        """Flip every ``- [ ]`` line in a spec body to ``- [x]``.

        Called when Verify decides every criterion is met. Keeps the spec
        file, the Verify response, and the derived Complete status aligned.
        """
        full = Path(self.cwd) / spec_path
        if not full.exists():
            return
        text = full.read_text()
        new_lines: list[str] = []
        for line in text.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("- [ ]"):
                indent = line[: len(line) - len(stripped)]
                new_lines.append(indent + "- [x]" + stripped[5:])
            else:
                new_lines.append(line)
        full.write_text("\n".join(new_lines))

    async def spec_build(self, spec_path: str) -> dict:
        """Build agent configs for a spec's open tasks.

        Returns agent configurations without actually spawning them.
        The frontend (Phase 3) handles the actual spawning.

        Returns::

            {"agents": [{"name": "...", "task_id": "...", "prompt": "..."}, ...]}
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        spec_name = Path(spec_path).stem
        spec_text = doc.get("body", "")
        task_ids = doc.get("task_ids", [])

        if not task_ids:
            return {"agents": []}

        # Get open tasks only. Normalize so arrow-prefixed IDs match.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        open_tasks = [
            t for t in all_tasks
            if self._normalize_task_id(t.get("id")) in id_set
            and t.get("status") != "closed"
        ]

        agents: list[dict] = []
        for task in open_tasks:
            task_id = task["id"]
            task_title = task.get("title", "")
            agent_name = f"spec-{spec_name}-{task_id}"
            prompt = (
                f"You are building task {task_id}: {task_title}\n\n"
                f"## Spec\n\n{spec_text}\n\n"
                f"## Instructions\n\n"
                f"- Complete the task described above.\n"
                f"- Use `ostk commit --spec {spec_name} --needle {task_id}` "
                f"for all your commits so your work is attributed to the spec.\n"
                f"- When done, close the task with `ostk work close ->{task_id}`.\n"
            )
            agents.append({
                "name": agent_name,
                "task_id": task_id,
                "task_title": task_title,
                "prompt": prompt,
            })

        return {"agents": agents}

    @staticmethod
    def compute_spec_status(
        base_status: str,
        task_ids: list[str],
        task_statuses: dict[str, str],
        ac_all_met: bool = False,
    ) -> str:
        """Derive a spec's lifecycle status from its tasks.

        - ``draft``: no tasks, original status is draft
        - ``ready``: promoted (status=spec) but no tasks yet
        - ``in-progress``: has tasks and at least one is open, OR all tasks
          are closed but Verify has not yet marked every acceptance
          criterion met (so the checklist is still visibly incomplete).
        - ``complete``: has tasks, all are closed, AND every acceptance
          criterion is checked (i.e. Verify has run and all boxes are met).
        - Falls back to base_status for unknown states.
        """
        if base_status == "draft":
            return "draft"
        if not task_ids:
            return "ready"
        statuses = [task_statuses.get(tid, "open") for tid in task_ids]
        all_closed = all(s == "closed" for s in statuses)
        if all_closed and ac_all_met:
            return "complete"
        return "in-progress"

    # --- Threads ---

    async def create_thread(self, name: str, needle_ids: Optional[list[str]] = None) -> str:
        """Create a thread grouping needles via the ostk CLI."""
        args = ["thread", "create", name]
        if needle_ids:
            args += ["--needles"] + needle_ids
            args += ["--force"]  # allow missing/closed needles
        return await self._run(*args)

    async def list_threads(self) -> str:
        """List all threads via the ostk CLI."""
        return await self._run("thread", "list")

    # --- MCP Servers ---

    async def mcp_list(self) -> list[dict]:
        """List MCP servers configured in ostk (via HUMANFILE).

        Parses the plain-text output of ``ostk mcp list`` into a list of
        dicts with ``name`` and ``command`` keys.  Returns an empty list
        when no servers are configured.
        """
        try:
            raw = await self._run("mcp", "list")
        except OstkError:
            return []

        if "no mcp servers configured" in raw.lower():
            return []

        return self._parse_mcp_list(raw)

    @staticmethod
    def _parse_mcp_list(raw: str) -> list[dict]:
        """Parse the plain-text output of ``ostk mcp list``.

        Expected format (one server per line)::

            name    command
            ────    ───────
            linear  npx -y @anthropic/linear-mcp-server
            github  gh mcp-server

        Also handles a simpler ``name: command`` format.
        """
        servers: list[dict] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.lower().startswith("name"):
                continue
            # Try tab/multi-space separated: "linear  npx -y ..."
            parts = re.split(r"\s{2,}|\t", line, maxsplit=1)
            if len(parts) == 2:
                servers.append({"name": parts[0].strip(), "command": parts[1].strip()})
                continue
            # Try "name: command" format
            if ":" in line:
                name, _, command = line.partition(":")
                name = name.strip()
                command = command.strip()
                if name and command:
                    servers.append({"name": name, "command": command})
        return servers

    # --- Nudges ---

    async def purge_agent_chat_state(self, agent_name: str) -> dict:
        """Delete every on-disk chat artifact for ``agent_name``.

        Called at the top of a fresh spawn so a second Roadmap (or any
        template) run never sees the previous run's nudges, replies, or
        transcript. Without this purge the UI's inline chat panel merges
        stale file-based entries from ``.ostk/nudges/{name}/`` and
        ``.ostk/nudges/{name}/replies/`` with the new agent's state and
        the operator sees messages that do not belong to the new run.

        Removes:
          * ``.ostk/nudges/{agent_name}/`` (user messages)
          * ``.ostk/nudges/{agent_name}/replies/`` (agent replies)
          * ``transcripts/{agent_name}.md`` (legacy markdown transcript)

        Safe to call when the directories do not exist (no-op). Errors
        on individual files are swallowed so one bad file does not
        block the spawn. Returns a small dict with the counts of
        artifacts removed so callers and tests can assert the purge
        actually fired.
        """
        import shutil

        counts = {"nudges": 0, "replies": 0, "transcripts": 0}

        # 1. Nudges + replies directory. A single rmtree on the parent
        #    takes both in one shot because replies is nested under it.
        nudges_dir = NUDGES_DIR / agent_name
        if nudges_dir.exists():
            try:
                nudge_files = list(nudges_dir.glob("*.json"))
                counts["nudges"] = len(nudge_files)
                replies_dir = nudges_dir / "replies"
                if replies_dir.exists():
                    counts["replies"] = len(list(replies_dir.glob("*.json")))
                shutil.rmtree(nudges_dir)
            except OSError:
                # Swallow: a partially broken dir should not block a
                # fresh spawn. The UI will still get a clean chat as
                # long as at least the new nudges land on top.
                pass

        # 2. Legacy markdown transcript. The spawn path reopens this
        #    with ``open(..., "w")`` which truncates, but we unlink
        #    first so a stale stub from a crashed prior run cannot
        #    briefly surface before the new output lands.
        transcript_path = PROJECT_ROOT / "transcripts" / f"{agent_name}.md"
        if transcript_path.exists():
            try:
                transcript_path.unlink()
                counts["transcripts"] = 1
            except OSError:
                pass

        return counts

    async def write_nudge(
        self,
        agent_name: str,
        message: str,
        kind: Optional[str] = None,
    ) -> dict:
        """Write a nudge file to .ostk/nudges/{agent_name}/.

        Nudges are the file-based messaging system for communicating with
        running agents. Each nudge is a JSON file with a timestamp-based
        name so multiple nudges can queue up.

        ``kind`` tags the nudge so the agent's mailbox poller and the
        inline chat UI can distinguish a plain follow-up message
        (``user_message``) from a course-correcting instruction
        (``correction``). When omitted, the field is left absent on
        disk for backward compatibility with older readers.
        """
        nudges_dir = NUDGES_DIR / agent_name
        nudges_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc)
        filename = f"{ts.strftime('%Y%m%dT%H%M%S')}_{int(ts.timestamp() * 1000) % 1000:03d}.json"
        nudge_path = nudges_dir / filename

        nudge_data: dict = {
            "agent": agent_name,
            "message": message,
            "timestamp": ts.isoformat(),
            "source": "ui",
        }
        if kind:
            nudge_data["kind"] = kind

        atomic_write_text(nudge_path, json.dumps(nudge_data, indent=2) + "\n")
        return nudge_data

    async def list_nudges(self, agent_name: str) -> list[dict]:
        """Read all nudge files for an agent, sorted oldest first."""
        nudges_dir = NUDGES_DIR / agent_name
        if not nudges_dir.exists():
            return []

        nudges = []
        for f in sorted(nudges_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                data["file"] = f.name
                nudges.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return nudges

    async def append_nudge_reply(
        self,
        agent_name: str,
        message: str,
        in_reply_to: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> dict:
        """Write an agent's reply to a prior nudge under .ostk/nudges/{agent}/replies/.

        Replies are stored as their own JSON files so any observer that
        polls the nudge directory also picks up new replies without
        schema changes. ``in_reply_to`` is an optional timestamp that
        correlates the reply to a specific user message, so the UI can
        thread them. When omitted, the reply is treated as a free form
        status update from the agent.

        ``kind`` lets the writer mark the reply so the inline chat UI
        can distinguish a warm ack bot reply (``ack``) from a real
        substantive subagent reply (``real``). Real subagent replies
        coming through POST /reply default to ``real`` so the UI can
        always tell the two apart, even when the field is absent on
        older records.

        Write-time dedupe
        -----------------
        If an identical ``(in_reply_to, message, kind)`` record was
        written within the last 30 seconds, the new write is skipped and
        the existing record is returned unchanged. This is the last line
        of defense for the inline chat dupe bug Tori hit: even when the
        process-local acked-id set in ``chat_ack_bot`` gets wiped (a
        restart, a backend reload during development, a reload watchdog
        replay), the on-disk store itself refuses to accept a second
        copy of the same reply. Callers never see an error: they get
        the first record, so the UI shows one bubble per ack.
        """
        replies_dir = NUDGES_DIR / agent_name / "replies"
        replies_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc)

        # Write-time dedupe. Scan the last few reply files. If one of
        # them has the same ``(in_reply_to, message, kind)`` fingerprint
        # AND was written within the last 30 seconds, treat this call
        # as a duplicate and return the existing record. The 30 second
        # window covers the practical failure modes (ack bot restart,
        # rapid double-ack race) without ever folding two genuinely
        # distinct replies that happen to repeat the same text hours
        # apart. We only walk the tail of the sorted list so this scan
        # stays O(1) amortised even when the replies dir has thousands
        # of entries.
        try:
            recent = sorted(replies_dir.glob("*.json"))[-16:]
            cutoff = ts.timestamp() - 30.0
            for existing_path in recent:
                try:
                    existing = json.loads(existing_path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                existing_kind = existing.get("kind") if kind else None
                if (
                    existing.get("message") == message
                    and existing.get("in_reply_to") == in_reply_to
                    and existing_kind == kind
                ):
                    existing_ts_raw = str(existing.get("timestamp", ""))
                    try:
                        existing_dt = datetime.fromisoformat(existing_ts_raw)
                    except ValueError:
                        continue
                    if existing_dt.timestamp() >= cutoff:
                        existing["file"] = existing_path.name
                        return existing
        except OSError:
            # A broken replies dir should not block a fresh write. Fall
            # through to the normal path so the caller's message still
            # lands on disk.
            pass

        # Use microsecond precision in the filename so two replies written
        # back to back with different identities never collide and silently
        # overwrite each other. Millisecond precision was not enough: the
        # ack bot's burst path (one ack per nudge within a few microseconds)
        # and the dedupe test case both exposed a collision where the
        # second write landed on the same filename as the first.
        filename = (
            f"{ts.strftime('%Y%m%dT%H%M%S')}_"
            f"{ts.microsecond:06d}.json"
        )
        reply_path = replies_dir / filename

        reply_data: dict = {
            "agent": agent_name,
            "message": message,
            "timestamp": ts.isoformat(),
            "source": "agent",
            "in_reply_to": in_reply_to,
        }
        if kind:
            reply_data["kind"] = kind

        atomic_write_text(reply_path, json.dumps(reply_data, indent=2) + "\n")
        return reply_data

    async def list_nudge_replies(self, agent_name: str) -> list[dict]:
        """Read all reply files for an agent, sorted oldest first."""
        replies_dir = NUDGES_DIR / agent_name / "replies"
        if not replies_dir.exists():
            return []

        replies = []
        for f in sorted(replies_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                data["file"] = f.name
                replies.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return replies

    # --- Grants / Permission Requests ---

    async def list_grants(self, status: str = "pending") -> list[dict]:
        """List agent permission requests filtered by status.

        Calls ``ostk grant list --status <status> --json`` and returns
        the parsed JSON list.  When ostk reports 'no <status> requests'
        (i.e. empty results), an empty list is returned.
        """
        try:
            return await self._run_json("grant", "list", "--status", status, "--json")
        except OstkError as e:
            # ostk prints 'no pending requests' (non-JSON) when the list
            # is empty, which causes _run to raise OstkError.
            if "no" in str(e).lower() and "requests" in str(e).lower():
                return []
            raise
        except json.JSONDecodeError:
            return []

    async def approve_grant(self, grant_id: str, ttl: int = 0, scope: Optional[str] = None) -> str:
        """Approve a permission request by ID.

        ``ttl`` is lifetime in seconds (0 = no expiry).
        ``scope`` overrides the default target scope if provided.
        """
        args = ["grant", "approve", grant_id, "--ttl", str(ttl)]
        if scope:
            args += ["--scope", scope]
        return await self._run(*args)

    async def deny_grant(self, grant_id: str, reason: str = "not permitted") -> str:
        """Deny a permission request by ID with an optional reason."""
        return await self._run("grant", "deny", grant_id, "--reason", reason)

    # --- Secrets ---

    # --- Decisions ---

    async def log_decision(self, key: str, value: str, reason: str = "") -> str:
        """Log a decision via ``ostk decide <key> <value> --reason <reason>``.

        Appends an entry to .ostk/decisions.jsonl with the key, value,
        reason, and a timestamp.
        """
        args = ["decide", key, value]
        if reason:
            args += ["--reason", reason]
        return await self._run(*args)

    def list_decisions(self) -> list[dict]:
        """Read .ostk/decisions.jsonl and return entries as a list of dicts.

        Each entry has key, value, reason, and timestamp fields.
        Returns newest first.
        """
        decisions_path = Path(self.cwd) / ".ostk" / "decisions.jsonl"
        if not decisions_path.exists():
            return []
        entries: list[dict] = []
        try:
            text = decisions_path.read_text()
        except OSError:
            return []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        entries.reverse()  # newest first
        return entries

    async def get_activity_summary(self) -> str:
        """Build a plain-text summary of the last 24 hours using ostk data.

        Combines ostk os history (recent events) and ostk os diff (session
        delta) into a short text block. This runs locally without any LLM
        call, so it is fast and free.

        Returns the summary text, or empty string if nothing is available.
        """
        parts: list[str] = []

        # Recent history (last 50 events)
        try:
            events = await self.get_history(last=50)
            if events:
                # Count events by type
                counts: dict[str, int] = {}
                for ev in events:
                    event_type = ev.get("event", "unknown")
                    counts[event_type] = counts.get(event_type, 0) + 1

                summary_lines = []
                for etype, count in sorted(counts.items(), key=lambda x: -x[1]):
                    summary_lines.append(f"  {count}x {etype}")
                parts.append("Recent activity:\n" + "\n".join(summary_lines))
        except OstkError:
            pass

        # Session diff
        try:
            diff = await self.get_session_diff()
            diff_lines = []
            if diff.get("files_changed"):
                diff_lines.append(f"  {len(diff['files_changed'])} files changed this session")
            if diff.get("needles_filed"):
                diff_lines.append(f"  {len(diff['needles_filed'])} tasks filed this session")
            if diff.get("audit_total"):
                diff_lines.append(f"  {diff['audit_total']} total audit events")
            if diff_lines:
                parts.append("Session summary:\n" + "\n".join(diff_lines))
        except OstkError:
            pass

        return "\n\n".join(parts)

    async def secret_set(self, key: str, value: str) -> str:
        """Store a secret in the system keychain via ostk."""
        return await self._run("secret", "set", key, "--value", value)

    async def secret_get(self, key: str) -> str:
        """Retrieve a secret from the system keychain.

        Returns the secret value, or an empty string if the key is not found.
        """
        try:
            return await self._run("secret", "get", key)
        except OstkError:
            return ""

    async def secret_list(self) -> list[dict]:
        """List available secret names with their status.

        Returns a list of dicts with 'name', 'tag', and 'available' fields.
        """
        try:
            raw = await self._run("secret", "list")
        except OstkError:
            return []

        secrets: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "secret list" in line.lower() or "/" in line and "keys" in line:
                continue
            # Parse lines like:
            #   + ANTHROPIC_API_KEY              [anthropic] available
            #   - GEMINI_API_KEY                 [google] missing
            available = line.startswith("+")
            # Strip the leading + or - indicator
            cleaned = line.lstrip("+-").strip()
            parts = cleaned.split()
            if not parts:
                continue
            name = parts[0]
            tag = ""
            for part in parts:
                if part.startswith("[") and part.endswith("]"):
                    tag = part.strip("[]")
                    break
            secrets.append({
                "name": name,
                "available": available,
                "tag": tag,
            })
        return secrets

    # --- Agent Correction (needle 333) ---

    async def correct_agent(self, agent_name: str, message: str) -> str:
        """Send a structured correction to an agent via ostk work correct.

        This calls the ostk :correct verb which records the correction in
        the audit trail and delivers it to the agent. The caller should
        also send a regular nudge so both systems stay in sync.
        """
        return await self._run("work", "correct", agent_name, message, timeout=10)

    # --- Agent Lifecycle / Context Pressure (needle 337) ---

    async def agent_lifecycle(self, agent_name: str) -> dict | None:
        """Query the lifecycle service for an agent.

        The :lifecycle service may be inactive. If it fails, returns None
        so callers can degrade gracefully.
        """
        try:
            raw = await self._run("work", "lifecycle", agent_name, timeout=5)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw, "agent": agent_name}
        except OstkError:
            return None

    async def check_context_pressure(self, agent_name: str) -> dict | None:
        """Check context pressure for an agent via the :dying service.

        The :dying service may be inactive. If it fails, returns None so
        callers can degrade gracefully without showing anything in the UI.
        """
        try:
            raw = await self._run("work", "dying", agent_name, timeout=5)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pct_match = re.search(r"(\d+)%", raw)
                if pct_match:
                    return {
                        "agent": agent_name,
                        "pressure_pct": int(pct_match.group(1)),
                        "raw": raw,
                    }
                return {"agent": agent_name, "raw": raw}
        except OstkError:
            return None

    # --- Coordination Locks (needle 338) ---

    async def list_locks(self) -> list[dict]:
        """List active coordination locks via ostk lock list.

        Returns a list of dicts with lock name, holder, and creation time.
        If no locks exist or the command fails, returns an empty list.
        """
        try:
            raw = await self._run("lock", "list", timeout=5)
        except OstkError:
            return []

        if not raw.strip() or "no active locks" in raw.lower():
            return []

        locks: list[dict] = []
        # Try JSON parse first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "locks" in parsed:
                return parsed["locks"]
        except json.JSONDecodeError:
            pass

        # Parse text output line by line
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.startswith("="):
                continue
            # Skip header lines
            if "LOCK" in line.upper() and "NAME" in line.upper():
                continue
            parts = line.split()
            if len(parts) >= 1:
                lock: dict = {"name": parts[0]}
                if len(parts) >= 2:
                    lock["holder"] = parts[1]
                if len(parts) >= 3:
                    lock["created_at"] = " ".join(parts[2:])
                locks.append(lock)
        return locks

    async def release_lock(self, name: str) -> str:
        """Release a coordination lock by name."""
        return await self._run("lock", "release", name, timeout=5)


ostk = OstkService()

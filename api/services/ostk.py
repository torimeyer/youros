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

PROJECT_DIR = str(PROJECT_ROOT)
OSTK_DIR = _OSTK_DIR
NUDGES_DIR = OSTK_DIR / "nudges"


class OstkError(Exception):
    pass


class OstkService:
    def __init__(self, cwd: str = PROJECT_DIR):
        self.cwd = cwd

    async def _run(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "ostk", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode().strip()
        if proc.returncode != 0:
            err = stderr.decode().strip() or output
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
        return await self._run_json(*args)

    async def add_task(self, title: str, priority: str = "P1") -> str:
        return await self._run("work", "add", title, "--priority", priority)

    async def close_task(self, task_id: str) -> str:
        return await self._run("work", "close", task_id)

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
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"reopened {task_id}"

    async def update_task_priority(self, task_id: str, priority: str) -> str:
        """Update a task's priority by editing the JSONL file directly."""
        valid = {"P0", "P1", "P2"}
        if priority not in valid:
            raise OstkError(f"invalid priority '{priority}', must be one of {valid}")

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
                elif stripped.startswith("\u2713") or stripped.startswith("\u2192"):
                    resolved = stripped.startswith("\u2713")
                    # Remove one leading ✓ or → plus whitespace
                    text = re.sub(r"^[\u2713\u2192]\s*", "", stripped).strip()
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

    async def compile_hay(self, dry_run: bool = False) -> str:
        args = ["work", "compile"]
        if dry_run:
            args.append("--dry-run")
        return await self._run(*args)

    async def delete_hay(self, straw: str) -> str:
        """Remove a hay entry from audit.jsonl by its straw text."""
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
                continue  # skip this line to delete it
            updated.append(line)

        if not found:
            raise OstkError(f"hay item not found: {straw}")

        audit_path.write_text("\n".join(updated) + "\n")
        return f"deleted hay: {straw}"

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
        with open(audit_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def list_converted_hay(self) -> list[dict]:
        """Return hay items that have been converted into tasks.

        Reads audit.jsonl for hay.converted events and returns a list of
        dicts with the straw text and the task ID they became.
        """
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if not audit_path.exists():
            return []

        converted: list[dict] = []
        try:
            text = audit_path.read_text()
        except OSError:
            return []

        for line in text.strip().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
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
        """Search tasks and ideas by concept using ostk work near.

        Returns a dict with 'tasks' (list of matching needles) and
        'ideas' (list of matching hay items from the audit log).
        The CLI output is parsed into structured results.
        """
        tasks: list[dict] = []
        ideas: list[dict] = []

        # 1. Search tasks (needles) via ostk work near
        try:
            raw = await self._run("work", "near", query)
            tasks = self._parse_near_output(raw)
        except OstkError:
            # "no open needles matching ..." is not a real error
            pass

        # 2. Search ideas (hay) by scanning audit.jsonl for hay.filed events
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if audit_path.exists():
            try:
                text = audit_path.read_text()
            except OSError:
                text = ""

            # Build a set of converted straws so we can mark them
            converted_straws: set[str] = set()
            for line in text.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("event") == "hay.converted":
                    converted_straws.add(entry.get("straw", ""))

            query_lower = query.lower()
            for line in text.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("event") == "hay.filed":
                    straw = entry.get("straw", "")
                    if query_lower in straw.lower():
                        ideas.append({
                            "straw": straw,
                            "timestamp": entry.get("timestamp", ""),
                            "converted": straw in converted_straws,
                        })

        return {"tasks": tasks, "ideas": ideas, "query": query}

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

        Expected format:
          [2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management
        """
        m = re.match(
            r"\[(\d{4}-\d{2}-\d{2}T[\d:]+Z)\]\s+(\S+)\s+(.*)",
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
        """
        try:
            raw = await self._run("kernel", "ps")
        except OstkError:
            raw = "no daemon running"

        daemon_running = "no daemon" not in raw.lower()
        agents: list[dict] = []

        if daemon_running and raw.strip():
            # Parse tabular or line-based output from kernel ps.
            # Each non-header line with an agent name is treated as an entry.
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
        if not audit_path.exists():
            return []

        agents_by_name: dict[str, dict] = {}
        try:
            text = audit_path.read_text()
        except OSError:
            return []

        for line in text.strip().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

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
        args = ["ostk", "kernel", "spawn", name]
        if prompt:
            args.append(prompt)
        args += ["--model", model, "--budget", str(budget)]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
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

    async def doc_decompose(self, path: str) -> str:
        """Break a spec into tasks. Returns the created task list."""
        return await self._run("doc", "decompose", path, "--auto")

    async def list_docs(self) -> list[dict]:
        """Scan docs/draft and docs/spec directories for documents.

        Returns a list of dicts with path, title, status, and timestamps
        parsed from the YAML front matter.
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

        return results

    def _parse_doc_frontmatter(self, path: Path, fallback_status: str) -> dict:
        """Read YAML front matter from a markdown file."""
        text = path.read_text()
        doc: dict = {
            "path": str(path.relative_to(self.cwd)),
            "filename": path.name,
            "title": path.stem.replace("-", " "),
            "status": fallback_status,
            "created_at": "",
            "promoted_at": "",
            "body": "",
        }

        lines = text.split("\n")
        if lines and lines[0].strip() == "---":
            end = None
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "---":
                    end = i
                    break
            if end:
                for line in lines[1:end]:
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
                # Body is everything after the front matter
                doc["body"] = "\n".join(lines[end + 1:]).strip()
        else:
            doc["body"] = text.strip()

        return doc

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

    async def write_nudge(self, agent_name: str, message: str) -> dict:
        """Write a nudge file to .ostk/nudges/{agent_name}/.

        Nudges are the file-based messaging system for communicating with
        running agents. Each nudge is a JSON file with a timestamp-based
        name so multiple nudges can queue up.
        """
        nudges_dir = NUDGES_DIR / agent_name
        nudges_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc)
        filename = f"{ts.strftime('%Y%m%dT%H%M%S')}_{int(ts.timestamp() * 1000) % 1000:03d}.json"
        nudge_path = nudges_dir / filename

        nudge_data = {
            "agent": agent_name,
            "message": message,
            "timestamp": ts.isoformat(),
            "source": "ui",
        }

        nudge_path.write_text(json.dumps(nudge_data, indent=2) + "\n")
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


ostk = OstkService()

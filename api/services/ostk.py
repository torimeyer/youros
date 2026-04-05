import asyncio
import json
import re
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

    async def next_task(self) -> str:
        return await self._run("work", "next")

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

    # --- OS Status ---

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

    async def kernel_reap(self) -> str:
        try:
            return await self._run("kernel", "reap")
        except OstkError as e:
            return str(e)

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


ostk = OstkService()

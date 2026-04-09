import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from models.schemas import AgentSpawn, AgentNudge, GrantApprove, GrantDeny
from services.ostk import ostk, OstkError
import services.agent_memory as agent_memory_svc


class AgentMemorySave(BaseModel):
    key: str
    value: str


class AgentComplete(BaseModel):
    summary: Optional[str] = None

router = APIRouter(tags=["agents"])

# In-memory registry of active agent processes
active_agents: dict[str, object] = {}

# Spawn metadata (timestamp, budget, model) for API-spawned agents
agent_metadata: dict[str, dict] = {}

# In-memory log of nudges sent during this session (visible in UI)
nudge_history: dict[str, list[dict]] = {}

from config import AGENTS_DIR, OSTK_DIR

# Persistent file tracking agent state across server restarts
AGENT_STATE_PATH = OSTK_DIR / "agent_state.json"


def _load_agent_state() -> dict:
    """Load persisted agent state from disk."""
    if AGENT_STATE_PATH.exists():
        try:
            return json.loads(AGENT_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_agent_state():
    """Persist current agent metadata to disk."""
    try:
        AGENT_STATE_PATH.write_text(json.dumps(agent_metadata, indent=2))
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    import os
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _recover_stale_agents():
    """On startup, mark any persisted 'running' agents as 'abandoned'.

    An agent that was left as 'running' in the state file when the server
    stopped has no live process now. We cannot know whether it finished or
    crashed, so we mark it 'abandoned' so it does not show as running forever.
    """
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        # Claude Code agents have no local PID — they run as remote subprocesses.
        # Leave them alone; they mark themselves complete via the API.
        if meta.get("source") == "claude-code":
            continue
        pid = meta.get("pid")
        # If there is a live PID we can verify, leave it alone.
        if pid and _is_pid_alive(pid):
            continue
        # No live PID (or no PID recorded at all). Mark as abandoned.
        meta["status"] = "abandoned"
        meta["abandoned_at"] = datetime.now(timezone.utc).isoformat()
        changed = True
    if changed:
        _save_agent_state()


# Restore metadata from disk on startup, then recover any stale running agents.
agent_metadata.update(_load_agent_state())
_recover_stale_agents()

# Persistent file for learned agent durations
DURATION_STATS_PATH = OSTK_DIR / "agent_durations.json"


def _load_duration_stats() -> dict:
    """Load historical agent duration stats from disk."""
    if DURATION_STATS_PATH.exists():
        try:
            return json.loads(DURATION_STATS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"durations": []}


def _save_duration(model: str, budget: float, duration_sec: float):
    """Record a completed agent's duration for future estimates."""
    stats = _load_duration_stats()
    stats["durations"].append({
        "model": model,
        "budget": budget,
        "duration_sec": round(duration_sec),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Keep last 100 entries
    stats["durations"] = stats["durations"][-100:]
    try:
        DURATION_STATS_PATH.write_text(json.dumps(stats, indent=2))
    except OSError:
        pass


def _avg_minutes_per_dollar() -> dict[str, float]:
    """Calculate average minutes per dollar of budget from historical data."""
    stats = _load_duration_stats()
    # Group by model
    model_data: dict[str, list[tuple[float, float]]] = {}
    for entry in stats.get("durations", []):
        model = entry.get("model", "")
        budget = entry.get("budget", 0)
        duration = entry.get("duration_sec", 0)
        if budget > 0 and duration > 0:
            model_data.setdefault(model, []).append((budget, duration))

    result = {}
    for model, entries in model_data.items():
        total_minutes = sum(d / 60 for _, d in entries)
        total_budget = sum(b for b, _ in entries)
        if total_budget > 0:
            result[model] = round(total_minutes / total_budget, 2)
    return result


def _get_transcript_metrics(name: str) -> dict:
    """Get activity metrics from an agent's transcript file."""
    from config import PROJECT_ROOT
    transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
    if not transcript.exists():
        return {"transcript_bytes": 0, "transcript_lines": 0}
    try:
        size = transcript.stat().st_size
        lines = 0
        with open(transcript, "rb") as f:
            for _ in f:
                lines += 1
        return {"transcript_bytes": size, "transcript_lines": lines}
    except OSError:
        return {"transcript_bytes": 0, "transcript_lines": 0}


def _format_jsonl_transcript(jsonl_path: Path) -> str:
    """Parse a Claude Code agent JSONL output file into a readable transcript.

    Each line is a JSON object representing one message. We pull out:
      - Initial user prompts (string content) -> "User:"
      - Assistant text blocks -> "Assistant:"
      - Assistant tool_use blocks -> "[tool: <name>]"
      - User tool_result blocks -> "Tool result:"
    Malformed lines are skipped silently.
    """
    parts: list[str] = []
    try:
        with open(jsonl_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue

                msg_type = entry.get("type")
                message = entry.get("message") or {}
                content = message.get("content") if isinstance(message, dict) else None

                if msg_type == "assistant" and isinstance(content, list):
                    text_chunks: list[str] = []
                    tool_chunks: list[str] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            text = block.get("text") or ""
                            if text.strip():
                                text_chunks.append(text)
                        elif btype == "tool_use":
                            tool_name = block.get("name") or "tool"
                            tool_chunks.append(f"[tool: {tool_name}]")
                    if text_chunks:
                        parts.append("Assistant: " + "\n".join(text_chunks))
                    if tool_chunks:
                        parts.append("Assistant: " + " ".join(tool_chunks))
                elif msg_type == "user":
                    if isinstance(content, str) and content.strip():
                        parts.append("User: " + content.strip())
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_result":
                                result = block.get("content")
                                if isinstance(result, str) and result.strip():
                                    parts.append("Tool result: " + result.strip())
                                elif isinstance(result, list):
                                    text_pieces: list[str] = []
                                    for sub in result:
                                        if isinstance(sub, dict) and sub.get("type") == "text":
                                            t = sub.get("text") or ""
                                            if t.strip():
                                                text_pieces.append(t)
                                    if text_pieces:
                                        parts.append("Tool result: " + "\n".join(text_pieces))
    except OSError:
        return ""
    return "\n\n".join(parts)


@router.get("/agents/{name}/transcript")
async def get_agent_transcript(name: str):
    """Return the readable transcript content for a specific agent.

    Looks in three places, in order:
      1. The legacy markdown file at PROJECT_ROOT/transcripts/{name}.md
         (where daemon-spawned agents write their stdout).
      2. A JSONL output file recorded in this agent's metadata under
         ``transcript_path`` (where Claude Code subagents write their
         per-message stream). JSONL is parsed into a readable transcript
         with clear speaker labels.
      3. (Future) Best-effort glob over /private/tmp/claude-501/**.
    """
    from config import PROJECT_ROOT
    # Basic safety: reject path traversal.
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid agent name")

    # Source 1: legacy markdown file from daemon-spawned agents.
    transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
    if transcript.exists() and transcript.stat().st_size > 0:
        try:
            content = transcript.read_text(errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read transcript: {exc}") from exc
        return {"name": name, "content": content, "bytes": len(content)}

    # Source 2: per-agent JSONL output recorded at register time.
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path:
        jsonl_path = Path(raw_path)
        if jsonl_path.exists() and jsonl_path.stat().st_size > 0:
            suffix = jsonl_path.suffix.lower()
            if suffix in (".output", ".jsonl") or _looks_like_jsonl(jsonl_path):
                content = _format_jsonl_transcript(jsonl_path)
            else:
                try:
                    content = jsonl_path.read_text(errors="replace")
                except OSError as exc:
                    raise HTTPException(status_code=500, detail=f"Could not read transcript: {exc}") from exc
            if content:
                return {"name": name, "content": content, "bytes": len(content)}

    raise HTTPException(
        status_code=404,
        detail=(
            f"No transcript found for agent '{name}'. This can happen if the "
            f"agent was spawned by an older myOS version without transcript tracking."
        ),
    )


def _looks_like_jsonl(path: Path) -> bool:
    """Cheap sniff: read the first non-empty line and check if it parses as JSON."""
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                    return True
                except (json.JSONDecodeError, ValueError):
                    return False
    except OSError:
        pass
    return False


@router.get("/agents")
async def list_agents():
    ps_result = await ostk.kernel_ps()
    audit_agents_list = await ostk.audit_agents()
    daemon_running = ps_result.get("daemon_running", False)
    daemon_agent_names = {a["name"] for a in ps_result.get("agents", [])}

    # Build a unified agent map: name -> agent info.
    # Priority: daemon (most authoritative) > in-memory > audit log.
    agents_map: dict[str, dict] = {}

    # 1. Audit log agents (lowest priority, background context)
    from config import PROJECT_ROOT
    for agent in audit_agents_list:
        # If no daemon is running and audit says "spawned", the agent is dead.
        # Also if daemon IS running but this agent isn't in the daemon's list.
        if agent.get("status") in ("spawned", "running"):
            if not daemon_running or agent["name"] not in daemon_agent_names:
                # Check if it's in our in-memory registry (API-spawned this session)
                if agent["name"] not in active_agents:
                    # Check transcript to determine if agent completed or crashed
                    transcript = PROJECT_ROOT / "transcripts" / f"{agent['name']}.md"
                    if transcript.exists() and transcript.stat().st_size > 0:
                        agent = {**agent, "status": "completed"}
                    else:
                        agent = {**agent, "status": "stopped"}
        agents_map[agent["name"]] = agent

    # 2. In-memory agents (spawned via API this session)
    for name in list(active_agents.keys()):
        proc = active_agents[name]
        meta = agent_metadata.get(name, {})
        # Check if the process is still alive
        if hasattr(proc, 'returncode') and proc.returncode is not None:
            del active_agents[name]
            # Record duration for future estimates
            if meta.get("spawned_at") and meta.get("budget") and meta.get("model"):
                try:
                    start = datetime.fromisoformat(meta["spawned_at"])
                    duration = (datetime.now(timezone.utc) - start).total_seconds()
                    _save_duration(meta["model"], float(meta["budget"]), duration)
                except (ValueError, TypeError):
                    pass
            agents_map[name] = {
                "name": name,
                "status": "completed" if proc.returncode == 0 else "failed",
                "source": "api",
                **meta,
            }
        else:
            agents_map[name] = {
                "name": name,
                "status": "running",
                "source": "api",
                **meta,
            }

    # 2b. Persisted metadata (agents from previous server sessions)
    for name, meta in agent_metadata.items():
        if name in active_agents or name in agents_map:
            continue  # already handled above
        pid = meta.get("pid")
        is_registered = meta.get("source") == "claude-code"
        persisted_status = meta.get("status")
        # If the completion endpoint explicitly stamped this row as completed,
        # trust that over everything else.
        if persisted_status == "completed":
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
                "status": "completed",
            }
        # If the register endpoint explicitly stamped this row as running,
        # trust that and show it in the UI immediately. This is the case
        # for Claude Code subagents that register before they start work.
        # Note: stale running agents from previous server sessions are cleaned
        # up by _recover_stale_agents() at startup, so any agent that still
        # has status="running" here was registered during this server session.
        elif persisted_status == "running":
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
                "status": "running",
            }
        elif persisted_status == "abandoned":
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
                "status": "abandoned",
            }
        elif pid and _is_pid_alive(pid):
            agents_map[name] = {
                "name": name,
                "status": "running",
                "source": "api",
                **meta,
            }
        else:
            # Process is dead (or externally managed). Check transcript for completion.
            transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
            if transcript.exists() and transcript.stat().st_size > 0:
                agents_map[name] = {
                    "name": name,
                    "status": "completed",
                    "source": meta.get("source", "api"),
                    **meta,
                }
            elif is_registered:
                # Externally registered agent with no pid and no transcript.
                # If it has been "running" for more than 20 minutes without
                # any transcript activity, mark it as abandoned so the UI
                # does not show forever-stuck ghost agents.
                from datetime import datetime, timezone
                spawned_at_str = meta.get("spawned_at", "")
                is_stale = False
                if spawned_at_str:
                    try:
                        spawned_at = datetime.fromisoformat(spawned_at_str.replace("Z", "+00:00"))
                        age_seconds = (datetime.now(timezone.utc) - spawned_at).total_seconds()
                        is_stale = age_seconds > 1200  # 20 minutes
                    except (ValueError, TypeError):
                        pass
                if is_stale:
                    # Persist the abandoned status so we do not keep recomputing.
                    meta["status"] = "abandoned"
                    agent_metadata[name] = meta
                    _save_agent_state()
                    agents_map[name] = {
                        "name": name,
                        "status": "abandoned",
                        "source": "claude-code",
                        **meta,
                    }
                else:
                    agents_map[name] = {
                        "name": name,
                        "status": "running",
                        "source": "claude-code",
                        **meta,
                    }

    # 3. Daemon agents (highest priority, ground truth)
    for agent in ps_result.get("agents", []):
        agents_map[agent["name"]] = agent

    all_agents = list(agents_map.values())

    # Enrich agents with transcript metrics
    for agent in all_agents:
        metrics = _get_transcript_metrics(agent["name"])
        agent.update(metrics)

    return {
        "daemon_running": daemon_running,
        "status": ps_result.get("raw", "unknown"),
        "active": [
            a["name"] for a in all_agents
            if a.get("status") == "running"
        ],
        "agents": all_agents,
        "avg_min_per_dollar": _avg_minutes_per_dollar(),
    }


import shutil
CLAUDE_BIN = shutil.which("claude") or "claude"

MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
}


@router.post("/agents/spawn")
async def spawn_agent(body: AgentSpawn):
    from config import PROJECT_ROOT

    model = MODEL_MAP.get(body.model, body.model)
    transcript_path = PROJECT_ROOT / "transcripts" / f"{body.name}.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    # Prepend past memory context so the agent picks up where it left off
    memory_ctx = agent_memory_svc.get_context(body.name)
    prompt_with_memory = (memory_ctx + body.prompt) if memory_ctx and body.prompt else body.prompt

    # Prepend shared workspace summary so agents can see findings from peers
    try:
        from services.agent_workspace import agent_workspace_service as _aws
        _workspace_summary = _aws.get_summary()
        if _workspace_summary and prompt_with_memory:
            prompt_with_memory = _workspace_summary + "\n\n---\n\n" + prompt_with_memory
        elif _workspace_summary:
            prompt_with_memory = _workspace_summary
    except Exception:
        pass

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", model,
        "--output-format", "text",
        "--max-budget-usd", str(body.budget),
        "--permission-mode", "bypassPermissions",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=open(str(transcript_path), "w"),
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )

        # Send the prompt (with prepended memory) to stdin and close it
        if prompt_with_memory:
            proc.stdin.write(prompt_with_memory.encode())
            await proc.stdin.drain()
        proc.stdin.close()

        active_agents[body.name] = proc
        agent_metadata[body.name] = {
            "spawned_at": datetime.now(timezone.utc).isoformat(),
            "budget": str(body.budget),
            "model": model,
            "pid": proc.pid,
        }
        _save_agent_state()

        # Log to audit
        try:
            await ostk._run("os", "audit", "--event", "agent.spawned",
                           "--data", json.dumps({"name": body.name, "model": model, "budget": str(body.budget)}))
        except Exception:
            pass

        return {
            "result": f"Agent '{body.name}' spawned",
            "pid": proc.pid,
            "transcript": str(transcript_path),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/register")
async def register_agent(body: AgentSpawn):
    """Register an external agent (e.g., Claude Code subagent) without spawning a process.

    This lets myOS track agents that are managed by another system. Agents
    should call this BEFORE they start work so they show up as "running"
    in the Agents page in real time. The default status is "running" so a
    simple register call is enough to make the agent visible immediately.
    """
    model = MODEL_MAP.get(body.model, body.model)
    # Default status to "running" so newly registered agents appear in the UI
    # immediately. Callers may pass an explicit status to override.
    status = body.status or "running"
    record: dict = {
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "budget": str(body.budget),
        "model": model,
        "source": "claude-code",
        "status": status,
    }
    if body.description:
        record["description"] = body.description
    if body.prompt:
        record["prompt"] = body.prompt[:500]
    if body.transcript_path:
        record["transcript_path"] = body.transcript_path
    agent_metadata[body.name] = record
    _save_agent_state()

    # Log to audit
    try:
        await ostk._run("os", "audit", "--event", "agent.spawned",
                       "--data", json.dumps({"name": body.name, "model": model, "budget": str(body.budget)}))
    except Exception:
        pass

    return {"result": f"Agent '{body.name}' registered", "source": "claude-code", "status": status}


@router.post("/agents/{name}/complete")
async def mark_agent_complete(name: str, body: Optional[AgentComplete] = None):
    """Mark an externally managed agent as completed.

    This writes the completion status to the persistent agent metadata store
    so the agent shows as completed in the UI across server restarts, and
    also writes a transcript marker as a belt-and-suspenders signal.

    If ``body.summary`` is provided it is appended to the agent's persistent
    memory so future sessions can pick up where this one left off.
    """
    # Save session summary to memory if provided
    if body and body.summary:
        try:
            agent_memory_svc.append_summary(name, body.summary)
        except Exception:
            pass

    meta = agent_metadata.get(name, {})
    if meta.get("spawned_at"):
        try:
            start = datetime.fromisoformat(meta["spawned_at"])
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            _save_duration(meta.get("model", ""), float(meta.get("budget", "0")), duration)
        except (ValueError, TypeError):
            pass

    # Persist completion status so the listing endpoint returns "completed"
    # even if the transcript file is missing or the server restarts.
    now_iso = datetime.now(timezone.utc).isoformat()
    if name in agent_metadata:
        agent_metadata[name]["status"] = "completed"
        agent_metadata[name]["completed_at"] = now_iso
    else:
        # Agent was never registered. Create a minimal record so it still shows up.
        agent_metadata[name] = {
            "spawned_at": now_iso,
            "completed_at": now_iso,
            "status": "completed",
            "source": "claude-code",
        }
    _save_agent_state()

    # Log to audit so the audit_agents() helper also reflects completion
    try:
        await ostk._run("os", "audit", "--event", "agent.completed",
                       "--data", json.dumps({"name": name}))
    except Exception:
        pass

    # Write a transcript marker so the status check finds it even on legacy rows
    from config import PROJECT_ROOT
    transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    if not transcript.exists() or transcript.stat().st_size == 0:
        transcript.write_text(f"Agent '{name}' completed (registered externally).\n")

    # Fire a persistent notification so the bell lights up when an agent finishes.
    try:
        from services.notifications import notifications_service
        description = agent_metadata.get(name, {}).get("description", "")
        body = description if description else f"Agent '{name}' finished its work."
        notifications_service.add(
            type="agent",
            title=f"Agent done: {name}",
            body=body,
            action_label="View agents",
            action_url="/agents",
            metadata={"agent_name": name},
        )
    except Exception:
        pass

    return {"result": f"Agent '{name}' marked complete", "status": "completed"}


@router.get("/agents/{name}/memory")
async def get_agent_memory(name: str):
    """Return stored memory (facts and session summaries) for an agent."""
    data = agent_memory_svc.get_memory(name)
    return {"agent": name, "facts": data.get("facts", {}), "summaries": data.get("summaries", [])}


@router.post("/agents/{name}/memory")
async def save_agent_memory(name: str, body: AgentMemorySave):
    """Save a key/value fact to an agent's persistent memory."""
    agent_memory_svc.save_memory(name, body.key, body.value)
    return {"result": f"Saved memory for '{name}'", "key": body.key}


@router.delete("/agents/{name}/memory")
async def clear_agent_memory(name: str):
    """Clear all memory for an agent."""
    agent_memory_svc.clear_memory(name)
    return {"result": f"Memory cleared for '{name}'"}


@router.post("/agents/{name}/kill")
async def kill_agent(name: str):
    # 1. Try the in-memory process handle (API-spawned agents)
    proc = active_agents.get(name)
    if proc:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass  # already dead
        del active_agents[name]
        return {"result": f"Agent '{name}' killed", "source": "in-memory"}

    # 2. Try system-level kill by finding the process by name
    kill_result = await ostk.kernel_kill(name)
    if kill_result["killed"]:
        return {
            "result": f"Agent '{name}' killed",
            "source": "system",
            "pids": kill_result["pids"],
        }

    # 3. Last resort: generic reap of dead agents
    reap_result = await ostk.kernel_reap()
    raise HTTPException(
        status_code=404,
        detail=f"Agent '{name}' not found. No matching process to kill. Reap result: {reap_result}",
    )


@router.post("/agents/{name}/nudge")
async def nudge_agent(name: str, body: AgentNudge):
    """Send a message (nudge) to a running agent.

    This does two things:
    1. Writes a nudge file to .ostk/nudges/{name}/ so the agent (or any
       watcher) can pick it up from the filesystem.
    2. If the agent was spawned via the API and its process stdin is available,
       writes the message directly to stdin so it arrives immediately.
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Write the nudge to the filesystem
    nudge_data = await ostk.write_nudge(name, message)

    # Also try to write to the process stdin for immediate delivery
    proc = active_agents.get(name)
    stdin_sent = False
    if proc and hasattr(proc, "stdin") and proc.stdin:
        try:
            proc.stdin.write((message + "\n").encode())
            await proc.stdin.drain()
            stdin_sent = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            stdin_sent = False

    # Track in session history
    if name not in nudge_history:
        nudge_history[name] = []
    record = {
        "message": message,
        "timestamp": nudge_data["timestamp"],
        "source": "ui",
        "stdin_delivered": stdin_sent,
    }
    nudge_history[name].append(record)

    return {
        "result": f"Nudge sent to '{name}'",
        "nudge": record,
    }


@router.get("/agents/{name}/nudges")
async def list_agent_nudges(name: str):
    """List all nudges for an agent: both file-based and in-memory session history."""
    file_nudges = await ostk.list_nudges(name)
    session_nudges = nudge_history.get(name, [])
    return {
        "agent": name,
        "nudges": file_nudges,
        "session_nudges": session_nudges,
    }


@router.get("/agents/delegate")
async def delegation_suggestions(needle_id: Optional[str] = None):
    """Return tasks that are good candidates for agent delegation.

    Wraps ``ostk work radiate`` which finds nearby open tasks that could
    be handed off to an agent.
    """
    try:
        data = await ostk.work_radiate(needle_id or None)
        return data
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/templates")
async def list_templates():
    templates = []
    if AGENTS_DIR.exists():
        for f in AGENTS_DIR.glob("*.agent"):
            content = f.read_text()
            templates.append({
                "name": f.stem,
                "file": f.name,
                "content": content[:500],
            })
    return {"templates": templates}


# ── PM Agent Templates (built-in + custom CRUD) ─────────────────────


from services.agent_templates_store import agent_templates_store  # noqa: E402


@router.get("/agents/pm-templates")
async def list_pm_templates():
    """List all PM-focused agent templates (built-ins + custom)."""
    return {"templates": agent_templates_store.list_all()}


@router.post("/agents/pm-templates")
async def create_pm_template(body: dict):
    """Create a new custom agent template."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    template = agent_templates_store.create(body)
    return {"template": template}


@router.put("/agents/pm-templates/{template_id}")
async def update_pm_template(template_id: str, body: dict):
    """Update an existing custom agent template."""
    if template_id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Built-in templates cannot be edited")
    updated = agent_templates_store.update(template_id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": updated}


@router.delete("/agents/pm-templates/{template_id}")
async def delete_pm_template(template_id: str):
    """Delete a custom agent template."""
    if template_id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Built-in templates cannot be deleted")
    deleted = agent_templates_store.delete(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"result": "deleted", "id": template_id}


# ── Grants / Permission Requests ────────────────────────────────────


@router.get("/agents/grants")
async def list_grants(status: str = "pending"):
    """List agent permission requests, filtered by status (default: pending).

    Normalizes the ostk shape (agent_alias/request_type/timestamp) to the
    friendlier names the frontend expects (agent/type/requested_at). Also
    filters out grants from "unknown" agents — those are almost always
    stale secret-lookup stubs from a missing key, not real agent requests.
    """
    try:
        raw_grants = await ostk.list_grants(status)
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    normalized: list[dict] = []
    for g in raw_grants:
        agent = g.get("agent_alias") or g.get("agent") or ""
        # Skip stale "unknown" agent requests (usually orphaned secret lookups).
        if not agent or agent == "unknown":
            continue
        normalized.append({
            "id": g.get("id", ""),
            "agent": agent,
            "type": g.get("request_type") or g.get("type") or "other",
            "target": g.get("target", ""),
            "status": g.get("status", status),
            "detail": g.get("reason") or g.get("detail") or "",
            "requested_at": g.get("timestamp") or g.get("requested_at") or "",
        })
    return {"grants": normalized, "status_filter": status}


@router.post("/agents/grants/{grant_id}/approve")
async def approve_grant(grant_id: str, body: Optional[GrantApprove] = None):
    """Approve a pending permission request."""
    ttl = body.ttl if body else 0
    scope = body.scope if body else None
    try:
        result = await ostk.approve_grant(grant_id, ttl=ttl, scope=scope)
        return {"result": result, "grant_id": grant_id, "action": "approved"}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/grants/{grant_id}/deny")
async def deny_grant(grant_id: str, body: Optional[GrantDeny] = None):
    """Deny a pending permission request."""
    reason = body.reason if body else "not permitted"
    try:
        result = await ostk.deny_grant(grant_id, reason=reason)
        return {"result": result, "grant_id": grant_id, "action": "denied"}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.websocket("/ws/agent/{name}")
async def agent_stream(websocket: WebSocket, name: str):
    await websocket.accept()
    proc = active_agents.get(name)
    if not proc or not proc.stdout:
        await websocket.send_json({"type": "error", "data": f"No active agent '{name}'"})
        await websocket.close()
        return

    async def read_stdout():
        """Stream agent stdout lines to the WebSocket client."""
        try:
            async for line in proc.stdout:
                await websocket.send_json({
                    "type": "output",
                    "data": line.decode().rstrip(),
                })
            return_code = await proc.wait()
            await websocket.send_json({
                "type": "done",
                "return_code": return_code,
            })
            if name in active_agents:
                del active_agents[name]
        except (WebSocketDisconnect, Exception):
            pass

    async def read_client():
        """Read messages from the WebSocket client and forward to agent."""
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")
                if msg_type == "nudge":
                    message = data.get("message", "").strip()
                    if not message:
                        continue

                    # Write nudge file
                    nudge_data = await ostk.write_nudge(name, message)

                    # Try stdin delivery
                    stdin_sent = False
                    if proc and hasattr(proc, "stdin") and proc.stdin:
                        try:
                            proc.stdin.write((message + "\n").encode())
                            await proc.stdin.drain()
                            stdin_sent = True
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass

                    # Track in session history
                    if name not in nudge_history:
                        nudge_history[name] = []
                    record = {
                        "message": message,
                        "timestamp": nudge_data["timestamp"],
                        "source": "ui",
                        "stdin_delivered": stdin_sent,
                    }
                    nudge_history[name].append(record)

                    # Echo the nudge back to the client for display
                    await websocket.send_json({
                        "type": "nudge_ack",
                        "data": record,
                    })
        except (WebSocketDisconnect, Exception):
            pass

    # Run both tasks concurrently
    stdout_task = asyncio.create_task(read_stdout())
    client_task = asyncio.create_task(read_client())
    try:
        done, pending = await asyncio.wait(
            [stdout_task, client_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception:
        stdout_task.cancel()
        client_task.cancel()

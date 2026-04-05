import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from models.schemas import AgentSpawn, AgentNudge
from services.ostk import ostk, OstkError

router = APIRouter(tags=["agents"])

# In-memory registry of active agent processes
active_agents: dict[str, object] = {}

# In-memory log of nudges sent during this session (visible in UI)
nudge_history: dict[str, list[dict]] = {}

from config import AGENTS_DIR


@router.get("/agents")
async def list_agents():
    ps_result = await ostk.kernel_ps()
    audit_agents_list = await ostk.audit_agents()

    # Build a unified agent map: name -> agent info.
    # Priority: daemon (most authoritative) > in-memory > audit log.
    agents_map: dict[str, dict] = {}

    # 1. Audit log agents (lowest priority, background context)
    for agent in audit_agents_list:
        agents_map[agent["name"]] = agent

    # 2. In-memory agents (spawned via API this session)
    for name in active_agents:
        agents_map[name] = {
            "name": name,
            "status": "running",
            "source": "api",
        }

    # 3. Daemon agents (highest priority, ground truth)
    for agent in ps_result.get("agents", []):
        agents_map[agent["name"]] = agent

    all_agents = list(agents_map.values())

    return {
        "daemon_running": ps_result.get("daemon_running", False),
        "status": ps_result.get("raw", "unknown"),
        "active": [
            a["name"] for a in all_agents
            if a.get("status") == "running"
            or (a.get("status") == "spawned" and a.get("source") != "audit")
        ],
        "agents": all_agents,
    }


@router.post("/agents/spawn")
async def spawn_agent(body: AgentSpawn):
    try:
        proc = await ostk.kernel_spawn(
            body.name, body.prompt, body.model, body.budget
        )
        active_agents[body.name] = proc
        return {"result": f"Agent '{body.name}' spawned", "pid": proc.pid}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{name}/kill")
async def kill_agent(name: str):
    proc = active_agents.get(name)
    if proc:
        proc.terminate()
        del active_agents[name]
        return {"result": f"Agent '{name}' killed"}
    # Try ostk reap as fallback
    result = await ostk.kernel_reap()
    return {"result": result}


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

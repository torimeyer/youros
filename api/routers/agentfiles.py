"""Agentfile router.

Browse, read, create, and launch agents from Agentfile templates.
Agentfiles are config files in the ``agents/`` directory that ``ostk run``
uses to spawn an agent with a standardized configuration.

Built-in templates ship with the repo. User-created Agentfiles are stored
in ``~/.myos/agentfiles/`` so they survive git pulls.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import AGENTS_DIR

router = APIRouter(tags=["agentfiles"])

# User-created Agentfiles live outside the repo so git pull never clobbers them.
USER_AGENTFILES_DIR = Path.home() / ".myos" / "agentfiles"


class AgentfileCreate(BaseModel):
    name: str
    prompt: str
    model: str = "auto"
    tools: list[str] = ["shell", "file:read", "file:write"]
    token_limit: int = 200000
    description: Optional[str] = None


class AgentfileRunRequest(BaseModel):
    env_passthrough: list[str] = []
    runtime: str = "host"
    prompt_override: Optional[str] = None


# ---------- helpers ----------

def _parse_agentfile(path: Path) -> dict:
    """Parse an Agentfile into a structured dict.

    The format uses Dockerfile-style directives:
      FROM <model>
      PROMPT "..."
      TOOL <name>
      LIMIT tokens <n>
      BOOT <command>
      PIN <name>
      # comments
    """
    result: dict = {
        "name": path.stem,
        "path": str(path),
        "model": "auto",
        "prompt": "",
        "tools": [],
        "token_limit": 0,
        "boot": "",
        "pin": "",
        "description": "",
        "source": "builtin" if str(path).startswith(str(AGENTS_DIR)) else "user",
    }

    if not path.exists():
        return result

    text = path.read_text()
    for line in text.splitlines():
        stripped = line.strip()

        # Extract description from first comment line that looks like a subtitle
        if stripped.startswith("# Agentfile"):
            # Title line, extract the part after the dash
            if " — " in stripped:
                result["description"] = stripped.split(" — ", 1)[1]
            elif " - " in stripped:
                result["description"] = stripped.split(" - ", 1)[1]
            continue

        # Second comment line is a longer description
        if stripped.startswith("#") and not result.get("_desc_set"):
            desc = stripped.lstrip("# ").strip()
            if desc and not desc.startswith("Agentfile"):
                if result["description"]:
                    result["description"] += ". " + desc
                else:
                    result["description"] = desc
                result["_desc_set"] = True
            continue

        if stripped.startswith("FROM "):
            result["model"] = stripped[5:].strip()
        elif stripped.startswith("PROMPT "):
            # Remove surrounding quotes
            prompt_val = stripped[7:].strip()
            if prompt_val.startswith('"') and prompt_val.endswith('"'):
                prompt_val = prompt_val[1:-1]
            result["prompt"] = prompt_val
        elif stripped.startswith("TOOL "):
            result["tools"].append(stripped[5:].strip())
        elif stripped.startswith("LIMIT tokens "):
            try:
                result["token_limit"] = int(stripped[13:].strip())
            except ValueError:
                pass
        elif stripped.startswith("BOOT "):
            result["boot"] = stripped[5:].strip()
        elif stripped.startswith("PIN "):
            result["pin"] = stripped[4:].strip()

    # Clean up internal flag
    result.pop("_desc_set", None)
    return result


def _list_agentfiles() -> list[dict]:
    """List all Agentfiles from both built-in and user directories."""
    files: list[dict] = []
    seen_names: set[str] = set()

    # Built-in templates from the repo
    if AGENTS_DIR.is_dir():
        for p in sorted(AGENTS_DIR.glob("*.agent")):
            parsed = _parse_agentfile(p)
            files.append(parsed)
            seen_names.add(parsed["name"])

    # User-created Agentfiles
    if USER_AGENTFILES_DIR.is_dir():
        for p in sorted(USER_AGENTFILES_DIR.glob("*.agent")):
            parsed = _parse_agentfile(p)
            if parsed["name"] not in seen_names:
                files.append(parsed)
                seen_names.add(parsed["name"])

    return files


def _agentfile_to_text(data: AgentfileCreate) -> str:
    """Serialize an AgentfileCreate model to the Agentfile text format."""
    lines: list[str] = []
    safe_name = data.name.lower().replace(" ", "-")
    safe_name = re.sub(r"[^a-z0-9\-]", "", safe_name)
    lines.append(f"# Agentfile — {data.name}")
    if data.description:
        lines.append(f"# {data.description}")
    lines.append("")
    lines.append(f"FROM {data.model}")
    lines.append(f'PROMPT "{data.prompt}"')
    for tool in data.tools:
        lines.append(f"TOOL {tool}")
    lines.append(f"LIMIT tokens {data.token_limit}")
    lines.append("BOOT ostk boot")
    lines.append("PIN default")
    lines.append("")
    return "\n".join(lines)


# ---------- endpoints ----------

@router.get("/agentfiles")
async def list_agentfiles():
    """List all available Agentfile templates (built-in and user-created)."""
    return {"agentfiles": _list_agentfiles()}


@router.get("/agentfiles/{name}")
async def get_agentfile(name: str):
    """Read a specific Agentfile's parsed config and raw text."""
    # Search built-in first, then user directory
    for directory in [AGENTS_DIR, USER_AGENTFILES_DIR]:
        path = directory / f"{name}.agent"
        if path.exists():
            parsed = _parse_agentfile(path)
            parsed["raw"] = path.read_text()
            return parsed

    raise HTTPException(status_code=404, detail=f"Agentfile '{name}' not found")


@router.post("/agentfiles")
async def create_agentfile(body: AgentfileCreate):
    """Create a new user Agentfile.

    User-created files are stored in ~/.myos/agentfiles/ so they are
    never overwritten by a git pull.
    """
    USER_AGENTFILES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = body.name.lower().replace(" ", "-")
    safe_name = re.sub(r"[^a-z0-9\-]", "", safe_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Name must contain at least one letter or number")

    path = USER_AGENTFILES_DIR / f"{safe_name}.agent"
    path.write_text(_agentfile_to_text(body))

    return {"created": safe_name, "path": str(path)}


@router.post("/agentfiles/{name}/run")
async def run_agentfile(name: str, body: Optional[AgentfileRunRequest] = None):
    """Launch an agent from an Agentfile via ``ostk run``.

    This calls ``ostk run <path>`` in a subprocess. The agent registers
    itself with the ToriOS API on boot (via the BOOT directive), so it
    will appear in the Active tab automatically.
    """
    body = body or AgentfileRunRequest()

    # Find the Agentfile
    agentfile_path: Path | None = None
    for directory in [AGENTS_DIR, USER_AGENTFILES_DIR]:
        candidate = directory / f"{name}.agent"
        if candidate.exists():
            agentfile_path = candidate
            break

    if agentfile_path is None:
        raise HTTPException(status_code=404, detail=f"Agentfile '{name}' not found")

    # Build the ostk run command
    cmd = ["ostk", "run"]
    for key in (body.env_passthrough or []):
        cmd.extend(["--env-passthrough", key])
    if body.runtime and body.runtime != "host":
        cmd.extend(["--runtime", body.runtime])
    cmd.append(str(agentfile_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Do not await completion. The agent runs in the background.
        # Give it a moment to start, then return.
        await asyncio.sleep(0.5)
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="ostk CLI not found. Make sure ostk is installed and on PATH.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    parsed = _parse_agentfile(agentfile_path)
    return {
        "launched": name,
        "pid": proc.pid,
        "model": parsed.get("model", "auto"),
        "agentfile": str(agentfile_path),
    }

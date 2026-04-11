"""Workflow service -- multi-agent pipelines.

Workflows let you chain agents together. Each step can run in parallel (when
it has no dependencies) or wait for earlier steps to finish first.

Data is stored in ~/.myos/workflows.json, never in the repo.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from services.atomic_io import atomic_write_json

MYOS_DIR = Path.home() / ".myos"
WORKFLOWS_FILE = MYOS_DIR / "workflows.json"

# Valid step statuses
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"

# Valid workflow statuses
WF_PENDING = "pending"
WF_RUNNING = "running"
WF_DONE = "done"
WF_FAILED = "failed"


# ---------------------------------------------------------------------------
# Low-level persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    if not WORKFLOWS_FILE.exists():
        return {}
    try:
        return json.loads(WORKFLOWS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    atomic_write_json(WORKFLOWS_FILE, data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_workflow(name: str, steps: list[dict]) -> dict:
    """Create a new workflow and persist it.

    Each step should have:
      - agent_name (str)
      - prompt (str)
      - model (str, optional, default "sonnet")
      - budget (float, optional, default 2.0)
      - depends_on (list[str], optional) -- list of step IDs that must finish first

    Returns the saved workflow dict.
    """
    wf_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    # Assign IDs to steps that don't have one yet
    normalised_steps = []
    for i, step in enumerate(steps):
        s = {
            "id": step.get("id") or f"step-{i + 1}",
            "agent_name": step.get("agent_name", f"agent-{i + 1}"),
            "prompt": step.get("prompt", ""),
            "model": step.get("model", "sonnet"),
            "budget": float(step.get("budget", 2.0)),
            "depends_on": step.get("depends_on") or [],
            "status": STEP_PENDING,
        }
        normalised_steps.append(s)

    workflow = {
        "id": wf_id,
        "name": name,
        "steps": normalised_steps,
        "status": WF_PENDING,
        "created_at": now,
        "completed_at": None,
    }

    data = _load()
    data[wf_id] = workflow
    _save(data)
    return workflow


def list_workflows() -> list[dict]:
    """Return all workflows, sorted newest-first."""
    data = _load()
    workflows = list(data.values())
    workflows.sort(key=lambda w: w.get("created_at", ""), reverse=True)
    return workflows


def get_workflow(workflow_id: str) -> Optional[dict]:
    """Return a single workflow or None if not found."""
    return _load().get(workflow_id)


def delete_workflow(workflow_id: str) -> bool:
    """Delete a workflow. Returns True if it existed."""
    data = _load()
    if workflow_id not in data:
        return False
    del data[workflow_id]
    _save(data)
    return True


def get_workflow_status(workflow_id: str) -> Optional[dict]:
    """Return workflow with per-step status detail."""
    wf = get_workflow(workflow_id)
    if wf is None:
        return None
    return {
        "id": wf["id"],
        "name": wf["name"],
        "status": wf["status"],
        "created_at": wf["created_at"],
        "completed_at": wf["completed_at"],
        "steps": wf["steps"],
    }


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


async def run_workflow(workflow_id: str) -> dict:
    """Execute a workflow, running steps in dependency order.

    Steps with no unresolved deps run in parallel. Steps that depend on
    earlier steps wait for them to finish. If any step fails, all remaining
    pending steps are skipped and the workflow is marked failed.

    Returns the updated workflow dict.
    """
    data = _load()
    wf = data.get(workflow_id)
    if wf is None:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    # Reset step statuses so we can re-run
    for step in wf["steps"]:
        step["status"] = STEP_PENDING
        step.pop("started_at", None)
        step.pop("finished_at", None)

    wf["status"] = WF_RUNNING
    wf["completed_at"] = None
    data[workflow_id] = wf
    _save(data)

    # Build a map for quick lookup
    step_map: dict[str, dict] = {s["id"]: s for s in wf["steps"]}

    # Track which step IDs are done/failed
    finished: set[str] = set()
    failed: set[str] = set()

    async def _execute_step(step: dict) -> None:
        """Spawn the agent for one step and wait for it to finish."""
        step["status"] = STEP_RUNNING
        step["started_at"] = datetime.now(timezone.utc).isoformat()
        data[workflow_id] = wf
        _save(data)

        try:
            from routers.agents import spawn_agent
            from models.schemas import AgentSpawn

            body = AgentSpawn(
                name=step["agent_name"],
                prompt=step["prompt"],
                model=step.get("model", "sonnet"),
                budget=float(step.get("budget", 2.0)),
            )
            await spawn_agent(body)

            # Poll the spawned process until it finishes
            from routers.agents import active_agents
            proc = active_agents.get(step["agent_name"])
            if proc is not None:
                await proc.wait()
                exit_code = proc.returncode
                if exit_code != 0:
                    raise RuntimeError(f"Agent exited with code {exit_code}")

            step["status"] = STEP_DONE
            finished.add(step["id"])
        except Exception as exc:
            step["status"] = STEP_FAILED
            step["error"] = str(exc)
            failed.add(step["id"])
        finally:
            step["finished_at"] = datetime.now(timezone.utc).isoformat()
            data[workflow_id] = wf
            _save(data)

    # Iteratively run waves of steps whose dependencies are all satisfied
    all_ids = set(step_map.keys())
    launched: set[str] = set()

    while launched | finished | failed != all_ids or (launched - finished - failed):
        # Steps ready to run: pending, all deps done, not yet launched
        ready = [
            s for s in wf["steps"]
            if s["id"] not in launched
            and s["id"] not in finished
            and s["id"] not in failed
            and all(dep in finished for dep in s.get("depends_on", []))
            and not any(dep in failed for dep in s.get("depends_on", []))
        ]

        # Steps that are blocked because a dep failed: skip them
        blocked = [
            s for s in wf["steps"]
            if s["id"] not in launched
            and s["id"] not in finished
            and s["id"] not in failed
            and s["id"] not in {r["id"] for r in ready}
            and any(dep in failed for dep in s.get("depends_on", []))
        ]
        for s in blocked:
            s["status"] = STEP_SKIPPED
            finished.add(s["id"])  # treat skipped as done for dependency tracking

        if not ready:
            # Nothing left to run
            break

        # Launch all ready steps in parallel
        tasks = []
        for s in ready:
            launched.add(s["id"])
            tasks.append(asyncio.create_task(_execute_step(s)))

        await asyncio.gather(*tasks, return_exceptions=True)

    # Determine final workflow status
    any_failed = bool(failed & {s["id"] for s in wf["steps"] if s["status"] == STEP_FAILED})
    wf["status"] = WF_FAILED if any_failed else WF_DONE
    wf["completed_at"] = datetime.now(timezone.utc).isoformat()
    data[workflow_id] = wf
    _save(data)

    return wf

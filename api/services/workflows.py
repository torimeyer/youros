"""Workflow service -- multi-agent pipelines.

Workflows let you chain agents together. Each step can run in parallel (when
it has no dependencies) or wait for earlier steps to finish first.

Data is stored in ~/.youros/workflows.json, never in the repo.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from services.atomic_io import atomic_write_json
from services.workflow_events import bus as _wf_bus
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
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


# Process-wide lock for the workflows.json file. Two concurrent
# ``run_workflow`` coroutines used to race on the load-modify-save
# sequence: each loaded the full dict, mutated their own entry, and
# saved the whole dict back. With no lock, the second writer would
# overwrite the first writer's progress (e.g. the Daily Standup ending
# would silently revert the Weekly Review status to "running" and the
# weekly workflow would never reach a terminal state). Serialising
# every load-modify-save through this lock keeps the file consistent
# without forcing every per-workflow operation through one queue (the
# lock is only held for the brief window of the actual mutation).
_WORKFLOWS_LOCK = asyncio.Lock()


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


async def _persist_workflow(workflow_id: str, wf: dict[str, Any]) -> None:
    """Atomically merge a single workflow back into workflows.json.

    Acquires ``_WORKFLOWS_LOCK`` so a concurrent run_workflow on a
    different workflow cannot trample our entry by re-saving its own
    stale snapshot. Reads the current on-disk dict, replaces only the
    matching workflow_id, and writes the result back. Cheap; the lock
    only spans the load-modify-save burst.
    """
    async with _WORKFLOWS_LOCK:
        data = _load()
        data[workflow_id] = wf
        _save(data)


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


def get_workflow_runs(workflow_id: str) -> Optional[list[dict]]:
    """Return the run history for a workflow, newest first.

    Each entry is a snapshot of the workflow at the moment it finished,
    containing status, started_at, completed_at, duration_seconds, and
    a per-step breakdown.
    """
    wf = get_workflow(workflow_id)
    if wf is None:
        return None
    runs = wf.get("run_history", [])
    return list(reversed(runs))


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


def _cancel_step_agents(agent_names: list[str]) -> None:
    """Mark every named step agent that is still running as cancelled.

    Called by ``run_workflow`` immediately after the workflow reaches a
    terminal state (done or failed). This fires before the reconcile sweep
    at GET /agents so the Agents page reflects the correct status on the
    very next poll.

    Only agents that are still ``running`` are touched. Agents that already
    called ``/complete`` on their own are left alone.
    """
    if not agent_names:
        return
    try:
        from routers.agents import agent_metadata, _save_agent_state, _now_iso
    except ImportError:
        return

    now_iso = _now_iso()
    changed = False
    for name in agent_names:
        meta = agent_metadata.get(name)
        if meta is None:
            continue
        if meta.get("status") != "running":
            continue
        meta["status"] = "cancelled"
        meta["terminated_at"] = now_iso
        meta["terminated_reason"] = "workflow ended"
        changed = True

    if changed:
        _save_agent_state()


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

    # Generate a stable run ID upfront. This ID is stamped on every step
    # agent's metadata as ``workflow_run_id`` so the reconcile pass can find
    # them. It also ends up in the run_history record.
    current_run_id = str(uuid.uuid4())[:8]

    # Reset step statuses so we can re-run
    for step in wf["steps"]:
        step["status"] = STEP_PENDING
        step.pop("started_at", None)
        step.pop("finished_at", None)

    wf["status"] = WF_RUNNING
    wf["completed_at"] = None
    wf["run_started_at"] = datetime.now(timezone.utc).isoformat()
    await _persist_workflow(workflow_id, wf)
    asyncio.create_task(_wf_bus.publish("delta", {"id": workflow_id}))

    # Build a map for quick lookup
    step_map: dict[str, dict] = {s["id"]: s for s in wf["steps"]}

    # Track which step IDs are done/failed
    finished: set[str] = set()
    failed: set[str] = set()

    # Track agent names spawned in this run so we can cancel leftovers.
    spawned_agent_names: list[str] = []

    async def _execute_step(step: dict) -> None:
        """Spawn the agent for one step and wait for it to finish."""
        step["status"] = STEP_RUNNING
        step["started_at"] = datetime.now(timezone.utc).isoformat()
        await _persist_workflow(workflow_id, wf)
        asyncio.create_task(_wf_bus.publish("delta", {"id": workflow_id}))

        try:
            from routers.agents import spawn_agent
            from models.schemas import AgentSpawn

            body = AgentSpawn(
                name=step["agent_name"],
                prompt=step["prompt"],
                model=step.get("model", "sonnet"),
                budget=float(step.get("budget", 2.0)),
                # Link this step agent back to the parent workflow. Stored as
                # the workflow's own ID (not the run ID) so the reconcile
                # pass can call get_workflow(workflow_run_id) to check status.
                workflow_run_id=workflow_id,
                source="api",
            )
            spawned_agent_names.append(step["agent_name"])
            await spawn_agent(body)

            # Poll the spawned process until it finishes. The subprocess
            # runs to completion on its own; any failure surfaces as a
            # non-zero exit code.
            from routers.agents import active_agents
            proc = active_agents.get(step["agent_name"])
            if proc is not None:
                await proc.wait()
                exit_code = proc.returncode
                if exit_code is not None and exit_code != 0:
                    raise RuntimeError(f"Agent exited with code {exit_code}")

            step["status"] = STEP_DONE
            finished.add(step["id"])
        except Exception as exc:
            step["status"] = STEP_FAILED
            step["error"] = str(exc)
            failed.add(step["id"])
        finally:
            step["finished_at"] = datetime.now(timezone.utc).isoformat()
            await _persist_workflow(workflow_id, wf)
            asyncio.create_task(_wf_bus.publish("delta", {"id": workflow_id}))

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
    completed_at = datetime.now(timezone.utc).isoformat()
    wf["completed_at"] = completed_at
    # Store the run ID on the workflow so external readers (e.g. the reconcile
    # pass at GET /agents) can confirm which run_id just completed.
    wf["current_run_id"] = current_run_id

    # Direct-cancel pass: immediately mark any step agent that is still
    # "running" as cancelled. This fires before the reconcile sweep so the
    # Agents page reflects the correct state on the very next poll.
    _cancel_step_agents(spawned_agent_names)

    # Record this run in history so the user can see past results
    run_record: dict[str, Any] = {
        "run_id": current_run_id,
        "status": wf["status"],
        "started_at": wf.get("run_started_at") or wf.get("created_at"),
        "completed_at": completed_at,
        "steps": [
            {
                "id": s["id"],
                "agent_name": s["agent_name"],
                "status": s["status"],
                "started_at": s.get("started_at"),
                "finished_at": s.get("finished_at"),
                "error": s.get("error"),
            }
            for s in wf["steps"]
        ],
    }
    # Compute duration in seconds if we have a start time
    if run_record["started_at"]:
        try:
            t_start = datetime.fromisoformat(run_record["started_at"])
            t_end = datetime.fromisoformat(completed_at)
            run_record["duration_seconds"] = round((t_end - t_start).total_seconds(), 1)
        except (ValueError, TypeError):
            run_record["duration_seconds"] = None

    run_history: list[dict[str, Any]] = wf.get("run_history", [])
    run_history.append(run_record)
    # Keep at most 50 runs to avoid unbounded growth
    if len(run_history) > 50:
        run_history = run_history[-50:]
    wf["run_history"] = run_history

    await _persist_workflow(workflow_id, wf)
    asyncio.create_task(_wf_bus.publish("delta", {"id": workflow_id}))

    # Files tab artifact + follow-up tasks. Every workflow run writes a
    # rollup markdown file to ~/.youros/files/ so the run shows up on
    # Recent Documents, and any next-step bullets in the output become
    # actual tasks via the shared automation_outputs hook. Best-effort:
    # a filesystem or ostk failure here must never block the workflow's
    # return value, otherwise a broken Files hook would kill the whole
    # automation.
    #
    # Skippable under MYOS_SKIP_AUTOMATION_FILES_SAVE so test suites
    # that exercise workflow lifecycle without stubbing the files path
    # do not pollute the real ``~/.youros/files/`` directory. conftest.py
    # sets this flag automatically during pytest runs.
    import os as _os
    if _os.environ.get("MYOS_SKIP_AUTOMATION_FILES_SAVE", "").lower() not in ("1", "true", "yes"):
        try:
            await _write_workflow_artifact(wf, run_record)
        except Exception:
            pass

    return wf


async def _write_workflow_artifact(
    wf: dict[str, Any],
    run_record: dict[str, Any],
) -> None:
    """Write a workflow rollup .md file and auto-create follow-up tasks.

    Pulls each step's agent summary (when the agent called /complete)
    from the agent_metadata store so the workflow artifact doubles as a
    single-page view of what every step produced. Next-step bullets
    inside any step's summary become tasks so actionable follow-ups
    show up on the Tasks list automatically.
    """
    try:
        from services import automation_outputs
    except Exception:
        return
    try:
        from routers.agents import agent_metadata
    except Exception:
        agent_metadata = {}  # type: ignore[assignment]

    name = wf.get("name") or wf.get("id") or "workflow"
    status = wf.get("status", "")
    duration = run_record.get("duration_seconds")

    body_lines: list[str] = [
        f"Workflow '{name}' finished with status: {status}.",
        "",
    ]
    if duration is not None:
        body_lines.append(f"Total duration: {duration} seconds.")
        body_lines.append("")

    body_lines.append("## Steps")
    body_lines.append("")
    aggregated_next_steps: list[str] = []
    for step in wf.get("steps", []):
        agent_name = step.get("agent_name", step.get("id", "step"))
        step_status = step.get("status", "")
        body_lines.append(f"### {agent_name}")
        body_lines.append("")
        body_lines.append(f"- Status: {step_status}")
        error = step.get("error")
        if error:
            body_lines.append(f"- Error: {error}")
        started = step.get("started_at")
        finished = step.get("finished_at")
        if started:
            body_lines.append(f"- Started: {started}")
        if finished:
            body_lines.append(f"- Finished: {finished}")

        # Pull agent summary if available so the rollup includes the
        # actual output the step produced.
        summary = ""
        meta = agent_metadata.get(agent_name, {}) if isinstance(agent_metadata, dict) else {}
        if isinstance(meta, dict):
            summary = str(meta.get("summary") or "").strip()
        if summary and len(summary) >= 20:
            body_lines.append("")
            body_lines.append("Summary:")
            body_lines.append("")
            body_lines.append(summary)
            aggregated_next_steps.extend(
                automation_outputs.parse_next_steps(summary)
            )
        body_lines.append("")

    content = "\n".join(body_lines).strip() + "\n"

    # Deduplicate aggregated next steps (parse_next_steps dedupes within
    # a single summary, but across summaries we could see repeats).
    deduped: list[str] = []
    seen: set[str] = set()
    for item in aggregated_next_steps:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    automation_outputs.write_output(
        automation_kind="workflow",
        name=str(name),
        content=content,
        next_steps=deduped,
        metadata={
            "workflow_id": wf.get("id", ""),
            "status": status,
            "run_id": run_record.get("run_id", ""),
        },
    )

    if deduped:
        try:
            await automation_outputs.auto_create_tasks(
                deduped,
                source_name=str(name),
                automation_kind="workflow",
            )
        except Exception:
            pass

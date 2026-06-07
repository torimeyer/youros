from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models.schemas import (
    TaskCreate, TaskClose, TaskLink, TaskUpdate, CommitCreate, TaskReorder,
    ResolveDuplicateBody, ResolveDuplicatesBulkBody, TaskDeleteAll,
)
from services.ostk import ostk, OstkError
from services.labels_store import labels_store, LABEL_COLORS
from services.task_labels_store import task_labels_store
from services.task_order_store import task_order_store
from services.task_source_store import task_source_store
from services.threads_store import threads_store
from services.task_labeling import (
    apply_auto_labels,
    extract_task_id as _extract_task_id,
    schedule_auto_labels,
)
from services import session_task_map
from services.notifications_events import bus as _notifications_events_bus
from services import recent_deletes
from services.task_visibility import is_session_task, is_ac_child_task
from services.tracing import trace_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])

# Phase tags are milestones, not labels. Everything else is a label/area tag.
_PHASE_RE = re.compile(r"^phase-\d+$")

# Human-friendly names for goal tags (kept for backward compatibility
# with the "goal" field on tasks derived from ostk tags).
_GOAL_LABELS: dict[str, str] = {
    "foundation": "Foundation",
    "chat": "Chat",
    "ostk": "ostk Bridge",
    "work": "Work Tools",
    "agents": "Agents",
    "projects": "Projects",
    "polish": "Polish",
    "lego-app": "Lego App",
    "torios": "yourOS",
    "myos": "myOS",
    "guess-who": "Guess Who",
}


def _plan_path_for(bare_id: str) -> Optional[str]:
    """Return relative path to plan file if it exists, else None."""
    rel = f"transcripts/plan-{bare_id}.md"
    full = os.path.join(ostk.cwd, rel)
    return rel if os.path.exists(full) else None


def _attach_plan_path(task: dict) -> dict:
    """Add plan_path field to a task dict (mutates in place)."""
    raw_id = str(task.get("id") or "")
    bare_id = raw_id.lstrip("→")
    if bare_id:
        task["plan_path"] = _plan_path_for(bare_id)
    else:
        task["plan_path"] = None
    return task


def _enrich_task(
    task: dict,
    all_assignments: Optional[dict] = None,
    task_thread_map: Optional[dict] = None,
    session_task_map_pairs: Optional[dict] = None,
    children_counts: Optional[dict] = None,
) -> dict:
    """Add 'goal', 'label_ids', 'auto_label_ids', 'thread_id', and
    session-linked fields ('session_id', 'child_task_count') so the
    Tasks page can surface a 'View transcript' link and a
    'N tasks created in this session' count on session-task rows.
    """
    tags = task.get("tags") or []
    goal = None
    for tag in tags:
        if not _PHASE_RE.match(tag):
            goal = _GOAL_LABELS.get(tag, tag.replace("-", " ").title())
            break
    task["goal"] = goal

    # Attach assigned label IDs
    task_id = task.get("id", "")
    if all_assignments is not None:
        task["label_ids"] = all_assignments.get(task_id, [])
    else:
        task["label_ids"] = task_labels_store.get_labels_for_task(task_id)

    # Mark which labels were auto-applied so the UI can show an indicator.
    task["auto_label_ids"] = task_labels_store.get_auto_applied(task_id)

    # Attach thread (group) ID if the task belongs to one
    if task_thread_map is not None:
        task["thread_id"] = task_thread_map.get(task_id, None)
    else:
        thread = threads_store.get_thread_for_task(task_id)
        task["thread_id"] = thread["id"] if thread else None

    # Default ``closed_reason`` so the frontend can render a badge even on
    # legacy closed tasks that predate the Tasks audit feature. Closed
    # tasks without a structured reason are treated as "completed" so the
    # badge falls back to a sensible label. Open tasks always have no
    # closed_reason.
    allowed_reasons = {"completed", "duplicate", "archived"}
    raw_reason = task.get("closed_reason")
    if task.get("status") == "closed":
        if raw_reason not in allowed_reasons:
            task["closed_reason"] = "completed"
    else:
        task["closed_reason"] = None

    # Link this task back to its Claude Code session when one is recorded.
    # Two cases:
    #   1. The task IS the auto-filed session task. session_id comes from
    #      the session_to_task map. child_task_count is the number of
    #      tasks spawned during that session.
    #   2. The task is a child of a parent session. session_id comes from
    #      the task_to_session map. child_task_count is always 0 because
    #      child tasks do not themselves own a transcript.
    if session_task_map_pairs is None:
        session_task_map_pairs = session_task_map.all_session_task_pairs()
    if children_counts is None:
        children_counts = session_task_map.all_children_counts()

    task_id = task.get("id", "")
    # Invert session_to_task for fast lookup of "which session is this task?"
    task_to_parent_session: dict[str, str] = {
        tid: sid for sid, tid in session_task_map_pairs.items()
    }

    owned_session = task_to_parent_session.get(task_id)
    if owned_session:
        task["session_id"] = owned_session
        task["child_task_count"] = int(children_counts.get(owned_session, 0))
    else:
        # Fallback: if the task was recorded as a child of some session,
        # surface that parent session id too. Child tasks have count 0.
        parent_session = session_task_map.get_session_for_task(task_id)
        if parent_session and parent_session not in session_task_map_pairs:
            # Only populate if it is NOT already covered by the owned path
            task["session_id"] = parent_session
            task["child_task_count"] = 0
        elif parent_session:
            task["session_id"] = parent_session
            task["child_task_count"] = 0
        else:
            task["session_id"] = None
            task["child_task_count"] = 0

    source_entry = task_source_store.get_source(task_id)
    task["source"] = source_entry["source"]
    task["source_ref"] = source_entry["source_ref"]

    return task


def _compute_compound_scores(tasks: list[dict]) -> dict[str, int]:
    """Compute how many open tasks each task transitively unblocks.

    Uses blocks/depends_on fields to build a graph, then counts
    downstream open tasks via BFS for each open task.
    """
    open_ids = {t.get("id", "") for t in tasks if t.get("status") == "open"}
    # Build adjacency: blocker -> set of tasks it blocks
    blocks_graph: dict[str, set[str]] = {}
    for t in tasks:
        tid = t.get("id", "")
        for blocked_id in (t.get("blocks") or []):
            blocks_graph.setdefault(tid, set()).add(blocked_id)
        for dep_id in (t.get("depends_on") or []):
            blocks_graph.setdefault(dep_id, set()).add(tid)

    scores: dict[str, int] = {}
    for tid in open_ids:
        # BFS: count all transitively blocked OPEN tasks
        visited: set[str] = set()
        queue = list(blocks_graph.get(tid, set()))
        while queue:
            nid = queue.pop(0)
            if nid in visited or nid == tid:
                continue
            visited.add(nid)
            if nid in open_ids:
                queue.extend(blocks_graph.get(nid, set()))
        count = len(visited & open_ids)
        if count > 0:
            scores[tid] = count
    return scores


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    include_test_data: bool = False,
    include_session_tasks: bool = False,
    include_ac_children: bool = False,
    source: Optional[str] = None,
    clear_to_build: Optional[bool] = None,
    include_closed: bool = False,
):
    try:
        # Default to active-only to avoid shipping ~1400 closed needles on
        # every 3s poll (→1694). Callers opt into closed history via
        # ?status=closed or ?include_closed=true.
        if status is None and not include_closed:
            ostk_status = "open"
        elif include_closed:
            ostk_status = None
        else:
            ostk_status = status
        tasks = await ostk.list_tasks(status=ostk_status, priority=priority)
        # Apply custom sort order within each priority group
        tasks = task_order_store.apply_order(tasks)
        # Load all assignments once for efficiency
        all_assignments = task_labels_store.get_all_assignments()
        task_thread_map = threads_store.get_all_task_thread_map()
        # Load the session-task map once so every row can be enriched
        # without re-reading the JSON for each task.
        session_pairs = session_task_map.all_session_task_pairs()
        children_counts = session_task_map.all_children_counts()
        tasks = [
            _enrich_task(
                t,
                all_assignments,
                task_thread_map,
                session_task_map_pairs=session_pairs,
                children_counts=children_counts,
            )
            for t in tasks
        ]
        # Live-agent overlay: any task with a currently-running agent
        # reports ``status=in_progress`` in the response, regardless of
        # what spawn path created the agent (Tasks page, chat tool call,
        # external Claude Code, etc.). When the last live agent finishes
        # the override disappears and the stored status takes over
        # again, so the flag follows work-happening-right-now and never
        # sticks. Terminal states (closed, shelved) are not touched so
        # finished work never gets resurrected. Imported lazily to keep
        # the agents router optional at import time for tools that only
        # need the tasks module.
        try:
            from routers.agents import get_running_task_ids
            _live_task_ids = get_running_task_ids()
        except Exception:
            _live_task_ids = set()
        if _live_task_ids:
            for t in tasks:
                tid = str(t.get("id") or "")
                if not tid or tid not in _live_task_ids:
                    continue
                if t.get("status") in ("closed", "shelved"):
                    continue
                t["status"] = "in_progress"
        # Needle overlay: same live-agent signal applied to ostk needles.
        # An agent spawned with needle_id set carries that id in
        # agent_metadata; while any such agent is live the needle shows
        # in_progress regardless of the stored value in issues.jsonl.
        try:
            from routers.agents import get_running_needle_ids
            _live_needle_ids = get_running_needle_ids()
        except Exception:
            _live_needle_ids = set()
        if _live_needle_ids:
            for t in tasks:
                # Normalize both bare ("922") and arrow-prefixed ("→922") ids.
                raw_id = str(t.get("id") or "")
                bare_id = raw_id.lstrip("→")
                if raw_id not in _live_needle_ids and bare_id not in _live_needle_ids:
                    continue
                if t.get("status") in ("closed", "shelved"):
                    continue
                t["status"] = "in_progress"
        # Hide e2e smoke-test tasks from normal responses. They are only
        # visible when ?include_test_data=true so leftovers from a failed run
        # do not pollute the task list in the UI.
        if not include_test_data:
            tasks = [
                t for t in tasks
                if not t.get("title", "").lower().startswith("e2e-")
            ]
        # Hide session-lifecycle bookkeeping tasks ("Session in <cwd>") by
        # default. These are telemetry rows auto-filed by the SessionStart
        # hook so the session_task_map can link a transcript back to its
        # task. They have no user meaning and were previously leaking
        # through this endpoint even though the Dashboard and Tasks page
        # already hide them. Callers that need the full set (admin UIs,
        # the backfill script, enrichment tests) pass
        # ?include_session_tasks=true explicitly.
        if not include_session_tasks:
            tasks = [t for t in tasks if not is_session_task(t)]
        # Hide spec-derived AC child tasks by default. These are per-requirement
        # rows auto-created when ostk breaks a spec into acceptance criteria. The
        # _read_active_store_ids filter in ostk.list_tasks already excludes them in
        # production (they live outside issues.jsonl). This filter makes the
        # exclusion explicit so test environments and future code paths stay correct.
        if not include_ac_children:
            tasks = [t for t in tasks if not is_ac_child_task(t)]
        if source is not None:
            tasks = [t for t in tasks if t.get("source") == source]
        # Add compound scores (how many tasks each one unblocks)
        compound_scores = _compute_compound_scores(tasks)
        for t in tasks:
            tid = t.get("id", "")
            if tid in compound_scores:
                t["unblocks"] = compound_scores[tid]
        # Sort closed tasks by closed_at descending (most recently closed first).
        # Open and in-progress tasks keep their existing custom order.
        open_tasks = [t for t in tasks if t.get("status") != "closed"]
        closed_tasks = [t for t in tasks if t.get("status") == "closed"]
        closed_tasks.sort(key=lambda t: t.get("closed_at") or "", reverse=True)
        all_tasks = open_tasks + closed_tasks
        for t in all_tasks:
            _attach_plan_path(t)
        # Gemini-ready enrichment: compute readiness and attach to each task.
        # Done after plan_path attachment so the service can reuse plan_path.
        try:
            from services.gemini_ready import compute_task_readiness
            for t in all_tasks:
                if t.get("status") == "closed":
                    t["clear_to_build"] = False
                    t["clear_to_build_checks"] = []
                    continue
                r = compute_task_readiness(t)
                t["clear_to_build"] = r.ready
                t["clear_to_build_checks"] = r.as_dict()["checks"]
                if not r.ready:
                    t["needs_clarity"] = True
                    if t.get("status") == "ready":
                        t["effective_status"] = "draft"
        except Exception:
            for t in all_tasks:
                t.setdefault("clear_to_build", False)
                t.setdefault("clear_to_build_checks", [])
        if clear_to_build is not None:
            all_tasks = [t for t in all_tasks if t.get("clear_to_build") is clear_to_build]
        return {"tasks": all_tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/counts")
async def task_counts():
    """Return a count that matches the default Tasks page view.

    The sidebar badge must equal the number of rows the user sees on the
    Tasks page under its default filter. The default view is:
      - status is open or in_progress (not closed, not shelved).
      - e2e smoke-test tasks are hidden (titles starting with "e2e-").
      - session tasks auto-filed by the SessionStart hook are hidden.

    Session tasks are identified by three rules (matching the frontend
    isSessionTask helper and the GET /tasks filter):
      1. description starts with "session-task:"
      2. description contains "Auto-filed by SessionStart hook"
      3. title matches the old hook format "Claude Code session claude-code-..."
    """
    import re as _re

    _session_re = _re.compile(r"^Claude Code session claude-code-", _re.IGNORECASE)

    def _is_session_task(t: dict) -> bool:
        desc = t.get("description") or ""
        title = t.get("title") or ""
        if desc.startswith("session-task:"):
            return True
        if "Auto-filed by SessionStart hook" in desc:
            return True
        if _session_re.match(title):
            return True
        return False

    def _is_e2e_task(t: dict) -> bool:
        return (t.get("title") or "").lower().startswith("e2e-")

    def _is_active(t: dict) -> bool:
        # Mirror the frontend isActiveTask helper so a task claimed by an
        # agent (in_progress) still counts toward the badge.
        return t.get("status") not in ("closed", "shelved")

    try:
        # Active-only: in_progress is an overlay applied by the router; ostk
        # stores those tasks as "open". Passing status="open" avoids loading
        # ~1400 closed needles just to count the active ones (→1694).
        tasks = await ostk.list_tasks(status="open")
        open_count = sum(
            1 for t in tasks
            if _is_active(t)
            and not _is_session_task(t)
            and not _is_e2e_task(t)
            and not is_ac_child_task(t)
        )
        return {"open": open_count}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/next")
async def next_task():
    try:
        result = await ostk.next_task()
        if result and result.strip():
            return {"message": result.strip()}
        return {"message": "No open tasks right now."}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/health")
async def task_health_check():
    """Run a health check on all open tasks.

    Uses ostk work refine to analyze task quality and find
    duplicates, missing descriptions, and isolated tasks.
    """
    try:
        result = await ostk.refine_tasks()
        return result
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/duplicates")
async def find_duplicate_tasks(threshold: float = 0.8):
    """Find pairs of open tasks with similar titles.

    Uses difflib.SequenceMatcher as a simple Levenshtein-style ratio.
    Pairs scoring above ``threshold`` (default 0.8) are returned as
    duplicate candidates. This is a cheap O(n^2) scan over open tasks,
    which is fine for the typical personal task list size.
    """
    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Precompute normalized titles once so we do not redo the work
    # for every pair in the inner loop.
    normalized = [(t, _normalize_title(t.get("title", ""))) for t in tasks]

    duplicates: list[dict] = []
    for i in range(len(normalized)):
        task_a, title_a = normalized[i]
        if not title_a:
            continue
        for j in range(i + 1, len(normalized)):
            task_b, title_b = normalized[j]
            if not title_b:
                continue
            similarity = SequenceMatcher(None, title_a, title_b).ratio()
            if similarity > threshold:
                duplicates.append({
                    "task_a": task_a,
                    "task_b": task_b,
                    "similarity": round(similarity, 3),
                })

    # Highest similarity first so the UI shows the strongest candidates
    # at the top of the list.
    duplicates.sort(key=lambda d: d["similarity"], reverse=True)
    return {"duplicates": duplicates}


@router.get("/tasks/audit")
async def audit_tasks():
    """Comprehensive task audit: duplicates, stale tasks, and potential redundancies.

    Returns categorized findings the user can act on.
    """
    from datetime import datetime, timezone, timedelta
    from difflib import SequenceMatcher

    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    now = datetime.now(timezone.utc)
    findings: list[dict] = []

    # 1. Duplicates (similarity > 0.7)
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            title_a = _normalize_title(a.get("title", ""))
            title_b = _normalize_title(b.get("title", ""))
            if not title_a or not title_b:
                continue
            sim = SequenceMatcher(None, title_a, title_b).ratio()
            if sim > 0.7:
                findings.append({
                    "type": "duplicate",
                    "severity": "warning",
                    "message": f'"{a.get("title")}" and "{b.get("title")}" look similar ({sim:.0%} match). Consider merging or closing one.',
                    "task_ids": [a.get("id"), b.get("id")],
                })

    # 2. Stale tasks (open > 7 days, P2 only since P0/P1 are intentional)
    for t in tasks:
        created = t.get("created_at", "")
        if not created:
            continue
        try:
            age = (now - datetime.fromisoformat(created)).days
        except (ValueError, TypeError):
            continue
        if age > 7 and t.get("priority") == "P2":
            findings.append({
                "type": "stale",
                "severity": "info",
                "message": f'"{t.get("title")}" has been open for {age} days at P2. Still relevant?',
                "task_ids": [t.get("id")],
            })

    # 3. Vague titles (too short to be actionable)
    for t in tasks:
        title = (t.get("title") or "").strip()
        if len(title) < 15:
            findings.append({
                "type": "vague",
                "severity": "info",
                "message": f'"{title}" is very short. Consider adding more detail so it is clear what needs to happen.',
                "task_ids": [t.get("id")],
            })

    return {
        "findings": findings,
        "total_open": len(tasks),
        "summary": {
            "duplicates": len([f for f in findings if f["type"] == "duplicate"]),
            "stale": len([f for f in findings if f["type"] == "stale"]),
            "vague": len([f for f in findings if f["type"] == "vague"]),
        },
    }




# ---------------------------------------------------------------------------
# Wave planning — conflict-safe grouping of open needles
# ---------------------------------------------------------------------------

# Tokens that appear in almost every task description and carry no scope signal.
_WAVE_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "when", "into",
    "also", "via", "use", "add", "fix", "get", "set", "run", "new", "old",
    "all", "any", "its", "not", "are", "can", "has", "was", "had", "will",
    "make", "move", "show", "them", "then", "only", "each", "more", "some",
    "need", "work", "task", "open", "page", "data", "list", "item", "view",
    # Generic English that leaked through as false scope-conflict tokens
    # (they are 5+ chars so the length gate alone never caught them).
    # Curated from the 2026-05-31 over-split: unrelated tasks were colliding
    # on words like "api", "action", "actually", "cause". Domain nouns
    # (auth, login, dashboard, chat, agent file paths) are deliberately NOT
    # listed so genuine scope overlap still forces a split.
    "action", "actions", "actually", "after", "before", "alone", "across",
    "access", "about", "agent", "agents", "because", "cause", "broken",
    "change", "changed", "actively", "depend", "dependent", "where",
    "which", "while", "would", "could", "should", "these", "those",
    "their", "there", "enable", "rename", "update", "parser", "audit",
    "parity", "stale", "neutral", "internal", "terminal", "likely",
    "whole", "hidden", "folder", "flag", "gaps", "dead",
}

# Heuristic v1: three signal classes
_WAVE_FILE_RE = re.compile(r"[a-z][a-z0-9_/]*\.(?:py|tsx|ts|rs|sh|json|yaml|yml|toml|md)")
# 'api' and 'app' were removed: they are too broad and matched generic
# prose like "Google Docs API" or "the app", colliding unrelated tasks.
# Real api/ or app/ scope is still captured precisely by _WAVE_FILE_RE
# (e.g. 'app/src/pages/Settings.tsx', 'routers/tasks.py').
_WAVE_DIR_RE = re.compile(
    r"\b(?:frontend|backend|haystack|scripts|services|routers|models|tests|hooks|tools|components|stores|pages|lib)\b"
)
_WAVE_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_]{3,}\b")

_WAVE_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_WAVE_MAX = 4  # max tasks per wave (matches feedback_saa_split_when_scope_is_big.md)
_WAVES_PATH = Path.home() / ".youros" / "waves.json"
_SPECS_DIR = Path.home() / ".youros" / "specs"


def _load_open_specs(specs_dir: Path) -> list[dict]:
    """Read spec files from specs_dir (excluding archive/) and return task-shaped dicts.

    Each returned dict has:
      id       -- "spec:<stem>" e.g. "spec:pattern-watcher"
      title    -- frontmatter title field, or the stem if missing
      priority -- "P2" (neutral default)
      status   -- "open"
      tags     -- []
      type     -- "spec"

    Only .md files directly under specs_dir are returned; archive/ is excluded.
    If specs_dir does not exist, returns [].
    """
    if not specs_dir.exists():
        return []

    specs = []
    for md_file in sorted(specs_dir.glob("*.md")):
        # Parse minimal YAML frontmatter (between --- delimiters)
        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        try:
            text = md_file.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.index("---", 3)
                fm_block = text[3:end]
                for line in fm_block.splitlines():
                    if line.startswith("title:"):
                        raw = line.split(":", 1)[1].strip().strip('"').strip("'")
                        if raw:
                            title = raw
                        break
        except Exception:
            pass  # use stem-derived title if parsing fails

        specs.append({
            "id": f"spec:{md_file.stem}",
            "title": title,
            "priority": "P2",
            "status": "open",
            "tags": [],
            "type": "spec",
        })
    return specs


def _wave_scope_tokens(task: dict) -> set[str]:
    """Extract non-trivial scope tokens from a task\'s title + description.

    Three signal classes:
    1. File paths (high signal): e.g. ``routers/tasks.py``, ``Tasks.tsx``
    2. Directory/module keywords: ``frontend``, ``api``, ``services``
    3. Long module-ish words not in the stopword list
    """
    text = " ".join([
        task.get("title") or "",
        task.get("description") or "",
    ]).lower()

    tokens: set[str] = set()

    for m in _WAVE_FILE_RE.finditer(text):
        tokens.add(m.group(0))

    for m in _WAVE_DIR_RE.finditer(text):
        tokens.add(m.group(0))

    for m in _WAVE_TOKEN_RE.finditer(text):
        word = m.group(0)
        if word not in _WAVE_STOPWORDS and len(word) > 4:
            tokens.add(word)

    return tokens


@router.get("/tasks/waves")
async def plan_waves(include_specs: bool = False):
    """Group open needles (and optionally design specs) into parallel-safe waves.

    Conflict heuristic v1:
    - Extract scope tokens per task: file paths, directory names, module-ish words.
    - Two tasks conflict when their token sets share any non-trivial token.
    - Greedy bin-packing ordered by priority (P0 first, then P1, P2, P3):
      assign each task to the earliest wave that has no conflict with any
      already-assigned task in that wave.
    - Wave capacity is capped at 4 (feedback_saa_split_when_scope_is_big.md).

    Query params:
      include_specs -- when true, open design specs from ~/.youros/specs/ (excluding
                       archive/) are merged into the candidate list alongside needles.
                       Spec items carry type="spec"; needle items carry type="needle".
                       Default false -- backward-compatible with existing callers.

    Response shape:
      waves -- ordered list of wave objects:
        wave            -- 1-indexed wave number
        needles         -- [{id, title, priority, scope_hint, type}]
        blocked_by_prior -- true for every wave after the first
      total_needles -- count of open tasks considered
    """
    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Tag each needle so the frontend can distinguish them from specs.
    needle_tasks = [{**t, "type": "needle"} for t in tasks]

    # Optionally merge open design specs into the candidate list.
    all_candidates: list[dict]
    if include_specs:
        spec_items = _load_open_specs(_SPECS_DIR)
        all_candidates = needle_tasks + spec_items
    else:
        all_candidates = needle_tasks

    def _sort_key(t: dict) -> tuple:
        prio = _WAVE_PRIORITY_ORDER.get(t.get("priority", "P2"), 2)
        return (prio, t.get("created_at") or "")

    sorted_tasks = sorted(all_candidates, key=_sort_key)

    waves: list[list[dict]] = []
    wave_tokens: list[set[str]] = []

    for task in sorted_tasks:
        tokens = _wave_scope_tokens(task)
        placed = False
        for idx, existing_tokens in enumerate(wave_tokens):
            if len(waves[idx]) < _WAVE_MAX and not (tokens & existing_tokens):
                waves[idx].append(task)
                wave_tokens[idx] |= tokens
                placed = True
                break
        if not placed:
            waves.append([task])
            wave_tokens.append(set(tokens))

    result = []
    for i, wave_tasks in enumerate(waves):
        needles = []
        for t in wave_tasks:
            tok = _wave_scope_tokens(t)
            hint = ", ".join(sorted(tok)[:3]) if tok else "general"
            needles.append({
                "id": t.get("id", ""),
                "title": t.get("title", ""),
                "priority": t.get("priority", ""),
                "scope_hint": hint,
                "type": t.get("type", "needle"),
            })
        result.append({
            "wave": i + 1,
            "needles": needles,
            "blocked_by_prior": i > 0,
        })

    # Persist assignments so task rows can show their wave badge.
    assignments: dict[str, int] = {}
    for wave_obj in result:
        for needle in wave_obj["needles"]:
            if needle["id"]:
                assignments[needle["id"]] = wave_obj["wave"]
    try:
        _WAVES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WAVES_PATH.write_text(json.dumps({"assignments": assignments}))
    except Exception:
        pass  # persistence is best-effort; don't fail the response

    return {"waves": result, "total_needles": len(sorted_tasks)}


@router.get("/tasks/waves/assignments")
async def get_wave_assignments():
    """Return persisted wave assignments keyed by task id: {task_id: wave_number}.

    Returns an empty dict when no wave plan has been run yet.
    """
    if not _WAVES_PATH.exists():
        return {"assignments": {}}
    try:
        data = json.loads(_WAVES_PATH.read_text())
        return {"assignments": data.get("assignments", {})}
    except Exception:
        return {"assignments": {}}

# Matches a task id reference like "→160" inside a blocker text line.
_BLOCKER_ID_RE = re.compile(r"\u2192\d+")


def _normalize_task_id(raw: str) -> str:
    """Strip the leading arrow so the id can be matched against task records."""
    return raw.lstrip("\u2192").strip()


async def _enrich_blockers(
    task_id: str,
    task_title: str,
    task_description: str,
    blockers: list[dict],
) -> list[dict]:
    """Look up full task records for each blocker and add a plain-language note.

    Each input dict is shaped ``{"text": str, "resolved": bool}``. The output
    keeps every existing field and adds:
      - ``blocker_id``: the bare task id (no arrow), or empty if not parseable
      - ``blocker_task``: the full task record from ``ostk list_tasks`` if
        we could match it, or None
      - ``explanation``: a short plain-language note from Claude when
        available, or None when the AI key is missing or the call failed
    """
    if not blockers:
        return blockers

    # Pull every task once so we can join by id without N round trips.
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []
    by_id: dict[str, dict] = {}
    for t in all_tasks:
        raw_id = t.get("id", "")
        if raw_id:
            by_id[_normalize_task_id(raw_id)] = t

    from services.blocker_explanation import explain_blocker

    enriched: list[dict] = []
    for blocker in blockers:
        text = blocker.get("text") or ""
        match = _BLOCKER_ID_RE.search(text)
        blocker_id = _normalize_task_id(match.group(0)) if match else ""

        full_task = by_id.get(blocker_id) if blocker_id else None
        explanation: Optional[str] = None
        # Only try the AI when the blocker is unresolved and we have at
        # least the blocker title to work with. Resolved blockers do not
        # need an explanation because they are not in the user's way.
        if (
            not blocker.get("resolved")
            and full_task
            and full_task.get("title")
        ):
            try:
                explanation = await explain_blocker(
                    task_id=task_id,
                    task_title=task_title or "",
                    task_description=task_description or "",
                    blocker_id=blocker_id,
                    blocker_title=full_task.get("title", ""),
                    blocker_description=full_task.get("description", "") or "",
                )
            except Exception:
                explanation = None

        enriched.append({
            **blocker,
            "blocker_id": blocker_id,
            "blocker_task": full_task,
            "explanation": explanation,
        })

    return enriched


@router.get("/tasks/by-needle/{needle_id}")
async def get_task_by_needle(needle_id: str):
    """Look up a task by its ostk needle ID.

    Accepts bare numbers (``1067``) or arrow-prefixed IDs (``→1067``).
    Searches first by exact task ID match, then by needle reference
    appearing in the task title or description. Returns the bare numeric
    task_id (no arrow prefix) so callers can embed it in a URL safely.

    Used by the post-commit hook so it can find the real task ID before
    calling POST /api/tasks/{task_id}/close.
    """
    bare = needle_id.lstrip("→").strip()
    if not bare.isdigit():
        raise HTTPException(
            status_code=400,
            detail=f"needle_id must be numeric, got {needle_id!r}",
        )
    needle_arrow = f"→{bare}"

    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == needle_arrow), None)

    if task is None:
        for t in tasks:
            title = t.get("title") or ""
            desc = t.get("description") or ""
            if needle_arrow in title or needle_arrow in desc:
                task = t
                break

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"No task found for needle {needle_arrow}",
        )

    raw_id = task.get("id", "")
    bare_id = raw_id.lstrip("→")
    return {"task_id": bare_id, "needle": needle_arrow, "task": task}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Fetch a single task by ID.

    Accepts bare numbers (``853``) or arrow-prefixed IDs (``→853``).
    Returns 404 when the task does not exist.
    """
    # Normalise: bare numbers like "853" become "→853".
    normalised = task_id if task_id.startswith("→") else f"→{task_id}"
    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))
    task = next((t for t in tasks if t.get("id") == normalised), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    all_assignments = task_labels_store.get_all_assignments()
    task_thread_map = threads_store.get_all_task_thread_map()
    session_pairs = session_task_map.all_session_task_pairs()
    children_counts = session_task_map.all_children_counts()
    enriched = _enrich_task(
        task,
        all_assignments,
        task_thread_map,
        session_task_map_pairs=session_pairs,
        children_counts=children_counts,
    )
    _attach_plan_path(enriched)
    return enriched


# Trailing machine-id patterns we strip from task titles. Smoke
# scripts and roadmap generators sometimes embed timestamps, PIDs, or
# short 4-to-6 digit IDs into titles. The user-facing task list should
# never show these raw tokens. The separator before the number can be
# a space, a hyphen, or an underscore.
_TRAILING_ID_RE = re.compile(r"[\s_\-]+\d{4,}[-_\d]*$")
# Trailing UUID (standard 8-4-4-4-12 hex form, with or without a
# surrounding pair of brackets or parens).
_TRAILING_UUID_RE = re.compile(
    r"\s*[\[\(\{]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[\]\)\}]?\s*$"
)
# Pure-test artifact patterns. These titles come from smoke runs and
# have no meaning to the user. Reject them with a plain message so the
# caller can fix the source.
#
# The check must fire when any of the following appears ANYWHERE in the
# title, not just at the start. Real smoke output looks like
# "Build e2e alpha feature" and "Something e2e-label-1234567", so
# anchoring the old regex at the start was letting these through.
# Patterns that match "e2e" as a token or as an "e2e-label-" prefix.
# These are the only smoke signatures we allow through when a caller
# sets ?include_test_data=true. The smoke script creates titles like
# "e2e-crud-<timestamp>" to exercise the CRUD lifecycle, so the API
# must accept them when test mode is explicit, and keep rejecting them
# everywhere else so user-facing writes stay clean.
_E2E_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "e2e" as a standalone word (matches "e2e", "-e2e-", " e2e ").
    re.compile(r"(?:^|[\s\-_])e2e(?:[\s\-_]|$)", re.IGNORECASE),
    # Machine-generated label name leaked into the title.
    re.compile(r"e2e-label-\d+", re.IGNORECASE),
)
# Patterns that are pure UI noise regardless of caller. These always
# reject: "feature alpha", "demo-smoke-roadmap", build-123, all-caps
# placeholders. Even a test harness should not be creating these.
_NON_E2E_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "feature alpha/beta/gamma/delta/epsilon" anywhere.
    re.compile(
        r"\bfeature[\s\-_]+(?:alpha|beta|gamma|delta|epsilon)\b",
        re.IGNORECASE,
    ),
    # "alpha/beta/... feature" (reversed order, same smoke signature).
    re.compile(
        r"\b(?:alpha|beta|gamma|delta|epsilon)[\s\-_]+feature\b",
        re.IGNORECASE,
    ),
    # Smoke demo prefixes that sometimes leak via automations.
    re.compile(r"demo-smoke-(?:roadmap|prd)", re.IGNORECASE),
    # "build-123" or "test-foo" style machine titles at the start.
    re.compile(r"^build-\d+\b", re.IGNORECASE),
    re.compile(r"^test-\w+", re.IGNORECASE),
    # All-caps placeholder like FOO_BAR_BAZ with 2+ underscores.
    re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}$"),
)
# Union view used by code paths that want to check every smoke
# signature at once (for example, the cleanup-test-artifacts sweep).
_TEST_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    _E2E_ARTIFACT_PATTERNS + _NON_E2E_ARTIFACT_PATTERNS
)
# Labels whose names match this regex are machine-generated artifacts
# from e2e tests. They must never appear in the user-facing label list
# and must be stripped from any task that arrives carrying them.
_TEST_LABEL_NAME_RE = re.compile(
    r"^e2e-label-\d+$|^demo-smoke-(?:roadmap|prd)",
    re.IGNORECASE,
)
# Tasks created by automation_outputs.auto_create_tasks carry a
# description like "Follow-up from the automation run called '<src>'.".
# When the source name begins with ``e2e-``, the task came from an e2e
# probe run. The cleanup-test-artifacts sweep matches this so those
# tasks get deleted even when their user-visible titles look clean.
_TEST_DESCRIPTION_SOURCE_RE = re.compile(
    r"called ['\"]e2e-",
    re.IGNORECASE,
)


def _description_is_test_artifact(description: str) -> bool:
    """Return True when a task description references an e2e- source.

    The task creation path in :mod:`services.automation_outputs` stores
    the source-name inside the description as ``called 'NAME'``. E2E
    probes that set an ``e2e-`` prefixed agent_name on the chat endpoint
    end up with ``called 'e2e-...'`` in the description. This helper
    lets the sweep target those tasks without needing the title itself
    to carry the prefix.
    """
    if not description:
        return False
    return bool(_TEST_DESCRIPTION_SOURCE_RE.search(description))
_MAX_TITLE_LEN = 80


_PURE_ID_RE = re.compile(r"^[\s_\-]*\d{4,}[-_\d]*$")


def _sanitize_task_title(title: str) -> str:
    """Strip machine-generated noise from a task title.

    - Removes trailing numeric IDs like "27557-1776316239".
    - Removes trailing UUIDs.
    - Collapses runs of whitespace to a single space.
    - Title-cases the first word so lists look consistent.
    - Truncates to 80 characters with an ellipsis.

    Returns an empty string if the title is only noise. Callers are
    expected to reject empty results so we never store a blank title.
    """
    if not title:
        return ""
    t = str(title).strip()
    if not t:
        return ""
    # A title that is ONLY a numeric ID has no user meaning. Drop it
    # entirely so the caller can reject it with a plain message.
    if _PURE_ID_RE.match(t):
        return ""
    # Strip trailing UUID first, then trailing numeric id sequences.
    # Run each pattern in a loop in case a title carries several
    # trailing tokens (for example a UUID followed by a stamp).
    for _ in range(3):
        new_t = _TRAILING_UUID_RE.sub("", t).rstrip()
        new_t = _TRAILING_ID_RE.sub("", new_t).rstrip()
        if new_t == t:
            break
        t = new_t
    # Collapse internal whitespace.
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    # Title-case only the first letter of the first word so brand
    # names further down the string keep their own casing.
    t = t[0].upper() + t[1:]
    # Max length 80 chars with ellipsis.
    if len(t) > _MAX_TITLE_LEN:
        t = t[: _MAX_TITLE_LEN - 1].rstrip() + "\u2026"
    return t


def _reject_bad_title(
    title: str,
    allow_test_data: bool = False,
) -> Optional[str]:
    """Return a plain-language error if the title is not user-friendly.

    Returns None when the title is acceptable. The message is written
    for a product manager, not an engineer. The check scans the whole
    title for smoke signatures so strings like "Build e2e alpha feature"
    are rejected regardless of where the e2e token sits.

    When ``allow_test_data`` is True (set by callers that pass
    ``?include_test_data=true``), titles containing the ``e2e`` token
    or an ``e2e-label-<digits>`` leak are accepted so the smoke script
    can exercise the CRUD lifecycle with titles like ``e2e-crud-<ts>``.
    Pure UI noise (``feature alpha``, ``demo-smoke-*``, all-caps
    placeholders) is still rejected in test mode.
    """
    if not title or not title.strip():
        return (
            "Task title is empty after removing machine-generated noise. "
            "Try a short sentence like 'Build the login form'."
        )
    stripped = title.strip()
    patterns: tuple[re.Pattern[str], ...]
    if allow_test_data:
        patterns = _NON_E2E_ARTIFACT_PATTERNS
    else:
        patterns = _TEST_ARTIFACT_PATTERNS
    for pattern in patterns:
        if pattern.search(stripped):
            return (
                "Task title looks like a test artifact. "
                "Try: 'Build the login form'."
            )
    return None


def _is_test_label_name(name: str) -> bool:
    """Return True when a label name looks machine-generated.

    Labels like ``e2e-label-1234567`` and ``demo-smoke-roadmap`` come
    from smoke runs and must never surface in the user-facing list.
    """
    if not name:
        return False
    return bool(_TEST_LABEL_NAME_RE.match(name.strip()))


def _clean_task_title(title: str) -> str:
    """Fix grammar and capitalization in a task title.

    Capitalizes the first letter, fixes common proper nouns (brand names,
    app names), and cleans up whitespace. No API call, just fast regex
    so task creation stays instant.
    """
    if not title or not title.strip():
        return title

    # Strip machine-generated noise first so downstream proper-noun
    # replacement works on the clean text. If the sanitizer returned
    # an empty string the title was pure noise; return "" so the
    # rejection check fails loudly instead of re-inserting the raw
    # junk.
    sanitized = _sanitize_task_title(title)
    if not sanitized:
        return ""
    t = sanitized

    # Known proper nouns and brand names (case-insensitive match, correct replacement)
    proper_nouns = {
        "lego": "Lego",
        "roblox": "Roblox",
        "minecraft": "Minecraft",
        "fortnite": "Fortnite",
        "nintendo": "Nintendo",
        "playstation": "PlayStation",
        "xbox": "Xbox",
        "iphone": "iPhone",
        "ipad": "iPad",
        "macbook": "MacBook",
        "airpods": "AirPods",
        "youtube": "YouTube",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "spotify": "Spotify",
        "netflix": "Netflix",
        "amazon": "Amazon",
        "google": "Google",
        "gmail": "Gmail",
        "slack": "Slack",
        "github": "GitHub",
        "figma": "Figma",
        "notion": "Notion",
        "discord": "Discord",
        "gameboy": "Game Boy",
        "game boy": "Game Boy",
        "pokemon": "Pokemon",
        "disney": "Disney",
        "costco": "Costco",
        "target": "Target",
        "ikea": "IKEA",
        "uber": "Uber",
        "lyft": "Lyft",
        "doordash": "DoorDash",
        "grubhub": "Grubhub",
        "venmo": "Venmo",
        "paypal": "PayPal",
        "stripe": "Stripe",
        "torios": "yourOS",
        "myos": "myOS",
        "ostk": "ostk",
        "claude": "Claude",
        "gemini": "Gemini",
        "anthropic": "Anthropic",
        "new relic": "New Relic",
    }

    for wrong, right in proper_nouns.items():
        # Word boundary match, case insensitive
        t = re.sub(
            r'\b' + re.escape(wrong) + r'\b',
            right,
            t,
            flags=re.IGNORECASE,
        )

    return t


@router.post("/tasks")
async def create_task(body: TaskCreate, include_test_data: bool = False):
    if len(body.title) > 120:
        raise HTTPException(
            status_code=400,
            detail="Title should be one short sentence (max 120 chars). Put longer detail into the `description` field.",
        )
    clean_title = _clean_task_title(body.title)

    # Reject titles that are empty or obvious test artifacts so
    # machine-generated noise never lands in the user-facing list.
    # Returns 400 so automation can distinguish these from 409 (the
    # resurrection block). When the caller passes ?include_test_data=true
    # the sanitizer still blocks pure UI noise but lets e2e-prefixed
    # titles through so the smoke script can exercise the CRUD lifecycle.
    rejection = _reject_bad_title(clean_title, allow_test_data=include_test_data)
    if rejection:
        raise HTTPException(status_code=400, detail=rejection)

    # Block resurrection: if this title was deleted in the last five
    # minutes, skip the create so an automation or agent retry cannot
    # re-inject a task the user just removed. Returns 409 Conflict so
    # the caller can distinguish this from a validation error.
    if recent_deletes.is_recent(clean_title):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task '{clean_title}' was deleted in the last few minutes. "
                "Skipping re-create to prevent resurrection. "
                "Wait a few minutes before re-adding, or re-open the "
                "original from the closed list."
            ),
        )

    try:
        result = await ostk.add_task(
            clean_title,
            body.priority,
            description=body.description or "",
        )
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fire-and-forget auto label suggestion. Never blocks task creation.
    new_id = _extract_task_id(result)

    # Historical note: we used to reject when ostk reused an ID that had
    # just been deleted ("resurrection by id"). That check fired too often
    # on the normal case where ostk's next-id sequence legitimately lands
    # on a just-freed slot and the user is creating a wholly different
    # task. The title tombstone above is the real resurrection guard and
    # handles the case the user cares about (same title re-added by an
    # automation). The ID tombstone stays in place for the file-restore
    # path that writes issues.jsonl directly, which does not go through
    # this API endpoint.

    schedule_auto_labels(new_id, clean_title, body.description or "")

    # Record links to a Claude Code session when the caller provided one.
    # session_id: this task IS the auto-filed session task.
    # parent_session_id: this task was created DURING a session.
    if new_id and body.session_id:
        session_task_map.link_session_to_task(body.session_id, new_id)
    if new_id and body.parent_session_id:
        session_task_map.link_child_task(new_id, body.parent_session_id)

    if new_id and (body.source is not None or body.source_ref is not None):
        task_source_store.set_source(new_id, body.source, body.source_ref)

    trace_event("task_created", task_id=new_id, title=clean_title, priority=body.priority)
    return {"result": result, "task_id": new_id}


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    try:
        results: list[str] = []
        if body.title is not None or body.description is not None or body.notes is not None:
            results.append(
                await ostk.update_task_fields(
                    task_id,
                    title=body.title,
                    description=body.description,
                    notes=body.notes,
                )
            )
        if body.priority:
            results.append(
                await ostk.update_task_priority(
                    task_id, body.priority, reason=body.reason
                )
            )
        if body.status is not None:
            # Only "open" is accepted here. in_progress is derived at read
            # time from live agent state (see GET /tasks overlay), so writing
            # it directly would create stuck rows if the agent dies without
            # clearing it. Closing and pausing go through their own endpoints.
            valid_statuses = {"open"}
            if body.status not in valid_statuses:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid status '{body.status}'. "
                        "Only 'open' is accepted here. "
                        "To close or pause a task, call the dedicated endpoint."
                    ),
                )
            results.append(
                await ostk.update_task_status(task_id, body.status)
            )
        if not results:
            raise HTTPException(status_code=400, detail="No update fields provided")
        return {"result": "; ".join(results)}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/shelve")
async def shelve_task(task_id: str):
    """Pause a task so it stays on the board but is hidden from the active view."""
    try:
        result = await ostk.shelve_task(task_id)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/unshelve")
async def unshelve_task(task_id: str):
    """Resume a previously paused task."""
    try:
        result = await ostk.unshelve_task(task_id)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/reorder")
async def reorder_task(body: TaskReorder):
    """Change a task's priority and set its custom sort position within that group.

    Body: {task_id, new_priority, position}

    If new_priority differs from the task's current priority, the priority is
    updated first. Then the custom sort index is saved to task_order.json.
    The sort index is global across all tasks but only compared within the same
    priority group, so each group maintains its own relative order.
    """
    valid_priorities = {"P0", "P1", "P2", "P3"}
    if body.new_priority not in valid_priorities:
        raise HTTPException(status_code=400, detail=f"Invalid priority '{body.new_priority}'")

    try:
        # Load current tasks to check if priority changed
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == body.task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Update priority in ostk if it changed
    if task.get("priority") != body.new_priority:
        try:
            await ostk.update_task_priority(body.task_id, body.new_priority)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Save custom sort position
    task_order_store.set_task_position(body.task_id, body.position)

    return {"task_id": body.task_id, "new_priority": body.new_priority, "position": body.position}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    # Grab the title first so we can tombstone it. Failure to look up
    # the title is not fatal, the delete still runs, we just lose the
    # resurrection guard for this one task.
    deleted_title = ""
    try:
        existing = await ostk.list_tasks()
        for t in existing:
            if t.get("id") == task_id:
                deleted_title = t.get("title") or ""
                break
    except Exception:
        pass

    try:
        result = await ostk.delete_task(task_id)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Record the title in the recent-delete tombstone cache so any
    # automation that tries to re-create the same title within five
    # minutes is rejected with a 409. Prevents the spec-build smoke
    # loop from instantly resurrecting tasks the user just deleted.
    if deleted_title:
        recent_deletes.record(deleted_title)
    # Also tombstone the numeric task ID. The title cache alone misses
    # the case where something rewrites issues.jsonl from a backup and
    # the same IDs come back with reworded titles.
    recent_deletes.record_id(task_id)

    # Clean up label assignments, thread memberships, sort order, and source for this task
    task_labels_store.remove_task(task_id)
    threads_store.remove_task_from_all_threads(task_id)
    task_order_store.remove_task(task_id)
    task_source_store.remove_task(task_id)
    trace_event("task_deleted", task_id=task_id, title=deleted_title)
    return {"result": result}


# ---------------------------------------------------------------------------
# Clarity: AI-suggest endpoints (→1565)
# ---------------------------------------------------------------------------

class TaskClarifySuggestBody(BaseModel):
    check: str


class TaskClarifyApplyBody(BaseModel):
    check: str
    fix: str


@router.post("/tasks/{task_id}/clarify/suggest")
async def task_clarify_suggest(task_id: str, body: TaskClarifySuggestBody):
    """Return an AI-proposed fix for a failing readiness check.

    Does NOT persist anything. The caller previews the suggestion and
    decides whether to apply it via ``/clarify/apply``.
    """
    normalised = task_id if task_id.startswith("→") else f"→{task_id}"
    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == normalised), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    context = {
        "kind": "task",
        "title": task.get("title") or "",
        "description": task.get("description") or "",
        "file_text": "",
        "failing_check": body.check,
    }

    try:
        from services.clarity_suggest import suggest_clarification
        result = await suggest_clarification(body.check, context)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI suggestion failed: {e}")

    return result


@router.post("/tasks/{task_id}/clarify/apply")
async def task_clarify_apply(task_id: str, body: TaskClarifyApplyBody):
    """Append a clarification fix to the task description and re-run readiness.

    Returns the fresh checks dict so the frontend can update the modal live.
    """
    normalised = task_id if task_id.startswith("→") else f"→{task_id}"
    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == normalised), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    existing_description = task.get("description") or ""
    new_description = (existing_description.rstrip() + "\n\n" + body.fix.strip()).strip()

    try:
        await ostk.update_task_fields(normalised, description=new_description)
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Re-fetch so readiness sees the updated description
    try:
        tasks_fresh = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task_fresh = next((t for t in tasks_fresh if t.get("id") == normalised), task)
    task_fresh["description"] = new_description  # ensure in-memory update is reflected

    from services.gemini_ready import compute_task_readiness
    r = compute_task_readiness(task_fresh)
    return {"checks": r.as_dict()["checks"], "ready": r.ready}


@router.post("/tasks/delete-all")
async def delete_all_tasks(body: TaskDeleteAll = TaskDeleteAll()):
    """Delete every task matching the optional filter.

    Body fields (all optional, defaults to deleting everything):
      - status: "open", "closed", or "all" (default)
      - priority: list of priority strings, e.g. ["P0", "P1"]; null = any
      - labels: list of label IDs; null = any

    Returns the count of deleted tasks and their IDs so the frontend can
    confirm the operation. Logs the deletion for the audit trail.
    """
    valid_statuses = {"open", "closed", "all"}
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'. Use 'open', 'closed', or 'all'.")

    try:
        all_tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Apply status filter
    if body.status == "open":
        candidates = [t for t in all_tasks if t.get("status") != "closed"]
    elif body.status == "closed":
        candidates = [t for t in all_tasks if t.get("status") == "closed"]
    else:
        candidates = list(all_tasks)

    # Apply priority filter
    if body.priority:
        priority_set = set(body.priority)
        candidates = [t for t in candidates if t.get("priority") in priority_set]

    # Apply label filter: only keep tasks that have at least one of the requested labels
    if body.labels:
        label_set = set(body.labels)
        all_assignments = task_labels_store.get_all_assignments()
        candidates = [
            t for t in candidates
            if label_set & set(all_assignments.get(t.get("id", ""), []))
        ]

    deleted_names: list[str] = []
    for task in candidates:
        task_id = task.get("id", "")
        if not task_id:
            continue
        try:
            await ostk.delete_task(task_id)
            task_labels_store.remove_task(task_id)
            threads_store.remove_task_from_all_threads(task_id)
            task_order_store.remove_task(task_id)
            # Tombstone by title and by ID so automations cannot resurrect
            # either variant within the five minute window.
            title = task.get("title") or ""
            if title:
                recent_deletes.record(title)
            recent_deletes.record_id(task_id)
            deleted_names.append(task_id)
        except OstkError:
            # Log but continue: one missing task should not abort the batch.
            logger.warning("delete-all: could not delete task %s", task_id)

    logger.info(
        "delete-all: deleted %d tasks (status=%s priority=%s labels=%s)",
        len(deleted_names), body.status, body.priority, body.labels,
    )
    return {"deleted": len(deleted_names), "names": deleted_names}


async def _has_labeling_auth() -> bool:
    from services.ai_backend import get_ai_client
    client = await get_ai_client()
    return client is not None


@router.post("/tasks/backfill-labels")
async def backfill_labels():
    """Run auto-labeling on every active task that has no labels yet.

    Active = anything not closed. ostk emits both ``open`` and
    ``in_progress`` as live statuses and Tori's tasks are mostly
    in_progress once an agent picks them up. Strict ``status="open"``
    filtering was silently skipping every in_progress row and the
    Label All button labeled 0 or 1 tasks instead of everything the
    UI showed. Regression guard for needle 283.

    IMPORTANT: the per-task Claude calls run in PARALLEL, capped by a
    small semaphore so we do not hammer the Anthropic API. Before this
    change the loop was sequential and 14 tasks × ~2-8s per Claude
    call = up to 112 seconds of "Labeling..." spinner with nothing
    visible until the whole batch finished. The UI looked hung. Now
    the total wall time is roughly ``ceil(n / concurrency) * per_call``
    so 14 tasks at concurrency 6 finish in about 2 Claude calls of
    wall time. Regression guard for needle 284.
    """
    import asyncio

    try:
        tasks_raw = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    tasks = [t for t in tasks_raw if t.get("status") != "closed"]

    all_assignments = task_labels_store.get_all_assignments()
    unlabeled = [
        t for t in tasks
        if not all_assignments.get(t.get("id", "")) and t.get("id") and t.get("title")
    ]

    if unlabeled:
        if not await _has_labeling_auth():
            raise HTTPException(
                status_code=400,
                detail="No API key configured. Add an Anthropic API key in Settings, or sign in to Claude Code, to use smart labeling.",
            )

    # Concurrency cap. 6 parallel Claude calls is well under Anthropic
    # rate limits and keeps the total backfill time under ~10 seconds
    # even for a backlog of 50+ tasks.
    semaphore = asyncio.Semaphore(6)

    async def _label_one(task: dict) -> bool:
        task_id = task.get("id", "")
        title = task.get("title", "")
        async with semaphore:
            try:
                await asyncio.wait_for(
                    apply_auto_labels(task_id, title, ""),
                    timeout=10.0,
                )
                return bool(task_labels_store.get_labels_for_task(task_id))
            except Exception:
                return False

    results = await asyncio.gather(
        *[_label_one(t) for t in unlabeled],
        return_exceptions=False,
    )
    processed = len(unlabeled)
    labeled = sum(1 for r in results if r)

    return {"processed": processed, "labeled": labeled, "total_open": len(tasks)}


@router.post("/tasks/{task_id}/labels/auto")
async def auto_label_task(task_id: str):
    """Run auto-labeling for a single task and return the assigned labels.

    Applies label suggestions based on the task title. Safe to call at any
    time. Returns the full list of label IDs now assigned to the task.
    """
    import asyncio

    # Fetch the task title so the suggester has something to work with.
    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    title = task.get("title", "")
    try:
        await asyncio.wait_for(
            apply_auto_labels(task_id, title, ""),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Auto-labeling timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    label_ids = task_labels_store.get_labels_for_task(task_id)
    if not label_ids:
        if not await _has_labeling_auth():
            raise HTTPException(
                status_code=400,
                detail="No API key configured. Add an Anthropic API key in Settings, or sign in to Claude Code, to use smart labeling.",
            )
    return {"label_ids": label_ids}


# Batch-close guard (needle 317). On 2026-04-11 an agent called this
# endpoint 13 times in 3 minutes and silently closed tasks that Tori
# expected to still be open. The guard rejects closes after a short
# burst so nothing can ever batch-close without going through the
# audit review flow (which requires one-by-one user approval).
_CLOSE_WINDOW_SECONDS = 60
_CLOSE_BURST_LIMIT = 3
_recent_closes: list[float] = []


@router.post("/tasks/{task_id}/close")
async def close_task(
    task_id: str,
    body: TaskClose = TaskClose(),
    source: Optional[str] = Query(None),
):
    """Close a task. ``reason`` is the structured audit tag.

    Only accepts the controlled vocabulary ``completed``, ``duplicate`` or
    ``archived`` when present. Anything else is dropped so pre-audit
    callers that still send free-form text continue to work.

    Batch-close guard: rejects the request when more than 3 tasks have
    been closed in the last 60 seconds. Pass ``?source=user`` to bypass
    the guard for direct user actions (checkbox clicks in the UI).
    """
    import time as _time

    now = _time.time()
    user_initiated = source == "user"

    if not user_initiated:
        cutoff = now - _CLOSE_WINDOW_SECONDS
        # Prune stale entries.
        while _recent_closes and _recent_closes[0] < cutoff:
            _recent_closes.pop(0)
        if len(_recent_closes) >= _CLOSE_BURST_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many tasks closed in the last {_CLOSE_WINDOW_SECONDS} seconds. "
                    "Use the Audit review to close tasks in bulk so each one "
                    "gets reviewed first."
                ),
            )

    # Normalise pure numeric IDs like "1067" to "→1067" for ostk. This
    # lets the post-commit hook pass a bare number from the URL without
    # embedding the non-ASCII → character in the path. Non-numeric IDs
    # (test slugs, UUIDs, already-prefixed IDs) are passed through as-is.
    normalised_id = f"→{task_id}" if task_id.isdigit() else task_id

    allowed_reasons = {"completed", "duplicate", "archived"}
    raw_reason = (body.reason or "").strip()
    structured_reason = raw_reason if raw_reason in allowed_reasons else None
    try:
        result = await ostk.close_task(normalised_id, closed_reason=structured_reason)
        if not user_initiated:
            _recent_closes.append(now)
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Advance the spec frontmatter status when this was the last open
    # builder task for a spec. Import inside the call so we do not
    # introduce a cross-router circular import at module load. Errors
    # here are swallowed so a flaky disk never breaks the close call
    # that already succeeded.
    try:
        from routers.specs import (
            _advance_spec_status_if_all_builder_tasks_closed_async,
        )
        await _advance_spec_status_if_all_builder_tasks_closed_async(task_id)
    except Exception:
        logger.exception(
            "close_task: spec-status advance failed for task %s", task_id
        )

    trace_event("task_closed", task_id=task_id, reason=structured_reason)
    await _notifications_events_bus.publish("needle_closed", {"task_id": normalised_id})
    return {"result": result}


@router.post("/tasks/{task_id}/reopen")
async def reopen_task(task_id: str):
    try:
        result = await ostk.reopen_task(task_id)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/close-by-session/{session_id}")
async def close_task_by_session(session_id: str):
    """Close the auto-filed session task linked to ``session_id``.

    Called by the SessionEnd hook when a Claude Code session stops.
    Looks up the linked task in session_task_map and closes it as
    ``completed`` via ostk. Idempotent: a second call after the task is
    already closed returns ``already_closed`` without erroring.

    Returns:
        - 200 ``{"closed": true, "task_id": "→123"}`` on success.
        - 200 ``{"closed": false, "task_id": "→123", "reason": "already_closed"}``
          when the task is already closed (idempotent path).
        - 404 when no mapping exists for this session_id.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=422, detail="session_id is required")

    task_id = session_task_map.get_task_for_session(session_id.strip())
    if not task_id:
        raise HTTPException(
            status_code=404,
            detail=f"No session task mapping for session_id '{session_id}'",
        )

    # Look up the current status so a repeat call after close is a no-op.
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    match = next((t for t in all_tasks if t.get("id") == task_id), None)
    if match is None:
        # The mapping points at a task that no longer exists (e.g. it was
        # deleted manually). Treat as already-closed so the hook succeeds.
        return {
            "closed": False,
            "task_id": task_id,
            "reason": "not_found",
        }
    if match.get("status") == "closed":
        return {
            "closed": False,
            "task_id": task_id,
            "reason": "already_closed",
        }

    try:
        await ostk.close_task(task_id, closed_reason="completed")
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"closed": True, "task_id": task_id}


@router.post("/tasks/cleanup-session-duplicates")
async def cleanup_session_duplicates(max_age_hours: int = 1):
    """Delete stale auto-filed session tasks so they stop piling up.

    Sweeps every OPEN task that looks like a SessionStart hook auto-file
    (matched by the same three rules used elsewhere) and deletes any whose
    creation timestamp is older than ``max_age_hours`` OR whose
    session_id has no live mapping in session_task_map. This lets Tori
    clear the visible duplicate pile in one call without touching tasks
    she actually cares about.

    Returns ``{"deleted": N, "kept": M, "deleted_ids": [...]}``.
    """
    import re as _re
    from datetime import datetime, timezone, timedelta

    _session_re = _re.compile(r"^Claude Code session claude-code-", _re.IGNORECASE)
    _session_in_re = _re.compile(r"^Session in ", _re.IGNORECASE)

    def _is_session_task(t: dict) -> bool:
        desc = t.get("description") or ""
        title = t.get("title") or ""
        if desc.startswith("session-task:"):
            return True
        if "Auto-filed by SessionStart hook" in desc:
            return True
        if _session_re.match(title):
            return True
        if _session_in_re.match(title):
            return True
        return False

    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(max_age_hours)))
    session_pairs = session_task_map.all_session_task_pairs()
    # Build a set of task IDs that ARE the linked session task. Anything
    # else is either an orphaned legacy row or has no live mapping.
    linked_task_ids = set(session_pairs.values())

    deleted: list[str] = []
    kept = 0

    for t in tasks:
        if not _is_session_task(t):
            continue
        task_id = t.get("id") or ""
        if not task_id:
            continue

        # Decide whether to delete.
        delete_reason = None

        created_raw = t.get("created_at") or ""
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                delete_reason = "stale"
        except (ValueError, AttributeError):
            # Unparseable timestamps are treated as old.
            delete_reason = "stale"

        if delete_reason is None and task_id not in linked_task_ids:
            # No live session_id mapping means the session is gone.
            delete_reason = "no_mapping"

        if delete_reason is None:
            kept += 1
            continue

        try:
            await ostk.delete_task(task_id)
            deleted.append(task_id)
            # Tidy associated bookkeeping so dangling label/thread/order
            # entries do not linger.
            task_labels_store.remove_task(task_id)
            threads_store.remove_task_from_all_threads(task_id)
            task_order_store.remove_task(task_id)
        except OstkError as e:
            logger.warning(
                "cleanup_session_duplicates: delete_task failed for %s: %s",
                task_id,
                e,
            )
            kept += 1
            continue

    return {"deleted": len(deleted), "kept": kept, "deleted_ids": deleted}


@router.post("/tasks/cleanup-test-artifacts")
async def cleanup_test_artifacts():
    """Delete every open task whose title or labels match a smoke-test signature.

    Scans every open task and removes any whose title trips the
    test-artifact regex, plus any task carrying a machine-generated
    label name (``e2e-label-123`` and friends). Also prunes the
    matching label records so they stop appearing in the label picker.

    Returns the count plus the IDs so the caller can log what was
    removed. Safe to run as often as you like, it is idempotent.
    """
    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Pre-compute which label IDs are machine-generated so we can check
    # each task's assignments without re-scanning labels every iteration.
    all_labels = labels_store.list_labels()
    bad_label_ids: set[str] = {
        label["id"] for label in all_labels
        if _is_test_label_name(label.get("name", ""))
    }
    all_assignments = task_labels_store.get_all_assignments()

    deleted: list[str] = []
    for t in tasks:
        task_id = t.get("id") or ""
        if not task_id:
            continue
        title = t.get("title") or ""

        # Match on title signature OR on carrying a machine label OR on
        # a description that points back to an e2e- automation source.
        # The description check catches tasks from
        # /chat/roadmap/create-tasks where the title is a real
        # user-readable phrase but the source name is e2e- prefixed.
        matched_by_title = _reject_bad_title(title) is not None
        assigned = set(all_assignments.get(task_id, []))
        matched_by_label = bool(assigned & bad_label_ids)
        description = t.get("description") or ""
        matched_by_description = _description_is_test_artifact(description)

        if not matched_by_title and not matched_by_label and not matched_by_description:
            continue

        try:
            await ostk.delete_task(task_id)
        except OstkError as exc:
            logger.warning(
                "cleanup_test_artifacts: delete_task failed for %s: %s",
                task_id,
                exc,
            )
            continue

        task_labels_store.remove_task(task_id)
        threads_store.remove_task_from_all_threads(task_id)
        task_order_store.remove_task(task_id)
        if title:
            recent_deletes.record(title)
        recent_deletes.record_id(task_id)
        deleted.append(task_id)

    # Also prune the machine-generated label records themselves so the
    # label picker stops listing them.
    deleted_labels: list[str] = []
    for label_id in bad_label_ids:
        task_labels_store.remove_label_from_all_tasks(label_id)
        if labels_store.delete_label(label_id):
            deleted_labels.append(label_id)

    logger.info(
        "cleanup_test_artifacts: deleted %d tasks, %d labels",
        len(deleted),
        len(deleted_labels),
    )
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "deleted_labels": len(deleted_labels),
        "deleted_label_ids": deleted_labels,
    }


@router.post("/tasks/pull")
async def pull_next_task():
    """Pull model: claim the next available task atomically.

    Uses ostk's pull primitive (work next --claim). The agent declares
    readiness, ostk selects the highest-priority task using sphere-radius
    ordering, and atomically marks it as in_progress. Returns the claimed
    task or null if nothing is available.

    This is the pull-model counterpart to push-based task assignment.
    Agents call this when they are ready for more work instead of being
    pushed tasks by an orchestrator.
    """
    try:
        result = await ostk.claim_next_task()
        if not result:
            return {"claimed": False, "task": None}
        return {"claimed": True, "task": result}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/health/autofix/{issue_type}")
async def autofix_issue(issue_type: str, body: dict = {}):
    """Fix a single health issue by type for a specific task.

    Body: {"task_id": "→123"}
    Returns {"fixed": true, "details": "..."} or {"fixed": false, "reason": "..."}.
    """
    from services.task_autofix import fix_issue

    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="task_id is required")

    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return await fix_issue(issue_type, task_id, task)


@router.post("/tasks/health/autofix-all")
async def autofix_all_issues():
    """Fix every current health issue by dispatching per-issue-type handlers.

    Re-runs the health check, then dispatches an auto-fix handler for each
    issue. Returns {"fixed": N, "skipped": M, "details": [...]}.
    """
    from services.task_autofix import fix_issue

    try:
        health = await ostk.refine_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    issues = [i for i in (health.get("issues") or []) if i.get("type") != "isolated"]

    if not issues:
        return {"fixed": 0, "skipped": 0, "details": []}

    try:
        all_tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task_map = {t.get("id"): t for t in all_tasks}

    fixed_count = 0
    skipped_count = 0
    details: list[dict] = []

    for issue in issues:
        issue_type = issue.get("type", "")
        for task_id in issue.get("task_ids", []):
            task = task_map.get(task_id)
            if task is None:
                skipped_count += 1
                details.append({"issue_type": issue_type, "task_id": task_id, "fixed": False, "reason": "task not found"})
                continue
            result = await fix_issue(issue_type, task_id, task)
            if result.get("fixed"):
                fixed_count += 1
                details.append({"issue_type": issue_type, "task_id": task_id, **result})
            elif result.get("fixable") is False:
                skipped_count += 1
                details.append({"issue_type": issue_type, "task_id": task_id, "fixed": False, "reason": "no handler for this issue type"})
            else:
                skipped_count += 1
                details.append({"issue_type": issue_type, "task_id": task_id, **result})

    return {"fixed": fixed_count, "skipped": skipped_count, "details": details}


def _normalize_title(title: str) -> str:
    """Normalize a task title for fuzzy comparison.

    Lowercases, strips surrounding whitespace, and collapses internal
    whitespace to single spaces so tiny spacing differences do not
    affect the similarity score.
    """
    return " ".join((title or "").lower().split())


@router.post("/tasks/duplicates/resolve")
async def resolve_duplicate(body: ResolveDuplicateBody):
    """Close the duplicate (discard_id) and keep keep_id open.

    Returns 404 when discard_id is not found among open tasks so the UI can
    surface a helpful message.  Returns ``{resolved: 1}`` on success.
    """
    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    open_ids = {t.get("id") for t in tasks}
    if body.discard_id not in open_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Task {body.discard_id} not found among open tasks. It may already be closed.",
        )

    try:
        await ostk.close_task(body.discard_id, closed_reason="duplicate")
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"resolved": 1, "closed": body.discard_id, "kept": body.keep_id}


@router.post("/tasks/duplicates/resolve-all")
async def resolve_all_duplicates(body: ResolveDuplicatesBulkBody):
    """Close every lower-priority duplicate in the detected duplicate pairs.

    Scans for duplicate pairs at the given threshold (default 0.8) and closes
    task_b in each pair (the one with the lower position in the list, i.e. the
    later-created or second task of each pair).  Returns ``{resolved: N}``.
    """
    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    normalized = [(t, _normalize_title(t.get("title", ""))) for t in tasks]

    to_close: list[dict] = []
    seen_discard: set[str] = set()
    for i in range(len(normalized)):
        task_a, title_a = normalized[i]
        if not title_a:
            continue
        task_a_id = task_a.get("id", "")
        if task_a_id in seen_discard:
            continue
        for j in range(i + 1, len(normalized)):
            task_b, title_b = normalized[j]
            if not title_b:
                continue
            task_b_id = task_b.get("id", "")
            if task_b_id in seen_discard:
                continue
            similarity = SequenceMatcher(None, title_a, title_b).ratio()
            if similarity > body.threshold:
                to_close.append(task_b)
                seen_discard.add(task_b_id)

    closed_ids: list[str] = []
    errors: list[str] = []
    for task in to_close:
        task_id = task.get("id", "")
        try:
            await ostk.close_task(task_id, closed_reason="duplicate")
            closed_ids.append(task_id)
        except OstkError as e:
            errors.append(f"{task_id}: {e}")

    result: dict = {"resolved": len(closed_ids), "closed": closed_ids}
    if errors:
        result["errors"] = errors
    return result


def _filter_neighbors(
    neighbors: list[str],
    blocked_by: list[dict],
    unblocks: list[str],
    current_task_record: Optional[dict],
    all_tasks: list[dict],
) -> list[str]:
    """Keep neighbors that are structurally or topically related to the task.

    A neighbor passes if it appears in blocked_by/unblocks (structural link)
    or shares at least one tag with the current task.
    """
    if not neighbors:
        return neighbors

    related_ids: set[str] = set()
    for blocker in blocked_by:
        text = blocker.get("text") or ""
        m = _BLOCKER_ID_RE.search(text)
        if m:
            related_ids.add(_normalize_task_id(m.group(0)))
    for unblock in unblocks:
        m = _BLOCKER_ID_RE.search(str(unblock))
        if m:
            related_ids.add(_normalize_task_id(m.group(0)))

    current_tags: set[str] = (
        set(current_task_record.get("tags") or []) if current_task_record else set()
    )

    by_id: dict[str, dict] = {}
    for t in all_tasks:
        raw_id = t.get("id", "")
        if raw_id:
            by_id[_normalize_task_id(raw_id)] = t

    kept: list[str] = []
    for neighbor in neighbors:
        m = re.match(r"^(\d+)", neighbor)
        if not m:
            continue
        neighbor_id = m.group(1)

        if neighbor_id in related_ids:
            kept.append(neighbor)
            continue

        if current_tags:
            neighbor_record = by_id.get(neighbor_id)
            if neighbor_record:
                neighbor_tags = set(neighbor_record.get("tags") or [])
                if current_tags & neighbor_tags:
                    kept.append(neighbor)

    return kept


@router.get("/tasks/{task_id}/briefing")
async def task_briefing(task_id: str):
    """Get a context briefing for a task.

    Calls ``ostk work activate`` and returns structured context:
    related tasks, blockers, things this task unblocks, and nearby ideas.

    When the briefing has unresolved blockers, each blocker entry is
    enriched with the full task record (title, description, priority,
    status) plus a short plain-language note generated by Claude that
    explains why the two tasks are linked.
    """
    try:
        briefing = await ostk.activate_task(task_id)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Find this task's own record for description enrichment and neighbor filtering.
    task_description = ""
    current_task_record: Optional[dict] = None
    all_tasks: list[dict] = []
    try:
        all_tasks = await ostk.list_tasks()
        for t in all_tasks:
            if _normalize_task_id(t.get("id", "")) == _normalize_task_id(task_id):
                task_description = t.get("description", "") or ""
                current_task_record = t
                break
    except OstkError:
        pass

    briefing["blocked_by"] = await _enrich_blockers(
        task_id=task_id,
        task_title=briefing.get("title", "") or "",
        task_description=task_description,
        blockers=briefing.get("blocked_by", []) or [],
    )

    briefing["neighbors"] = _filter_neighbors(
        neighbors=briefing.get("neighbors", []) or [],
        blocked_by=briefing.get("blocked_by", []) or [],
        unblocks=briefing.get("unblocks", []) or [],
        current_task_record=current_task_record,
        all_tasks=all_tasks,
    )
    return {"briefing": briefing}


@router.get("/tasks/{task_id}/trace")
async def task_trace(task_id: str):
    """Get the attribution chain for a task.

    Shows where the task came from (idea, spec, or draft),
    what it depends on, what it unblocks, and which commits
    belong to it.
    """
    try:
        trace = await ostk.trace(task_id)
        return {"trace": trace}
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Commits linked to tasks
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/commit")
async def commit_for_task(task_id: str, body: CommitCreate):
    """Save a code change and link it to a task.

    Runs ``ostk commit`` with the --needle flag set to the task ID,
    so the commit shows up in the task's history.
    """
    try:
        result = await ostk.commit(
            message=body.message,
            needle=task_id,
            spec=body.spec,
            section=body.section,
            agent=body.agent,
        )
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/commits")
async def commit_standalone(body: CommitCreate):
    """Save a code change, optionally linked to a task.

    When ``needle`` is provided in the body, the commit is attributed
    to that task. Otherwise it is an unlinked commit.
    """
    try:
        result = await ostk.commit(
            message=body.message,
            needle=body.needle,
            spec=body.spec,
            section=body.section,
            agent=body.agent,
        )
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))



# ---------------------------------------------------------------------------
# Task dependencies (what blocks what)
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}/dependencies")
async def get_task_dependencies(task_id: str):
    """Get which tasks this one blocks and which ones it needs done first."""
    try:
        deps = await ostk.get_dependencies(task_id)
        return deps
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/tasks/{task_id}/link")
async def link_task(task_id: str, body: TaskLink):
    """Create a dependency between two tasks.

    The relation can be 'blocks' (this task blocks the target) or
    'depends-on' (this task needs the target done first).
    """
    try:
        result = await ostk.link_tasks(task_id, body.relation, body.target)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tasks/{task_id}/link")
async def unlink_task(task_id: str, target: str, relation: str = "blocks"):
    """Remove a dependency between two tasks."""
    try:
        result = await ostk.unlink_tasks(task_id, relation, target)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Labels CRUD
# ---------------------------------------------------------------------------

@router.get("/labels")
async def list_labels():
    """List all labels with task counts (total, open, closed).

    Machine-generated smoke labels (``e2e-label-123``, ``demo-smoke-*``)
    are filtered out so the user never sees them in the label picker.
    """
    labels = [
        label for label in labels_store.list_labels()
        if not _is_test_label_name(label.get("name", ""))
    ]
    all_assignments = task_labels_store.get_all_assignments()

    # Get task statuses
    task_statuses: dict[str, str] = {}
    try:
        tasks = await ostk.list_tasks()
        for t in tasks:
            task_statuses[t.get("id", "")] = t.get("status", "open")
    except Exception:
        pass

    # Count tasks per label (total, open, closed)
    label_counts: dict[str, dict[str, int]] = {}
    for task_id, label_ids in all_assignments.items():
        status = task_statuses.get(task_id, "open")
        for lid in label_ids:
            if lid not in label_counts:
                label_counts[lid] = {"total": 0, "open": 0, "closed": 0}
            label_counts[lid]["total"] += 1
            if status == "closed":
                label_counts[lid]["closed"] += 1
            else:
                label_counts[lid]["open"] += 1

    for label in labels:
        counts = label_counts.get(label["id"], {"total": 0, "open": 0, "closed": 0})
        label["task_count"] = counts["total"]
        label["open_count"] = counts["open"]
        label["closed_count"] = counts["closed"]

    return {"labels": labels}


@router.get("/labels/colors")
async def label_colors():
    """Return the predefined palette of label colors."""
    return {"colors": LABEL_COLORS}


@router.post("/labels")
async def create_label(body: dict):
    name = (body.get("name") or "").strip()
    color = (body.get("color") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Label name is required")
    if not color:
        raise HTTPException(status_code=400, detail="Label color is required")
    if _is_test_label_name(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "That label name looks like a smoke-test artifact. "
                "Try a short tag like 'launch' or 'website'."
            ),
        )
    try:
        label = labels_store.create_label(name, color)
        label["task_count"] = 0
        return {"label": label}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/labels/{label_id}")
async def delete_label(label_id: str):
    # Remove the label from all tasks first
    task_labels_store.remove_label_from_all_tasks(label_id)
    deleted = labels_store.delete_label(label_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Label not found")
    return {"result": "deleted"}


@router.patch("/labels/{label_id}")
async def update_label(label_id: str, body: dict):
    name = body.get("name")
    color = body.get("color")
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Label name cannot be empty")
    try:
        updated = labels_store.update_label(label_id, name=name, color=color)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Label not found")
    return {"label": updated}


# ---------------------------------------------------------------------------
# Task-Label assignments
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/labels/{label_id}")
async def assign_label_to_task(task_id: str, label_id: str):
    """Add a label to a task."""
    # Verify the label exists
    if not labels_store.get_label(label_id):
        raise HTTPException(status_code=404, detail="Label not found")
    label_ids = task_labels_store.assign_label(task_id, label_id)
    return {"label_ids": label_ids}


@router.delete("/tasks/{task_id}/labels/{label_id}")
async def remove_label_from_task(task_id: str, label_id: str):
    """Remove a label from a task.

    If the label was auto-applied, mark it as rejected so the auto-suggester
    will not re-apply it on the next title or description update.
    """
    was_auto = task_labels_store.is_auto_applied(task_id, label_id)
    label_ids = task_labels_store.remove_label(task_id, label_id, mark_rejected=was_auto)
    return {"label_ids": label_ids}


@router.get("/tasks/{task_id}/context")
async def get_task_context(task_id: str):
    """Return related emails, events, and files for a task.

    Matches by keywords from the task title and description against
    recent Gmail messages, upcoming Calendar events, and Drive files.
    Results are cached in memory for 5 minutes.
    """
    from services.context_linker import get_linked_context

    # Look up the task to get its title and description
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    task = None
    for t in all_tasks:
        if t.get("id") == task_id:
            task = t
            break

    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    title = task.get("title", "")
    description = task.get("description", "")
    linked = await get_linked_context(task_id, title, description)
    return linked

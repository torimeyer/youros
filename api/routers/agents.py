import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from models.schemas import AgentSpawn, AgentNudge, AgentNudgeReply, GrantApprove, GrantDeny
from services.ostk import ostk, OstkError
import services.agent_memory as agent_memory_svc

logger = logging.getLogger(__name__)


class AgentMemorySave(BaseModel):
    key: str
    value: str


class AgentComplete(BaseModel):
    summary: Optional[str] = None


class AgentCancel(BaseModel):
    reason: Optional[str] = "user cancelled"


class AgentHeartbeat(BaseModel):
    step: Optional[str] = None

router = APIRouter(tags=["agents"])

# In-memory registry of active agent processes
active_agents: dict[str, object] = {}

# Spawn metadata (timestamp, budget, model) for API-spawned agents
agent_metadata: dict[str, dict] = {}

# In-memory log of nudges sent during this session (visible in UI)
nudge_history: dict[str, list[dict]] = {}

# In-memory log of replies agents have posted back during this session.
# Populated via ``POST /api/agents/{name}/reply`` and surfaced alongside
# the user's own nudges by ``GET /api/agents/{name}/nudges``.
nudge_replies: dict[str, list[dict]] = {}

from config import AGENTS_DIR, OSTK_DIR

# Persistent file tracking agent state across server restarts
AGENT_STATE_PATH = OSTK_DIR / "agent_state.json"

# How long a running agent can go without a heartbeat before the list
# endpoint marks it ``terminated_stale``. Fifteen minutes is long enough
# to cover a slow pytest run, a tsc build, or a large write where the
# agent legitimately goes quiet on the HTTP channel while the
# subprocess is still doing real work. Short enough that orphans from
# real crashes still clear within a coffee break. Exposed as a module
# constant so tests and future tuning can override it.
#
# Needle 300 belt and suspenders: this is only half the safety net. The
# sweep also refuses to terminate any record whose proc handle in
# ``active_agents`` is still alive (ground truth). POST /reply and
# POST /heartbeat refresh ``last_heartbeat_at`` so any agent following
# the mailbox polling contract is effectively immune to the sweeper.
# GET /nudges does NOT refresh the heartbeat because the frontend also
# polls it, which would keep dead agents alive forever.
STALE_AGENT_TIMEOUT_SECONDS = 900

# How often every spawned agent must poll its nudge mailbox so Tori's
# inline messages reach it in a human amount of time. Sixty seconds is
# a deliberate trade off: short enough that Tori is not left waiting
# for minutes after typing a follow up, long enough that the agent is
# not burning most of its turn on HTTP polls. The value is surfaced in
# the user facing delivery status line and in the standard mailbox
# instruction block so the UI copy and the agent contract never drift.
# Tests assert this stays at or below two minutes.
MAILBOX_CHECK_INTERVAL_SECONDS = 60


def agent_mailbox_instruction(agent_name: str) -> str:
    """Return the standard mailbox checking prompt block for a spawned agent.

    Every Claude Code subagent spawned by the orchestrator must have
    this block pasted into its prompt so it actually reads the nudges
    Tori writes through the Agents page. Without it the mailbox fills
    up with orphan messages and the inline UI silently swallows the
    conversation. The block is a single source of truth shared by the
    orchestrator, tests, and any future spawn helper.

    The block intentionally keeps the wording plain. No jargon. No em
    dashes. No placeholders the agent has to fill in, other than its
    own name which is baked in here at format time. The agent name is
    embedded literally so the curl commands can be copy pasted as is.
    """
    interval = MAILBOX_CHECK_INTERVAL_SECONDS
    return (
        f"## Agent registration and mailbox (mandatory)\n\n"
        "### Step 0: Register immediately\n"
        "Before doing ANY work, register yourself so the user can see you "
        "in the Agents page:\n"
        f"   `curl -s -X POST http://localhost:8000/api/agents/register "
        "-H 'Content-Type: application/json' "
        f"-d '{{\"name\": \"{agent_name}\", \"model\": \"sonnet\", \"budget\": 5}}'`\n\n"
        f"### Mailbox checking (every {interval} seconds)\n\n"
        "The user may send you follow up instructions while you work via "
        "the Agents page in myOS. To pick those up, you MUST do the "
        "following on a regular schedule, alongside your heartbeat:\n\n"
        f"1. Every ~{interval} seconds, call:\n"
        f"   `curl -s http://localhost:8000/api/agents/{agent_name}/nudges`\n"
        "2. Compare the timestamps to the last batch you handled. Any "
        "nudge with a newer timestamp is a NEW message from the user.\n"
        "3. Treat each new nudge as an additional instruction added to "
        "your task. Decide if it changes your plan.\n"
        "4. After acting on a nudge, post a short reply so the user sees "
        "it inline:\n"
        f"   `curl -s -X POST http://localhost:8000/api/agents/{agent_name}/reply"
        " -H 'Content-Type: application/json' -d '{\"message\": \"<your reply>\"}'`\n"
        "5. If a nudge cancels your work, finish the current safe "
        f"step, post a final reply, then POST /api/agents/{agent_name}/complete"
        " and exit.\n\n"
        "This loop lives alongside your heartbeats. Do not skip it. "
        "Tori is waiting on the other end.\n\n"
        "### Pull model (when you finish a task)\n"
        "When you complete your assigned work, you can pull the next "
        "available task instead of stopping:\n"
        "   `curl -s -X POST http://localhost:8000/api/tasks/pull`\n"
        "If the response has `claimed: true`, work on that task next. "
        "If `claimed: false`, no tasks are available. Complete and exit."
    )


def _load_agent_state() -> dict:
    """Load persisted agent state from disk."""
    if AGENT_STATE_PATH.exists():
        try:
            return json.loads(AGENT_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_agent_state():
    """Persist current agent metadata to disk atomically.

    Every mutation site calls this after touching ``agent_metadata``.
    The write goes through ``atomic_write_json`` so a crash mid-save
    cannot leave a half-written JSON blob that would wipe every agent
    record on the next load. Single-loop asyncio guarantees the dict is
    consistent at the moment json.dumps runs, so we do not need a
    separate lock: no await can interleave synchronous serialization
    on the same loop.
    """
    from services.atomic_io import atomic_write_json
    try:
        atomic_write_json(AGENT_STATE_PATH, agent_metadata)
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


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp. Wrapped so tests can patch it."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp tolerant of trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# Maximum number of automatic recovery attempts before giving up. Prevents
# infinite loops where a consistently crashing agent gets re-spawned forever.
MAX_RECOVERY_ATTEMPTS = 3

# Approximate cost per 1M tokens by model family. Used to estimate cost from
# token counts. These are rough averages of input+output pricing.
_COST_PER_MILLION_TOKENS = {
    "claude-opus-4-6": 30.0,
    "claude-sonnet-4-6": 6.0,
    "claude-haiku-4-5": 2.0,
}


def _read_handoff_note(name: str) -> Optional[str]:
    """Read the handoff note for an agent, if one exists.

    Handoff notes are written by ``ostk handoff`` when an agent saves its
    progress before stopping. They live at ``.ostk/handoffs/{name}.md``.
    Returns the note text or None if no handoff exists.
    """
    handoff_path = OSTK_DIR / "handoffs" / f"{name}.md"
    if not handoff_path.exists():
        return None
    try:
        text = handoff_path.read_text().strip()
        return text if text else None
    except OSError:
        return None


def _estimate_cost(model: str, tokens_used: int) -> float:
    """Estimate dollar cost from token count and model.

    Returns a rough estimate. The real cost depends on the input/output
    split, but this gives a useful ballpark for the UI.
    """
    rate = _COST_PER_MILLION_TOKENS.get(model, 6.0)
    return round(tokens_used * rate / 1_000_000, 4)


def _proc_handle_is_alive(name: str) -> bool:
    """Return True if we hold a live subprocess handle for this agent.

    The sweep uses this as ground truth: a record whose proc is still
    running must NEVER be marked ``terminated_stale`` no matter how old
    its last heartbeat looks. A proc with ``returncode is None`` is
    still executing. Any other state (returncode set, no handle, or a
    handle without the attribute) means we cannot prove it is alive, so
    the normal heartbeat age check wins.
    """
    proc = active_agents.get(name)
    if proc is None:
        return False
    returncode = getattr(proc, "returncode", "missing")
    if returncode == "missing":
        return False
    return returncode is None


def _sweep_stale_running_agents() -> bool:
    """Mark any running agent with no recent heartbeat as ``terminated_stale``,
    or flag it for recovery if a handoff note exists.

    Called at the top of the list endpoint so every poll picks up agents
    that died without calling ``/complete`` (external kill, OOM, crashed
    parent process). Agents without ``last_heartbeat_at`` fall back to
    ``spawned_at`` so legacy records from before the heartbeat field was
    added still get swept. Returns ``True`` if any records changed, so
    the caller can persist once instead of per-record.

    Needle 300 safety: before terminating, we check ``active_agents``
    for a live proc handle. If the subprocess is still running, we
    leave the record alone even past the timeout. The death signal
    that matters most is the proc itself, not the HTTP silence.
    """
    now = datetime.now(timezone.utc)
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        last_seen_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        last_seen = _parse_iso(last_seen_raw) if isinstance(last_seen_raw, str) else None
        if last_seen is None:
            continue
        age_seconds = (now - last_seen).total_seconds()
        if age_seconds <= STALE_AGENT_TIMEOUT_SECONDS:
            continue
        # Needle 300: proc is ground truth. If we hold a live handle,
        # the agent is working even if the HTTP channel is quiet.
        if _proc_handle_is_alive(name):
            continue

        # Check if we should attempt recovery instead of terminating
        recovery_count = meta.get("recovery_count", 0)
        handoff_note = _read_handoff_note(name)
        if handoff_note and recovery_count < MAX_RECOVERY_ATTEMPTS:
            meta["status"] = "recovering"
            meta["recovery_count"] = recovery_count + 1
            meta["last_recovery_at"] = now.isoformat()
            changed = True
        else:
            meta["status"] = "terminated_stale"
            meta["terminated_at"] = now.isoformat()
            reason = (
                f"No heartbeat for {int(age_seconds)}s "
                f"(limit {STALE_AGENT_TIMEOUT_SECONDS}s)"
            )
            if recovery_count >= MAX_RECOVERY_ATTEMPTS:
                reason += (
                    f". Recovery exhausted ({recovery_count}/{MAX_RECOVERY_ATTEMPTS})"
                )
            meta["terminated_reason"] = reason
            changed = True
    return changed


def _recover_stale_agents():
    """On startup, mark any persisted 'running' agents as 'abandoned'.

    An agent that was left as 'running' in the state file when the server
    stopped has no live process now. We cannot know whether it finished or
    crashed, so we mark it 'abandoned' so it does not show as running forever.

    This includes Claude Code (source='claude-code') agents. They have no
    local PID, but if the server restarted they are certainly dead. The old
    code skipped them, assuming they would call /complete themselves, but if
    the parent session ended they never do.
    """
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
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


def _claude_code_projects_dir() -> Path:
    """Return the Claude Code projects directory.

    Wrapped in a function so tests can patch ``Path.home`` or this
    helper directly.
    """
    return Path.home() / ".claude" / "projects"


def _claude_code_tasks_root() -> Path:
    """Return the Claude Code scratch tasks root.

    The Claude Code Agent tool writes each subagent's streaming output
    to ``/private/tmp/claude-<uid>/<project-label>/<session-id>/tasks/
    <task-id>.output``. Wrapped so tests can patch it.
    """
    import os
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(f"/private/tmp/claude-{uid}")


# Completion stubs look like this: "Agent 'X' completed (registered externally).".
# They are tiny single-line markdown files written by mark_agent_complete when
# no real transcript has been recorded yet. Treat anything under this threshold
# as "not a real transcript" so a real JSONL transcript wins over the stub.
_STUB_MAX_BYTES = 256


def _is_stub_markdown(path: Path) -> bool:
    """True if ``path`` is a tiny completion-stub markdown file."""
    try:
        if path.stat().st_size > _STUB_MAX_BYTES:
            return False
        text = path.read_text(errors="replace").strip()
    except OSError:
        return False
    return text.endswith("(registered externally).") or text.endswith("completed.")


# name -> (expires_at_monotonic, resolved_path). The resolver below walks
# filesystem globs and opens candidate files per agent, which cost about
# 11ms each. Multiplied by ~140 rows the Agents list endpoint was stuck
# at 1.5s and froze the event loop for every other request. A short TTL
# cache is enough: when a new agent actually starts writing, the next
# list call after the TTL elapses picks it up. Tests that mutate files
# during one process run should call _reset_transcript_resolver_cache().
_resolve_cache: dict[str, tuple[float, Optional[Path]]] = {}
_RESOLVE_TTL_SECONDS = 30.0


def _reset_transcript_resolver_cache() -> None:
    """Test hook. Drop the in-memory resolver cache."""
    _resolve_cache.clear()


def _resolve_transcript_source(name: str) -> Optional[Path]:
    """Cached wrapper around :func:`_resolve_transcript_source_uncached`.

    Results are memoized per agent name for ``_RESOLVE_TTL_SECONDS`` so a
    single /api/agents request does not walk the filesystem 140 times.
    """
    import time as _time
    now = _time.monotonic()
    cached = _resolve_cache.get(name)
    if cached is not None and cached[0] > now:
        return cached[1]
    result = _resolve_transcript_source_uncached(name)
    _resolve_cache[name] = (now + _RESOLVE_TTL_SECONDS, result)
    return result


def _resolve_transcript_source_uncached(name: str) -> Optional[Path]:
    """Resolve the on-disk transcript for an agent.

    Looks in several places and returns the first real hit:

    1. ``PROJECT_ROOT/transcripts/{name}.md`` from the legacy
       daemon-spawned flow, but only if the file is not the tiny
       "completed externally" stub that :func:`mark_agent_complete`
       writes for Claude Code subagents.
    2. The ``transcript_path`` recorded in the agent's metadata at
       register time.
    3. The freshest ``subagents/agent-*.jsonl`` file under
       ``~/.claude/projects/<dashes>/<session>/subagents/`` whose
       spawn prompt references this agent name. This is where Claude
       Code subagents (``saa``-spawned) actually write their
       transcripts.
    4. The freshest ``tasks/*.output`` file under
       ``/private/tmp/claude-<uid>/<dashes>/<session>/tasks/`` whose
       first line references this agent name. Same format, different
       location.
    5. The legacy stub markdown (only if nothing else matched), so
       at least "completed externally" is still returned for agents
       that genuinely have no transcript.

    Returns ``None`` if no candidate exists. The same resolver is
    used by every reader (View Transcript, list metrics, share
    snapshot) so they cannot drift apart again.
    """
    from config import PROJECT_ROOT

    stub_md: Optional[Path] = None

    # 1. Legacy markdown. Only trust it if it is not a tiny completion stub.
    md = PROJECT_ROOT / "transcripts" / f"{name}.md"
    if md.exists() and md.stat().st_size > 0:
        if _is_stub_markdown(md):
            stub_md = md
        else:
            return md

    # 2. Per-agent JSONL recorded at register time.
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path:
        candidate = Path(raw_path)
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate

    # 3. Scan Claude Code subagent JSONL files whose spawn prompt
    #    mentions this agent name. We restrict to the project dir
    #    matching PROJECT_ROOT so we do not surface a transcript
    #    from an unrelated repo.
    projects_dir = _claude_code_projects_dir()
    project_label = str(PROJECT_ROOT).replace("/", "-").lstrip("-")
    project_dir = projects_dir / f"-{project_label}"
    needle = name.lower()

    if project_dir.exists():
        # Subagent transcripts live at
        #   <project_dir>/<session-id>/subagents/agent-<id>.jsonl
        # so the pattern needs the ``*`` for the session-id directory.
        hit = _find_freshest_matching_jsonl(
            project_dir,
            needle,
            "*/subagents/agent-*.jsonl",
        )
        if hit is not None:
            return hit

    # 4. Scan /private/tmp/claude-<uid>/... tasks output files.
    tasks_root = _claude_code_tasks_root()
    tasks_project_dir = tasks_root / f"-{project_label}"
    if tasks_project_dir.exists():
        hit = _find_freshest_matching_jsonl(
            tasks_project_dir,
            needle,
            "*/tasks/*.output",
        )
        if hit is not None:
            return hit

    # 5. Last resort: the stub markdown, so at least something renders.
    return stub_md


# (root, pattern) -> (expires_at_monotonic, [(mtime, path, first_line_lower)])
# Holds the result of one glob+first-line scan so that 140 resolver calls
# in the same /api/agents request share a single sweep of the filesystem.
# Before this cache each agent row re-globbed ~300 candidate files and
# re-opened each one until it found a strict match, which pinned the cold
# endpoint at 1.7 seconds. Keyed on string root/pattern so the cache
# survives fresh Path object identities.
_candidates_cache: dict[tuple[str, str], tuple[float, list[tuple[float, Path, str]]]] = {}
_CANDIDATES_TTL_SECONDS = 10.0


def _reset_candidates_cache() -> None:
    """Test hook. Drop the cached glob+first-line index."""
    _candidates_cache.clear()


def _load_candidates(root: Path, pattern: str) -> list[tuple[float, Path, str]]:
    """Return a cached list of ``(mtime, path, first_line_lower)`` tuples
    for every file under ``root`` matching ``pattern``.

    First call for a (root, pattern) pair does the real filesystem work
    (glob, stat, open + readline per file). Subsequent calls within the
    TTL return the cached list. Sorted freshest-first so callers can
    stop at the first match.
    """
    import time as _time
    now = _time.monotonic()
    key = (str(root), pattern)
    entry = _candidates_cache.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]

    candidates: list[tuple[float, Path, str]] = []
    try:
        for p in root.glob(pattern):
            try:
                stat = p.stat()
                with open(p, "rb") as f:
                    raw = f.readline(4096)
            except OSError:
                continue
            try:
                first_line = raw.decode("utf-8", errors="replace").lower()
            except Exception:
                first_line = ""
            candidates.append((stat.st_mtime, p, first_line))
            if len(candidates) >= _MAX_GLOB_FILES:
                break
    except OSError:
        candidates = []

    candidates.sort(key=lambda t: t[0], reverse=True)
    _candidates_cache[key] = (now + _CANDIDATES_TTL_SECONDS, candidates)
    return candidates


def _first_line_matches_needle(first_line_lower: str, needle_lower: str) -> bool:
    """In-memory equivalent of :func:`_jsonl_strict_match` that takes an
    already-loaded first line instead of re-opening the file. Must stay
    in lockstep with the strict match patterns below.
    """
    needle = needle_lower.strip()
    if not needle or not first_line_lower:
        return False
    register_patterns = (
        f'"name": "{needle}"',
        f'"name":"{needle}"',
        f'\\"name\\": \\"{needle}\\"',
        f'\\"name\\":\\"{needle}\\"',
    )
    endpoint_patterns = (
        f"/agents/{needle}/complete",
        f"/agents/{needle}/register",
        f"/api/agents/{needle}/",
    )
    intro_patterns = (
        f'you are "{needle}"',
        f'you are \\"{needle}\\"',
        f"you are '{needle}'",
        f"you are the {needle} agent",
        f"agent: {needle}",
    )
    if any(p in first_line_lower for p in register_patterns):
        return True
    if any(p in first_line_lower for p in endpoint_patterns):
        return True
    if any(p in first_line_lower for p in intro_patterns):
        return True
    return False


def _find_freshest_matching_jsonl(
    root: Path,
    needle_lower: str,
    pattern: str,
) -> Optional[Path]:
    """Return the freshest file under ``root`` matching ``pattern`` that
    strict-matches ``needle_lower`` on its first line.

    Strict match only. Agent transcripts frequently mention other
    agent names as examples or inside tool results, so a loose
    substring match returns a confident-but-wrong file. Uses the
    shared ``_load_candidates`` cache so one glob sweep serves every
    agent row in the request.
    """
    for _mtime, path, first_line_lower in _load_candidates(root, pattern):
        if _first_line_matches_needle(first_line_lower, needle_lower):
            return path
    return None


# Hard cap on how many candidate files the glob scan considers. The real
# projects dir has a few hundred subagent files so 2000 is comfortably
# above "real" and still below "pathological".
_MAX_GLOB_FILES = 2000


def _jsonl_strict_match(path: Path, needle_lower: str) -> bool:
    """True only if the subagent's *first* message targets this agent name.

    We only inspect the **first** JSONL line. In a Claude Code
    subagent transcript that line is always the initial user spawn
    prompt. Anything later in the file is noise (tool results,
    assistant text, diagnostic chatter) that can coincidentally
    mention other agent names without actually being about them.

    Strict match: the first-line content must contain the register
    POST body in the standard shape or a ``You are "<name>"`` intro.

    Note: Claude Code JSONL lines embed the prompt as a JSON-escaped
    string inside the outer JSON, so a literal ``{"name": "X"}`` in
    the prompt shows up as ``\\"name\\": \\"X\\"`` in the raw line.
    We match both the escaped form (most common) and the plain form
    (templates that bypass the JSON escaping).
    """
    needle = needle_lower.strip()
    if not needle:
        return False
    # Register-POST body shapes. Plain and JSON-escaped variants.
    register_patterns = (
        f'"name": "{needle}"',
        f'"name":"{needle}"',
        f'\\"name\\": \\"{needle}\\"',
        f'\\"name\\":\\"{needle}\\"',
    )
    # API endpoint shapes ``/api/agents/<name>/complete`` and
    # ``/api/agents/<name>/register`` used by saa spawn templates
    # that give the agent its name via URL rather than body.
    endpoint_patterns = (
        f"/agents/{needle}/complete",
        f"/agents/{needle}/register",
        f"/api/agents/{needle}/",
    )
    # Narrative intros.
    intro_patterns = (
        f'you are "{needle}"',
        f'you are \\"{needle}\\"',
        f"you are '{needle}'",
        f"you are the {needle} agent",
        f"agent: {needle}",
    )
    try:
        with open(path, "r", errors="replace") as f:
            first_line = f.readline()
    except OSError:
        return False
    if not first_line:
        return False
    lowered = first_line.lower()
    if any(p in lowered for p in register_patterns):
        return True
    if any(p in lowered for p in endpoint_patterns):
        return True
    if any(p in lowered for p in intro_patterns):
        return True
    return False


def _jsonl_mentions_agent(path: Path, needle_lower: str) -> bool:
    """Loose fallback: True if any of the first 200 lines contains ``needle_lower``.

    Used only when no strict match (register-POST shape or
    ``You are "<name>"`` intro) is found in any candidate. Prefer
    :func:`_jsonl_strict_match` whenever possible, since substring
    matches can false-positive on narrative mentions of other agent
    names.
    """
    needle = needle_lower.strip()
    if not needle:
        return False
    try:
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 200:
                    return False
                if needle in line.lower():
                    return True
    except OSError:
        return False
    return False


def _autodiscover_recent_transcript_path(max_age_seconds: int = 300) -> Optional[str]:
    """Return the most recent Claude Code task output path modified in the
    last ``max_age_seconds`` seconds.

    Called from :func:`register_agent` when the caller did not pass a
    ``transcript_path``. This lets Tori's Agents page show real transcripts
    for agents that Claude Code spawned without knowing their own output
    path.
    """
    import time
    from config import PROJECT_ROOT

    project_label = str(PROJECT_ROOT).replace("/", "-").lstrip("-")
    tasks_root = _claude_code_tasks_root() / f"-{project_label}"
    if not tasks_root.exists():
        return None

    cutoff = time.time() - max_age_seconds
    best_path: Optional[Path] = None
    best_mtime = 0.0
    try:
        # Scope: /private/tmp/claude-<uid>/<project>/<session>/tasks/*.output
        for session_dir in tasks_root.iterdir():
            tasks_dir = session_dir / "tasks"
            if not tasks_dir.is_dir():
                continue
            for p in tasks_dir.glob("*.output"):
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_path = p
    except OSError:
        return None
    return str(best_path) if best_path else None


# path -> (size, mtime_ns, metrics). Keyed on resolved transcript path so
# that a finished agent whose file is static (the vast majority) hits cache
# instead of forcing a full line-count scan on every /api/agents request.
# Without this cache, 142 transcripts * ~12ms per sync read pinned the
# Agents list endpoint at 1.7s and starved the uvicorn event loop of other
# requests while it was reading.
_transcript_metrics_cache: dict[Path, tuple[int, int, dict]] = {}


def _get_transcript_metrics(name: str) -> dict:
    """Get activity metrics from an agent's transcript file.

    Looks at every transcript source the resolver knows about, so
    Claude Code subagents (which write JSONL, not markdown) report
    real byte and line counts on the Agents page. Cached per file by
    (size, mtime_ns) so unchanged transcripts skip the full re-read.
    """
    source = _resolve_transcript_source(name)
    if source is None:
        return {"transcript_bytes": 0, "transcript_lines": 0}
    try:
        stat = source.stat()
    except OSError:
        return {"transcript_bytes": 0, "transcript_lines": 0}
    size = stat.st_size
    mtime_ns = stat.st_mtime_ns
    cached = _transcript_metrics_cache.get(source)
    if cached is not None and cached[0] == size and cached[1] == mtime_ns:
        return cached[2]
    lines = 0
    try:
        with open(source, "rb") as f:
            for _ in f:
                lines += 1
    except OSError:
        return {"transcript_bytes": 0, "transcript_lines": 0}
    metrics = {"transcript_bytes": size, "transcript_lines": lines}
    _transcript_metrics_cache[source] = (size, mtime_ns, metrics)
    return metrics


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

    Resolves the on-disk source via :func:`_resolve_transcript_source`
    so every reader (this endpoint, the list metrics, the share
    snapshot) agrees on where transcripts live. Markdown files are
    returned as-is; JSONL files are parsed into a readable transcript
    with clear speaker labels.
    """
    # Basic safety: reject path traversal.
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid agent name")

    source = _resolve_transcript_source(name)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No transcript found for agent '{name}'. This can happen if "
                f"the agent was spawned by an older myOS version without "
                f"transcript tracking."
            ),
        )

    suffix = source.suffix.lower()
    try:
        if suffix in (".output", ".jsonl") or _looks_like_jsonl(source):
            content = _format_jsonl_transcript(source)
        else:
            content = source.read_text(errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read transcript: {exc}") from exc

    if not content:
        raise HTTPException(
            status_code=404,
            detail=f"Transcript for '{name}' is empty.",
        )

    return {"name": name, "content": content, "bytes": len(content)}


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
                "source": "api",
                **meta,
                "status": "completed" if proc.returncode == 0 else "failed",
            }
        else:
            agents_map[name] = {
                "name": name,
                "source": "api",
                **meta,
                "status": "running",
            }

    # 2b. Persisted metadata (agents from previous server sessions)
    for name, meta in agent_metadata.items():
        if name in active_agents:
            continue  # in-memory process, step 2 already handled it
        # If this agent is already in agents_map from the audit log (step 1)
        # but agent_metadata says it's "running" or "completed", the metadata
        # from the register/complete endpoint is more authoritative than the
        # audit log's guess. Override the audit log entry.
        persisted_status_check = meta.get("status")
        if name in agents_map and persisted_status_check in ("running", "completed"):
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
            }
            continue
        if name in agents_map:
            continue  # audit log entry stands
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
        #
        # Safety net for needle 240: a legacy record with no last_heartbeat_at
        # (registered under older code, or an agent that never polled
        # /heartbeat at all) would otherwise pass through forever. Age
        # it out on spawned_at at the same 20 minute cutoff the else
        # branch uses for is_stale below. The fast 10 minute sweep
        # below still wins for any record that does have last_heartbeat_at,
        # so nothing regresses on the good path.
        elif persisted_status == "running":
            spawned_at_str = meta.get("spawned_at", "")
            has_heartbeat = isinstance(meta.get("last_heartbeat_at"), str)
            is_stale_no_heartbeat = False
            if not has_heartbeat and spawned_at_str:
                try:
                    spawned_at = datetime.fromisoformat(
                        spawned_at_str.replace("Z", "+00:00")
                    )
                    age_seconds = (
                        datetime.now(timezone.utc) - spawned_at
                    ).total_seconds()
                    is_stale_no_heartbeat = age_seconds > 1200
                except (ValueError, TypeError):
                    pass
            if is_stale_no_heartbeat:
                now_iso = datetime.now(timezone.utc).isoformat()
                meta["status"] = "terminated_stale"
                meta["terminated_at"] = now_iso
                meta["terminated_reason"] = (
                    "Running with no heartbeat for over 20 minutes "
                    "(legacy record, swept by list endpoint)"
                )
                agent_metadata[name] = meta
                _save_agent_state()
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "terminated_stale",
                }
            else:
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
        elif persisted_status in ("terminated_stale", "cancelled", "failed", "killed", "stopped"):
            # Preserve any terminal status the register/complete/cancel/sweep
            # paths stamped on the record. Without this branch the else below
            # would try to re-derive the status and could flip terminal states
            # back to running or abandoned.
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
                "status": persisted_status,
            }
        elif pid and _is_pid_alive(pid):
            agents_map[name] = {
                "name": name,
                "source": "api",
                **meta,
                "status": "running",
            }
        else:
            # Process is dead (or externally managed). Check transcript for completion.
            transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
            if transcript.exists() and transcript.stat().st_size > 0:
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "completed",
                }
            elif is_registered:
                # Externally registered agent with no pid and no transcript.
                # If it has been "running" for more than 20 minutes without
                # any transcript activity, mark it as abandoned so the UI
                # does not show forever-stuck ghost agents.
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
                        "source": "claude-code",
                        **meta,
                        "status": "abandoned",
                    }
                else:
                    agents_map[name] = {
                        "name": name,
                        "source": "claude-code",
                        **meta,
                        "status": "running",
                    }

    # 3. Daemon agents (highest priority, ground truth)
    for agent in ps_result.get("agents", []):
        agents_map[agent["name"]] = agent

    # Sweep: mark running agents with no recent heartbeat as terminated_stale.
    # Done after merging the three sources so we catch every record that still
    # says running, regardless of which source set that status. We persist once
    # per request via _save_agent_state so 50 stale rows are cleaned in a single
    # write, not 50 writes.
    # Important: we ONLY sweep records that have a last_heartbeat_at
    # field set. Legacy records registered before the heartbeat field
    # existed fall back to the older abandoned-at-20-min logic above,
    # so an agent that never calls /heartbeat does not get swept
    # purely on spawned_at. Once an agent hits /register or
    # /heartbeat under the new code, last_heartbeat_at is set and it
    # becomes eligible for the fast 10-minute sweep.
    sweep_changed = False
    now_for_sweep = datetime.now(timezone.utc)
    for name, agent in agents_map.items():
        if agent.get("status") != "running":
            continue
        last_heartbeat_raw = agent.get("last_heartbeat_at")
        if not isinstance(last_heartbeat_raw, str):
            continue
        last_seen = _parse_iso(last_heartbeat_raw)
        if last_seen is None:
            continue
        age_seconds = (now_for_sweep - last_seen).total_seconds()
        if age_seconds <= STALE_AGENT_TIMEOUT_SECONDS:
            continue
        # Needle 300: proc is ground truth. If the subprocess is still
        # running, the agent is working even if its HTTP channel has
        # been quiet past the timeout. Only the death signal matters.
        if _proc_handle_is_alive(name):
            continue
        terminated_at = now_for_sweep.isoformat()
        reason = (
            f"No heartbeat for {int(age_seconds)}s "
            f"(limit {STALE_AGENT_TIMEOUT_SECONDS}s)"
        )
        agent["status"] = "terminated_stale"
        agent["terminated_at"] = terminated_at
        agent["terminated_reason"] = reason
        # Persist to agent_metadata so the next request does not re-sweep.
        meta = agent_metadata.get(name)
        if meta is not None:
            meta["status"] = "terminated_stale"
            meta["terminated_at"] = terminated_at
            meta["terminated_reason"] = reason
            sweep_changed = True
    if sweep_changed:
        _save_agent_state()

    all_agents = list(agents_map.values())

    # Enrich agents with transcript metrics and budget info
    for agent in all_agents:
        metrics = _get_transcript_metrics(agent["name"])
        agent.update(metrics)
        # Add budget/token info from metadata
        meta = agent_metadata.get(agent["name"], {})
        tokens_used = meta.get("tokens_used", 0)
        token_limit = meta.get("token_limit")
        agent["tokens_used"] = tokens_used
        agent["token_limit"] = token_limit
        if token_limit and token_limit > 0:
            agent["token_usage_pct"] = round(tokens_used / token_limit * 100, 1)
        else:
            agent["token_usage_pct"] = None
        agent["cost_estimate"] = _estimate_cost(
            meta.get("model", ""), tokens_used
        )
        # Add recovery info
        agent["recovery_count"] = meta.get("recovery_count", 0)
        agent["max_recoveries"] = MAX_RECOVERY_ATTEMPTS

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
async def spawn_agent(body: AgentSpawn, request: Request = None):
    from config import PROJECT_ROOT
    from services.policy_enforcement import (
        check_budget,
        check_approval_required,
        get_isolation_level,
        isolation_to_permission_mode,
    )

    # Enterprise policy checks: budget limit and approval threshold
    allowed, reason = check_budget(body.budget)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    if check_approval_required(body.budget):
        from services import enterprise_store as _es
        _threshold = _es.get_policies().get("require_approval_above", 5.0)
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent budget ${body.budget:.2f} requires admin approval "
                f"(limit: ${_threshold:.2f}). Ask an admin to approve or "
                f"raise the threshold in Settings > Enterprise > Policies."
            ),
        )

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

    # Prepend the mandatory mailbox instruction block so every spawned
    # agent knows it must poll /nudges and reply via /reply. Without
    # this block the agent has no idea Tori may send follow ups inline
    # and the Agents page mailbox silently fills up. Regression guard
    # for needle 240. The block goes at the very top so it survives
    # any truncation or model prompt reformatting downstream.
    mailbox_block = agent_mailbox_instruction(body.name)
    if prompt_with_memory:
        prompt_with_memory = mailbox_block + "\n\n---\n\n" + prompt_with_memory
    else:
        prompt_with_memory = mailbox_block

    # Append quality gate instructions from the matching Agentfile.
    # When the caller passes an explicit template name (e.g. template="saa"),
    # resolve by template and inject the FULL template envelope: PROMPT,
    # TOOL list, LIMIT lines, and AC gates. Otherwise fall back to the
    # legacy name-based lookup that only injects quality gates. See
    # needle 295 for the Tasks page "Implement with saa" flow.
    from services.agentfile_parser import (
        build_quality_gate_instructions,
        build_template_instructions,
        get_agent_config,
        get_agent_config_by_template,
        list_available_templates,
    )
    if body.template:
        template_config = get_agent_config_by_template(body.template)
        if template_config is None:
            available = list_available_templates()
            available_str = ", ".join(available) if available else "none found"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown saa template: '{body.template}'. "
                    f"Available templates: {available_str}."
                ),
            )
        template_instructions = build_template_instructions(template_config)
        if template_instructions:
            prompt_with_memory = prompt_with_memory + "\n\n---\n\n" + template_instructions
    else:
        agent_config = get_agent_config(body.name)
        quality_instructions = build_quality_gate_instructions(agent_config)
        if quality_instructions:
            prompt_with_memory = prompt_with_memory + "\n\n---\n\n" + quality_instructions

    # Map isolation level to Claude CLI permission mode
    _perm_mode = isolation_to_permission_mode(get_isolation_level())

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", model,
        "--output-format", "text",
        "--max-budget-usd", str(body.budget),
        "--permission-mode", _perm_mode,
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
        now_spawn = datetime.now(timezone.utc).isoformat()
        spawn_meta: dict = {
            "status": "running",
            "spawned_at": now_spawn,
            "last_heartbeat_at": now_spawn,
            "budget": str(body.budget),
            "model": model,
            "pid": proc.pid,
            "tokens_used": 0,
        }
        if body.token_limit is not None:
            spawn_meta["token_limit"] = body.token_limit
        # Preserve recovery_count across re-spawns so the cap is tracked
        existing_meta = agent_metadata.get(body.name) or {}
        if existing_meta.get("recovery_count"):
            spawn_meta["recovery_count"] = existing_meta["recovery_count"]
        agent_metadata[body.name] = spawn_meta
        _save_agent_state()

        # Log to audit
        try:
            audit_data = {"name": body.name, "model": model, "budget": str(body.budget)}
            if request:
                from services import enterprise_store
                if enterprise_store.is_enterprise():
                    from services.session import verify_session, SESSION_COOKIE_NAME
                    _tok = request.cookies.get(SESSION_COOKIE_NAME, "")
                    _claims = verify_session(_tok) if _tok else None
                    if _claims:
                        audit_data["user"] = _claims["sub"]
            await ostk._run("os", "audit", "--event", "agent.spawned",
                           "--data", json.dumps(audit_data))
        except Exception:
            pass

        return {
            "result": f"Agent '{body.name}' spawned",
            "pid": proc.pid,
            "transcript": str(transcript_path),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FleetSpawn(BaseModel):
    fleet_id: str
    context: str = ""
    model: str = "sonnet"
    budget: float = 2.0


@router.get("/agents/fleets")
async def list_fleets():
    """Return the built-in fleet templates."""
    from services.fleet_templates import list_fleet_templates
    return {"fleets": list_fleet_templates()}


@router.post("/agents/fleets/spawn")
async def spawn_fleet(body: FleetSpawn):
    """Spawn all members of a fleet template as parallel agents.

    Each member gets a role-specific prompt with the user's context
    prepended. All agents share the workspace for coordination.
    """
    from services.fleet_templates import list_fleet_templates
    from services.policy_enforcement import check_budget, check_approval_required

    # Fail fast: check the per-member budget before spawning anything
    allowed, reason = check_budget(body.budget)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    if check_approval_required(body.budget):
        from services import enterprise_store as _es
        _threshold = _es.get_policies().get("require_approval_above", 5.0)
        raise HTTPException(
            status_code=403,
            detail=(
                f"Fleet per-member budget ${body.budget:.2f} requires admin approval "
                f"(limit: ${_threshold:.2f}). Ask an admin to approve or "
                f"raise the threshold in Settings > Enterprise > Policies."
            ),
        )

    templates = list_fleet_templates()
    fleet = next((f for f in templates if f["id"] == body.fleet_id), None)
    if not fleet:
        raise HTTPException(status_code=404, detail=f"Fleet template '{body.fleet_id}' not found")

    model = MODEL_MAP.get(body.model, body.model)
    spawned = []

    for member in fleet["members"]:
        role_slug = member["role"].lower().replace(" ", "-")
        agent_name = f"{fleet['id']}-{role_slug}"

        full_prompt = (
            f"ROLE: {member['role']}\n\n"
            f"CONTEXT FROM USER: {body.context}\n\n"
            f"{member['prompt']}\n\n"
            "COORDINATION: You are part of a fleet. Other agents with different "
            "roles are working on the same task. Use the shared agent workspace "
            "to read their output and save yours. Check the workspace before "
            "starting so you build on what others have done."
        )

        agent_body = AgentSpawn(
            name=agent_name,
            prompt=full_prompt,
            model=body.model,
            budget=body.budget,
        )

        try:
            result = await spawn_agent(agent_body)
            spawned.append({
                "name": agent_name,
                "role": member["role"],
                "pid": result.get("pid"),
            })
        except Exception as e:
            spawned.append({
                "name": agent_name,
                "role": member["role"],
                "error": str(e),
            })

    return {
        "fleet": fleet["name"],
        "spawned": spawned,
        "total": len(spawned),
    }


@router.post("/agents/register")
async def register_agent(body: AgentSpawn, request: Request = None):
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
    now_iso = datetime.now(timezone.utc).isoformat()
    # Preserve spawned_at across re-registers so an agent that calls
    # register again (for a heartbeat-like ping) does not lose its
    # original start time and its duration stays accurate.
    existing = agent_metadata.get(body.name) or {}
    spawned_at = existing.get("spawned_at") or now_iso
    record: dict = {
        "spawned_at": spawned_at,
        "budget": str(body.budget),
        "model": model,
        "source": "claude-code",
        "status": status,
        # Heartbeat field. Set on register and refreshed on every re-register
        # or /heartbeat call. The list endpoint compares this to
        # STALE_AGENT_TIMEOUT_SECONDS to auto-sweep orphans.
        "last_heartbeat_at": now_iso,
        "tokens_used": existing.get("tokens_used", 0),
    }
    if body.token_limit is not None:
        record["token_limit"] = body.token_limit
    elif existing.get("token_limit") is not None:
        record["token_limit"] = existing["token_limit"]
    # Preserve recovery_count across re-registers
    if existing.get("recovery_count"):
        record["recovery_count"] = existing["recovery_count"]
    if body.description:
        record["description"] = body.description
    if body.prompt:
        record["prompt"] = body.prompt[:500]
    if body.transcript_path:
        record["transcript_path"] = body.transcript_path
    else:
        # Best-effort auto-discovery: Claude Code's Agent tool writes
        # streaming output to /private/tmp/claude-<uid>/.../tasks/*.output
        # but does not pass that path to ``POST /api/agents/register``.
        # Find the freshest .output file touched in the last few minutes
        # and record it so View Transcript can find a real transcript even
        # if the caller forgot (or could not) pass transcript_path.
        discovered = _autodiscover_recent_transcript_path()
        if discovered:
            record["transcript_path"] = discovered
    agent_metadata[body.name] = record
    _save_agent_state()

    # Log to audit
    try:
        audit_data = {"name": body.name, "model": model, "budget": str(body.budget)}
        if request:
            from services import enterprise_store
            if enterprise_store.is_enterprise():
                from services.session import verify_session, SESSION_COOKIE_NAME
                _tok = request.cookies.get(SESSION_COOKIE_NAME, "")
                _claims = verify_session(_tok) if _tok else None
                if _claims:
                    audit_data["user"] = _claims["sub"]
        await ostk._run("os", "audit", "--event", "agent.spawned",
                       "--data", json.dumps(audit_data))
    except Exception:
        pass

    # Return the mailbox contract so the caller (a Claude Code subagent
    # calling /register at step 0) learns the polling rule from the API
    # itself, not from the parent session's prompt. Without this block
    # the subagent may never know it should poll /nudges and Tori's
    # follow up messages pile up unseen. Regression guard for needle
    # 240. Keyed under ``mailbox_instruction`` so old callers that only
    # read ``result`` still work.
    return {
        "result": f"Agent '{body.name}' registered",
        "source": "claude-code",
        "status": status,
        "mailbox_instruction": agent_mailbox_instruction(body.name),
        "mailbox_check_interval_seconds": MAILBOX_CHECK_INTERVAL_SECONDS,
    }


@router.post("/agents/{name}/complete")
async def mark_agent_complete(name: str, body: Optional[AgentComplete] = None):
    """Mark an externally managed agent as completed.

    This writes the completion status to the persistent agent metadata store
    so the agent shows as completed in the UI across server restarts, and
    also writes a transcript marker as a belt-and-suspenders signal.

    If ``body.summary`` is provided it is appended to the agent's persistent
    memory so future sessions can pick up where this one left off.
    """
    # Defensive: if this agent was already marked terminal (cancelled,
    # terminated_stale), do NOT flip it back to completed. A zombie
    # /complete arriving after a kill or sweep must not revive the
    # record. Return a 200 so callers treat it as a noop.
    existing_meta = agent_metadata.get(name, {})
    terminal_status = existing_meta.get("status")
    if terminal_status in ("cancelled", "terminated_stale"):
        return {
            "result": f"Agent '{name}' already {terminal_status}, complete ignored",
            "status": terminal_status,
        }

    # Run quality gate checks from the matching Agentfile
    from services.agentfile_parser import get_agent_config
    agent_config = get_agent_config(name)
    if agent_config.acceptance_criteria:
        import subprocess
        from config import PROJECT_ROOT
        gate_failures: list[str] = []
        for ac_cmd in agent_config.acceptance_criteria:
            try:
                result = subprocess.run(
                    ac_cmd, shell=True, capture_output=True, text=True,
                    cwd=str(PROJECT_ROOT), timeout=60,
                )
                if result.returncode != 0:
                    gate_failures.append(
                        f"AC failed: `{ac_cmd}` (exit {result.returncode})"
                    )
            except subprocess.TimeoutExpired:
                gate_failures.append(f"AC timed out: `{ac_cmd}`")
            except Exception as e:
                gate_failures.append(f"AC error: `{ac_cmd}` ({e})")

        if gate_failures:
            # Record the failure but still allow completion
            # (the agent already did the work, blocking helps nobody)
            if name in agent_metadata:
                agent_metadata[name]["gate_results"] = gate_failures
                _save_agent_state()

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

    # Write a transcript marker so the status check finds it even on
    # legacy rows. IMPORTANT: only write the stub if no real transcript
    # source exists. Otherwise the stub would mask the real JSONL that
    # ``_resolve_transcript_source`` would otherwise return and View
    # Transcript would show "completed (registered externally)" forever.
    from config import PROJECT_ROOT
    transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    should_write_stub = not transcript.exists() or transcript.stat().st_size == 0
    if should_write_stub:
        # Does the resolver already know where the real transcript lives?
        real_source = _resolve_transcript_source(name)
        if real_source is not None and real_source != transcript:
            should_write_stub = False
    if should_write_stub:
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


@router.post("/agents/{name}/heartbeat")
async def heartbeat_agent(name: str, body: Optional[AgentHeartbeat] = None):
    """Refresh an agent's ``last_heartbeat_at`` so the stale sweep does
    not mark it terminated.

    Agents should POST here on a short interval (every minute or so)
    while they are still doing work. The body is optional. If ``step``
    is provided it is stored on the record so the UI can surface the
    current phase the agent is working on.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found. Register first with /api/agents/register.",
        )
    meta = agent_metadata[name]
    now_iso = _now_iso()
    meta["last_heartbeat_at"] = now_iso
    if body and body.step:
        meta["current_step"] = body.step
    _save_agent_state()
    return {"ok": True, "last_heartbeat_at": now_iso}


class TokenUsageUpdate(BaseModel):
    tokens_used: int


@router.get("/agents/{name}/budget")
async def get_agent_budget(name: str):
    """Return the token budget status for an agent.

    Returns tokens_used, token_limit (if set), and an estimated cost
    based on the agent's model. The UI uses this to render a progress
    bar showing how much of the budget has been consumed.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found.",
        )
    meta = agent_metadata[name]
    tokens_used = meta.get("tokens_used", 0)
    token_limit = meta.get("token_limit")
    model = meta.get("model", "")
    cost_estimate = _estimate_cost(model, tokens_used)

    result: dict = {
        "agent": name,
        "tokens_used": tokens_used,
        "token_limit": token_limit,
        "cost_estimate": cost_estimate,
        "model": model,
    }
    if token_limit and token_limit > 0:
        result["usage_pct"] = round(tokens_used / token_limit * 100, 1)
    return result


@router.post("/agents/{name}/budget")
async def update_agent_budget(name: str, body: TokenUsageUpdate):
    """Update the token usage counter for an agent.

    Called by agents or orchestrators to report how many tokens have been
    consumed so far. The UI polls GET /agents/{name}/budget to show a
    live progress bar.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found.",
        )
    meta = agent_metadata[name]
    meta["tokens_used"] = body.tokens_used
    _save_agent_state()
    return {"ok": True, "tokens_used": body.tokens_used}


@router.post("/agents/{name}/recover")
async def recover_agent(name: str):
    """Manually recover a failed or stale agent.

    Reads the agent's last handoff note (or last transcript snippet) and
    re-spawns it with that context as a prompt prefix. This lets Tori
    manually revive agents that crashed without waiting for the automatic
    sweep.

    Recovery is capped at MAX_RECOVERY_ATTEMPTS to prevent infinite loops.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found.",
        )
    meta = agent_metadata[name]
    recovery_count = meta.get("recovery_count", 0)

    if recovery_count >= MAX_RECOVERY_ATTEMPTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Agent '{name}' has already been recovered "
                f"{recovery_count} times (limit {MAX_RECOVERY_ATTEMPTS}). "
                f"Check the transcript for the root cause."
            ),
        )

    # Only allow recovery of dead agents, not running ones
    status = meta.get("status", "")
    if status == "running":
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{name}' is still running. Cancel it first if you want to restart.",
        )

    # Build recovery context from handoff note or transcript
    handoff_note = _read_handoff_note(name)
    recovery_context = ""
    if handoff_note:
        recovery_context = (
            f"You are being recovered after a crash. Here is where you left off:\n\n"
            f"{handoff_note}\n\n"
            f"Continue from where you stopped. Do not repeat work already done."
        )
    else:
        # Try to get last lines from transcript as context
        source = _resolve_transcript_source(name)
        if source and source.exists():
            try:
                lines = source.read_text(errors="replace").strip().split("\n")
                last_lines = lines[-20:] if len(lines) > 20 else lines
                transcript_tail = "\n".join(last_lines)
                recovery_context = (
                    f"You are being recovered after a crash. Here is the tail of your "
                    f"last session transcript:\n\n{transcript_tail}\n\n"
                    f"Continue from where you stopped. Do not repeat work already done."
                )
            except OSError:
                pass

    if not recovery_context:
        recovery_context = (
            f"You are being recovered after a crash. No handoff note or transcript "
            f"was found. Check the codebase for your previous work and continue."
        )

    # Get the original prompt if available
    original_prompt = meta.get("prompt", "")
    model_short = meta.get("model", "sonnet")
    # Map full model names back to short names for AgentSpawn
    for short, full in MODEL_MAP.items():
        if full == model_short:
            model_short = short
            break
    budget = float(meta.get("budget", "2.0"))

    # Build the recovery spawn body
    full_prompt = recovery_context
    if original_prompt:
        full_prompt = f"{recovery_context}\n\nOriginal task:\n{original_prompt}"

    # Update recovery metadata
    meta["recovery_count"] = recovery_count + 1
    meta["last_recovery_at"] = _now_iso()
    meta["status"] = "recovering"
    _save_agent_state()

    # Spawn the recovered agent
    spawn_body = AgentSpawn(
        name=name,
        prompt=full_prompt,
        model=model_short,
        budget=budget,
        token_limit=meta.get("token_limit"),
    )
    try:
        result = await spawn_agent(spawn_body)
        return {
            "result": f"Agent '{name}' recovered (attempt {recovery_count + 1}/{MAX_RECOVERY_ATTEMPTS})",
            "recovery_count": recovery_count + 1,
            "max_recoveries": MAX_RECOVERY_ATTEMPTS,
            "spawn_result": result,
        }
    except Exception as e:
        # Roll back to the terminal state if spawn fails
        meta["status"] = "failed"
        meta["terminated_at"] = _now_iso()
        meta["terminated_reason"] = f"Recovery spawn failed: {e}"
        _save_agent_state()
        raise HTTPException(status_code=500, detail=f"Recovery spawn failed: {e}")


@router.post("/agents/{name}/cancel")
async def cancel_agent(name: str, body: Optional[AgentCancel] = None):
    """Mark an agent as cancelled.

    Unlike ``/kill`` which tries to actually terminate an in-process
    subprocess, this endpoint just marks the agent record as
    cancelled so it falls out of Active Sessions. It is the right
    call for externally managed agents (Claude Code subagents) that
    myOS cannot signal directly.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found.",
        )
    reason = body.reason if body and body.reason else "user cancelled"
    now_iso = _now_iso()
    meta = agent_metadata[name]
    meta["status"] = "cancelled"
    meta["terminated_at"] = now_iso
    meta["terminated_reason"] = reason
    _save_agent_state()

    # Audit so the audit log reflects the cancel.
    try:
        await ostk._run(
            "os",
            "audit",
            "--event",
            "agent.cancelled",
            "--data",
            json.dumps({"name": name, "reason": reason}),
        )
    except Exception:
        pass

    return {"ok": True, "status": "cancelled", "terminated_at": now_iso}


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


def _nudge_delivery_message(delivery: str, name: str) -> str:
    """Return the plain language status line the UI shows to the user.

    The wording is deliberately non technical and tells the user what
    will actually happen next. This is the surface for Tori's feedback
    that silent success on a dead delivery pipe is not acceptable.
    The file_only branch cites the real mailbox check interval so the
    user sees a specific wait time, not a vague "next time" promise.
    """
    if delivery == "stdin":
        return "Sent. The agent should respond shortly."
    if delivery == "file_only":
        return (
            f"Saved. The agent will see this within about "
            f"{MAILBOX_CHECK_INTERVAL_SECONDS} seconds on its next "
            f"mailbox check."
        )
    return f"Could not deliver to '{name}'. No running agent was found."


@router.post("/agents/{name}/nudge")
async def nudge_agent(name: str, body: AgentNudge):
    """Send a message (nudge) to a running agent.

    Delivery is reported honestly. There are three cases:

    * ``stdin``: the agent has a live process handle and its stdin
      accepted the message.
    * ``file_only``: the message was written to
      ``.ostk/nudges/{name}/`` for the agent to pick up on its next
      check, but there is no live pipe. This is the normal case for
      Claude Code subagents that register over HTTP.
    * ``unavailable``: the agent is not registered at all, so there is
      no place for the message to land.
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Agents must be registered for a nudge to mean anything. If the
    # name is unknown we refuse early with a 404 so the UI can show
    # "agent not found" instead of writing orphan files forever.
    meta = agent_metadata.get(name)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' is not registered.",
        )

    # Write the nudge to the filesystem so any watcher can pick it up.
    nudge_data = await ostk.write_nudge(name, message)

    # Try to write to the process stdin for immediate delivery. This
    # only works when the agent was spawned by this API (has a live
    # process handle in ``active_agents``). Claude Code subagents that
    # registered over HTTP will never have a proc here, and that is
    # expected. We report ``file_only`` for them, not ``unavailable``.
    proc = active_agents.get(name)
    delivery = "file_only"
    if proc and hasattr(proc, "stdin") and proc.stdin:
        try:
            proc.stdin.write((message + "\n").encode())
            await proc.stdin.drain()
            delivery = "stdin"
        except (BrokenPipeError, ConnectionResetError, OSError):
            delivery = "file_only"

    delivery_message = _nudge_delivery_message(delivery, name)

    # Track in session history
    if name not in nudge_history:
        nudge_history[name] = []
    record = {
        "message": message,
        "timestamp": nudge_data["timestamp"],
        "source": "ui",
        # Legacy field kept for any old clients that still read it.
        "stdin_delivered": delivery == "stdin",
        # New structured delivery fields.
        "delivery": delivery,
        "delivery_message": delivery_message,
    }
    nudge_history[name].append(record)

    return {
        "result": f"Nudge sent to '{name}'",
        "nudge": record,
    }


@router.post("/agents/{name}/reply")
async def post_agent_reply(name: str, body: AgentNudgeReply):
    """Record a reply from the agent to a previous nudge.

    Agents (or any worker watching the nudge directory) call this to
    post their answer back into the inline conversation. The reply is
    persisted to ``.ostk/nudges/{name}/replies/`` and mirrored in
    session memory so ``GET /api/agents/{name}/nudges`` surfaces it on
    the next poll. A 404 is returned if the agent is not registered so
    stale wrappers cannot stuff orphan replies into the store.
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Reply cannot be empty")

    if agent_metadata.get(name) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' is not registered.",
        )

    reply_data = await ostk.append_nudge_reply(
        name,
        message,
        in_reply_to=body.in_reply_to,
    )

    if name not in nudge_replies:
        nudge_replies[name] = []
    nudge_replies[name].append(reply_data)

    # Needle 300: a live /reply is proof the agent is alive and working.
    # Refresh last_heartbeat_at so the sweep cannot mark an actively
    # chatting agent as terminated_stale. If the record was already
    # marked terminated_stale (false positive from an earlier sweep),
    # flip it back to completed. The reply itself proves the agent did
    # its work, even if it was a final sign off.
    meta = agent_metadata.get(name)
    revived = False
    if meta is not None:
        now_iso = _now_iso()
        meta["last_heartbeat_at"] = now_iso
        if meta.get("status") == "terminated_stale":
            meta["status"] = "completed"
            meta["completed_at"] = now_iso
            meta["revival_reason"] = (
                "Reply arrived after the record was marked terminated_stale. "
                "The agent was still working. Record restored to completed."
            )
            revived = True
        _save_agent_state()

    return {
        "result": f"Reply recorded for '{name}'",
        "reply": reply_data,
        "revived": revived,
    }


@router.get("/agents/{name}/nudges")
async def list_agent_nudges(name: str):
    """List all nudges and replies for an agent.

    Returns four lists:

    * ``nudges``: file-based user messages written by /nudge.
    * ``session_nudges``: in-memory user messages from the current
      session. Same shape as ``nudges``, kept separate so the client
      can deduplicate.
    * ``replies``: file-based replies the agent has posted via /reply.
    * ``session_replies``: in-memory replies from the current session.

    Needle 300: heartbeat is NOT refreshed here. The frontend also
    polls this endpoint to show nudge replies, so refreshing here
    would keep dead agents alive forever. Agents refresh their own
    heartbeat via POST /heartbeat, POST /reply, or POST /register.
    """
    file_nudges = await ostk.list_nudges(name)
    session_nudges = nudge_history.get(name, [])
    file_replies = await ostk.list_nudge_replies(name)
    session_replies = nudge_replies.get(name, [])

    # DO NOT refresh last_heartbeat_at here. The frontend also polls
    # this endpoint (every 3-5s) to display nudge replies. If we
    # refresh the heartbeat on every frontend poll, dead agents look
    # alive forever because the browser keeps their heartbeat fresh.
    # Only the agent itself should refresh its heartbeat, via
    # POST /heartbeat, POST /reply, or POST /register.

    return {
        "agent": name,
        "nudges": file_nudges,
        "session_nudges": session_nudges,
        "replies": file_replies,
        "session_replies": session_replies,
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
    """List every Agentfile in the repo with parsed capabilities.

    Each entry carries a ``capabilities`` field so the Agents page can
    show writes, restrictions, budget, time limit, and sandbox in plain
    language before the user hits Spawn. If an Agentfile fails to
    parse, the entry still appears with ``parse_error`` set, so the UI
    can mark the card unspawnable and tell the user to fix the file.
    """
    from services.agentfile_parser import (
        build_capabilities_summary,
        parse_agentfile,
        AgentfileParseError,
    )

    templates = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.agent")):
            content = f.read_text()
            entry: dict = {
                "name": f.stem,
                "file": f.name,
                "content": content[:500],
                "capabilities": None,
                "parse_error": None,
            }
            try:
                config = parse_agentfile(f)
                entry["capabilities"] = build_capabilities_summary(config)
                entry["description"] = config.description or ""
            except AgentfileParseError as exc:
                entry["parse_error"] = str(exc)
            templates.append(entry)
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
    filters out grants from "unknown" agents, those are almost always
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


# ── Agent Correction (needle 333) ──────────────────────────────────


@router.post("/agents/{name}/correct")
async def correct_agent(name: str, body: AgentNudge):
    """Send a structured correction to a running agent.

    Calls ostk :correct to record the correction in the audit trail,
    then sends a regular nudge so both systems stay in sync. The nudge
    is tagged as a correction so the UI can render it with a distinct
    amber/orange visual.
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Correction message cannot be empty")

    meta = agent_metadata.get(name)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' is not registered.",
        )

    # 1. Call ostk correct for the audit trail
    correction_result = None
    try:
        correction_result = await ostk.correct_agent(name, message)
    except OstkError:
        # ostk correct may not be available. Continue with the nudge
        # so the message still reaches the agent.
        pass

    # 2. Send a regular nudge so the agent sees the message
    correction_message = f"[CORRECTION] {message}"
    nudge_data = await ostk.write_nudge(name, correction_message)

    # Try stdin delivery
    proc = active_agents.get(name)
    delivery = "file_only"
    if proc and hasattr(proc, "stdin") and proc.stdin:
        try:
            proc.stdin.write((correction_message + "\n").encode())
            await proc.stdin.drain()
            delivery = "stdin"
        except (BrokenPipeError, ConnectionResetError, OSError):
            delivery = "file_only"

    delivery_message = _nudge_delivery_message(delivery, name)

    # Track in session history
    if name not in nudge_history:
        nudge_history[name] = []
    record = {
        "message": correction_message,
        "timestamp": nudge_data["timestamp"],
        "source": "ui",
        "type": "correction",
        "delivery": delivery,
        "delivery_message": delivery_message,
        "stdin_delivered": delivery == "stdin",
    }
    nudge_history[name].append(record)

    return {
        "result": f"Correction sent to '{name}'",
        "nudge": record,
        "ostk_result": correction_result,
    }


# ── Context Pressure (needle 337) ─────────────────────────────────


@router.get("/agents/{name}/context-pressure")
async def get_context_pressure(name: str):
    """Return context pressure data for an agent if the service is available.

    If the :dying service is inactive, returns available: false so the
    UI knows not to show anything.
    """
    data = await ostk.check_context_pressure(name)
    if data is None:
        return {"available": False, "agent": name}
    return {"available": True, "agent": name, **data}


# ── Coordination Locks (needle 338) ───────────────────────────────


@router.get("/agents/locks")
async def list_locks():
    """List all active coordination locks."""
    locks = await ostk.list_locks()
    return {"locks": locks}


@router.delete("/agents/locks/{lock_name}")
async def release_lock(lock_name: str):
    """Force release a coordination lock by name."""
    try:
        result = await ostk.release_lock(lock_name)
        return {"result": result, "lock": lock_name, "action": "released"}
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
        except WebSocketDisconnect:
            # Normal client-side disconnect. Not an error.
            pass
        except Exception:
            # Unexpected failures must be visible in server logs,
            # otherwise silent WebSocket deaths hide real bugs
            # (JSON encode errors, send_json on a closed socket,
            # subprocess read failures). Leave the stack trace.
            logger.exception("agent attach read_stdout failed for %s", name)

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
                    delivery = "file_only"
                    if proc and hasattr(proc, "stdin") and proc.stdin:
                        try:
                            proc.stdin.write((message + "\n").encode())
                            await proc.stdin.drain()
                            delivery = "stdin"
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass

                    delivery_message = _nudge_delivery_message(delivery, name)

                    # Track in session history
                    if name not in nudge_history:
                        nudge_history[name] = []
                    record = {
                        "message": message,
                        "timestamp": nudge_data["timestamp"],
                        "source": "ui",
                        "stdin_delivered": delivery == "stdin",
                        "delivery": delivery,
                        "delivery_message": delivery_message,
                    }
                    nudge_history[name].append(record)

                    # Echo the nudge back to the client for display
                    await websocket.send_json({
                        "type": "nudge_ack",
                        "data": record,
                    })
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("agent attach read_client failed for %s", name)

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
        logger.exception("agent attach task wait failed for %s", name)
        stdout_task.cancel()
        client_task.cancel()


@router.websocket("/ws/agents/{name}/stream")
async def agent_attach_stream(websocket: WebSocket, name: str):
    """Stream a running agent's live output via ``ostk attach``.

    This endpoint spawns ``ostk attach <name>`` as a subprocess and
    forwards every line of stdout to the WebSocket client as a JSON
    message with ``{"type": "output", "data": "<line>"}``. When the
    subprocess exits (agent completes or is killed) the endpoint sends
    ``{"type": "done", "return_code": N}`` and closes the socket.

    If the client disconnects first, the subprocess is killed so we
    do not leak orphan processes.
    """
    import shutil

    await websocket.accept()

    # Reject path traversal in the agent name.
    if "/" in name or ".." in name:
        await websocket.send_json({"type": "error", "data": "Invalid agent name"})
        await websocket.close()
        return

    # Locate the ostk binary. Prefer the PATH lookup so tests can
    # substitute a mock, fall back to the MYOS_OSTK_BIN env override,
    # then the canonical per-user install path under ~/.local/bin.
    # Never hardcode a literal username. The binary has to exist or
    # the subprocess spawn below will fail fast with a clear error.
    import os
    ostk_bin = (
        shutil.which("ostk")
        or os.environ.get("MYOS_OSTK_BIN")
        or str(Path.home() / ".local" / "bin" / "ostk")
    )

    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            ostk_bin, "attach", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _stream_output():
            """Forward subprocess stdout to the WebSocket client."""
            assert proc is not None and proc.stdout is not None
            try:
                async for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace").rstrip("\n\r")
                    await websocket.send_json({"type": "output", "data": line})
                # Subprocess ended. Wait for the exit code.
                return_code = await proc.wait()
                await websocket.send_json({
                    "type": "done",
                    "return_code": return_code,
                })
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("agent stream _stream_output failed for %s", name)

        async def _read_client():
            """Keep reading from the client so we detect disconnects."""
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("agent stream _read_client failed for %s", name)

        output_task = asyncio.create_task(_stream_output())
        client_read_task = asyncio.create_task(_read_client())
        try:
            _done, _pending = await asyncio.wait(
                [output_task, client_read_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in _pending:
                task.cancel()
        except Exception:
            logger.exception("agent stream task wait failed for %s", name)
            output_task.cancel()
            client_read_task.cancel()
    finally:
        # Always kill the subprocess to avoid orphans.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass

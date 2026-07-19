import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from models.schemas import AgentSpawn, AgentNudge, AgentNudgeReply, GrantApprove, GrantDeny
from services.ostk import ostk, OstkError
from services.agentfile_parser import get_agent_config
import services.agent_memory as agent_memory_svc
from services import chat_ack_bot
from services import recent_deletes
from services import agent_chat_responder
from services import teams as _teams_svc
from services.tracing import trace_event
from services.event_bus import (
    bus as _event_bus,
    AGENT_DELTA,
    AGENT_SWEEP,
    AGENT_SPAWNED,
    AGENT_COMPLETED,
)
from services.grants_events import GrantsEventBus
import services.locks_events as _locks_events_mod
from services.youros_paths import youros_home

try:
    from services import time_primitive as _time_primitive
except ImportError:
    _time_primitive = None

_grants_bus: GrantsEventBus = GrantsEventBus()

logger = logging.getLogger(__name__)


# The single module-level terminal set (→2615). This used to be defined
# twice; the second copy (without "stalled") shadowed this one, so
# "stalled" was never terminal at runtime. That behavior is now the
# deliberate decision: "stalled" is NOT terminal. It is set only by
# lib/agent_reaper.detect_stalled_agents, which fires while the agent's
# PID is still ALIVE (transcript/step just flatlined), and stalled agents
# can recover — /heartbeat accepts their pings (see _HEARTBEAT_TERMINAL),
# /register lets them come back as running, and the kernel-fleet merge in
# the snapshot loop can flip them back to "running". A terminal flip here
# would wrongly release the agent's needles (→2039) and unlock its
# worktree (→2612) while the process may still be working.
_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "cancelled", "terminated_stale",
    "killed", "stopped", "abandoned", "completed_timeout",
})


def _fire_delta(name: str, status: str) -> None:
    """Schedule a delta publish on the consolidated event bus without blocking the caller.

    →2946: publishes AGENT_DELTA ("agent.delta") on services/event_bus.bus.
    Terminal transitions additionally publish AGENT_COMPLETED so SSE
    consumers of GET /api/events see completions without parsing deltas.
    """
    payload: dict = {"name": name, "status": status}
    terminal = status in _TERMINAL_STATUSES
    if terminal:
        meta = agent_metadata.get(name) or {}
        payload["terminal"] = True
        _plf = globals().get("_plain_language_feedback")
        payload["feedback"] = _plf(name, meta) if _plf else ""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (startup / test context)
    loop.create_task(_event_bus.publish(AGENT_DELTA, payload))
    if terminal:
        loop.create_task(
            _event_bus.publish(AGENT_COMPLETED, {"name": name, "status": status})
        )


def _fire_event(event_type: str, payload: dict) -> None:
    """Schedule a publish of any event type on the consolidated bus. Non-blocking."""
    try:
        asyncio.get_running_loop().create_task(
            _event_bus.publish(event_type, payload)
        )
    except RuntimeError:
        pass  # no running loop (startup / test context)


# →2627: strong references to in-flight unlock tasks. Untracked
# fire-and-forget tasks can be garbage-collected mid-flight, and tests
# that drive a terminal status flip need a handle to await so the task
# never outlives the test's event loop (loop close hangs otherwise).
_unlock_worktree_tasks: set = set()

# →2640 fix 4: strong references to in-flight startup-deadline watchdog tasks.
# Same rationale as _unlock_worktree_tasks: asyncio GC drops unreferenced tasks.
_startup_watchdog_tasks: set = set()

# Seconds after spawn before the watchdog kills a process with a 0-byte transcript.
STARTUP_DEADLINE_SECONDS = 45
# Grace window: watchdog does nothing while elapsed < this (agent may still be
# initializing the Claude Code CLI and has not written any output yet).
STARTUP_GRACE_SECONDS = 30


def _fire_unlock_worktree(name: str, meta: dict) -> None:
    """→2612: unlock the agent's git worktree on a terminal transition.

    Worktrees are created with ``git worktree add --lock``
    (services/spawn_isolation.create_worktree) and nothing unlocked them
    when the agent finished, so parked worktrees piled up locked forever
    and the cleanup reaper (→2608 guard) rightly refused all of them.

    Called from _set_agent_status when an agent transitions INTO a
    terminal status. Fire-and-forget: scheduled on the running loop when
    there is one, run inline otherwise (the idle sweep runs in a
    to_thread worker; unit tests call _set_agent_status directly). Any
    failure is logged and must never break the status flip.
    """
    try:
        wt_path = (meta or {}).get("worktree_path")
        if not wt_path:
            return
        from config import PROJECT_ROOT as _ul_root
        from services import spawn_isolation as _spawn_iso
        coro = _spawn_iso.unlock_worktree(
            project_root=str(_ul_root), wt_path=str(wt_path),
        )
        try:
            _task = asyncio.get_running_loop().create_task(coro)
            _unlock_worktree_tasks.add(_task)
            _task.add_done_callback(_unlock_worktree_tasks.discard)
        except RuntimeError:
            # No running loop. Run inline — `git worktree unlock` is a
            # fast metadata-only operation.
            asyncio.run(coro)
    except Exception as exc:
        logger.warning(
            "agent.worktree_unlock_failed name=%s err=%s", name, exc,
        )


# Background snapshot cache (→1219): the snapshotter loop writes here every 500 ms;
# list_agents reads directly from it so every GET /agents completes in <10 ms.
_cached_snapshot: dict = {"agents": [], "computed_at": None, "daemon_running": False}
_snapshot_lock: asyncio.Lock = asyncio.Lock()
# Single-flights the cold-cache snapshot compute so a polling storm can't
# stampede _compute_agents_snapshot_async() and wedge the loop (→1687/→1738).
_snapshot_compute_lock: asyncio.Lock = asyncio.Lock()

# →2224: hard timeout for each scan cycle so a wedged scan can't block the loop forever.
_SCAN_TIMEOUT_SECONDS: float = 5.0
# →2225: how many agents were processed before the last timeout fired (used in log).
_scan_agents_processed: int = 0
# →2226: True while a scan is in flight; next cycle skips rather than queuing behind.
_snapshot_scan_active: bool = False

# Merge-debt cache (→1555): the merge_debt_tick_loop refreshes every 60 s.
_cached_merge_debt: dict = {"count": 0, "items": []}
_merge_debt_lock: asyncio.Lock = asyncio.Lock()


def _set_agent_status(name: str, new_status: str, **extra_fields) -> None:
    """Update agent_metadata[name]['status'] and fire a WS delta. No-op if absent."""
    meta = agent_metadata.get(name)
    if meta is None:
        return
    meta["status"] = new_status
    for k, v in extra_fields.items():
        meta[k] = v
    # →2953: a /complete that arrived while the spawn PID was still alive
    # parked its summary as pending_summary (see mark_agent_complete's
    # deferral branch). Attach it now that the row actually completes.
    # The agent's own parked words beat any synthesized sweep placeholder
    # passed via extra_fields; a newer direct /complete clears the parked
    # copy before this runs, so the newest summary always wins.
    if new_status == "completed":
        _pending_summary = meta.pop("pending_summary", None)
        meta.pop("pending_summary_at", None)
        if isinstance(_pending_summary, str) and _pending_summary.strip():
            meta["summary"] = _pending_summary
    _fire_delta(name, new_status)
    if new_status in _TERMINAL_STATUSES:
        # Reset any in_progress needle(s) this agent held back to open, so a task
        # never sticks at in_progress after its agent dies without closing it
        # (→2039). Skips needles still claimed by another live agent.
        nid = meta.get("needle_id")
        if nid:
            _fire_release_needle_if_orphaned(nid)
        for _extra_nid in meta.get("needle_ids") or []:
            if _extra_nid:
                _fire_release_needle_if_orphaned(_extra_nid)
        # →2612: the agent is finished — unlock its worktree so the
        # cleanup reaper can triage it (absorbed → removed, unique →
        # parked) instead of refusing it forever as locked.
        _fire_unlock_worktree(name, meta)


class AgentMemorySave(BaseModel):
    key: str
    value: str


class AgentComplete(BaseModel):
    summary: Optional[str] = None


class AgentCancel(BaseModel):
    reason: Optional[str] = "user cancelled"


class AgentHeartbeat(BaseModel):
    step: Optional[str] = None


class AgentHandoff(BaseModel):
    summary: str


class AgentArrive(BaseModel):
    milestone: Optional[str] = None


class AgentNote(BaseModel):
    content: str


router = APIRouter(tags=["agents"])

# In-memory registry of active agent processes
active_agents: dict[str, object] = {}

# Spawn metadata (timestamp, budget, model) for API-spawned agents
agent_metadata: dict[str, dict] = {}

# Alias map: caller-supplied name -> canonical name in agent_metadata.
# Populated when a claude-code subagent's self-register call is merged into
# the row a PreToolUse hook already created a few seconds earlier. Every
# agent lookup (heartbeat / status / complete / nudge / list membership)
# runs through ``_resolve_agent_name`` so the alias is transparent to the
# caller. Purely in-memory: a backend restart clears the map, which is
# fine since the hook-preregister row outlives any in-flight subagent
# and fresh spawns re-establish their own aliases.
agent_aliases: dict[str, str] = {}


def _resolve_agent_name(name: str) -> str:
    """Return the canonical ``agent_metadata`` key for ``name``.

    If ``name`` is an alias created by the merge-with-hook-preregister
    path in ``/agents/register``, return the target row's name. Otherwise
    return ``name`` unchanged. Safe to call with any string: never
    raises. Lookups remain O(1).
    """
    if not name:
        return name
    target = agent_aliases.get(name)
    if target and target in agent_metadata:
        return target
    return name


# Time window for matching a subagent self-register to a recent
# hook-preregister row. The hook fires at PreToolUse (subprocess boot)
# and the subagent's first tool call runs within seconds to a minute.
# 120 seconds gives comfortable headroom for slow Sonnet cold starts
# without accidentally merging into an unrelated prior agent.
_HOOK_PREREGISTER_MERGE_WINDOW_SECONDS = 120


# Stop tokens dropped before similarity comparison so two agents that
# both happen to mention "the", "for", or "a" do not count as matching.
# Kept deliberately small. The real signal is domain words (the verb,
# the slug, the topic) not English filler.
_HOOK_PREREGISTER_MERGE_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does",
    "for", "from", "get", "has", "have", "how", "if", "in", "into",
    "is", "it", "its", "not", "of", "on", "or", "so", "that", "the",
    "then", "there", "this", "to", "was", "what", "when", "where",
    "why", "will", "with", "agent", "subagent", "claude", "code",
    "claude-code", "gemini", "gemini-cli", "run", "running", "task",
})


def _hook_preregister_tokens(text) -> set:
    """Lowercase the string, split on non-word chars, drop stopwords
    and one-char tokens. Returns the set of content tokens used by the
    merge matcher. Safe on None and empty input.
    """
    if not text:
        return set()
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return set()
    import re as _re
    tokens = _re.split(r"[^a-z0-9]+", text.lower())
    return {
        t for t in tokens
        if t and len(t) > 1 and t not in _HOOK_PREREGISTER_MERGE_STOPWORDS
    }


def _hook_preregister_matches_register(
    hook_name: str,
    hook_meta: dict,
    body_name: str,
    body_description: str,
    body_prompt: str,
) -> bool:
    """Return True when the hook-preregister row (``hook_name`` /
    ``hook_meta``) is textually close enough to the incoming /register
    call that merging is safe.

    The time window alone is not sufficient. Two subagents spawned
    within 120 seconds of each other produce two hook rows and two
    self-register calls. Without a name/description check the second
    self-register silently lands on the first hook row, making the
    second subagent invisible under its chosen name. This was the
    demo-blocking bug: a "diagnose-probe-agent-<uuid>" self-register
    got absorbed into an unrelated "fix-testagents-ordering-pollution"
    hook row.

    A match is any one of:

    * exact name equality (``hook_name == body_name``)
    * one name is a substring of the other (catches "-v2" / "-retry"
      suffix divergence the hook cannot predict)
    * two or more content tokens shared between either pair of
      (hook name, hook description, hook prompt) and
      (body name, body description, body prompt)
    * the hook's slug-of-description name appears inside the body's
      description or prompt (the common case: hook names the row
      "fix-foo-bar-baz", the subagent's prompt mentions "fix foo")

    Stopwords and single-character tokens are dropped before the
    token overlap count so "the", "and", "a" do not create spurious
    matches.
    """
    if not isinstance(hook_meta, dict):
        return False
    hook_name = hook_name or ""
    body_name = body_name or ""
    hook_description = str(hook_meta.get("description") or "")
    hook_prompt = str(hook_meta.get("prompt") or "")
    body_description = body_description or ""
    body_prompt = body_prompt or ""

    # 1. Exact or substring name match.
    if hook_name and body_name:
        if hook_name == body_name:
            return True
        h_lower = hook_name.lower()
        b_lower = body_name.lower()
        if h_lower in b_lower or b_lower in h_lower:
            return True

    # 2. Hook's slug-of-description name appears inside body text.
    # Hook names are slugged descriptions, so finding that slug in the
    # subagent's own description or prompt is a strong signal.
    h_slug = hook_name.lower().replace("_", "-") if hook_name else ""
    if h_slug and len(h_slug) >= 6:
        combined_body = (body_description + " " + body_prompt).lower()
        # Replace spaces with dashes so "fix foo bar" matches slug
        # "fix-foo-bar" and vice versa.
        combined_body_dashed = combined_body.replace(" ", "-")
        if h_slug in combined_body_dashed:
            return True

    # 3. Token overlap. Build the content-token sets for each side
    # across name + description + prompt, then require at least 2
    # shared tokens. Two is enough to weed out coincidental single
    # word overlaps ("test" or "fix") while staying permissive for
    # hand-written subagents whose internal name diverges from the
    # Task description slug.
    hook_tokens = (
        _hook_preregister_tokens(hook_name)
        | _hook_preregister_tokens(hook_description)
        | _hook_preregister_tokens(hook_prompt)
    )
    body_tokens = (
        _hook_preregister_tokens(body_name)
        | _hook_preregister_tokens(body_description)
        | _hook_preregister_tokens(body_prompt)
    )
    if len(hook_tokens & body_tokens) >= 2:
        return True

    return False


def _find_recent_hook_preregister(
    now_iso: str,
    *,
    body_name: str = "",
    body_description: str = "",
    body_prompt: str = "",
):
    """Find a recently spawned running claude-code row that was created
    by the PreToolUse hook and is still awaiting its subagent's own
    self-register.

    Returns ``(name, meta)`` for the best match, or ``None`` if no
    candidate is within the merge window AND textually similar enough
    to the incoming register call. A candidate must be:

    * explicitly flagged ``hook_preregister: True`` by the hook
    * ``source == 'claude-code'``
    * ``status == 'running'``
    * spawned within ``_HOOK_PREREGISTER_MERGE_WINDOW_SECONDS`` of now
    * not already merged into by another alias (so two back-to-back
      subagent spawns cannot both claim the same hook row)
    * textually close to the register body (name / description /
      prompt overlap). See ``_hook_preregister_matches_register``.

    Requiring BOTH the explicit flag AND a textual match prevents the
    silent-absorb bug where a fresh subagent's self-register got
    merged into an unrelated pre-existing hook row just because it
    arrived within the time window. Returns the newest matching row
    so a fresh spawn always wins over a stale one.
    """
    try:
        now = datetime.fromisoformat(now_iso)
    except Exception:
        return None
    already_merged_targets = set(agent_aliases.values())
    best_name = None
    best_meta = None
    best_spawned = None
    for name, meta in agent_metadata.items():
        if not isinstance(meta, dict):
            continue
        if not meta.get("hook_preregister"):
            continue
        if meta.get("source") != "claude-code":
            continue
        if meta.get("status") != "running":
            continue
        if name in already_merged_targets:
            continue
        spawned_raw = meta.get("spawned_at")
        if not spawned_raw:
            continue
        try:
            spawned = datetime.fromisoformat(spawned_raw)
        except Exception:
            continue
        delta = (now - spawned).total_seconds()
        if delta < 0 or delta > _HOOK_PREREGISTER_MERGE_WINDOW_SECONDS:
            continue
        # Textual similarity guard. Without this the matcher picks the
        # newest in-window hook row regardless of whether it has
        # anything to do with the incoming self-register, producing the
        # "merged into unrelated row" bug.
        if not _hook_preregister_matches_register(
            name, meta, body_name, body_description, body_prompt,
        ):
            continue
        if best_spawned is None or spawned > best_spawned:
            best_name = name
            best_meta = meta
            best_spawned = spawned
    if best_name is None:
        return None
    return best_name, best_meta

# Stdin writers for API-spawned claude-code subagents. Keyed by agent name.
# When /agents/spawn keeps stdin open (i.e. does not close it after the
# initial prompt), the asyncio.StreamWriter is stored here so /nudge can
# push follow-up messages directly instead of waiting for the file-based
# mailbox poll. Entries are removed when the agent exits, is killed, or
# completes.  Non-API-registered agents (those that only called /register
# over HTTP) never have an entry here, so they always fall back to file_only.
_agent_stdin_writers: dict[str, asyncio.StreamWriter] = {}

# In-memory log of nudges sent during this session (visible in UI)
nudge_history: dict[str, list[dict]] = {}

# In-memory log of replies agents have posted back during this session.
# Populated via ``POST /api/agents/{name}/reply`` and surfaced alongside
# the user's own nudges by ``GET /api/agents/{name}/nudges``.
nudge_replies: dict[str, list[dict]] = {}

# Long-poll wake-up events per agent name. When an agent calls
# ``GET /api/agents/{name}/nudges?wait=<seconds>`` with nothing new yet,
# the handler blocks on this event. POST /nudge and POST /reply set the
# event so the long poll returns within a millisecond of the new message
# landing, instead of waiting out the full poll interval. Each event is
# created lazily on first use and cleared after a wake-up so the next
# cycle starts fresh.
_nudge_waiters: dict[str, asyncio.Event] = {}

# Count of long-poll requests currently parked per agent name. Used by
# POST /nudge and POST /reply to decide the truthful delivery message:
# if a waiter is parked, delivery will be sub-second; if not, the agent
# will pick up the nudge on its NEXT mailbox check, which can be as long
# as MAILBOX_SLOW_POLL_SECONDS away if it backed off during a quiet run.
# This is how the UI avoids the "should see this within a couple of
# seconds" lie when the agent is deep in a tool chain and not polling.
_nudge_parked_count: dict[str, int] = {}

# Absolute ceiling for a single long-poll wait. Keeps HTTP connections
# from piling up during quiet periods and bounds the worst case if a
# client forgets to disconnect. Callers asking for a larger value are
# silently capped at this number.
NUDGE_LONG_POLL_MAX_SECONDS = 30


def _get_nudge_waiter(name: str) -> asyncio.Event:
    """Return (creating if needed) the wake-up event for ``name``.

    The event is the long-poll signal. Waiters block on it. POST /nudge
    and POST /reply set it so every pending long-poller for this agent
    returns immediately with the new data.
    """
    event = _nudge_waiters.get(name)
    if event is None:
        event = asyncio.Event()
        _nudge_waiters[name] = event
    return event


def _wake_nudge_waiters(name: str) -> None:
    """Signal every long-poller waiting on ``name`` that new data is here.

    Called from POST /nudge and POST /reply. Each waiter consumes the
    event and recomputes its own list, so one set() is enough: waiters
    clear the event after draining it so the next call starts fresh.
    """
    event = _nudge_waiters.get(name)
    if event is not None:
        event.set()


def _is_long_poll_parked(name: str) -> bool:
    """Return True if at least one /nudges long-poller is currently parked.

    Used by the nudge and reply handlers to decide how optimistic the
    delivery message can be. A parked waiter means wake-up latency is
    measured in tens of milliseconds. No waiter means the agent is
    either between polls or not polling at all, and the truthful message
    must say so instead of promising a couple of seconds.
    """
    return _nudge_parked_count.get(name, 0) > 0


from config import AGENTS_DIR, OSTK_DIR
from services.kernel_fleet import read_kernel_fleet as _read_kernel_fleet

# Persistent file tracking agent state across server restarts
AGENT_STATE_PATH = OSTK_DIR / "agent_state.json"
DELETED_AGENTS_PATH = OSTK_DIR / "deleted_agents.json"

def _load_deleted_agents() -> set[str]:
    """Return the set of agent names the user has deleted from the UI.

    Audit log entries are immutable, so we track deletions separately
    and filter them out of list responses.
    """
    if not DELETED_AGENTS_PATH.exists():
        return set()
    try:
        data = json.loads(DELETED_AGENTS_PATH.read_text())
        if isinstance(data, list):
            return set(data)
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def _save_deleted_agents(names: set[str]) -> None:
    """Persist the deleted agent names set."""
    from services.atomic_io import atomic_write_json
    try:
        atomic_write_json(DELETED_AGENTS_PATH, sorted(names))
    except OSError:
        pass


def _cleanup_dead_numbered_copies(base_name: str) -> list:
    """→2956 (4): when an agent reclaims its base row, remove leftover DEAD
    numbered-copy rows the old 409 register path forced it to mint
    (``name-2``, ``name-retry-1``, ``name-r2``). Copies still running are
    never touched — a live agent that happens to carry a suffixed name is
    not a leftover. Removed rows are tombstoned in deleted_agents.json so
    their audit-log entries stay hidden from the list endpoint too (they
    can always self-reclaim by re-registering, like any deleted name).
    """
    import re as _re
    pattern = _re.compile(
        rf"^{_re.escape(base_name)}-(?:retry-[A-Za-z0-9_]+|r\d+|\d+)$"
    )
    removed: list = []
    for other_name, other_meta in list(agent_metadata.items()):
        if other_name == base_name or not pattern.match(other_name):
            continue
        if (other_meta or {}).get("status") not in _TERMINAL_STATUSES:
            continue
        agent_metadata.pop(other_name, None)
        removed.append(other_name)
    if removed:
        _tombstones = _load_deleted_agents()
        _tombstones.update(removed)
        _save_deleted_agents(_tombstones)
        logger.info(
            "register.reclaim_cleaned_copies base=%s removed=%s",
            base_name, ",".join(removed),
        )
    return removed


_PRUNE_TTL_DAYS = 7
_last_prune_time: float = -999999.0
_PRUNE_INTERVAL_SECONDS = 300


def _prune_stale_completed_agents() -> int:
    """Soft-delete completed agents older than _PRUNE_TTL_DAYS days.

    Runs at most once per _PRUNE_INTERVAL_SECONDS to avoid slowing
    down every GET /agents poll.
    """
    global _last_prune_time
    import time as _time
    now_mono = _time.monotonic()
    if now_mono - _last_prune_time < _PRUNE_INTERVAL_SECONDS:
        return 0
    _last_prune_time = now_mono

    cutoff = datetime.now(timezone.utc) - timedelta(days=_PRUNE_TTL_DAYS)
    deleted = _load_deleted_agents()
    pruned = 0
    for name, meta in list(agent_metadata.items()):
        if meta.get("status") not in _TERMINAL_STATUSES:
            continue
        ts_str = meta.get("completed_at") or meta.get("spawned_at") or ""
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            deleted.add(name)
            pruned += 1
    if pruned:
        _save_deleted_agents(deleted)
        logger.info("prune.stale_agents count=%d ttl_days=%d", pruned, _PRUNE_TTL_DAYS)
    return pruned


_last_reaped_prune_time: float = -999999.0


def _prune_reaped_worktree_agents() -> int:
    """Soft-delete agents whose worktree dir is gone and PID (if any) is dead.

    Rule quoted from feedback_ghost_reaper_live_pid_check.md:
    "add os.kill(int(pid), 0) check before heartbeat staleness check. If
    ProcessLookupError → dead, fall through. If success or PermissionError/OSError
    → alive, skip."

    This function applies the same principle at the list-endpoint level: an agent
    with isolation=worktree whose directory has been reaped and whose process no
    longer exists is noise. We mark it terminal and add it to deleted_names so it
    no longer appears in GET /api/agents.

    The agent_state.json record is preserved (audit trail). Only the display filter
    (deleted_agents.json) is updated.
    """
    global _last_reaped_prune_time
    import time as _time
    now_mono = _time.monotonic()
    if now_mono - _last_reaped_prune_time < _PRUNE_INTERVAL_SECONDS:
        return 0
    _last_reaped_prune_time = now_mono

    deleted = _load_deleted_agents()
    pruned = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for name, meta in list(agent_metadata.items()):
        if name in deleted:
            continue
        worktree_path = meta.get("worktree_path")
        if not worktree_path:
            continue
        # Worktree dir still present — agent may still be active.
        if Path(worktree_path).exists():
            continue
        # PID alive — agent is running even though the worktree dir is absent.
        pid = meta.get("pid")
        if pid:
            try:
                import os as _os
                _os.kill(int(pid), 0)
                continue  # alive: PermissionError or success both mean the PID exists
            except (ProcessLookupError, OSError):
                pass  # dead — fall through to prune
        elif meta.get("status") not in _TERMINAL_STATUSES:
            # →2956: no pid on record and the row still says running — the
            # saa-2953 shape. A reaped worktree dir alone must never delete
            # a live agent: registration-only agents work in isolated
            # workspaces whose dir cleanup can remove while the process
            # keeps working elsewhere (and their transcript byte counter
            # reads 0, so absence of output is not evidence either). Only
            # when the row is ALSO silent on the heartbeat channel, holds
            # no live proc handle, and shows no fresh transcript growth may
            # it be hidden.
            _prune_now = datetime.now(timezone.utc)
            _last_seen = _last_seen_dt(meta)
            if _last_seen is not None and (
                (_prune_now - _last_seen).total_seconds()
                <= STALE_AGENT_TIMEOUT_SECONDS
            ):
                continue  # fresh heartbeat: alive
            if _proc_handle_is_alive(name):
                continue
            if _transcript_recently_active(name, _prune_now):
                continue
        # Both worktree and PID are gone: mark terminal if not already, then hide.
        if meta.get("status") not in _TERMINAL_STATUSES:
            _set_agent_status(
                name, "terminated_stale",
                terminated_at=now_iso,
                terminated_reason="reaped: worktree dir gone and PID dead",
                flagged_by="stale_sweep",
            )
        deleted.add(name)
        pruned += 1
    if pruned:
        _save_deleted_agents(deleted)
        logger.info("prune.reaped_worktree_agents count=%d", pruned)
    return pruned


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
# Agents that were spawned within this window are treated as live even when
# their subprocess pid is already dead (e.g. empty-transcript fast exit).
# This covers the gap between /spawn returning 200 and the first heartbeat
# arriving (→1950/→2020). Stale rows are unaffected because their spawned_at
# timestamps are far outside this window.
SPAWN_GRACE_PERIOD_SECONDS = 30

# Shorter stale-sweep threshold for Claude Code Agent-tool subagents
# (source="claude-code"). These agents do NOT run pytest/tsc the way
# externally-registered agents do. They are one-shot spawns from the
# parent session that finish and exit. The PreToolUse hook
# register-agent.sh spawns a detached heartbeat loop that keeps pinging
# /heartbeat for up to 45 minutes even after the subprocess is gone,
# which masks the normal 900s stale sweep and leaves rows showing as
# running for 14+ minutes past actual completion. 480 seconds (8 min)
# is well past the ~4 minute normal subagent runtime but short enough
# that a stale row clears before the user gets annoyed. The row is
# demoted to "completed_timeout" (not "terminated_stale") because the
# normal reason these rows hang is that the agent finished cleanly but
# never called /complete, not that it crashed.
STALE_CLAUDE_CODE_SUBAGENT_SECONDS = 480

# Auto-complete threshold for Agent-tool subagents (source="claude-code").
# If no heartbeat for this many seconds AND transcript has not grown in the
# last STALE_AGENT_TRANSCRIPT_GRACE_SECONDS AND the process is not alive,
# we flip the row to "completed" with a note that the agent exited cleanly
# without calling /complete. This is gentler than terminated_stale (which
# fires at 900s and implies an unclean exit). 300s = 5 minutes.
STALE_AGENT_AUTOCOMPLETE_SECONDS = 300

# Grace window for transcript growth during auto-complete evaluation.
# If the transcript file mtime is within this many seconds, the agent is
# still writing output and must not be auto-completed yet.
STALE_AGENT_TRANSCRIPT_GRACE_SECONDS = 120

# →2896: quiet threshold for flagging an agent whose death is UNPROVEN
# (no recorded pid, so no ground-truth process check is possible). The
# longest observed normal quiet stretch is ~10 minutes: a full api pytest
# run inside ONE tool call, during which the agent can neither heartbeat
# nor append to its log (saa-2892 2026-07-13, saa-2894 and saa-2880
# 2026-07-14 were all false-flagged exactly this way). 1200s = 2x that
# observed maximum, and matches the existing "no heartbeat for over 20
# minutes" legacy list-endpoint sweep, so no new cadence is introduced.
# Rows with a stored pid confirmed dead keep the fast flip paths; this
# threshold only gates inference-based flips.
IDLE_WATCHDOG_QUIET_SECONDS = 1200

# →2896: how many times a sweep-flagged row may be revived by a real
# heartbeat before the 409 becomes final. Bounds status flapping if some
# stray process keeps posting step-carrying heartbeats for a dead agent.
MAX_HEARTBEAT_REVIVALS = 3

# Response-time staleness cutoff for GET /api/agents (→1151, →1212).
# Non-running rows whose last_seen (last_heartbeat_at or spawned_at) is
# older than this are dropped from the serialized response. Running rows
# are always kept regardless of last_seen so a transiently-delayed
# heartbeat never hides an active agent.
# 24 hours so the Recent tab shows today's full session history. The
# expensive transcript enrichment pass already has its own 24h cutoff
# (_enrich_cutoff) that skips I/O for old stopped rows, so widening
# this window does not re-introduce the perf concern from →1151.
_RESPONSE_STALE_SECONDS = 86400


# How often (seconds) to write a progress marker to the transcript file while
# a subagent process is alive but has not produced any stdout yet.  The
# subprocess (claude-code) uses full libc buffering on non-TTY pipes, so its
# output can stay in user-space buffers for the entire run.  The periodic
# marker ensures transcript_bytes grows on every tick so the Agents page shows
# a live agent instead of a stalled one.  Override in tests via monkeypatch.
#
# 25 s (not 30 s) so this flush interval does not coincide with the 60 s
# resolve/candidates cache TTLs — if they matched, every flush would
# simultaneously bust all three caches, causing a synchronized cold-rebuild
# spike on the next /api/agents request (→1192).
_TRANSCRIPT_FLUSH_INTERVAL: float = 25.0

# If a subprocess produces NO stdout for this many seconds while still
# running, we treat it as wedged and kill it. Without this guard, agents
# can register, heartbeat forever, and never stream model output, which
# looks indistinguishable from a healthy long-running agent. Override in
# tests via monkeypatch to keep suites fast.
_STDOUT_SILENCE_LIMIT_SECONDS: float = 300.0

# Tighter startup limit: if a subprocess has never produced any stdout,
# kill it much sooner. A subprocess hanging before its first byte is
# almost always wedged at API startup (rate-limit, auth delay, TLS
# stall) rather than doing legitimate thinking. 45s is long enough for
# slow cold starts but short enough that users aren't stuck staring at
# a spinner for 5 minutes before a clean retry.
_STDOUT_FIRST_BYTE_LIMIT_SECONDS: float = 45.0

# Mid-tier limit: subprocess emitted bytes (hook events arrived) but the
# model never produced any text/tool output. Hooks fire within 1-2s on a
# healthy claude --print run; if 120s pass without a model event the API
# is hung (rate-limit, TLS stall after auth, model timeout). This is
# intentionally longer than _STDOUT_FIRST_BYTE_LIMIT_SECONDS because the
# hook events prove the subprocess is alive — we just need more patience
# for the API round-trip itself.
_STDOUT_API_HANG_LIMIT_SECONDS: float = 120.0

# Adaptive poll constants: agents start polling fast right after a
# nudge (when Tori is most likely to iterate) and back off toward the
# slow cap during quiet stretches. This gives sub-15-second latency
# when it matters while keeping HTTP churn low during long runs.
#
# Adaptive schedule: start at MAILBOX_FAST_POLL_SECONDS; on each cycle
# with no new nudge, double the interval toward MAILBOX_SLOW_POLL_SECONDS;
# on receiving any nudge, reset back to MAILBOX_FAST_POLL_SECONDS.
#
# Both values are surfaced in the mailbox instruction block and in the
# user-facing delivery status line so UI copy and agent contract never
# drift. Tests assert the fast cap stays <= 15 and the slow cap <= 120.
MAILBOX_FAST_POLL_SECONDS = 10
# Restored to the documented 60s target (was briefly bumped to 300 during the
# →2165 wedge work, which violated this block's own "slow cap <= 120" contract
# and the needle-238 responsiveness guard: an idle agent must still notice a
# nudge within ~a minute). The real wedge mitigations (threaded reconciliation,
# transcript cap, sweep-pass lock) are unaffected by this value.
MAILBOX_SLOW_POLL_SECONDS = 60

# Legacy alias kept so existing callers and tests that import
# MAILBOX_CHECK_INTERVAL_SECONDS keep working without changes.
MAILBOX_CHECK_INTERVAL_SECONDS = MAILBOX_SLOW_POLL_SECONDS


def build_spec_claim_block(spec_id: str, agent_name: str) -> str:
    """Return the spec claim preamble block for terminal agent sessions.

    When a spawn is linked to a spec (spec_id set), this block is appended
    to the agent's prompt telling it to POST /api/specs/{spec_id}/claim once
    before starting work. That call flips the Specs page from Ready to
    Building immediately, covering terminal Claude/Gemini sessions launched
    outside the wrapper CLI. (→1425)

    Returns empty string when spec_id is falsy so call sites can guard with
    ``if block: ...`` or just always append (appending "" is a no-op).
    """
    if not spec_id:
        return ""
    return (
        "## Spec claim (run before starting implementation)\n\n"
        f"You are implementing a spec at: {spec_id}\n\n"
        "Before writing any code, run this command exactly once:\n\n"
        "```\n"
        f"curl -sSk -X POST https://127.0.0.1:8000/api/specs/{spec_id}/claim"
        " -H 'Content-Type: application/json'"
        f" -d '{{\"source\":\"agent\",\"agent\":\"{agent_name}\"}}'\n"
        "```\n\n"
        "This registers you as the active builder so the Specs page shows "
        "Building instead of Ready. Call it once only."
    )


def agent_mailbox_instruction_short(agent_name: str, model: str = "sonnet") -> str:
    """Return a compact mailbox block for fast-spawning agents.

    The long ``agent_mailbox_instruction`` block is ~4 KB of instructions
    that the model has to read before it does any work, which adds real
    first-byte latency on short demo spawns. This short variant keeps the
    full protocol contract (register, heartbeat, nudges, reply, complete)
    but trims the prose to fit well under 800 characters. The full block
    stays available for callers that still need the verbose form.

    The short block intentionally keeps every curl target the tests rely
    on: register, heartbeat, /nudges, /reply, /complete. It also embeds
    the agent name literally so curl examples copy paste without edits.
    """
    slow = MAILBOX_SLOW_POLL_SECONDS
    base = "https://127.0.0.1:8000/api/agents"
    # Terse mailbox protocol. Every curl target the tests lock in is
    # preserved. Prose is stripped to the bare contract so the model
    # reads it in one glance. The user is referenced so the "human on the
    # other end" cue survives.
    ct = "-H 'Content-Type: application/json'"
    _bootstrap = (
        'First: ToolSearch(query="select:mcp__ostk__bash", max_results=1). '
        "Bash is blocked and the shell tool is deferred; skipping this "
        "causes throwaway helper-agent spawns.\n"
    )
    register_body = (
        '{"name":"' + agent_name + '","model":"' + model + '","status":"running",'
        '"task":"<one line>","source":"claude-code"}'
    )
    heartbeat_body = '{"step":"<now>"}'
    reply_body = '{"message":"<reply>"}'
    complete_body = '{"summary":"<one line>"}'
    return (
        _bootstrap
        + "## Mailbox (mandatory). Reply fast, the user is on the other end.\n"
        f"Register: curl -sSk -X POST {base}/register {ct} "
        f"-d '{register_body}'\n"
        f"Heartbeat every {slow} seconds: "
        f"curl --connect-timeout 3 -m 5 -sSk -X POST {base}/{agent_name}/heartbeat "
        f"{ct} -d '{heartbeat_body}'\n"
        f"Between every tool call, poll /nudges (long poll 30s): "
        f"curl --connect-timeout 3 -m 35 -sSk "
        f'"{base}/{agent_name}/nudges?wait=30&since=<latest_ts>"\n'
        "If a nudge is waiting, pause at the next safe tool boundary. "
        "Gather whatever context you need (run a quick check, grep a "
        "file) then write a real, warm 2-5 sentence answer. Do NOT send a "
        "template ack ('On it', 'Acknowledged', 'Still working on this'). "
        "Be honest and specific. Do not invent a time estimate. "
        "POST /reply before resuming. "
        "Never wait for a response. Minimum cadence: check /nudges at "
        "least every 30 seconds of wall-clock work. Before any long tool "
        "call (pytest, tsc, large write) poll first, and poll again the "
        "instant it returns.\n"
        f"/reply after each nudge: "
        f"curl --connect-timeout 3 -m 5 -sSk -X POST {base}/{agent_name}/reply "
        f"{ct} -d '{reply_body}'\n"
        f"/complete when done: "
        f"curl --connect-timeout 3 -m 5 -sSk -X POST {base}/{agent_name}/complete "
        f"{ct} -d '{complete_body}'"
    )


def _demo_mode_active() -> bool:
    """True when the live-demo build rule should be appended to spawn prompts.

    Off by default. Turned on by creating ~/.youros/.demo_mode (the file the
    user toggles before a live demo) or by setting MYOS_DEMO_MODE=1. When on,
    every agent ToriOS spawns is told to deliver a feature that actually works
    in the running app, not just one that passes tests.
    """
    if os.environ.get("MYOS_DEMO_MODE") == "1":
        return True
    try:
        return os.path.exists(str(youros_home() / ".demo_mode"))
    except Exception:
        return False


_DEMO_BUILD_RULE = (
    "\n\n## DEMO MODE: deliver it working in the running app\n\n"
    "You are building during a LIVE DEMO, so \"done\" means the feature "
    "actually works in the running app on screen, not just that the tests "
    "pass. Hold yourself to all of this:\n"
    "- Prefer NO new dependencies, and reach for what the browser or runtime "
    "already has (for example the built-in Web Audio API instead of adding a "
    "library), so the frontend hot-reloads instead of needing an install and "
    "a dev-server restart that can break on stage.\n"
    "- Reuse the events and endpoints that already fire rather than rebuilding "
    "them.\n"
    "- Get your change into the running app, because agent worktrees are not "
    "auto-merged, so merge your work into the served branch before you finish.\n"
    "- Verify the feature actually works in the running app before you report "
    "it done.\n"
)


def _team_mailbox_section(team_id: Optional[str]) -> str:
    """Return the team-shared context block to append to the mailbox instruction.

    Only included when *team_id* is provided (i.e. the agent is part of a
    team). Non-team agents receive an empty string so the existing prompt
    contract is unchanged.
    """
    if not team_id:
        return ""
    team = _teams_svc.get_team(team_id)
    if team is None:
        return ""
    parent_task_id = team.get("parent_task_id", "")
    members = team.get("members", [])
    member_lines = "\n".join(
        f"  - {m['agent_name']} (role: {m['role']})" for m in members
    ) or "  (no members yet)"
    task_ids = team.get("task_ids", [])
    tasks_line = ", ".join(task_ids) if task_ids else "(none yet)"
    return (
        f"\n\n## Team membership (→2147)\n\n"
        f"You are part of team **{team_id}**.\n"
        f"Parent task: **{parent_task_id}** -- this task must be closed "
        f"before the team is considered done.\n"
        f"Shared task list: {tasks_line}\n"
        f"Teammates:\n{member_lines}\n\n"
        f"TeammateIdle rule: do NOT call /complete or exit while the parent "
        f"task ({parent_task_id}) is still open. "
        f"Check: GET /api/teams/{team_id} to see current team state. "
        f"Call GET /api/teams/{team_id}/idle-check to verify the gate."
    )


def agent_mailbox_instruction(
    agent_name: str, model: str = "sonnet", team_id: Optional[str] = None
) -> str:
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

    Heartbeat contract: every agent MUST call POST /heartbeat at least
    once every STALE_AGENT_TIMEOUT_SECONDS (currently 900s, 15 min).
    The stale sweep runs on every GET /api/agents and will mark any
    agent with an older heartbeat as terminated_stale. Long shell
    commands (pytest, tsc, large writes) do not ping the HTTP channel,
    so agents must heartbeat before AND after every long tool call.

    Adaptive poll: agents should start polling at MAILBOX_FAST_POLL_SECONDS
    and double the interval on each empty cycle up to MAILBOX_SLOW_POLL_SECONDS.
    On receiving any nudge, reset to MAILBOX_FAST_POLL_SECONDS. This keeps
    delivery fast when Tori is iterating and drops churn during long quiet runs.

    Signal file: when the user sends a nudge via POST /agents/{name}/nudge
    the backend also touches ~/.youros/nudges/{name}.signal. Agents can stat
    this file each cycle: if mtime is newer than the last check, skip ahead
    and poll /nudges immediately without waiting for the interval to expire.
    The stat call is cheap and works even at the slow poll cadence.

    Sentinels (→2891): the whole block is wrapped in
    ``<!-- mailbox:begin -->`` / ``<!-- mailbox:end -->`` lines so the
    transcript title logic in routers/transcripts.py can excise it
    wholesale. Without them, individual sentences from this block kept
    leaking into transcript titles every time the wording changed.
    Keep the sentinels as the first and last line of the returned string.
    """
    fast = MAILBOX_FAST_POLL_SECONDS
    slow = MAILBOX_SLOW_POLL_SECONDS
    return (
        "<!-- mailbox:begin -->\n"
        "## Bootstrap (do this before ANYTHING else)\n\n"
        "Load the shell tool now, it is deferred until you call ToolSearch:\n"
        "   `ToolSearch(query=\"select:mcp__ostk__bash\", max_results=1)`\n\n"
        "The `Bash` tool is blocked globally. `mcp__ostk__bash` is your only "
        "shell and it is deferred; calling it before ToolSearch loads it fails "
        "with InputValidationError. Skipping this step leaves you with no working "
        "shell, which causes agents to spawn throwaway helper agents "
        "(run-curl-command, execute-bash-command) to run single commands. "
        "Load it first, then proceed.\n\n"
        "---\n\n"
        f"## Agent registration and mailbox (mandatory)\n\n"
        "### Step 0: Register immediately\n"
        "Before doing ANY work, register yourself so the user can see you "
        "in the Agents page:\n"
        f"   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register "
        "-H 'Content-Type: application/json' "
        f"-d '{{\"name\": \"{agent_name}\", \"model\": \"{model}\", \"task\": \"<one line description of your task>\", \"source\": \"claude-code\"}}'`\n\n"
        "### CRITICAL ENV NOTE\n\n"
        "mcp__ostk__bash and mcp__ostk__fs_ops are workspace-sandboxed: "
        "they run relative to the project root only. Paths outside the workspace "
        "(such as ~/.claude, ~/.config, or /dev/null) fail or hang silently "
        "inside these tools. Use native Bash, Read, Edit, or Write for any "
        "paths that live outside the project directory.\n\n"
        f"### Heartbeat (every {slow} seconds, CRITICAL for long tasks)\n\n"
        "The Agents page marks you as stopped if it does not hear from "
        f"you for more than {STALE_AGENT_TIMEOUT_SECONDS} seconds (15 min). "
        "Long shell commands like pytest, tsc, or large file writes do not "
        "count. You MUST actively ping the heartbeat before and after every "
        "long-running tool call:\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/heartbeat "
        "-H 'Content-Type: application/json' "
        "-d '{\"step\": \"<what you are doing now>\"}'`\n\n"
        f"### Mailbox checking (adaptive: {fast}s to {slow}s)\n\n"
        "The user may send you follow up instructions while you work via "
        "the Agents page in yourOS. To pick those up, you MUST do the "
        "following on a regular schedule, alongside your heartbeat:\n\n"
        f"**Adaptive poll schedule**: start your poll interval at {fast} "
        f"seconds. On each cycle with no new nudge, double the interval "
        f"(20s, 40s, ...) up to a cap of {slow} seconds. When you receive "
        f"any nudge, reset the interval back to {fast} seconds. This keeps "
        "delivery fast when the user is replying and saves your turn budget "
        "during long quiet stretches.\n\n"
        f"**Signal file shortcut**: each time the user sends a nudge the "
        f"backend also touches `~/.youros/nudges/{agent_name}.signal`. On "
        "each poll cycle, stat that file first. If its mtime is newer than "
        "your last check, skip ahead and poll /nudges immediately rather "
        "than waiting for the interval to expire. The stat call is a single "
        "syscall and effectively free.\n\n"
        f"**Long-poll (fastest delivery)**: the /nudges endpoint supports "
        f"a `?wait=<seconds>&since=<iso_timestamp>` parameter. When you "
        f"pass a `since` marker and `wait` up to {NUDGE_LONG_POLL_MAX_SECONDS}, "
        f"the server holds the request open and returns the instant the user "
        f"sends a new message, not on the next poll. Recommended pattern: "
        f"`?wait={NUDGE_LONG_POLL_MAX_SECONDS}&since=<latest_ts_you_saw>`. "
        f"Use a curl timeout of {NUDGE_LONG_POLL_MAX_SECONDS + 5} seconds "
        f"(`-m {NUDGE_LONG_POLL_MAX_SECONDS + 5}`) so the long poll has "
        f"room to return. On timeout just loop and reconnect.\n\n"
        f"1. On each cycle, call:\n"
        f"   `curl --connect-timeout 3 -m {NUDGE_LONG_POLL_MAX_SECONDS + 5} -sSk "
        f"\"https://127.0.0.1:8000/api/agents/{agent_name}/nudges?wait={NUDGE_LONG_POLL_MAX_SECONDS}&since=<latest_ts_you_saw>\"`\n"
        "2. Compare the timestamps to the last batch you handled. Any "
        "nudge with a newer timestamp is a NEW message from the user.\n"
        "3. Treat each new nudge as an additional instruction added to "
        "your task. Decide if it changes your plan.\n"
        "4. When a nudge arrives, treat it as a real conversational turn, "
        "not just a task interrupt. Pause at the next safe tool boundary "
        "(the moment your current tool call returns, before starting the "
        "next one). Then:\n"
        "   a. Read the nudge fully.\n"
        "   b. Gather whatever context you need to answer it. If the "
        "question asks about status, grep the relevant file or run a "
        "quick check. If it asks about your progress, look at what you "
        "have done so far. Use tools if needed -- do not guess.\n"
        "   c. Write a real, warm answer: 2-5 sentences, specific, honest. "
        "Do NOT send a template ack ('On it', 'Got it', 'Acknowledged', "
        "'Still working on this'). Do not invent a time estimate. If "
        "you genuinely do not know, say that plainly in a full sentence.\n"
        "   d. Post the answer via /reply BEFORE resuming work:\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/reply"
        " -H 'Content-Type: application/json' -d '{\"message\": \"<your answer>\"}'`\n"
        "   Post another /reply when the work the nudge asked about is done.\n"
        "   Never wait for a response from the user after posting. Resume "
        "your task immediately. They want the answer now, not after "
        "they reply.\n"
        "   Between every tool call, poll /nudges once. If a message is "
        "waiting, pause, gather context if needed, answer fully, THEN "
        "keep working. If you are about to start a long-running tool "
        "call (pytest, tsc, big file write), poll BEFORE starting it "
        "and AGAIN the moment it returns. Minimum cadence: never let "
        "more than 30 seconds of wall-clock work pass without a "
        "/nudges check.\n"
        "5. If a nudge cancels your work, finish the current safe "
        f"step, post a final reply, then POST /api/agents/{agent_name}/complete"
        " and exit.\n"
        "6. CORRECTION nudges (kind=\"correction\" in /nudges, also "
        "prefixed [CORRECTION] in the message) are course changes you "
        "must apply RIGHT NOW. Do not defer to end of task. Stop your "
        "current step at the next safe boundary, change your approach to "
        "honour the correction, and POST /reply within 2 seconds "
        "confirming what you will do differently.\n\n"
        "This loop lives alongside your heartbeats. Do not skip it. "
        "The user is waiting on the other end.\n\n"
        "### Coordination primitives\n\n"
        "Use these primitives to share state, signal progress, and leave a trail.\n\n"
        "**Handoff summary** (write before /complete so a recovery agent can pick up):\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/handoff "
        "-H 'Content-Type: application/json' -d '{\"summary\": \"<one paragraph of context>\"}'`\n"
        "   The handoff must carry receipts and pointers, not just narrative: "
        "the commit hashes you got from git log yourself, the verbatim test "
        "summary line, the files you touched, and the paths to the original "
        "spec or plan documents so the next agent reads them itself.\n\n"
        "**Context pages** (share large data between agents):\n"
        "- Store: `mcp__ostk__context_store(name='<page>', content='...')`\n"
        "- Load: `mcp__ostk__context_load(name='<page>')`\n"
        "- Pin (keep in context): `mcp__ostk__context_pin(name='<page>')`\n\n"
        "**Arrive** (signal a meaningful milestone to the orchestrator):\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/arrive "
        "-H 'Content-Type: application/json' -d '{\"milestone\": \"<what you just finished>\"}'`\n\n"
        "**Note** (record a key decision or finding):\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/note "
        "-H 'Content-Type: application/json' -d '{\"content\": \"<your note>\"}'`\n\n"
        "### Stay in scope (mandatory)\n\n"
        "Do ONLY the task described in your brief. Do NOT build new features, "
        "add product code, or introduce new behavior to make a failing test "
        "pass; if a test expects something that does not exist yet, say so in "
        "your summary instead of building it. Do NOT fix unrelated failures, "
        "refactor, or change code outside your task. If the correct fix is "
        "outside your brief, STOP and surface it (what you found, where, and "
        "why it is out of scope) in your final summary and via /note above. "
        "Surfacing an out-of-scope problem is success; silently expanding "
        "scope is a failure even if your change works.\n\n"
        "### Finishing your work (mandatory)\n\n"
        "When you finish the work you were asked to do, you MUST mark "
        "yourself complete so the Agents page stops showing you as "
        "active. This is not optional. Do this as the very last step, "
        "after any final reply:\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/complete"
        " -H 'Content-Type: application/json' -d '{\"summary\": \"<one line summary + receipts>\"}'`\n"
        "Without this call the agent row stays in the running state "
        "forever even though you exited.\n"
        "Your completion summary must include receipts when they exist: the "
        "commit hash from git log you ran yourself, the verbatim test summary "
        "line (for example '12 passed'), the files you touched, and the path "
        "to any findings or checkpoint file. A claim without a receipt will "
        "be re-verified from scratch, so include the receipt.\n\n"
        "### Pull model (only if your brief explicitly says to chain tasks)\n"
        "Do NOT pull more work by default. ONLY if your spawn brief explicitly "
        "tells you to chain tasks may you pull the next available "
        "task instead of stopping:\n"
        "   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        "https://127.0.0.1:8000/api/tasks/pull`\n"
        "If the response has `claimed: true`, work on that task next. "
        f"If `claimed: false`, no tasks are available. POST /api/agents/{agent_name}/complete and exit.\n\n"
        "Atlassian (Jira and Confluence) is connected through yourOS. "
        "Server endpoints are available at /api/atlassian/jira/issue/{key} "
        "(GET for ticket detail), /api/atlassian/jira/issue/{key}/comment "
        "(POST {body}), /api/atlassian/jira/issue/{key}/transitions (GET) "
        "and /api/atlassian/jira/issue/{key}/transition (POST {transition_id}), "
        "and /api/atlassian/confluence/page/{id} (GET). Use these to read "
        "tickets, comment, or move work without bouncing the user out of yourOS. "
        "Skip if /api/atlassian/status returns connected=false."
        + _team_mailbox_section(team_id)
        + "\n<!-- mailbox:end -->"
    )


def _load_agent_state() -> dict:
    """Load persisted agent state from disk."""
    if AGENT_STATE_PATH.exists():
        try:
            return json.loads(AGENT_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _emit_audit_event(event: str, data: dict) -> None:
    """Append a single audit event to .ostk/audit.jsonl.

    Historically this code called ``ostk._run("os", "audit", "--event",
    ...)`` but the ostk CLI dropped generic emit in favor of typed
    subcommands (check / backfill / remap). The call silently errored
    and every agent.spawned / agent.completed registration was lost,
    which is why the Cost Tracking card showed "used 0 agents" even
    after dozens of registers. Writing directly to the audit log keeps
    the on-disk contract stable regardless of CLI shape.

    Dedup guard: ``agent.completed`` and ``agent.failed`` events are
    deduplicated within a 60-second window per agent name. This is the
    last line of defence against duplicate rows that sneak past the
    in-memory idempotency check (e.g. after a server restart that
    cleared agent_metadata for a deleted agent, or a race where two
    concurrent requests both passed the status check before either had
    written the terminal status back).
    """
    try:
        audit_path = OSTK_DIR / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Dedup terminal events within a 60-second window per agent name.
        # Read at most the last 32 KB of the file (roughly 200 typical rows)
        # so this stays O(1) regardless of how large audit.jsonl grows.
        name = data.get("name", "")
        if event in ("agent.completed", "agent.failed") and name:
            cutoff = now - timedelta(seconds=60)
            try:
                if audit_path.exists():
                    file_size = audit_path.stat().st_size
                    read_size = min(32768, file_size)
                    with audit_path.open("rb") as fb:
                        fb.seek(max(0, file_size - read_size))
                        raw_bytes = fb.read()
                    # Discard the (possibly partial) first line from the seek
                    tail_text = raw_bytes.decode("utf-8", errors="replace")
                    lines = tail_text.splitlines()
                    if read_size < file_size:
                        lines = lines[1:]  # drop potentially truncated first line
                    for raw in reversed(lines):
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            entry = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        entry_ts_raw = entry.get("timestamp", "")
                        if not entry_ts_raw:
                            break
                        try:
                            entry_ts = datetime.fromisoformat(entry_ts_raw.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if entry_ts < cutoff:
                            # Entries are roughly chronological; once we pass the
                            # 60-second window we can stop looking.
                            break
                        if entry.get("event") == event and entry.get("name") == name:
                            # Duplicate within the dedup window. Drop silently.
                            return
            except OSError:
                pass

        payload = {
            "event": event,
            "timestamp": now_iso,
            **data,
        }
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _save_agent_state():
    """Persist current agent metadata to disk atomically.

    Every mutation site calls this after touching ``agent_metadata``.
    The write goes through ``atomic_write_json`` so a crash mid-save
    cannot leave a half-written JSON blob that would wipe every agent
    record on the next load. Single-loop asyncio guarantees the dict is
    consistent at the moment json.dumps runs, so we do not need a
    separate lock: no await can interleave synchronous serialization
    on the same loop.

    Sync contexts (startup, tests, reconcile loop): call this directly.
    Async handlers: call ``_save_agent_state_async()`` instead so the
    event loop is not blocked by fsync while TLS handshakes are queued.
    """
    from services.atomic_io import atomic_write_json
    try:
        atomic_write_json(AGENT_STATE_PATH, agent_metadata)
    except OSError:
        pass


import threading as _threading
_save_state_write_lock = _threading.Lock()


def _write_state_content(content: str) -> None:
    """Write pre-serialized JSON state to disk. Called from asyncio.to_thread."""
    with _save_state_write_lock:
        from services.atomic_io import atomic_write_text
        try:
            atomic_write_text(AGENT_STATE_PATH, content)
        except OSError:
            pass


def _serialize_and_write_snapshot(snapshot: dict) -> None:
    """Serialize snapshot to JSON and write to disk. Runs inside asyncio.to_thread."""
    content = json.dumps(snapshot, indent=2, ensure_ascii=False)
    _write_state_content(content)


# Coalescing write gate (→2018): prevents N concurrent heartbeats from each
# spawning their own json.dumps thread and stacking GIL contention.
# _save_pending=True means at least one mutation arrived after the in-flight
# save took its snapshot; the loop does one extra pass to capture it.
# At most 1 thread in-flight at any time; at most 1 extra queued pass.
_save_inflight: bool = False
_save_pending: bool = False


async def _save_agent_state_async() -> None:
    """Non-blocking, coalescing save for use inside async handlers.

    Collapses N concurrent heartbeat/register saves into at most 2 serialized
    writes: one in-flight and one queued pass to capture mutations that arrived
    while the thread was running. Without coalescing, N concurrent heartbeats
    each spawn their own asyncio.to_thread(json.dumps) call; the GIL shuffles
    between them, saturating the thread pool and starving the event loop for
    TLS/HTTP work (→2018).
    """
    global _save_inflight, _save_pending
    _save_pending = True
    if _save_inflight:
        return  # the in-flight loop will re-check _save_pending on its next iteration
    _save_inflight = True
    try:
        while _save_pending:
            _save_pending = False
            # Snapshot taken on the event loop: no await can interleave here.
            snapshot = {k: dict(v) for k, v in agent_metadata.items()}
            await asyncio.to_thread(_serialize_and_write_snapshot, snapshot)
    finally:
        _save_inflight = False


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    import os
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _is_pid_my_child(pid: int) -> bool:
    """Check if `pid`'s parent is the current backend process.

    An orphaned subprocess (e.g., from a previous backend instance that
    uvicorn reloaded) stays alive but is reparented to init/launchd —
    `os.kill(pid, 0)` will still succeed, but the process has no drain or
    heartbeat task attached to it anymore. Combined with `_is_pid_alive`
    this lets `_recover_stale_agents` distinguish "real running agent" from
    "orphan zombie that shows as running but is doing nothing".

    Returns False on any error or unexpected output — better to mark
    abandoned than to keep a stale 'running' row.

    Added for →1453 defense-in-depth.
    """
    import os
    import subprocess
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid="],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return False
        ppid = int(result.stdout.strip())
        return ppid == os.getpid()
    except (subprocess.SubprocessError, ValueError, OSError):
        return False


# Grace period (seconds) between SIGTERM and SIGKILL when cancelling an
# agent subprocess. 5 seconds is long enough for clean shutdown but short
# enough that the UI does not feel stuck.
CANCEL_SIGKILL_GRACE_SECONDS = 5


async def _terminate_with_sigkill_fallback(proc) -> bool:
    """Send SIGTERM to ``proc``, then SIGKILL after a grace period.

    Returns True if the process was actually signalled. Claude Code
    subprocesses are resilient to SIGTERM (they trap it and keep going),
    so we follow up with SIGKILL after CANCEL_SIGKILL_GRACE_SECONDS.
    Runs entirely in the background so the caller is not blocked.
    """
    import signal

    try:
        proc.terminate()  # SIGTERM
    except (ProcessLookupError, OSError):
        return False

    async def _ensure_dead():
        """Wait for the process to exit, escalate to SIGKILL if needed."""
        try:
            # Give it CANCEL_SIGKILL_GRACE_SECONDS to exit cleanly.
            await asyncio.wait_for(
                asyncio.shield(
                    asyncio.ensure_future(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: proc.wait() if hasattr(proc, 'wait') and callable(proc.wait) else None,
                        )
                    )
                ),
                timeout=CANCEL_SIGKILL_GRACE_SECONDS,
            )
        except (asyncio.TimeoutError, Exception):
            # Still alive after grace period. Escalate to SIGKILL.
            try:
                if hasattr(proc, 'kill'):
                    proc.kill()  # SIGKILL to the process
                elif hasattr(proc, 'pid'):
                    import os
                    os.kill(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            # Kill the entire process group so children (e.g. a pytest run
            # spawned directly by the agent subprocess) are not left as
            # ppid=1 orphans. With start_new_session=True the agent's pgid
            # equals its own pid; os.getpgid raises OSError if already gone.
            if hasattr(proc, 'pid'):
                try:
                    import os as _os
                    _os.killpg(_os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

    asyncio.create_task(_ensure_dead())
    return True


async def _terminate_pid_with_sigkill_fallback(pid: int) -> bool:
    """Signal a raw PID we no longer hold a Popen for: SIGTERM, then SIGKILL.

    Used by ``cancel_agent`` when ``active_agents`` no longer holds a handle
    for this agent (backend restarted between spawn and cancel, autocomplete
    watcher removed the entry, etc.). Without this, cancel marked the row
    ``cancelled`` but the subagent kept running because nothing ever sent a
    signal to ``meta.get('pid')``. Repro: →1344, PID 94656 alive 7+ hours
    after cancel returned ``process_killed: false``.

    Returns True if SIGTERM was successfully sent (process existed at the
    time of the call). Escalates to SIGKILL in the background after
    ``CANCEL_SIGKILL_GRACE_SECONDS``.
    """
    import os
    import signal as _signal

    if not _is_pid_alive(pid):
        return False
    try:
        os.kill(pid, _signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False

    async def _ensure_dead_pid():
        deadline = asyncio.get_event_loop().time() + CANCEL_SIGKILL_GRACE_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            if not _is_pid_alive(pid):
                return
            await asyncio.sleep(0.1)
        try:
            os.kill(pid, _signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    asyncio.create_task(_ensure_dead_pid())
    return True



async def _startup_deadline_watchdog(
    proc,
    name: str,
    transcript_path: "Path",
    deadline_seconds: int = STARTUP_DEADLINE_SECONDS,
    grace_seconds: int = STARTUP_GRACE_SECONDS,
) -> None:
    """Kill a spawned process that never writes any transcript output.

    Sleeps for ``deadline_seconds``, then checks: if the agent is still in a
    non-terminal status AND the transcript is still 0 bytes, sends SIGTERM and
    marks the agent failed with error="startup_deadline_exceeded".

    The grace window (``grace_seconds``) is the portion of the deadline during
    which we do nothing even if the transcript is empty: the Claude Code CLI
    takes a few seconds to boot and write its first token, so we must not kill
    too eagerly. In practice: deadline=45s, grace=30s means the watchdog fires
    only if the transcript is still empty at t=45s.
    """
    import time as _time
    _spawn_monotonic = _time.monotonic()
    try:
        await asyncio.sleep(deadline_seconds)
    except asyncio.CancelledError:
        return

    # Already terminal: agent completed, cancelled, etc. Nothing to do.
    meta = agent_metadata.get(name) or {}
    if meta.get("status") in _TERMINAL_STATUSES:
        return

    # Check elapsed time against grace window (tests may patch time.monotonic)
    elapsed = _time.monotonic() - _spawn_monotonic
    if elapsed < grace_seconds:
        return

    # Check transcript size
    try:
        tsize = Path(transcript_path).stat().st_size if transcript_path else 0
    except OSError:
        tsize = 0

    if tsize > 0:
        return  # Agent produced output; let it run.

    # Process still running with 0-byte transcript past deadline: kill it.
    logger.warning(
        "startup_deadline_watchdog.kill name=%s deadline=%ds transcript_bytes=0",
        name, deadline_seconds,
    )
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass

    # Wait up to 3s then SIGKILL
    async def _ensure_dead_watchdog():
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: proc.wait() if callable(getattr(proc, "wait", None)) else None,
                ),
                timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    asyncio.create_task(_ensure_dead_watchdog())

    _set_agent_status(
        name, "failed",
        failed_at=datetime.now(timezone.utc).isoformat(),
        error="startup_deadline_exceeded",
        fail_reason="startup_deadline_exceeded",
    )
    try:
        await _save_agent_state_async()
    except Exception:
        try:
            _save_agent_state()
        except Exception:
            pass


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


def _last_seen_dt(agent: dict) -> Optional[datetime]:
    """Return the most recent activity timestamp for an agent row.

    Tries last_heartbeat_at first (set by /heartbeat and /register), then
    spawned_at, then the audit-log ``timestamp`` field (present on rows
    sourced from .ostk/audit.jsonl which carry ``timestamp`` instead of
    ``spawned_at``).  Returns None when no field is present or parseable;
    callers treat None as "keep the row" to avoid silently hiding agents
    whose timestamps are malformed.
    """
    raw = (
        agent.get("last_heartbeat_at")
        or agent.get("spawned_at")
        or agent.get("timestamp")
    )
    if not isinstance(raw, str):
        return None
    return _parse_iso(raw)


# Marker written at the top of any transcript whose owning agent terminated
# without producing real work (tokens_used == 0 and a terminated_reason is
# set, or explicit cancel). Non-obvious invariant: a cancelled 0-token agent
# can still leave subprocess stdout in transcripts/<name>.md that looks like
# a completed run ("Task is complete. I've implemented..."). Downstream
# consumers (inline chat assistant, audit tools) then attribute work that
# never happened to that agent. This banner gives them a reliable signal
# to filter on, and ``_transcript_is_stub`` below uses it to overwrite
# rather than append when a fresh run reuses the same name.
TERMINATED_WITHOUT_WORK_BANNER = "# TERMINATED WITHOUT WORK"


def _terminated_without_work(meta: dict) -> bool:
    """Return True when agent metadata says no real work was done.

    A cancelled or externally-terminated agent whose token counter stayed
    at zero never produced output we can trust. The transcript file on
    disk may contain subprocess stdout text that LOOKS like a completed
    run, so callers use this gate to decide whether to skip the transcript
    write entirely (for /complete stubs) or prepend the banner (for
    cancel paths that need to neutralize a misleading file).
    """
    if not isinstance(meta, dict):
        return False
    tokens = meta.get("tokens_used", 0) or 0
    status = (meta.get("status") or "").strip().lower()
    reason = (meta.get("terminated_reason") or "").strip()
    if tokens != 0:
        return False
    return status == "cancelled" or bool(reason)


def _transcript_is_stub(path) -> bool:
    """Return True when an existing transcript file is a TERMINATED stub.

    Callers that are about to write a real transcript for a reused name
    use this to decide: overwrite the stub rather than letting a rename
    race leave the banner next to real work.
    """
    try:
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists():
            return False
        if p.stat().st_size == 0:
            return False
        with open(str(p), "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(256)
        return head.startswith(TERMINATED_WITHOUT_WORK_BANNER)
    except (OSError, ValueError):
        return False


def _transcript_has_real_content(path) -> bool:
    """Return True when the transcript file has content beyond heartbeat markers.

    Heartbeat lines written by _drain_stdout look like ``[heartbeat ts=<iso>]``.
    The empty-stdout diagnostic note ends with ``with no stdout output.``.
    Any line that is neither blank, a heartbeat marker, nor the diagnostic note
    is considered real agent output -- a signal that real work was done and the
    transcript should not be clobbered by the cancelled-without-work banner.
    """
    try:
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        with open(str(p), "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("[heartbeat ts="):
                    continue
                if stripped.endswith("with no stdout output."):
                    continue
                return True
        return False
    except (OSError, ValueError):
        return False


def _close_orphan_plan_transcript(name: str) -> bool:
    """Delete transcripts/plan-NNN.md when its content matches an orphan pattern.

    Called at agent cancel and complete transitions to clean up plan transcript
    files that were never useful: cancelled runs, "no plan needed" responses,
    and zero-work completions. The list_docs() filter (→1149) already hides
    these at read time; this removes them from disk so they do not accumulate.

    Returns True if the file was deleted, False otherwise. Never raises to
    callers -- a failed delete must not block cancel or complete.
    """
    import re as _re
    if not _re.match(r"^plan-\d+$", name):
        return False
    try:
        from pathlib import Path as _Path
        from config import PROJECT_ROOT as _root
        from services.ostk import ostk as _ostk
        t_path = _Path(_root) / "transcripts" / f"{name}.md"
        if not t_path.exists():
            return False
        text = t_path.read_text(errors="replace")
        if not _ostk._is_orphan_plan_transcript(text):
            return False
        t_path.unlink(missing_ok=True)
        logger.debug("closed orphan plan transcript %s (→1147)", name)
        return True
    except Exception:
        return False


def _worktree_branch_has_commits(branch: str) -> bool:
    """Return True when the agent's worktree branch has commits ahead of main.

    Uses a blocking subprocess call with a short timeout -- acceptable because
    this is a best-effort guard on the cancel path, not a hot path.
    """
    if not branch:
        return False
    try:
        import subprocess as _subprocess
        from config import PROJECT_ROOT as _PR
        result = _subprocess.run(
            ["git", "log", f"main..{branch}", "--oneline"],
            capture_output=True, text=True, timeout=5, cwd=str(_PR),
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _compute_agent_badge(meta: dict) -> Optional[str]:
    """Derive a display badge for a terminal agent row.

    Returns one of "clean", "salvaged", "failed", "abandoned-no-work", or
    None when the agent is still running (badge not applicable).

    Badge semantics:
      clean            – completed by the agent itself (called /complete)
      salvaged         – non-completed terminal state, but the worktree branch
                         was merged into main by a peer session; real transcript
                         confirms work happened (transcript_bytes > 100)
      failed           – non-completed terminal state with real work attempted
                         (transcript_bytes > 100) but no salvage evidence
      abandoned-no-work – terminal state with little or no transcript and no
                          commits (agent never really started)
    """
    status = meta.get("status", "")
    if status not in _TERMINAL_STATUSES:
        return None

    transcript_bytes = meta.get("transcript_bytes") or 0
    worktree_branch = meta.get("worktree_branch") or ""

    # completed → always clean regardless of worktree state
    if status == "completed":
        return "clean"

    # For non-completed terminal states, check if the worktree branch was merged
    has_commits_ahead = bool(worktree_branch and _worktree_branch_has_commits(worktree_branch))

    # salvaged: branch exists, was merged (no longer ahead of main), real transcript
    if worktree_branch and not has_commits_ahead and transcript_bytes > 100:
        return "salvaged"

    # failed: real transcript but not salvaged
    if transcript_bytes > 100:
        return "failed"

    # abandoned-no-work: terminal with no real transcript and no commits
    return "abandoned-no-work"


def _write_terminated_banner(path, name: str, reason: str) -> bool:
    """Overwrite transcript at ``path`` with the terminated-without-work banner.

    Replaces any prior content so that cancelled/bulk-cancelled agents
    cannot leave misleading subprocess stdout on disk. Best-effort;
    filesystem errors are swallowed so a failed write never blocks the
    cancel path itself. Returns True on success.
    """
    try:
        from pathlib import Path as _Path
        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"{TERMINATED_WITHOUT_WORK_BANNER} tokens=0 reason={reason or 'unknown'}\n\n"
            f"Agent '{name}' terminated without producing real work. "
            "Any prior content in this file was subprocess stdout from a "
            "run that was cancelled or externally killed before the agent "
            "recorded any token usage, and must not be attributed as a "
            "completed result.\n"
        )
        p.write_text(body)
        return True
    except (OSError, ValueError):
        return False


# Maximum number of automatic recovery attempts before giving up. Prevents
# infinite loops where a consistently crashing agent gets re-spawned forever.
MAX_RECOVERY_ATTEMPTS = 3

# Queue populated by _autocomplete_exited_subagents (sync) and drained by
# _reconcile_loop (async) to schedule Haiku retries without blocking the sweep.
_pending_ghost_retries: list = []

# Queue populated by _autocomplete_exited_subagents (sync) and drained by
# the async callers (_reconcile_loop, _compute_agents_snapshot_async) to
# close the task/needle associated with each auto-completed agent (→2207).
# close_task is async so it cannot be called directly from the thread-pool
# worker; mirroring the ghost-retry pattern keeps the fix minimal.
_pending_needle_closes: list[str] = []

# Tier name for ghost retries — resolved via MODEL_MAP so it always matches
# whatever the canonical Haiku ID is in services/model_routing.py.
_GHOST_RETRY_HAIKU_MODEL = "haiku"


def _worktree_has_new_work(worktree_path: str) -> bool:
    """Return True if the worktree has new commits or dirty files vs main.

    Used by ghost detection to distinguish a quota-capped no-op spawn
    (transcript=0, tokens=0, worktree clean) from a spawn that wrote
    commits but produced no transcript (rare but possible for shell-only
    agents).
    """
    import subprocess as _sp
    try:
        r = _sp.run(
            ["git", "log", "--oneline", "main..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return True
        r2 = _sp.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path, capture_output=True, text=True, timeout=2,
        )
        if r2.returncode == 0 and r2.stdout.strip():
            return True
    except Exception:
        pass
    return False


def _is_scaffold_only_with_dirty_worktree(
    worktree_path: str,
    branch: str,
) -> tuple[bool, str]:
    """Return (True, reason) when a worktree has only scaffold commits AND dirty files.

    A "premature close" pattern is:
      1. All commits on the branch since main are chore(→…): scaffold … style
         (subject starts with "chore(" and contains ": scaffold")
      2. AND the worktree has uncommitted changes — staged, unstaged, OR
         untracked new files that were written but never `git add`-ed

    Both conditions must hold. A scaffold-only branch with a clean worktree
    is fine (agent scaffolded and stopped cleanly). A dirty worktree with
    real fix commits is also fine.

    Used by mark_agent_complete (→1346) to block premature closure.
    """
    import subprocess as _sp
    from pathlib import Path as _P

    wt = _P(worktree_path)
    if not wt.exists():
        return False, ""

    try:
        # 1. Find merge-base with main
        r_base = _sp.run(
            ["git", "merge-base", "HEAD", "main"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_base.returncode != 0 or not r_base.stdout.strip():
            return False, ""
        merge_base = r_base.stdout.strip()

        # 2. Count total commits ahead of main
        r_count = _sp.run(
            ["git", "rev-list", "--count", f"{merge_base}..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_count.returncode != 0:
            return False, ""
        total_commits = int(r_count.stdout.strip() or "0")
        # NOTE: do NOT early-return for total_commits==0 here.
        # A worktree with 0 commits ahead of main but dirty files is premature
        # (the agent wrote files and exited without committing). This was the
        # →2503 incident: three agents completed with 0 commits + dirty files;
        # the guard's early-return masked the bug. The 0-commit + dirty case is
        # caught after the dirty-files check below.

        # 3. Count non-scaffold commits (subject must start "chore(" and contain ": scaffold")
        r_log = _sp.run(
            ["git", "log", "--format=%s", f"{merge_base}..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_log.returncode != 0:
            return False, ""
        subjects = [s.strip() for s in r_log.stdout.splitlines() if s.strip()]
        non_scaffold = [
            s for s in subjects
            if not (s.startswith("chore(") and ": scaffold" in s)
        ]
        if non_scaffold:
            return False, ""  # has real commits — not premature

        # 4. Check for dirty state: staged, unstaged, or untracked files
        # git status --porcelain covers all three: M, A, ??, etc.
        r_status = _sp.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_status.returncode != 0:
            return False, ""
        dirty_lines = [l for l in r_status.stdout.splitlines() if l.strip()]
        if not dirty_lines:
            return False, ""  # clean worktree — scaffolded and stopped intentionally

        dirty_summary = ", ".join(l.strip() for l in dirty_lines[:3])

        # →2503: 0-commits + dirty is premature too. total_commits==0 means the
        # agent never committed ANY work; dirty files are uncommitted changes that
        # will be lost when the worktree is cleaned up. This is more premature than
        # the scaffold-only pattern (which at least has a chore commit as a marker).
        if total_commits == 0:
            reason = (
                f"no-commit-ahead-of-main + {len(dirty_lines)} dirty file(s): {dirty_summary}"
            )
            return True, reason

        # Both scaffold-only conditions met: scaffold-only history + dirty working tree
        reason = (
            f"scaffold-only-commits ({total_commits} scaffold, 0 real) "
            f"+ {len(dirty_lines)} dirty file(s): {dirty_summary}"
        )
        return True, reason

    except Exception as _exc:
        logger.debug("_is_scaffold_only_with_dirty_worktree: error wt=%s err=%s", worktree_path, _exc)
        return False, ""


# Below this many net committed lines a completion is treated as "near-no-op":
# it produced a diff, but one too small to plausibly be the build it was
# dispatched to do. The ca1439b0 evidence case was a single 42-line test-only
# commit; 50 lands just above that so the real signal fires. This is a SIGNAL
# threshold, never a gate — the agent still completes (torios informs, never
# blocks).
NEAR_NOOP_LINE_THRESHOLD = 50


def _compute_worktree_work_size(worktree_path: str) -> dict:
    """Measure the committed work magnitude of a worktree vs ``main``.

    Returns a dict with integer keys ``commits``, ``insertions``,
    ``deletions``, ``files_changed``. All zero when the path is missing,
    not a git repo, or has no commits ahead of main. Best-effort: any git
    error degrades to zeros so this never raises on the completion path.

    Why magnitude and not presence: the prior completion path only checked
    whether *any* edit/commit existed (``_stale_sweep_summary_for``), so a
    single tiny commit (e.g. a 42-line test file) read identically to a real
    multi-epic build. Measuring lines + files + commits is the ground-truth
    signal that distinguishes the two.
    """
    out = {"commits": 0, "insertions": 0, "deletions": 0, "files_changed": 0}
    if not worktree_path:
        return out
    import subprocess as _sp
    from pathlib import Path as _P

    wt = _P(worktree_path)
    if not wt.exists():
        return out
    try:
        # Commit count ahead of the merge-base with main. rev-list with the
        # symmetric-difference base keeps the count accurate even when main
        # has advanced underneath the branch.
        r_base = _sp.run(
            ["git", "merge-base", "HEAD", "main"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        base = r_base.stdout.strip() if r_base.returncode == 0 else ""
        if not base:
            return out
        r_count = _sp.run(
            ["git", "rev-list", "--count", f"{base}..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_count.returncode == 0:
            out["commits"] = int(r_count.stdout.strip() or "0")
        # Diff magnitude from the merge-base to HEAD (committed work only;
        # uncommitted/dirty files are handled by the scaffold guard).
        r_stat = _sp.run(
            ["git", "diff", "--shortstat", f"{base}..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if r_stat.returncode == 0:
            import re as _re
            line = r_stat.stdout.strip()
            m_files = _re.search(r"(\d+) files? changed", line)
            m_ins = _re.search(r"(\d+) insertions?\(\+\)", line)
            m_del = _re.search(r"(\d+) deletions?\(-\)", line)
            if m_files:
                out["files_changed"] = int(m_files.group(1))
            if m_ins:
                out["insertions"] = int(m_ins.group(1))
            if m_del:
                out["deletions"] = int(m_del.group(1))
    except Exception as _exc:  # noqa: BLE001 — best-effort signal, never raise
        logger.debug("_compute_worktree_work_size: error wt=%s err=%s", worktree_path, _exc)
    return out


def _classify_near_noop(work_size: dict, summary: str = "") -> tuple:
    """Return (near_noop: bool, reason: str) from a work_size dict.

    Pure and deterministic — no I/O — so it is cheap to call on every
    completion and trivial to unit-test. Flags as near-no-op when the
    committed diff is empty or below ``NEAR_NOOP_LINE_THRESHOLD`` net lines.

    This is an INFORMATIONAL signal for the orchestrator. The caller attaches
    ``near_noop``/``near_noop_reason`` to the agent row; it must NOT block or
    reverse the completion. A genuinely small-but-correct change (e.g. a
    one-line bugfix dispatched as such) will be flagged too — that is
    acceptable, because the flag's job is to make the orchestrator *look*, not
    to decide. The orchestrator weighs it against the task it dispatched.
    """
    ws = work_size or {}
    commits = int(ws.get("commits") or 0)
    insertions = int(ws.get("insertions") or 0)
    deletions = int(ws.get("deletions") or 0)
    changed = insertions + deletions

    if commits == 0 and changed == 0:
        return True, "near-no-op: empty diff (no commits ahead of main)"
    if changed == 0:
        return True, "near-no-op: empty diff (commits present but zero net lines changed)"
    if changed < NEAR_NOOP_LINE_THRESHOLD:
        return (
            True,
            f"near-no-op: only {changed} net line(s) across "
            f"{int(ws.get('files_changed') or 0)} file(s) in {commits} commit(s) "
            f"(below {NEAR_NOOP_LINE_THRESHOLD}-line signal threshold)",
        )
    return False, ""


def _attach_near_noop_signal(name: str, meta: dict) -> None:
    """Compute + attach the near-no-op signal to a worktree agent's row.

    INFORMS, never blocks: writes ``work_size``, ``near_noop`` and
    ``near_noop_reason`` onto the agent metadata so the orchestrator/UI can
    surface "this agent completed with a near-empty diff" without ever
    reversing the completion or holding the agent open. No-op for non-worktree
    agents (no committed diff to measure).
    """
    try:
        if not isinstance(meta, dict):
            return
        if meta.get("isolation") != "worktree":
            return
        wt_path = meta.get("worktree_path")
        if not wt_path:
            return
        ws = _compute_worktree_work_size(wt_path)
        flagged, reason = _classify_near_noop(ws, summary=meta.get("summary") or "")
        meta["work_size"] = ws
        meta["near_noop"] = flagged
        if flagged:
            meta["near_noop_reason"] = reason
            logger.info(
                "agent.near_noop name=%s commits=%s insertions=%s deletions=%s files=%s reason=%s",
                name, ws.get("commits"), ws.get("insertions"),
                ws.get("deletions"), ws.get("files_changed"), reason,
            )
        else:
            meta.pop("near_noop_reason", None)
    except Exception as _exc:  # noqa: BLE001 — signal must never break completion
        logger.debug("_attach_near_noop_signal: error name=%s err=%s", name, _exc)


def _sweep_close_verified(meta: dict) -> bool:
    """→2620: True only when the idle sweep has landing evidence for the agent.

    The sweep (``_autocomplete_exited_subagents``) flips dead agents to
    "completed" from liveness inference alone — process gone plus an idle
    transcript, or a stale heartbeat. That inference is fine for the agent
    ROW, but closing the agent's TASK on it is how →2618 got closed for an
    agent that died on a dropped model connection with zero commits: by
    liveness signals a crashed agent is indistinguishable from a finished
    one.

    Verified success for an agent that never called /complete means its
    isolated worktree has at least one commit ahead of main — committed
    work is the only landing evidence available here. Everything else
    (no worktree metadata, missing path, zero commits, git errors) reads
    as unverified and the task stays open; ``_set_agent_status`` has
    already reset the needle from in_progress back to open via
    ``_fire_release_needle_if_orphaned`` (→2039). Explicit success paths
    are unaffected: POST /complete (mark_agent_complete) and the verified
    auto-merge path close their needles themselves.
    """
    try:
        if not isinstance(meta, dict):
            return False
        if meta.get("isolation") != "worktree":
            return False
        wt_path = meta.get("worktree_path")
        if not wt_path:
            return False
        ws = _compute_worktree_work_size(wt_path)
        return int(ws.get("commits") or 0) > 0
    except Exception:  # noqa: BLE001 — verification must never break the sweep
        return False


def _is_ghost_completion(meta: dict, name: str) -> tuple:
    """Return (True, reason) if this agent completed with zero real work.

    Ghost signature (all must hold):
      - tokens_used == 0   (no API calls recorded)
      - transcript_bytes == 0   (subprocess wrote nothing)
      - if isolation == "worktree": no new commits and no dirty files

    Agents already in a recovery cycle (recovery_count > 0) are skipped
    to avoid double-marking a retry that legitimately ran out of quota.

    Returns (False, "") when the agent should be treated as completed normally.
    """
    if not isinstance(meta, dict):
        return False, ""
    if (meta.get("recovery_count") or 0) > 0:
        return False, ""
    tokens = meta.get("tokens_used") or 0
    if tokens != 0:
        return False, ""
    metrics = _get_transcript_metrics(name)
    if metrics.get("transcript_bytes", 0) != 0:
        return False, ""
    isolation = meta.get("isolation") or "none"
    if isolation == "worktree":
        worktree_path = meta.get("worktree_path")
        if worktree_path:
            from pathlib import Path as _Path
            if _Path(worktree_path).exists():
                if _worktree_has_new_work(worktree_path):
                    return False, ""
    return True, "silent_quota_or_subprocess_failure"


def _ghost_retry_enabled() -> bool:
    """Return True when OSTK_AUTO_RETRY_ON_HAIKU=1 is set in the environment."""
    import os as _os_env
    return _os_env.environ.get("OSTK_AUTO_RETRY_ON_HAIKU", "").lower() in ("1", "true", "yes")


async def _schedule_ghost_retry(name: str) -> None:
    """Spawn a Haiku retry for a ghost-failed agent.

    Only fires when OSTK_AUTO_RETRY_ON_HAIKU=1 and recovery_count < cap.
    Requires the original prompt to have been stored in metadata at spawn
    time (spawn_meta["prompt"]); logs a warning and skips if absent.
    """
    if not _ghost_retry_enabled():
        return
    meta = agent_metadata.get(name)
    if not meta:
        return
    recovery_count = meta.get("recovery_count") or 0
    if recovery_count >= MAX_RECOVERY_ATTEMPTS:
        logger.info(
            "ghost_retry.skip name=%s recovery_count=%d cap=%d",
            name, recovery_count, MAX_RECOVERY_ATTEMPTS,
        )
        return
    original_prompt = meta.get("prompt") or ""
    if not original_prompt:
        logger.warning(
            "ghost_retry.no_prompt name=%s — retry requires stored prompt", name
        )
        return
    meta["recovery_count"] = recovery_count + 1
    meta["last_recovery_at"] = _now_iso()
    meta["recovery_reason"] = "ghost_haiku_retry"
    await _save_agent_state_async()
    from models.schemas import AgentSpawn as _AgentSpawn
    spawn_body = _AgentSpawn(
        name=name,
        prompt=original_prompt,
        model=_GHOST_RETRY_HAIKU_MODEL,
        budget=float(meta.get("budget") or "2.0"),
        task_id=meta.get("task_id"),
        needle_id=meta.get("needle_id"),
        isolation=meta.get("isolation") or "none",
        locks=meta.get("locks"),
        token_limit=meta.get("token_limit"),
        source=meta.get("source") or "claude-code",
    )
    try:
        result = await spawn_agent(spawn_body)
        logger.info(
            "ghost_retry.spawned name=%s model=haiku result=%s",
            name, result.get("result", "?"),
        )
    except Exception as exc:
        logger.warning("ghost_retry.failed name=%s err=%s", name, exc)
        if name in agent_metadata:
            agent_metadata[name]["recovery_count"] = recovery_count
            await _save_agent_state_async()

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


def _transcript_recently_active(name: str, now: datetime) -> bool:
    """Return True if the agent's transcript file was modified within the
    last STALE_AGENT_TIMEOUT_SECONDS seconds.

    This is a server-side keepalive heuristic for Claude Code subagents
    spawned via the Agent tool. Those subagents never see the mailbox
    instruction block (it is only injected by POST /agents/spawn), so
    they have no way to know they should call POST /heartbeat. But their
    transcript file IS written continuously as the model streams output.
    If the file mtime is fresh, the agent is alive.

    We use the raw mtime, not the cached metrics, because the metrics
    cache is intentionally elision-aware and may return a cached result
    even when the file just changed.
    """
    source = _resolve_transcript_source(name)
    if source is None:
        return False
    try:
        mtime = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        return (now - mtime).total_seconds() <= STALE_AGENT_TIMEOUT_SECONDS
    except OSError:
        return False


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

    Transcript-growth safety: Claude Code subagents spawned via the
    Agent tool never see the mailbox instruction block, so they never
    call POST /heartbeat. Their only liveness signal is that their
    transcript file keeps growing. If the file mtime is within the
    stale window, the agent is alive and we leave it alone.
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
        # →2659: the stored pid is ground truth too. A busy agent mid long
        # tool call cannot heartbeat, but its process is demonstrably alive
        # (os.kill(pid, 0) succeeds); it must never be reaped as stale.
        _stored_pid = meta.get("pid")
        if _stored_pid and _is_pid_alive(_stored_pid):
            continue
        # Transcript-growth heuristic: if the transcript file was written
        # recently the agent is still active even with no HTTP heartbeat.
        # This catches Claude Code Agent-tool subagents that were never
        # given the mailbox instruction block.
        if _transcript_recently_active(name, now):
            continue
        # →2956: for a row that follows the heartbeat contract, heartbeat
        # silence alone is never death. Require one POSITIVE death signal
        # (dead pid, or a non-empty transcript gone idle) before any flip.
        # Registration-only agents in isolated workspaces have no pid and
        # an unresolvable or 0-byte transcript; those rows stay running
        # until real evidence appears.
        _evidence_detail = ""
        if _has_heartbeat_contract(meta):
            _allow_flip, _evidence_detail = _stale_flip_evidence(name, meta, now)
            if not _allow_flip:
                continue

        # Check if we should attempt recovery instead of terminating
        recovery_count = meta.get("recovery_count", 0)
        handoff_note = _read_handoff_note(name)
        # →2607: each flip gets its own timestamp, never the sweep's shared `now`.
        _stale_flip_ts = datetime.now(timezone.utc).isoformat()
        if handoff_note and recovery_count < MAX_RECOVERY_ATTEMPTS:
            _set_agent_status(name, "recovering", recovery_count=recovery_count + 1, last_recovery_at=_stale_flip_ts)
            changed = True
        else:
            reason = (
                f"No heartbeat for {int(age_seconds)}s "
                f"(limit {STALE_AGENT_TIMEOUT_SECONDS}s)"
            )
            if _evidence_detail:
                # →2956: record WHY the board believed this flip.
                reason += f"; evidence: {_evidence_detail}"
            if recovery_count >= MAX_RECOVERY_ATTEMPTS:
                reason += (
                    f". Recovery exhausted ({recovery_count}/{MAX_RECOVERY_ATTEMPTS})"
                )
            _set_agent_status(name, "terminated_stale", terminated_at=_stale_flip_ts, terminated_reason=reason, flagged_by="stale_sweep")
            changed = True
    return changed


def _transcript_grew_recently(
    name: str,
    now: datetime,
    window_seconds: int = STALE_AGENT_TRANSCRIPT_GRACE_SECONDS,
) -> bool:
    """Return True if the agent's transcript was modified within the last
    ``window_seconds`` seconds (default: STALE_AGENT_TRANSCRIPT_GRACE_SECONDS).

    Distinct from ``_transcript_recently_active`` (which uses the longer
    STALE_AGENT_TIMEOUT_SECONDS window to keep live agents from being swept).
    The tight 2-minute default is used by the auto-complete pass: if the
    transcript grew in the last 2 minutes the agent is still mid-stream and
    must not be auto-completed yet. →2896 passes IDLE_WATCHDOG_QUIET_SECONDS
    here when the row has no pid and death is unproven.
    """
    source = _resolve_transcript_source(name)
    if source is None:
        return False
    try:
        mtime = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        return (now - mtime).total_seconds() <= window_seconds
    except OSError:
        return False


def _has_heartbeat_contract(meta: dict) -> bool:
    """→2956: True when this row has PROVEN it follows the heartbeat
    contract. Only a real POST /heartbeat carrying a step writes
    ``current_step``, and only the revive/reclaim paths write
    ``revival_count`` / ``reclaim_count`` — so any of these fields is
    proof a live process has been talking to the board. Rows that never
    spoke after registration carry no such proof and keep the legacy
    timeout behaviour: an inert row is indistinguishable from a dead
    one, and the old sweeps are what clears those zombies.
    """
    return bool(
        meta.get("current_step")
        or meta.get("revival_count")
        or meta.get("reclaim_count")
    )


def _stale_flip_evidence(name: str, meta: dict, now: datetime) -> tuple:
    """→2956 evidence standard for reaper flips of a heartbeat-contract row.

    Returns ``(allow_flip, detail)``. Beyond the stale heartbeat that
    triggered the check, the board needs at least one POSITIVE death
    signal before flipping a running contract row:

      * a stored pid that ``os.kill(pid, 0)`` reports dead, or
      * a resolvable, NON-EMPTY transcript whose mtime went idle past
        ``STALE_AGENT_TIMEOUT_SECONDS``.

    Absence of a signal is never evidence: registration-only agents
    record no pid, and the transcript resolver finds nothing for agents
    working in isolated workspaces (their byte counter reads 0). A
    missing or zero-byte transcript therefore never counts as death.
    Silence on ONE signal is never death (saa-2944/2945/2946/2953).
    """
    pid = meta.get("pid")
    if pid:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            pid_int = None
        if pid_int:
            if _is_pid_alive(pid_int):
                return (False, "pid alive")
            return (True, f"pid {pid_int} probed dead (os.kill)")
    source = _resolve_transcript_source(name)
    if source is not None:
        try:
            st = source.stat()
        except OSError:
            st = None
        if st is not None and st.st_size > 0:
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            idle_seconds = (now - mtime).total_seconds()
            if idle_seconds <= STALE_AGENT_TIMEOUT_SECONDS:
                return (False, "transcript recently active")
            return (True, f"non-empty transcript idle for {int(idle_seconds)}s")
        # Resolvable but empty (0 bytes) or unreadable: NOT evidence —
        # the byte counter reads 0 for isolated workspaces.
    return (False, "no positive death evidence beyond heartbeat silence")


_STALE_SWEEP_SUMMARY_NO_WORK = (
    "Agent stopped responding. No visible changes; consider re-running."
)
_STALE_SWEEP_SUMMARY_DID_WORK = (
    "Agent finished its work. It didn't formally close the task - "
    "check git log / transcript for details."
)


def _stale_sweep_summary_for(name: str) -> str:
    """Pick a user-facing summary for an agent swept by the auto-complete path.

    The default "Agent exited without calling /complete" message is technically
    accurate but gives the user no signal about whether the underlying task
    was actually done. We scan the transcript for evidence of real work:

      * any Edit / Write / fs_write / str_replace tool_use block, or
      * a successful ``git commit`` shell call.

    If any is found, return the "Agent finished its work" variant so the user
    knows to look at git log / transcripts. Otherwise, return the gentler
    "no visible changes" variant so they know to re-run.

    Transcript paths can be either ``.md`` (legacy stub) or a JSONL stream.
    We only deep-scan JSONL because the .md files are just free text.
    """
    source = _resolve_transcript_source(name)
    if source is None:
        return _STALE_SWEEP_SUMMARY_NO_WORK
    try:
        # Only deep-scan JSONL transcripts (claude-code streaming format).
        if source.suffix != ".jsonl":
            # Fallback: a non-empty .md transcript means SOMETHING was
            # written. Treat it as "did work" so we do not falsely claim
            # nothing happened.
            if source.stat().st_size > 0:
                return _STALE_SWEEP_SUMMARY_DID_WORK
            return _STALE_SWEEP_SUMMARY_NO_WORK
        edit_tool_names = {"Edit", "Write", "str_replace",
                           "mcp__ostk__edit", "mcp__ostk__fs_write"}
        _bytes_read = 0
        _MAX_SCAN_BYTES = 512 * 1024
        with source.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                _bytes_read += len(line)
                if _bytes_read > _MAX_SCAN_BYTES:
                    # Cap scan at 512 KB to avoid stalling the thread pool on
                    # massive transcripts (->2165).
                    break
                if not line.strip():
                    continue
                # Cheap substring check first to avoid json.loads on every
                # line. These tokens are robustly present when an edit/commit
                # tool_use was emitted.
                if ('"tool_use"' not in line
                        and '"Edit"' not in line
                        and '"Write"' not in line
                        and 'git commit' not in line):
                    continue
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                msg = evt.get("message") if isinstance(evt, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                blocks = content if isinstance(content, list) else []
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_use":
                        if block.get("name") in edit_tool_names:
                            return _STALE_SWEEP_SUMMARY_DID_WORK
                        # Bash / shell tool_use carrying a git commit.
                        cmd = ""
                        binp = block.get("input")
                        if isinstance(binp, dict):
                            cmd = str(binp.get("command") or binp.get("cmd") or "")
                        if "git commit" in cmd:
                            return _STALE_SWEEP_SUMMARY_DID_WORK
    except OSError:
        pass
    return _STALE_SWEEP_SUMMARY_NO_WORK


def _autocomplete_exited_subagents() -> bool:
    """Auto-complete Agent-tool subagents that exited without calling /complete.

    Agent-tool subagents (source='claude-code') spawned via Claude Code's
    Agent tool finish cleanly and exit, but they have no way to call
    POST /complete because the mailbox instruction block is only injected
    by POST /agents/spawn. The orchestrator sees a task-notification but
    the row stays 'running' with a stale heartbeat forever unless something
    server-side cleans it up.

    Two completion paths (both require process to be dead and source='claude-code'):

    Path A -- transcript-idle (fast path, preferred):
      The agent has a transcript whose mtime is older than
      STALE_AGENT_TRANSCRIPT_GRACE_SECONDS (2 min). Transcript growth is
      the most reliable liveness signal: once the file stops changing the
      agent has finished writing. We do NOT require the heartbeat to be stale
      here, which lets us auto-complete a fast agent (< 5 min) as soon as
      its output goes quiet -- fixing count drift between the orchestrator
      and the UI for subagents that complete in under 5 minutes.

    Path B -- heartbeat-age fallback (for agents with no transcript):
      No transcript was found AND the stored pid is confirmed dead AND the
      heartbeat / spawned_at is older than STALE_AGENT_AUTOCOMPLETE_SECONDS
      (5 minutes). Covers agents that registered but never wrote a
      transcript file. →2659: rows without a pid on record are skipped —
      heartbeat silence alone is not a death signal, and a busy agent mid
      long tool call cannot heartbeat. Those rows fall to the 15-minute
      _sweep_stale_running_agents, which records an honest terminated_stale
      reason instead of a false "completed".

    We never auto-complete source='chat' or source='api' agents. Those are
    explicitly spawned and may still be in-flight even with no HTTP heartbeat.

    Returns True if any record was changed, so the caller can persist once.
    Emits exactly one agent.completed audit event per agent (existing dedup
    in _emit_audit_event handles races and server-restart replay).
    """
    now = datetime.now(timezone.utc)
    changed = False
    for name, meta in agent_metadata.items():
        # Only Agent-tool subagents.
        if meta.get("source") != "claude-code":
            continue
        if meta.get("status") != "running":
            continue
        # If we hold a live proc handle the agent is still running.
        if _proc_handle_is_alive(name):
            continue
        # Check stored PID on macOS/Linux.
        pid = meta.get("pid")
        if pid and _is_pid_alive(pid):
            continue

        # Path A: transcript-idle check (fast path).
        # Transcript growth is the primary liveness signal for Claude Code
        # subagents. If the file exists and stopped growing more than
        # STALE_AGENT_TRANSCRIPT_GRACE_SECONDS ago, the agent is done.
        # This fires much sooner than the 5-minute heartbeat fallback and
        # directly fixes count drift for fast subagents (< 5 min).
        transcript_source = _resolve_transcript_source(name)
        if transcript_source is not None:
            # →1227 fix: a recent heartbeat means the agent is alive between
            # tool calls, even if the transcript stub is momentarily idle.
            # The stub (written by /heartbeat) and the JSONL (written by the
            # model) have independent mtimes; the stub can lag when the agent
            # is making long Claude API calls without heartbeating. Treat a
            # heartbeat within the grace window as "still live".
            _last_hb_raw = meta.get("last_heartbeat_at")
            if _last_hb_raw:
                _last_hb_dt = _parse_iso(_last_hb_raw)
                if _last_hb_dt and (now - _last_hb_dt).total_seconds() <= STALE_AGENT_TRANSCRIPT_GRACE_SECONDS:
                    continue
            if not _transcript_grew_recently(name, now):
                # →2896: no recorded pid means death is UNPROVEN — the row
                # was curl-registered and there is no process to check. The
                # 2-minute idle grace repeatedly false-flagged agents that
                # were quiet inside one long tool call (saa-2892, saa-2894,
                # saa-2880: full test suites). Without proof of death, the
                # log must have been quiet for the LONG threshold before a
                # flip is allowed. Fresh heartbeats were already handled
                # above; confirmed-dead pids keep the fast path.
                if not pid and _transcript_grew_recently(
                    name, now, IDLE_WATCHDOG_QUIET_SECONDS
                ):
                    continue
                # Transcript exists and is idle: agent finished.
                # →2607: stamp each flip with its OWN timestamp. Batch-stamping
                # the sweep's shared `now` gave saa-reaper-fixtures-r2 and
                # saa-phantom-rows-r2 microsecond-identical completed_at values.
                _flip_ts = datetime.now(timezone.utc).isoformat()
                # Ghost check: if transcript is 0 bytes and no tokens were
                # used, this is likely a quota-cap silent failure.
                _ghost, _ghost_reason = _is_ghost_completion(meta, name)
                if _ghost:
                    _set_agent_status(name, "failed", failed_at=_flip_ts, fail_reason=_ghost_reason, summary=_stale_sweep_summary_for(name), flagged_by="idle_sweep")
                    _pending_ghost_retries.append(name)
                    logger.warning(
                        "ghost.detected path=A name=%s reason=%s",
                        name, _ghost_reason,
                    )
                else:
                    _attach_near_noop_signal(name, meta)
                    _set_agent_status(name, "completed", completed_at=_flip_ts, summary=_stale_sweep_summary_for(name), flagged_by="idle_sweep")
                    _emit_audit_event("agent.completed", {"name": name})
                    # Queue needle closes for the async drain (→2207) — but
                    # ONLY on verified success (→2620). This flip was inferred
                    # from liveness (dead PID + idle transcript); a crashed
                    # agent looks identical to a finished one, so without
                    # landing evidence the task stays open.
                    if _sweep_close_verified(meta):
                        _nid = meta.get("needle_id")
                        _extra_nids = list(meta.get("needle_ids") or [])
                        if _nid:
                            _pending_needle_closes.append(str(_nid))
                        for _extra in _extra_nids:
                            _s = str(_extra)
                            if _s not in _pending_needle_closes:
                                _pending_needle_closes.append(_s)
                    elif meta.get("needle_id") or meta.get("needle_ids"):
                        logger.info(
                            "sweep.task_close_skipped name=%s reason=no_verified_work (→2620)",
                            name,
                        )
                changed = True
            # Either idle (just completed/failed above) or still active.
            # Either way, skip Path B -- transcript is the authority.
            continue

        # Path B: no transcript -- fall back to heartbeat-age threshold.
        #
        # Hook-preregistered rows (created by the PreToolUse Agent hook before
        # the subagent self-registers) derive their name from the parent's
        # ``description`` slug, which frequently differs from the ``Name:`` the
        # user hand-wrote inside the Task prompt. When the two diverge,
        # ``_resolve_transcript_source`` cannot match the subagent's JSONL and
        # returns None. If we then fall through to Path B we will mark the
        # agent completed on its first 5-minute heartbeat gap even though it
        # is still writing its transcript. That is the "7 live subagents, 0
        # shown in torios" bug.
        #
        # Safer policy: for a hook-preregister row with no transcript match,
        # leave the row alone. The 15-minute ``_sweep_stale_running_agents``
        # (STALE_AGENT_TIMEOUT_SECONDS) is the only mechanism that should
        # close these rows, and only when both the heartbeat AND the transcript
        # are stale. The heartbeat-agent.sh parent idle sweep also feeds the
        # same transcript-match check, so a true zombie still gets closed at
        # its 15-minute ceiling.
        if meta.get("hook_preregister"):
            continue
        # →2659: with no transcript there is nothing to measure, so the only
        # positive death signal left is a confirmed-dead pid. An alive pid was
        # already skipped above; no pid at all means we know NOTHING about
        # this agent, and heartbeat silence alone must never flip a row to
        # "completed" — saa-2650-slack-chat was mid-pytest (quiet heartbeat,
        # output growing) when the idle pipeline marked it finished. True
        # zombies are still reaped by _sweep_stale_running_agents at the
        # 15-minute ceiling with an honest terminated_stale reason.
        if not pid:
            continue
        last_seen_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        last_seen = _parse_iso(last_seen_raw) if isinstance(last_seen_raw, str) else None
        if last_seen is None:
            continue
        age_seconds = (now - last_seen).total_seconds()
        if age_seconds <= STALE_AGENT_AUTOCOMPLETE_SECONDS:
            continue
        # All checks passed: the agent's process is confirmed dead and it
        # exited without calling /complete.
        # →2607: per-flip timestamp, never the sweep's shared `now`.
        _flip_ts_b = datetime.now(timezone.utc).isoformat()
        # Ghost check: transcript absent + no tokens = quota cap.
        _ghost_b, _ghost_reason_b = _is_ghost_completion(meta, name)
        if _ghost_b:
            _set_agent_status(name, "failed", failed_at=_flip_ts_b, fail_reason=_ghost_reason_b, summary=_stale_sweep_summary_for(name), flagged_by="idle_sweep")
            _pending_ghost_retries.append(name)
            logger.warning(
                "ghost.detected path=B name=%s reason=%s",
                name, _ghost_reason_b,
            )
        else:
            _attach_near_noop_signal(name, meta)
            _set_agent_status(name, "completed", completed_at=_flip_ts_b, summary=_stale_sweep_summary_for(name), flagged_by="idle_sweep")
            _emit_audit_event("agent.completed", {"name": name})
            # Queue needle closes for the async drain (→2207) — but ONLY on
            # verified success (→2620). Path B is even weaker inference than
            # Path A (no transcript at all, just a stale heartbeat); a task
            # must never close on it without landing evidence.
            if _sweep_close_verified(meta):
                _nid = meta.get("needle_id")
                _extra_nids = list(meta.get("needle_ids") or [])
                if _nid:
                    _pending_needle_closes.append(str(_nid))
                for _extra in _extra_nids:
                    _s = str(_extra)
                    if _s not in _pending_needle_closes:
                        _pending_needle_closes.append(_s)
            elif meta.get("needle_id") or meta.get("needle_ids"):
                logger.info(
                    "sweep.task_close_skipped name=%s reason=no_verified_work (→2620)",
                    name,
                )
        changed = True
    return changed


def _recover_stale_agents():
    """On startup, sweep persisted 'running' agents that are definitely dead.

    Three cases:

    1. Live PID we can verify: keep. The agent's subprocess survived the
       restart (rare but possible for daemonised or detached procs).
    2. No live PID, source == "claude-code" (external Claude Code session),
       and a recent heartbeat: keep. External sessions own their own
       process tree and keep heartbeating across our restarts.
    3. Anything else: mark abandoned. This covers backend-managed spawns
       (source="ui", "api", "chat") whose in-memory worker died with the
       backend. Without this rule those rows survive restart and show as
       RUNNING in the UI even though nothing is happening, surprising the
       user with phantom agents.
    """
    now = datetime.now(timezone.utc)
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        pid = meta.get("pid")
        # Case 1: live PID AND it's a child of this process. Keep.
        # The child-of-this-process check guards against orphan subprocesses
        # that survived a backend restart — those have a live PID but were
        # reparented to init, so their drain and heartbeat tasks are gone.
        # Per →1453 diagnostic.
        if pid and _is_pid_alive(pid) and _is_pid_my_child(pid):
            # →2488: refresh so _autocomplete_exited_subagents doesn't fire right after restart
            meta["last_heartbeat_at"] = now.isoformat()
            changed = True
            continue
        # Case 2: external Claude Code session with recent heartbeat. Keep.
        # We trust the heartbeat ONLY when the agent's source says it has
        # an external process tree. Backend-spawned agents do not.
        source = meta.get("source")
        if source == "claude-code":
            heartbeat_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
            heartbeat = _parse_iso(heartbeat_raw) if isinstance(heartbeat_raw, str) else None
            if heartbeat is not None:
                age_seconds = (now - heartbeat).total_seconds()
                if age_seconds <= STALE_AGENT_TIMEOUT_SECONDS:
                    # →2488: refresh so _autocomplete_exited_subagents doesn't fire right after restart
                    meta["last_heartbeat_at"] = now.isoformat()
                    changed = True
                    continue

        # Case 2b: multi-signal liveness check for worktree agents (→1505).
        # After a backend restart, worktree agents are reparented to init so
        # _is_pid_my_child returns False even though the agent is alive and
        # working.  Before marking abandoned we check three independent signals;
        # if ANY contradicts, we set stale_heartbeat=True and leave the row as
        # running.  Only when ALL signals agree the agent is dead do we abandon.
        #
        # Signals checked (any truthy = keep running):
        #   A. PID is alive (even as orphan/reparented process)
        #   B. Worktree branch has commits since spawn (agent wrote real work)
        #   C. Transcript file is non-empty (agent produced output)
        if source == "claude-code":
            _keep_alive = False
            # Signal A: PID alive as orphan
            if pid and _is_pid_alive(pid):
                _keep_alive = True
            # Signal B: worktree commits ahead of main
            if not _keep_alive:
                _wt_branch = meta.get("worktree_branch")
                _wt_path = meta.get("worktree_path")
                if _wt_branch and _worktree_branch_has_commits(_wt_branch):
                    _keep_alive = True
                elif _wt_path and _worktree_has_new_work(_wt_path):
                    _keep_alive = True
            # Signal C: transcript has content
            if not _keep_alive:
                _t_source = _resolve_transcript_source(name)
                try:
                    if _t_source and _t_source.exists() and _t_source.stat().st_size > 0:
                        _keep_alive = True
                except OSError:
                    pass
            if _keep_alive:
                meta["stale_heartbeat"] = True
                meta["last_heartbeat_at"] = now.isoformat()  # →2488: refresh so autocomplete sweep doesn't fire
                changed = True
                continue

        # Case 2c (→1678): PID-liveness guard for non-claude-code backend spawns.
        # The Case 2b multi-signal check above only runs for source=="claude-code",
        # so backend-managed spawns (api/chat) fell straight through to Case 3
        # and got marked abandoned even when their PID was still alive and working.
        # That false-abandon is what triggers the respawn cascade.
        #
        # Differentiate by source (→1453 vs →1678 conflict):
        # - source="api"/"chat": autonomous backend processes that may survive
        #   restart reparented to init. Keep them alive even if not our child.
        # - source="ui": orphan PIDs from old backend run — reparented to init
        #   but their drain/heartbeat tasks are gone. Let these fall to Case 3.
        _is_api_autonomous = source in ("api", "chat")
        if pid and _is_pid_alive(pid) and (_is_pid_my_child(pid) or _is_api_autonomous):
            meta["stale_heartbeat"] = True
            meta["last_heartbeat_at"] = now.isoformat()  # →2488: refresh so autocomplete sweep doesn't fire
            changed = True
            continue

        # Case 3: backend-managed spawn (ui/api/chat) or stale claude-code
        # session with no liveness signals. Worker is dead. Mark abandoned so
        # the Active Sessions list does not show phantoms.
        # →2640 fix 6(b): build a reason, send SIGTERM to reap any zombie,
        # and record the outcome in terminated_reason so the user can see why.
        import signal as _signal
        _stale_reason = "backend restart: no liveness signals"
        if pid:
            try:
                os.kill(pid, _signal.SIGTERM)
                _stale_reason = f"backend restart: SIGTERM sent to pid={pid}"
            except ProcessLookupError:
                _stale_reason = f"backend restart: pid={pid} already dead"
            except OSError as _ke:
                _stale_reason = f"backend restart: kill pid={pid} error={_ke}"
        _set_agent_status(
            name, "abandoned",
            abandoned_at=now.isoformat(),
            terminated_reason=_stale_reason,
        )
        changed = True
    if changed:
        _save_agent_state()


# Minimum age (in seconds) for an agent to be eligible for bulk cancel.
# Agents spawned within this window are freshly launched and must not be
# wiped by a cancel-all call triggered by a sibling agent's test suite or
# by a UI click that races with the spawn. 30 seconds is long enough to
# cover the register + first heartbeat round-trip for every Claude Code
# subagent, but short enough that a genuinely unwanted agent is still
# stoppable in under a minute.
CANCEL_ALL_GRACE_SECONDS = 30


def _recover_bulk_cancelled_agents() -> bool:
    """Recover agents that were wrongly marked cancelled by a bulk cancel.

    A bulk cancel triggered by a sibling agent's test call (or a mis-timed
    UI click) can stamp 'cancelled' on agents that were actively working.
    The tell-tale sign: the agent sent a heartbeat AFTER its terminated_at
    timestamp, proving it was still alive and running when the cancel was
    applied.

    Rules (all must be true):
    1. status == "cancelled"
    2. terminated_reason == "bulk cancel"  (not an explicit user cancel)
    3. last_heartbeat_at > terminated_at   (agent was alive after cancel)

    Such rows are flipped to "completed" with a recovery summary.

    Returns True if any record was changed so the caller can persist once.
    """
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "cancelled":
            continue
        if meta.get("terminated_reason") != "bulk cancel":
            continue
        terminated_raw = meta.get("terminated_at")
        heartbeat_raw = meta.get("last_heartbeat_at")
        if not terminated_raw or not heartbeat_raw:
            continue
        terminated = _parse_iso(terminated_raw)
        heartbeat = _parse_iso(heartbeat_raw)
        if terminated is None or heartbeat is None:
            continue
        if heartbeat <= terminated:
            continue
        # Agent was alive after the bulk cancel - it was caught by accident.
        _set_agent_status(name, "completed", completed_at=heartbeat_raw, summary="Recovered after bulk cancel: agent was still active when cancelled")
        # Clear the cancellation fields so the row reads cleanly.
        meta.pop("terminated_at", None)
        meta.pop("terminated_reason", None)
        changed = True
        _emit_audit_event("agent.completed", {"name": name, "recovery": "bulk_cancel_regression"})
    return changed


# How long a workflow must have been in a terminal state before the reconcile
# pass auto-cancels its lingering step agents. Two minutes is enough to let a
# fast step-agent receive the direct cancel that run_workflow() issues first,
# while still catching genuinely orphaned rows.
_WORKFLOW_ORPHAN_GRACE_SECONDS = 120


def _reconcile_workflow_step_agents() -> bool:
    """Auto-cancel step agents whose parent workflow has already finished.

    Called at the top of GET /agents (cheap: only touches in-memory dicts).
    Rules:
    - Only affects agents that have ``workflow_run_id`` set.
    - Only affects agents that are still ``running``.
    - The parent workflow is looked up via ``services.workflows.get_workflow``.
      If the workflow no longer exists (deleted) OR its status is terminal
      (``done`` or ``failed``) AND ``completed_at`` is older than
      ``_WORKFLOW_ORPHAN_GRACE_SECONDS``, the step agent is cancelled.
    - A user-spawned agent that has no ``workflow_run_id`` is NEVER touched.

    Returns True if any record changed so the caller can persist once.
    """
    try:
        from services.workflows import get_workflow, WF_DONE, WF_FAILED
    except ImportError:
        return False

    _WF_TERMINAL = {WF_DONE, WF_FAILED}
    now = datetime.now(timezone.utc)
    changed = False

    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        wf_run_id = meta.get("workflow_run_id")
        if not wf_run_id:
            continue

        wf = get_workflow(wf_run_id)
        # Workflow was deleted entirely: treat as terminal.
        if wf is None:
            is_terminal = True
            completed_at = None
        else:
            wf_status = wf.get("status", "")
            is_terminal = wf_status in _WF_TERMINAL
            completed_at = wf.get("completed_at")

        if not is_terminal:
            continue

        # Give the direct-cancel path in run_workflow() a head start before
        # we sweep. If completed_at is missing or too recent, wait.
        if completed_at:
            comp_dt = _parse_iso(completed_at)
            if comp_dt is not None:
                age = (now - comp_dt).total_seconds()
                if age < _WORKFLOW_ORPHAN_GRACE_SECONDS:
                    continue

        _set_agent_status(name, "cancelled", terminated_at=now.isoformat(), terminated_reason="workflow ended")
        changed = True

    return changed


# Restore metadata from disk on startup, then recover any stale running agents.
agent_metadata.update(_load_agent_state())
# NOTE: _recover_stale_agents() depends on _resolve_transcript_source defined
# later in this module. The bootstrap call was moved to the end of the file so
# the definition exists when recovery runs. See call at file bottom.
# Recover agents wrongly cancelled by a bulk cancel that swept actively-running
# workers. This repairs the on-disk state immediately so the UI shows the correct
# status on the first GET /agents after a server restart.
if _recover_bulk_cancelled_agents():
    _save_agent_state()

# ---------------------------------------------------------------------------
# Spawn-lock TTL sweep
# ---------------------------------------------------------------------------

import fnmatch as _fnmatch
import os as _os

_LOCK_SWEEP_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "cancelled", "terminated_stale", "timeout",
    "abandoned", "stopped", "killed",
})

MYOS_LOCK_SWEEP_INTERVAL_S = int(
    _os.environ.get("MYOS_LOCK_SWEEP_INTERVAL_S", "300")
)

# Locks held by an agent whose status looks alive (running/pending/spawned)
# but whose last heartbeat is older than this threshold are treated as orphaned
# and released by the sweep. Default 600 s (10 min): a healthy agent heartbeats
# every 60 s so 600 s means 10 missed beats.
MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS = int(
    _os.environ.get("MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS", "600")
)


def _sweep_stale_locks_once() -> int:
    """Release spawn locks whose owning agent has reached a terminal status or gone missing.

    Returns the count of locks released. Safe to call from tests directly.
    Sweep never releases locks for agents in running/pending/spawned status.
    """
    from services.spawn_isolation import (
        _spawn_lock_holders,
        _spawn_lock_mutex,
        release_spawn_locks as _release_spawn_locks,
    )

    now = time.time()
    with _spawn_lock_mutex:
        snapshot = list(_spawn_lock_holders.items())

    released_count = 0
    for key, entry in snapshot:
        spawn_id, raw_glob, acquired_epoch = entry
        meta = agent_metadata.get(spawn_id)
        status = meta.get("status") if meta else None

        age = now - acquired_epoch

        if status in ("running", "pending", "spawned"):
            # Protect agents that have sent a heartbeat recently.
            # If heartbeat is stale (or absent and lock is old), fall through
            # to the should_release logic below so the lock can be swept.
            if meta is not None:
                last_hb_str = meta.get("last_heartbeat_at")
                if last_hb_str:
                    try:
                        last_hb_ts = datetime.fromisoformat(last_hb_str).timestamp()
                        if now - last_hb_ts < MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS:
                            continue  # heartbeat is fresh; agent is alive
                    except Exception:
                        continue  # parse error: be conservative, protect the lock
                else:
                    # No heartbeat field yet; protect young locks.
                    if age < MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS:
                        continue
                # Fall through: heartbeat is stale or lock is old with no heartbeat.
            # meta is None: fall through to the should_release checks below.

        should_release = False

        if meta is None:
            should_release = True
        elif status in _LOCK_SWEEP_TERMINAL_STATUSES:
            should_release = True
        elif status in ("running", "pending", "spawned"):
            # Reached only when heartbeat check fell through above (stale/absent HB).
            if age >= MYOS_ORPHAN_LOCK_NO_HEARTBEAT_SECONDS:
                should_release = True
        else:
            budget = float(meta.get("budget", "2.0") or "2.0")
            ttl = max(budget * 3600, 1800.0)
            if age > ttl:
                should_release = True

        if should_release:
            released = _release_spawn_locks(spawn_id=spawn_id)
            for _ in released:
                logger.info(
                    "swept stale lock: spawn=%s path=%s age=%ds",
                    spawn_id, raw_glob, int(age),
                )
                released_count += 1

    return released_count


async def _spawn_lock_sweep_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            _sweep_stale_locks_once()
        except Exception:
            pass
        await asyncio.sleep(MYOS_LOCK_SWEEP_INTERVAL_S)


async def schedule_spawn_lock_sweep() -> None:
    asyncio.create_task(_spawn_lock_sweep_loop())


async def schedule_build_queue_startup_drain() -> None:
    """Drain any comprehensive builds queued before the last backend restart.

    On restart _running resets to 0 while persisted queue entries survive.
    Without this, queued builds sit forever unless another build completes.
    Runs once, 5s after startup so other init tasks settle first (→2497).
    """
    async def _drain() -> None:
        await asyncio.sleep(5)
        try:
            from services.build_queue import drain_startup_queue as _drain_startup_queue
            from models.schemas import AgentSpawn as _AgentSpawnCls
            entries = _drain_startup_queue()
            for _entry in entries:
                try:
                    _nb = _AgentSpawnCls(**_entry.spawn_kwargs)
                    await spawn_agent(_nb, request=None)
                    logger.info(
                        "build_queue.startup_spawned spawn_id=%s", _entry.spawn_id
                    )
                except Exception as _exc:
                    logger.error(
                        "build_queue.startup_spawn_failed spawn_id=%s err=%s",
                        _entry.spawn_id, _exc,
                    )
                    try:
                        from services.build_queue import return_to_queue as _return_to_queue
                        _return_to_queue(_entry)
                    except Exception:
                        pass
        except Exception as _exc:
            logger.warning("build_queue.startup_drain_failed err=%s", _exc)

    asyncio.create_task(_drain())


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
    import tempfile
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"claude-{uid}"


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
# 60 s (not 30 s) — decoupled from _TRANSCRIPT_FLUSH_INTERVAL (25 s) so the
# two timers don't expire at the same moment and trigger a synchronized
# cold-rebuild spike on every flush cycle (→1192).
_RESOLVE_TTL_SECONDS = 60.0

# →1687: bound the per-request read storm for per_agent_transcript_bytes.
# The /api/agents handler annotates every returned row with this field by
# calling _get_per_agent_transcript_bytes, which scans the (now bloated)
# transcript tree from disk via _resolve_transcript_source_uncached. With
# many historical rows and frequent polling under agent activity, the
# handler re-scanned the whole tree N times per request and pegged the CPU
# file-read-bound. This TTL cache collapses repeated polls to one disk scan
# per agent per window. A terminal agent's transcript never changes, so it
# gets a long TTL; a running agent's file grows, so it gets a short one to
# keep the displayed size fresh. per_agent_transcript_bytes is a display
# field (→1549); stall/ghost detection uses transcript_bytes, computed in
# the snapshot enrich pass, so coarse freshness here is safe.
_per_agent_bytes_cache: dict[str, tuple[float, int]] = {}
_PER_AGENT_BYTES_TTL_RUNNING = 3.0
_PER_AGENT_BYTES_TTL_TERMINAL = 60.0


def _per_agent_bytes_ttl_for(name: str) -> float:
    meta = agent_metadata.get(name) or {}
    if meta.get("status") == "running":
        return _PER_AGENT_BYTES_TTL_RUNNING
    return _PER_AGENT_BYTES_TTL_TERMINAL


def _reset_per_agent_bytes_cache() -> None:
    """Test hook. Drop the in-memory per-agent transcript byte cache."""
    _per_agent_bytes_cache.clear()

# Serialize concurrent enrich passes in list_agents. Without this, multiple
# pollers (standing-rules hooks across many sessions) hit a cold resolve
# and candidates cache simultaneously, each thread duplicating the same
# 1108-file glob+readline scan and contending on shared dicts. Two threads
# can amplify the cold pass from ~5s to >120s. With the lock, only one
# pass runs at a time; once it warms the caches, every queued poller gets
# a near-zero pass.
# asyncio.Lock (not threading.Lock) so waiting callers suspend at coroutine
# level without consuming a thread-pool slot (→1144: thread-pool saturation).
_enrich_async_lock: asyncio.Lock = asyncio.Lock()

# →2018: serialize _autocomplete_exited_subagents and _sweep_stale_running_agents
# between the snapshot loop (500 ms) and _reconcile_loop (60 s). Both call these
# via asyncio.to_thread; concurrent GIL-heavy threads starve the event loop.
# With this lock only one sweep runs at a time.
_sweep_pass_lock: asyncio.Lock = asyncio.Lock()
# →2018: the 500 ms snapshot loop throttles its autocomplete pass to at most
# once per this interval so the GIL-heavy to_thread sweep does not starve the
# event loop. The per-request cold-cache path and tests still run autocomplete
# unconditionally (run_autocomplete=True) so dead agents flip to completed on
# read with no added delay.
_last_autocomplete_mono: float = 0.0
_AUTOCOMPLETE_MIN_INTERVAL: float = 5.0

# Fields in agent dicts whose string values may contain raw control bytes
# (e.g. from heartbeat step messages, spawn prompts, or shell output).
# json.dumps escapes these correctly in most cases, but certain code paths
# (direct file reads, subprocess output, MCP socket text blocks) can produce
# strings with literal U+0000–U+001F bytes that survive into the HTTP response.
# RFC 8259 requires control characters to be escaped; Python's strict json.loads
# rejects them. Sanitize these fields at the serialization boundary so the
# /api/agents response always round-trips through json.loads(strict=True).
_SANITIZE_FIELDS = frozenset({
    "name",
    "current_step",
    "task",
    "description",
    "summary",
    "prompt",
    "terminated_reason",
    "fail_reason",
    "label",
    "error",
    "last_step_output",
})


def sanitize_for_json(s: str) -> str:
    """Replace unescapable control characters in ``s`` with their \\uXXXX forms.

    Keeps tab (U+0009), newline (U+000A), and carriage return (U+000D) — the
    three control characters that are valid in JSON string values — and replaces
    every other character below U+0020 with its \\uXXXX Unicode escape.  This
    makes the string safe to include in a JSON payload regardless of how the
    surrounding serializer handles ``ensure_ascii``.
    """
    if not s:
        return s
    return "".join(
        c if ord(c) >= 0x20 or c in "\t\n\r" else f"\\u{ord(c):04x}"
        for c in s
    )


def _sanitize_for_json(value):
    """Sanitize a single value for JSON serialization.

    Strings: strips ASCII control chars (except tab, newline, carriage return)
    by replacing each with its \\uXXXX Unicode escape form so the output
    round-trips through ``json.loads(strict=True)``.
    Non-strings: returned unchanged.
    """
    if isinstance(value, str):
        return sanitize_for_json(value)
    return value


def _run_enrich_pipeline(
    all_agents: list,
    deleted_names: set,
    now_for_sweep: "datetime",
    user_spawned_filter: "Optional[Any]",
    filter_status: "Optional[str]",
    filter_source: "Optional[str]",
    limit: "Optional[int]",
) -> list:
    """Run the status-flip, filter, and enrich passes in a thread.

    Caller holds _enrich_async_lock so concurrent /api/agents callers don't
    duplicate the cold filesystem scan. Once the first pass warms the
    resolve and metrics caches every queued caller runs in milliseconds.
    All I/O here is synchronous and belongs in a thread (not in the
    event loop) so TLS handshakes for other endpoints are never blocked.
    Serialization is done by _enrich_async_lock in the caller; this
    function does not hold any threading.Lock (→1144).
    """
    # 1. Status flip for terminated_stale rows still active on disk.
    for agent in all_agents:
        if agent.get("status") == "terminated_stale" and _transcript_recently_active(
            agent["name"], now_for_sweep
        ):
            agent["status"] = "running"
    # 1b. Drop stale non-running rows from the response (→1151).
    # Running rows are always kept to avoid hiding a transiently slow heartbeat.
    # Non-running rows whose last_seen is older than _RESPONSE_STALE_SECONDS are
    # omitted from the serialized payload — they stay on disk unchanged.
    # Rows with no parseable timestamp are kept so malformed records don't vanish
    # silently.
    _stale_cutoff = now_for_sweep - timedelta(seconds=_RESPONSE_STALE_SECONDS)
    all_agents = [
        a for a in all_agents
        if a.get("status") == "running"
        or _last_seen_dt(a) is None
        or _last_seen_dt(a) >= _stale_cutoff
    ]
    # 2. Apply filters.
    filtered: list = [a for a in all_agents if a.get("name") not in deleted_names]
    if user_spawned_filter is not None:
        filtered = [a for a in filtered if user_spawned_filter(a)]
    if filter_status:
        filtered = [a for a in filtered if a.get("status") == filter_status]
    if filter_source:
        filtered = [a for a in filtered if a.get("source") == filter_source]
    if limit is not None and limit >= 0:
        # Newest-first so limit=N returns the N most recently spawned agents (→1238).
        filtered = sorted(filtered, key=lambda a: a.get("spawned_at") or "", reverse=True)[:limit]
    # 3. Enrich the filtered subset with transcript metrics and cost.
    # Skip transcript I/O for old stopped agents: cold-cache walk over
    # 1000+ entries takes 14s. Only running and recently-spawned agents
    # need live transcript byte/line counts.
    _enrich_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for agent in filtered:
        # Note: we used to call time.sleep(0) here to "yield GIL", but since
        # this function already runs in a thread via asyncio.to_thread, the
        # event loop is never blocked. Removed to reduce overhead (->2165).
        _status = agent.get("status", "")
        _spawned_at = agent.get("spawned_at") or ""
        _is_old_stopped = (
            bool(_spawned_at)
            and _status in ("stopped", "completed", "failed", "abandoned")
            and _spawned_at < _enrich_cutoff
        )
        if _is_old_stopped:
            agent.setdefault("transcript_bytes", 0)
            agent.setdefault("transcript_lines", 0)
            # Skip per-agent transcript I/O for old stopped agents — file is frozen.
            # Use setdefault so a prior enrichment value is preserved if already set.
            agent.setdefault("kernel_event_index", agent.get("transcript_bytes") or 0)
            agent.setdefault("per_agent_transcript_bytes", 0)
        else:
            metrics = _get_transcript_metrics(agent["name"])
            agent.update(metrics)
            # →1702: compute per_agent_transcript_bytes once at snapshot-build time,
            # not on every /api/agents request.
            agent["kernel_event_index"] = agent.get("transcript_bytes") or 0
            agent["per_agent_transcript_bytes"] = _get_per_agent_transcript_bytes_cached(agent["name"])
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
        agent["recovery_count"] = meta.get("recovery_count", 0)
        agent["max_recoveries"] = MAX_RECOVERY_ATTEMPTS
        # Sanitize string fields that may carry raw control bytes from
        # heartbeat step messages, spawn prompts, or external tool output.
        for field in _SANITIZE_FIELDS:
            val = agent.get(field)
            if isinstance(val, str):
                agent[field] = sanitize_for_json(val)
    return filtered


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


import re as _re
_INFERRED_CC_PATTERN = _re.compile(r"^claude-code-([0-9a-f]{8,}(?:-[0-9a-f]+)*)")


def _is_real_conversation_jsonl(path: Path) -> bool:
    """True only if the file's first non-empty line is a JSON object with a
    ``type`` key. This distinguishes real Claude Code conversation transcripts
    ({"type": "user", ...}) from ostk task-summary .output files which are
    plain text (e.g. "ready\\nregistered 30/30\\n...").
    """
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    return isinstance(obj, dict) and "type" in obj
                except (json.JSONDecodeError, ValueError):
                    return False
    except OSError:
        pass
    return False


def _pick_fresher(a: Optional[Path], b: Optional[Path]) -> Optional[Path]:
    """Return whichever path has the larger mtime. Ignores missing files.

    Used by the transcript resolver to prefer a live JSONL subagent file
    over a stale legacy .md stub from a prior run with the same slug.
    The stale sweep's liveness check uses the returned file's mtime, so
    returning the fresher candidate is what keeps a running subagent
    from being marked completed ~20s after spawn.
    """
    def _mtime(p: Optional[Path]) -> float:
        if p is None:
            return -1.0
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0

    a_t = _mtime(a)
    b_t = _mtime(b)
    if a_t < 0 and b_t < 0:
        return None
    return a if a_t >= b_t else b


def _resolve_transcript_source_uncached(name: str, skip_transcript_path: bool = False) -> Optional[Path]:
    """Resolve the on-disk transcript for an agent.

    Looks in several places and returns the first real hit:

    0. Inferred Claude Code session: if the name matches ``claude-code-<stem>``
       (auto-generated by the list endpoint from JSONL session file mtimes),
       look for the session JSONL file directly in
       ``~/.claude/projects/<label>/`` whose stem starts with the encoded stem.
       This is the correct transcript for these agents because the session file
       IS the conversation, not a spawned subagent file.
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

    # 0. Inferred Claude Code session (name = "claude-code-<jsonl-stem[:10]>").
    # The session JSONL lives at ~/.claude/projects/<label>/<uuid>.jsonl and is
    # the real transcript for these agents. We recover the stem prefix from the
    # name and scan the project dir for a matching file.
    m = _INFERRED_CC_PATTERN.match(name)
    if m:
        stem_prefix = m.group(1)
        projects_dir = _claude_code_projects_dir()
        project_label = str(PROJECT_ROOT).replace("/", "-").lstrip("-")
        project_dir = projects_dir / f"-{project_label}"
        if project_dir.exists():
            try:
                for candidate in project_dir.glob("*.jsonl"):
                    if candidate.stem.startswith(stem_prefix):
                        try:
                            if candidate.stat().st_size > 0:
                                return candidate
                        except OSError:
                            pass
            except OSError:
                pass

    # 1. Legacy markdown. Only trust it if it is not a tiny completion stub.
    #
    # IMPORTANT: We do NOT return the .md here even when it is "real", because
    # a prior run with the same slug (e.g., "diagnose-and-fix-slow-page-loads")
    # can leave behind a legacy .md that is no longer being written, while the
    # CURRENT subagent is live-writing its JSONL under
    # ``~/.claude/projects/<proj>/<session>/subagents/agent-*.jsonl``. The
    # stale sweep (Path A in ``_autocomplete_exited_subagents``) called
    # ``_transcript_grew_recently`` on the resolved source. If the resolver
    # returned the old .md, that mtime was cold and the running agent got
    # marked completed ~20s after spawn while its real JSONL was still
    # streaming. Fix: capture the non-stub .md as ``legacy_md`` and keep
    # walking. After we find the freshest JSONL candidate below, pick
    # whichever (legacy .md or JSONL) has the more recent mtime. That way a
    # live JSONL always wins over a stale .md from a prior run, and a legacy
    # .md still wins when nothing else exists.
    legacy_md: Optional[Path] = None
    md = PROJECT_ROOT / "transcripts" / f"{name}.md"
    if md.exists() and md.stat().st_size > 0:
        if _is_stub_markdown(md):
            stub_md = md
        else:
            legacy_md = md

    # 2. Per-agent JSONL recorded at register time.
    # For .output and .jsonl files, validate that the file is actually a real
    # conversation transcript (first line parses as JSON with a "type" key).
    # The autodiscover heuristic sometimes picks up tiny ostk task-summary
    # .output files ("ready\n...") that are plain text, not conversation data.
    # Returning those causes the endpoint to parse zero turns and return
    # "empty" instead of falling through to the subagent glob scan.
    # Markdown files (.md) are always trusted as-is because they are written
    # directly by the agent spawn path and cannot be misidentified.
    # skip_transcript_path=True bypasses this step so callers computing
    # per_agent_transcript_bytes can avoid returning the shared orchestrator
    # session JSONL that _link_session_jsonl may have stored here (→1549).
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path and not skip_transcript_path:
        candidate = Path(raw_path)
        if candidate.exists() and candidate.stat().st_size > 0:
            suffix = candidate.suffix.lower()
            if suffix in (".output", ".jsonl"):
                if _is_real_conversation_jsonl(candidate):
                    # Guard (→2893): a JSONL that lives directly in a Claude
                    # Code project dir (<projects_dir>/<label>/<uuid>.jsonl)
                    # is the orchestrator's own session, not this agent's.
                    # _link_session_jsonl stores it in transcript_path so
                    # byte-count metrics have something to read, but returning
                    # it from the transcript resolver causes the /transcript
                    # endpoint to show the wrong conversation.  Skip it; step
                    # 3 will find the real subagent JSONL in subagents/.
                    # Only .jsonl files need this guard: .output files are
                    # per-agent task scratch files, never shared session logs.
                    # Exemption: a path the CALLER explicitly registered
                    # (transcript_path_source == "caller") is trusted even
                    # when session-shaped — explicit beats heuristics (→2893).
                    if (
                        suffix == ".jsonl"
                        and _is_orchestrator_session_jsonl(candidate)
                        and meta.get("transcript_path_source") != "caller"
                    ):
                        pass  # orchestrator session JSONL — fall through
                    else:
                        return candidate
                # Not a real conversation file; fall through to glob scan.
            else:
                return candidate

    # 3. Scan Claude Code subagent JSONL files whose spawn prompt
    #    mentions this agent name. We restrict to the project dir
    #    matching PROJECT_ROOT so we do not surface a transcript
    #    from an unrelated repo.
    projects_dir = _claude_code_projects_dir()
    project_label = str(PROJECT_ROOT).replace("/", "-").lstrip("-")
    project_dir = projects_dir / f"-{project_label}"
    needle = name.lower()

    subagent_hit: Optional[Path] = None
    if project_dir.exists():
        # Subagent transcripts live at
        #   <project_dir>/<session-id>/subagents/agent-<id>.jsonl
        # so the pattern needs the ``*`` for the session-id directory.
        subagent_hit = _find_freshest_matching_jsonl(
            project_dir,
            needle,
            "*/subagents/agent-*.jsonl",
        )

    # 3b. Description-matched fallback.
    #
    # When the hook pre-registers a row it derives the name from the Task
    # tool's description slug (e.g. "e2e-demo-path-test-worktree") while
    # the subagent's prompt body hand-writes a different name (e.g.
    # "Name: e2e-demo-path-v2"). Step 3 strict-matches on the row's name
    # inside the prompt's first line, so that divergence returns None and
    # the Agents list reports 0 transcript bytes for an agent that is
    # actively writing hundreds of KB of JSONL.
    #
    # Claude Code writes a sibling ``agent-<id>.meta.json`` next to each
    # subagent JSONL that records the Task tool's ``description`` verbatim.
    # That description matches the agent row's ``description`` field 1:1
    # because both come from the same Task call. Falling back to a
    # meta.description match when strict-name fails rescues worktree-
    # isolated subagents and any other case where the user-written Name:
    # diverges from the description slug.
    if subagent_hit is None and project_dir.exists():
        row_description = (agent_metadata.get(name) or {}).get("description")
        if row_description:
            subagent_hit = _find_subagent_by_meta_description(
                project_dir, row_description
            )

    # 3c. Worktree project dir: when the agent ran in a git worktree, Claude Code
    # encodes the worktree path (not PROJECT_ROOT) into its project dir label, so
    # the JSONL lives under a different ~/.claude/projects/ subdir. Search it so
    # worktree-isolated Agent-tool subagents are found by the resolver, preventing
    # false-positive ghost detection (transcript_bytes=0 triggering "failed" status
    # while the agent is still actively writing its JSONL).
    if subagent_hit is None:
        _wt_path_str = (agent_metadata.get(name) or {}).get("worktree_path")
        if _wt_path_str:
            _wt_project_label = str(_wt_path_str).replace("/", "-").lstrip("-")
            _wt_project_dir = projects_dir / f"-{_wt_project_label}"
            if _wt_project_dir.exists() and _wt_project_dir != project_dir:
                subagent_hit = _find_freshest_matching_jsonl(
                    _wt_project_dir,
                    needle,
                    "*/subagents/agent-*.jsonl",
                )

    # Prefer whichever of (legacy_md, subagent_hit) has the fresher mtime.
    # This is the core fix for the "agent marked completed 20s after spawn"
    # bug: a live JSONL must beat a stale legacy .md from a prior run with
    # the same slug. If only one of the two exists, use that one.
    best = _pick_fresher(legacy_md, subagent_hit)
    if best is not None:
        return best

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
# endpoint at 1.7 seconds. Keyed on (root, pattern, root_dir_mtime_ns) so
# the cache stays warm as long as the directory has not changed, but busts
# immediately when a new subagent file appears. This means View Transcript
# picks up a freshly-spawned agent's file on the very next request rather
# than waiting for the old TTL to expire.
# Cache value tuple is (expires_at, candidates_list, name_index).
# - candidates_list: linear scan, used by callers that need full list/sort.
# - name_index: O(1) lookup keyed on _extract_agent_name(first_line) so the
#   common "match this agent name" path skips the linear pattern walk that
#   otherwise costs ~6s of GIL time when 162 agents × 826 candidates ×
#   12 patterns must be compared (→1192).
# →1272: key is (root, pattern) only — mtime was defeating the cache because any new
# session dir under ~/.claude/projects/<label>/ changed the root mtime and forced a full
# rescan of 1500+ session dirs on every /api/agents request. TTL alone is sufficient.
_candidates_cache: dict[tuple[str, str], tuple[float, list[tuple[float, Path, str]], dict[str, Path]]] = {}
# 60 s (not 30 s) — decoupled from _TRANSCRIPT_FLUSH_INTERVAL (25 s) so the
# two timers don't expire at the same moment and trigger a synchronized
# cold-rebuild spike on every flush cycle (→1192, morning fix).
_CANDIDATES_TTL_SECONDS = 60.0
# Cap glob to the N most recently modified session dirs (→1272).
# 50 covers several days of active sessions; cold scan stays well under 30s.
_MAX_SESSION_DIRS = 50


def _reset_candidates_cache() -> None:
    """Test hook. Drop the cached glob+first-line index."""
    _candidates_cache.clear()


def _dir_mtime_ns(path: Path) -> int:
    """Return the mtime_ns of ``path`` if it exists, else 0. Used as a cheap
    cache-busting key: when a new subagent file is written the parent
    directory mtime changes, so the cache key changes and we rescan.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _recent_session_dirs(root: Path, max_dirs: int) -> list[Path]:
    """Return the max_dirs most recently modified direct subdirectories of root.

    Used to cap glob scope when root has 1000+ session dirs — scanning all of
    them for subagent files is prohibitively slow (→1272).
    """
    try:
        subdirs: list[tuple[float, Path]] = []
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            try:
                subdirs.append((entry.stat().st_mtime, entry))
            except OSError:
                continue
        subdirs.sort(reverse=True)
        return [d for _, d in subdirs[:max_dirs]]
    except OSError:
        return []


def _load_candidates(root: Path, pattern: str) -> list[tuple[float, Path, str]]:
    """Return a cached list of ``(mtime, path, first_line_lower)`` tuples
    for every file under ``root`` matching ``pattern``.

    First call for a (root, pattern) pair does the real filesystem work
    (glob, stat, open + readline per file). Subsequent calls within the TTL
    return the cached list. The cache key is TTL-only (no mtime) so a new
    session dir under root does not invalidate the cache and force a 30s
    rescan of 1500+ session dirs (→1272).
    Sorted freshest-first so callers can stop at the first match.
    """
    import time as _time
    now = _time.monotonic()
    key = (str(root), pattern)
    entry = _candidates_cache.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]

    candidates: list[tuple[float, Path, str]] = []
    try:
        _i = 0
        # For "*/subagents/..." patterns, cap the scan to the _MAX_SESSION_DIRS
        # most recent session dirs instead of globbing all of root (→1272).
        if pattern.startswith("*/"):
            sub_pattern = pattern[2:]
            session_dirs = _recent_session_dirs(root, _MAX_SESSION_DIRS)
            paths: Iterable[Path] = (
                p for sd in session_dirs for p in sd.glob(sub_pattern)
            )
        else:
            paths = root.glob(pattern)
        for p in paths:
            _i += 1
            if _i % 10 == 0:
                import time as _t; _t.sleep(0)  # yield GIL every 10 iters (→1192)
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
    name_index: dict[str, Path] = {}
    for _mt, p, fl in candidates:
        name = _extract_agent_name(fl)
        if name and name not in name_index:
            name_index[name] = p
    _candidates_cache[key] = (now + _CANDIDATES_TTL_SECONDS, candidates, name_index)
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
    if _matches_saa_brief_shapes(first_line_lower, needle):
        return True
    return False


def _matches_saa_brief_shapes(first_line_lower: str, needle: str) -> bool:
    """→2936: saa spawn-brief shapes shared by the strict matchers.

    Real saa briefs carry the agent name in two shapes the classic patterns
    miss, so register-only agents (no log_path) resolved no own log and the
    Agents page fell back to 0 bytes or the 60-byte completion stub:

      1. ``Locks: [/tmp/<name>.log]`` — the agent's own lock file. The bare
         /tmp path additionally requires the ``locks:`` header somewhere in
         the line so a brief that merely tails another agent's log file
         does not steal attribution.
      2. ``register ... with name <name>`` — the register instruction
         ("POST .../api/agents/register (curl ...) with name X, then ...").
         Requires ``register`` in the line, and a non-name character after
         the needle so needle "saa" cannot prefix-match "saa-ledger-audit".

    First-line-only shapes: the first JSONL line is the spawn prompt, so
    these cannot be confused with later tool-result noise. Must stay in
    lockstep with the regexes in :func:`_extract_agent_name`.
    """
    if f"/tmp/{needle}.log" in first_line_lower and "locks:" in first_line_lower:
        return True
    if "register" in first_line_lower:
        token = f"with name {needle}"
        idx = first_line_lower.find(token)
        if idx != -1:
            nxt = first_line_lower[idx + len(token): idx + len(token) + 1]
            if not nxt or not (nxt.isalnum() or nxt in "._-"):
                return True
    return False


def _extract_agent_name(first_line_lower: str) -> Optional[str]:
    """Extract the agent name from a candidate's first line.

    Covers the same patterns as _first_line_matches_needle so the inverted
    index built in _load_candidates captures every name that the linear scan
    would have matched. Returns the name in lowercase, or None if no pattern
    fires (triggering the linear-scan fallback in _find_freshest_matching_jsonl).
    """
    if not first_line_lower:
        return None
    m = _re.search(r'"name"\s*:\s*"([^"\\]+)"', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r'\\"name\\"\s*:\s*\\"([^\\]+)\\"', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r'/agents/([^/"\s]+)/(?:complete|register)', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r'/api/agents/([^/"\s]+)/', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r'you are "([^"\\]+)"', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r'you are \\"([^\\]+)\\"', first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r"you are '([^']+)'", first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r"you are the (\S+) agent", first_line_lower)
    if m:
        return m.group(1).strip()
    m = _re.search(r"agent:\s*(\S+)", first_line_lower)
    if m:
        return m.group(1).strip()
    # →2936: weak shapes LAST so the strong patterns above keep priority.
    # Lockstep with _matches_saa_brief_shapes.
    m = _re.search(r"locks:\s*\[\s*/tmp/([^/\]\s,]+)\.log", first_line_lower)
    if m:
        return m.group(1).strip()
    if "register" in first_line_lower:
        m = _re.search(r"with name ([a-z0-9][a-z0-9._-]*)", first_line_lower)
        if m:
            return m.group(1).strip()
    return None


# project_dir_str -> (expires_at_monotonic, [(mtime, jsonl_path, description)])
# Same shape as ``_candidates_cache`` but keyed on description rather than
# first-line content. Used by the meta.description fallback path when the
# strict needle match fails. Key is TTL-only (no mtime) for same reason as
# _candidates_cache (→1272).
_meta_candidates_cache: dict[str, tuple[float, list[tuple[float, Path, str]]]] = {}
_META_CANDIDATES_TTL_SECONDS = 60.0  # 60 s — decoupled from flush interval (→1192)


def _reset_meta_candidates_cache() -> None:
    """Test hook. Drop the cached meta.json index."""
    _meta_candidates_cache.clear()


def _load_meta_candidates(project_dir: Path) -> list[tuple[float, Path, str]]:
    """Return a cached list of ``(mtime, jsonl_path, description)`` tuples
    for every ``agent-<id>.meta.json`` under ``project_dir``.

    Sorted freshest-first so callers can stop at the first match.
    Cache key is TTL-only — mtime was invalidating the cache on every new
    Claude Code session, forcing a full rescan of 1500+ session dirs (→1272).
    Scope is capped to the _MAX_SESSION_DIRS most recent session directories.
    """
    import time as _time
    now = _time.monotonic()
    key = str(project_dir)
    entry = _meta_candidates_cache.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]

    candidates: list[tuple[float, Path, str]] = []
    try:
        _i = 0
        session_dirs = _recent_session_dirs(project_dir, _MAX_SESSION_DIRS)
        for session_dir in session_dirs:
            for meta_path in session_dir.glob("subagents/agent-*.meta.json"):
                _i += 1
                if _i % 10 == 0:
                    import time as _t; _t.sleep(0)  # yield GIL every 10 iters (→1192)
                jsonl_path = meta_path.with_suffix("")  # strips ".json"
                if jsonl_path.suffix != ".meta":
                    continue
                jsonl_path = jsonl_path.with_suffix(".jsonl")
                try:
                    stat = jsonl_path.stat()
                except OSError:
                    continue
                try:
                    with open(meta_path, "r", errors="replace") as f:
                        meta_obj = json.load(f)
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(meta_obj, dict):
                    continue
                desc = meta_obj.get("description")
                if not isinstance(desc, str) or not desc.strip():
                    continue
                candidates.append((stat.st_mtime, jsonl_path, desc.strip()))
                if len(candidates) >= _MAX_GLOB_FILES:
                    break
            if len(candidates) >= _MAX_GLOB_FILES:
                break
    except OSError:
        candidates = []

    candidates.sort(key=lambda t: t[0], reverse=True)
    _meta_candidates_cache[key] = (now + _META_CANDIDATES_TTL_SECONDS, candidates)
    return candidates


def _find_subagent_by_meta_description(
    project_dir: Path,
    description: str,
) -> Optional[Path]:
    """Return the freshest ``agent-<id>.jsonl`` under ``project_dir`` whose
    sibling ``.meta.json`` ``description`` field matches ``description``
    exactly (case-insensitive, trimmed).

    Used as a fallback when the strict-needle match on the prompt's first
    line fails because the agent row's name (derived from the Task tool's
    description slug) does not match the ``Name:`` the user hand-wrote
    inside the prompt body. The description stored in the meta.json is
    the same string both the agent row and the Task call carry, so it
    disambiguates the name divergence cleanly.
    """
    target = description.strip().lower()
    if not target:
        return None
    for _mtime, jsonl_path, desc in _load_meta_candidates(project_dir):
        if desc.lower() == target:
            return jsonl_path
    return None


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

    Fast path: O(1) inverted-index lookup built by _load_candidates.
    Fallback: linear scan for the rare case _extract_agent_name cannot
    parse a name from the first line (e.g. unusual intro formats).
    """
    candidates = _load_candidates(root, pattern)
    key = (str(root), pattern)
    entry = _candidates_cache.get(key)
    if entry is not None:
        path = entry[2].get(needle_lower.strip())
        if path is not None:
            return path
    for _mtime, path, first_line_lower in candidates:
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
    if _matches_saa_brief_shapes(lowered, needle):
        return True
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


def _is_orchestrator_session_jsonl(path: Path) -> bool:
    """True if ``path`` is the orchestrator's own Claude Code session JSONL.

    Orchestrator sessions live at:
      ~/.claude/projects/<label>/<uuid>.jsonl   (direct child of the label dir)

    Subagent files live two levels deeper:
      ~/.claude/projects/<label>/<session>/subagents/agent-<id>.jsonl

    The test is simply: the file's grandparent is the projects dir.  Files at
    any other location were explicitly stored in transcript_path by the agent
    or auto-discovery and are per-agent, not the shared session.  Used by
    _resolve_transcript_source_uncached step 2 to skip the orchestrator file
    that _link_session_jsonl stores at register time (→2893).
    """
    if path.suffix.lower() != ".jsonl":
        return False
    projects_dir = _claude_code_projects_dir()
    return path.parent.parent == projects_dir


def _is_per_agent_transcript_path(path_str: str) -> bool:
    """True if transcript_path is a per-agent file, not the shared orchestrator session JSONL.

    Shared session JSONLs live directly under ~/.claude/projects/<label>/<uuid>.jsonl
    (no subdirectory). Per-agent files are either:
      - daemon-spawned .md files in transcripts/
      - subagent JSONLs under .../subagents/agent-*.jsonl
    """
    p = Path(path_str)
    if p.suffix.lower() == ".md":
        return True
    if p.suffix.lower() in (".jsonl", ".output") and "subagents" in p.parts:
        return True
    return False


def _resolve_own_log_path(name: str) -> Optional[Path]:
    """→2895: resolve the agent's OWN log file, never the shared session.

    Priority:
      1. A caller-registered transcript_path (transcript_path_source ==
         "caller") — explicit attribution beats every heuristic (→2893).
      2. transcript_path when it is a per-agent file (.md, or a JSONL /
         .output under a subagents/ dir) rather than the shared session
         link _link_session_jsonl stores.
      3. The per-agent resolver scan with skip_transcript_path=True (the
         same seam _get_per_agent_transcript_bytes uses).

    Returns None when nothing per-agent exists anywhere.
    """
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path and (
        meta.get("transcript_path_source") == "caller"
        or _is_per_agent_transcript_path(raw_path)
    ):
        candidate = Path(raw_path)
        try:
            if candidate.exists():
                return candidate
        except OSError:
            pass
    return _resolve_transcript_source_uncached(name, skip_transcript_path=True)


# TTL cache for _resolve_own_log_path (same idiom as _resolve_cache):
# heartbeats arrive every ~25-60s per agent and the uncached resolver
# walks the filesystem. Maps name -> (expires_monotonic, Optional[Path]).
_own_log_cache: dict = {}


def _resolve_own_log_path_cached(name: str) -> Optional[Path]:
    """TTL-cached wrapper around :func:`_resolve_own_log_path`."""
    import time as _time
    now = _time.monotonic()
    cached = _own_log_cache.get(name)
    if cached is not None and cached[0] > now:
        return cached[1]
    result = _resolve_own_log_path(name)
    _own_log_cache[name] = (now + _RESOLVE_TTL_SECONDS, result)
    return result



def _get_per_agent_transcript_bytes(name: str) -> int:
    """Return the on-disk byte count for THIS agent's own transcript only.

    Unlike transcript_bytes (which reflects the shared orchestrator session
    JSONL when _link_session_jsonl ran at register time), this always returns
    a per-agent count. Used by the per_agent_transcript_bytes API field (→1549).

    Priority:
      1. transcript_path if it's a per-agent file (.md or subagents/ JSONL)
      2. Resolver steps 3-5 (subagent JSONL scan), skipping the transcript_path
         shortcut that may point to the shared session file.
    """
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path and _is_per_agent_transcript_path(raw_path):
        try:
            p = Path(raw_path)
            if p.exists():
                return p.stat().st_size
        except OSError:
            pass
    # transcript_path absent or shared session JSONL — scan for per-agent file.
    source = _resolve_transcript_source_uncached(name, skip_transcript_path=True)
    if source is None:
        return 0
    try:
        return source.stat().st_size
    except OSError:
        return 0


def _get_per_agent_transcript_bytes_cached(name: str) -> int:
    """TTL-cached wrapper around :func:`_get_per_agent_transcript_bytes` (→1687).

    The /api/agents handler calls this once per returned row. Without the
    cache, each poll re-scanned the transcript tree from disk for every
    agent, which saturated the CPU under agent activity. The TTL is
    status-aware: running agents refresh quickly, terminal agents (whose
    transcript is frozen) are cached for a minute.
    """
    now = time.monotonic()
    cached = _per_agent_bytes_cache.get(name)
    if cached is not None and cached[0] > now:
        return cached[1]
    value = _get_per_agent_transcript_bytes(name)
    _per_agent_bytes_cache[name] = (now + _per_agent_bytes_ttl_for(name), value)
    return value


def _fill_transcript_bytes(agents: list) -> None:
    """Populate per_agent_transcript_bytes for rows missing it (sync, safe to thread)."""
    for _ar in agents:
        if "per_agent_transcript_bytes" not in _ar:
            _ar_name = _ar.get("name", "")
            _ar["kernel_event_index"] = _ar.get("transcript_bytes") or 0
            _ar["per_agent_transcript_bytes"] = _get_per_agent_transcript_bytes_cached(_ar_name)


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
                    # Skip auto-injected runtime scaffolding (system
                    # reminders, image-source placeholders, wakeup pings).
                    # Claude Code marks these ``isMeta: true``. Without
                    # this skip the Agents "View transcript" modal repeats
                    # the same Claude Code opening banner dozens of times
                    # on scroll-up in a long session.
                    if entry.get("isMeta") is True:
                        continue
                    if isinstance(content, str) and content.strip():
                        line_text = "User: " + content.strip()
                        # Collapse consecutive identical user lines to one.
                        if not parts or parts[-1] != line_text:
                            parts.append(line_text)
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

    When no transcript exists, returns 200 with ``"empty": true`` and a
    human-readable ``"reason"`` string so the UI can display a helpful
    message instead of treating a missing transcript as a network error.
    """
    # Basic safety: reject path traversal.
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid agent name")

    # →2893: never serve a transcript_path that points at a shared top-level
    # session JSONL. _link_session_jsonl stores the freshest session file
    # there at register time (for byte-count metrics), and for a
    # harness-spawned subagent that file is the ORCHESTRATOR'S conversation.
    # The resolver's transcript_path shortcut used to return it before the
    # subagents/agent-*.jsonl scan ever ran, so this endpoint served someone
    # else's transcript. Only a caller-provided path (transcript_path_source
    # == "caller") may override the refusal; otherwise resolve strictly
    # per-agent and 404 when nothing can be attributed to this agent.
    # Wrong data is worse than no data.
    _meta = agent_metadata.get(name) or {}
    _raw_tp = _meta.get("transcript_path") or ""
    _linked_shared_session = (
        bool(_raw_tp)
        and _meta.get("transcript_path_source") != "caller"
        and _is_orchestrator_session_jsonl(Path(_raw_tp))
    )
    if _linked_shared_session:
        source = await asyncio.to_thread(
            _resolve_transcript_source_uncached, name, skip_transcript_path=True
        )
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No transcript can be attributed to agent '{name}'. Its "
                    "recorded transcript path points at a shared session log "
                    "written by the conversation that spawned it, and no "
                    "per-agent log (subagents/agent-*.jsonl) matches this "
                    "agent. Refusing to return another session's transcript."
                ),
            )
    else:
        source = _resolve_transcript_source(name)
    if source is None:
        meta = agent_metadata.get(name) or {}
        status = meta.get("status", "")
        terminated_reason = meta.get("terminated_reason", "")
        if status == "cancelled" or terminated_reason == "bulk cancel":
            reason = (
                f"This agent was cancelled before it wrote a transcript."
            )
        elif status in ("completed", "failed"):
            reason = (
                f"No transcript was recorded. This agent may have been registered "
                f"by an external tool that does not write transcripts."
            )
        else:
            reason = (
                f"No transcript yet. If the agent just started, check back in a few seconds."
            )
        return {"name": name, "content": "", "bytes": 0, "empty": True, "reason": reason}

    try:
        actual_bytes = source.stat().st_size
    except OSError:
        actual_bytes = 0

    suffix = source.suffix.lower()
    try:
        if suffix in (".output", ".jsonl") or _looks_like_jsonl(source):
            content = _format_jsonl_transcript(source)
        else:
            content = source.read_text(errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read transcript: {exc}") from exc

    if not content:
        meta = agent_metadata.get(name) or {}
        status = meta.get("status", "")
        if status == "running":
            reason = f"Agent '{name}' is still starting up. Check back in a moment."
        else:
            reason = f"Transcript for '{name}' is empty."
        return {"name": name, "content": "", "bytes": actual_bytes, "empty": True, "reason": reason}

    return {"name": name, "content": content, "bytes": actual_bytes, "empty": False}


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


@router.get("/agents/duration-stats")
async def agent_duration_stats():
    """Return median/p75 completed-agent durations (seconds) over the
    last 14 days. Used by the Agents UI to compute "~Nm left" estimates
    from real historical data instead of a hard-coded guess.

    Result is cached for 60 seconds in-process so frontend polls do not
    re-scan the state file on every tick.
    """
    from services.agent_duration_stats import get_duration_stats
    return get_duration_stats()


async def _compute_agents_snapshot_async(run_autocomplete: bool = True) -> dict:
    """Build the full unfiltered, enriched agent list.

    Called by the background snapshotter every 500 ms (→1219). All the
    expensive work — kernel_ps, audit_agents, 3-pass merge, PID-death
    reconcile, stale sweep, auto-complete, recovery, workflow reconcile,
    transcript-based CC-session inference, and enrich pipeline — runs
    here rather than on the request path.
    """
    global _scan_agents_processed
    _scan_agents_processed = 0
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _prune_stale_completed_agents)
    await loop.run_in_executor(None, _prune_reaped_worktree_agents)

    # Always read .ostk/agents.jsonl directly via registry_reader.
    # kernel_ps() scans the ostk daemon's gen_table on every call; with
    # 2000+ modified-file entries that costs 10+ seconds and caused the
    # watchdog to falsely SIGKILL uvicorn (→1379).
    # registry_reader reads the 38-line agents.jsonl (≤5.5 KB, mtime-cached)
    # and returns the same shape as kernel_ps(). agent_metadata overlays
    # all yourOS-specific fields (task, model, budget, source, etc.) in passes
    # 2b/2c below, so nothing is lost.
    from services.registry_reader import read_registry_for_snapshot as _rfs
    ps_result = await loop.run_in_executor(None, _rfs)
    audit_agents_list = await ostk.audit_agents()
    daemon_running = ps_result.get("daemon_running", False)
    daemon_agent_names = {a["name"] for a in ps_result.get("agents", [])}
    deleted_names = _load_deleted_agents()

    agents_map: dict[str, dict] = {}

    # 1. Audit log agents (lowest priority, background context)
    from config import PROJECT_ROOT
    _audit_i = 0
    for agent in audit_agents_list:
        _audit_i += 1
        _scan_agents_processed += 1
        if _audit_i % 10 == 0:
            await asyncio.sleep(0)
        if agent.get("status") in ("spawned", "running"):
            if not daemon_running or agent["name"] not in daemon_agent_names:
                if agent["name"] not in active_agents:
                    transcript = PROJECT_ROOT / "transcripts" / f"{agent['name']}.md"
                    if transcript.exists() and transcript.stat().st_size > 0:
                        agent = {**agent, "status": "completed"}
                    else:
                        agent = {**agent, "status": "stopped"}
        agents_map[agent["name"]] = agent

    # 2. In-memory agents (spawned via API this session)
    for name in list(active_agents.keys()):
        _scan_agents_processed += 1
        proc = active_agents[name]
        meta = agent_metadata.get(name, {})
        if hasattr(proc, 'returncode') and proc.returncode is not None:
            del active_agents[name]
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
            _TERMINAL_FROM_META = {
                "cancelled", "failed", "terminated_stale",
                "killed", "stopped", "abandoned",
                "completed_timeout", "completed",
            }
            persisted = meta.get("status", "")
            effective_status = persisted if persisted in _TERMINAL_FROM_META else "running"
            agents_map[name] = {
                "name": name,
                "source": "api",
                **meta,
                "status": effective_status,
            }

    # 2b. Persisted metadata (agents from previous server sessions).
    persisted_pass_changed = False
    _meta_i = 0
    # →1660: materialize items() before iterating. This loop awaits
    # (asyncio.sleep(0)) partway through, which yields to the event loop;
    # a concurrent agent register/heartbeat then mutates agent_metadata and
    # the resumed iteration raises "dictionary changed size during iteration",
    # which starves the snapshot loop and wedges /api/agents under load.
    for name, meta in list(agent_metadata.items()):
        _meta_i += 1
        _scan_agents_processed += 1
        if _meta_i % 10 == 0:
            await asyncio.sleep(0)
        if name in active_agents:
            continue
        _AUTHORITATIVE_STATUSES = {
            "running", "completed",
            "cancelled", "failed", "terminated_stale", "killed", "stopped", "abandoned",
            "completed_timeout",
        }
        persisted_status_check = meta.get("status")
        if name in agents_map and persisted_status_check in _AUTHORITATIVE_STATUSES:
            override_pid = meta.get("pid")
            if (
                persisted_status_check == "running"
                and override_pid
                and not _is_pid_alive(int(override_pid))
            ):
                now_iso = datetime.now(timezone.utc).isoformat()
                _set_agent_status(name, "completed",
                                  completed_at=now_iso,
                                  completion_reason="PID exited (list endpoint reconciled on read)")
                persisted_pass_changed = True
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "completed",
                }
                continue
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
            }
            continue
        if name in agents_map:
            meta_source = meta.get("source")
            if meta_source and meta_source != "audit":
                agents_map[name] = {**agents_map[name], "source": meta_source}
            continue
        pid = meta.get("pid")
        is_registered = meta.get("source") == "claude-code"
        persisted_status = meta.get("status")
        if persisted_status == "completed":
            agents_map[name] = {
                "name": name,
                "source": meta.get("source", "api"),
                **meta,
                "status": "completed",
            }
        elif persisted_status == "running":
            pid_for_check = meta.get("pid")
            if pid_for_check and not _is_pid_alive(int(pid_for_check)):
                now_iso = datetime.now(timezone.utc).isoformat()
                _set_agent_status(name, "completed",
                                  completed_at=now_iso,
                                  completion_reason="PID exited (list endpoint reconciled on read)")
                persisted_pass_changed = True
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "completed",
                }
                continue
            # Worktree-reaped fast path: worktree dir gone and no live PID → terminated.
            _wt_path = meta.get("worktree_path")
            if _wt_path and not Path(_wt_path).exists() and not pid_for_check:
                now_iso = datetime.now(timezone.utc).isoformat()
                _set_agent_status(
                    name, "terminated_stale",
                    terminated_at=now_iso,
                    terminated_reason="reaped: worktree dir gone and PID dead",
                )
                persisted_pass_changed = True
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "terminated_stale",
                }
                continue
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
                _set_agent_status(name, "terminated_stale",
                                  terminated_at=now_iso,
                                  terminated_reason=(
                                      "Running with no heartbeat for over 20 minutes "
                                      "(legacy record, swept by list endpoint)"
                                  ))
                persisted_pass_changed = True
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
        elif persisted_status in (
            "terminated_stale", "cancelled", "failed", "killed", "stopped",
            "completed_timeout",
        ):
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
            transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
            if transcript.exists() and transcript.stat().st_size > 0:
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "completed",
                }
            elif is_registered:
                spawned_at_str = meta.get("spawned_at", "")
                is_stale = False
                if spawned_at_str:
                    try:
                        spawned_at = datetime.fromisoformat(spawned_at_str.replace("Z", "+00:00"))
                        age_seconds = (datetime.now(timezone.utc) - spawned_at).total_seconds()
                        is_stale = age_seconds > 1200
                    except (ValueError, TypeError):
                        pass
                if is_stale:
                    _set_agent_status(name, "abandoned")
                    persisted_pass_changed = True
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

    # 4. Kernel fleet
    try:
        _kernel_rows = _read_kernel_fleet(
            socket_agents_path=OSTK_DIR / "agents.jsonl"
        )
        for _krow in _kernel_rows:
            _kname = _krow.get("name")
            if not _kname or _kname in deleted_names:
                continue
            if _kname not in agents_map:
                agents_map[_kname] = _krow
            else:
                _existing = agents_map[_kname]
                if _existing.get("status") not in _TERMINAL_STATUSES:
                    _k_status = _krow.get("status")
                    if _k_status in ("running", "completed", "failed", "killed"):
                        agents_map[_kname] = {**_existing, "status": _k_status}
                        _existing = agents_map[_kname]
                _k_hb = _krow.get("last_heartbeat_at") or ""
                _e_hb = _existing.get("last_heartbeat_at") or ""
                if _k_hb > _e_hb:
                    agents_map[_kname]["last_heartbeat_at"] = _k_hb
    except Exception:
        logger.debug("kernel_fleet read failed", exc_info=True)

    # Sweep: mark running agents with no recent heartbeat as terminated_stale.
    sweep_changed = False
    now_for_sweep = datetime.now(timezone.utc)
    _sweep_i = 0
    for name, agent in agents_map.items():
        _sweep_i += 1
        if _sweep_i % 10 == 0:
            await asyncio.sleep(0)
        if agent.get("status") != "running":
            continue
        last_heartbeat_raw = agent.get("last_heartbeat_at")
        if not isinstance(last_heartbeat_raw, str):
            continue
        last_seen = _parse_iso(last_heartbeat_raw)
        if last_seen is None:
            continue
        age_seconds = (now_for_sweep - last_seen).total_seconds()
        source = agent.get("source") or (agent_metadata.get(name) or {}).get("source")
        is_cc_subagent = source == "claude-code"
        threshold = (
            STALE_CLAUDE_CODE_SUBAGENT_SECONDS
            if is_cc_subagent
            else STALE_AGENT_TIMEOUT_SECONDS
        )
        if age_seconds <= threshold:
            continue
        if _proc_handle_is_alive(name):
            continue
        # →2896: the stored pid is ground truth (same rule →2659 added to
        # the other sweeps). A busy agent mid long tool call cannot
        # heartbeat, but its process is demonstrably alive; HTTP silence
        # alone must never demote it.
        _sweep_pid = agent.get("pid") or (agent_metadata.get(name) or {}).get("pid")
        if _sweep_pid:
            try:
                if _is_pid_alive(int(_sweep_pid)):
                    continue
            except (TypeError, ValueError):
                pass
        if _transcript_recently_active(name, now_for_sweep):
            continue
        # →2956: same evidence standard as _sweep_stale_running_agents.
        # A heartbeat-contract row (real steps seen) is never demoted on
        # heartbeat silence alone — this 480s path is what closed
        # saa-2944/2945/2946 mid-run while they sat inside long test runs
        # with no pid on record and an unresolvable (isolated workspace,
        # 0-byte counter) transcript.
        _gate_meta = agent_metadata.get(name) or agent
        if _has_heartbeat_contract(_gate_meta):
            _allow_flip, _gate_detail = _stale_flip_evidence(
                name, _gate_meta, now_for_sweep
            )
            if not _allow_flip:
                continue
        terminated_at = now_for_sweep.isoformat()
        demoted_status = "completed_timeout" if is_cc_subagent else "terminated_stale"
        reason = (
            f"No heartbeat for {int(age_seconds)}s "
            f"(limit {threshold}s)"
        )
        agent["status"] = demoted_status
        agent["terminated_at"] = terminated_at
        agent["terminated_reason"] = reason
        # →2896: sweep demotions are inferences; stamp them revivable so a
        # real heartbeat arriving later flips the row back to running.
        agent["flagged_by"] = "stale_sweep"
        if is_cc_subagent:
            agent["completed_at"] = terminated_at
        meta = agent_metadata.get(name)
        if meta is not None:
            _set_agent_status(name, demoted_status,
                              terminated_at=terminated_at,
                              terminated_reason=reason,
                              flagged_by="stale_sweep")
            if is_cc_subagent:
                meta["completed_at"] = terminated_at
            sweep_changed = True
    if sweep_changed or persisted_pass_changed:
        await _save_agent_state_async()

    # →2018: serialize autocomplete with _reconcile_loop's sweep via
    # _sweep_pass_lock so two GIL-heavy asyncio.to_thread passes never run
    # concurrently (that contention returned HTTP 000). The 500 ms snapshot
    # loop passes run_autocomplete=False on most ticks (it throttles to once
    # per _AUTOCOMPLETE_MIN_INTERVAL) so it does not starve the loop; the
    # cold-cache request path and tests use the default True so dead agents
    # flip on read. Skip when a sweep already holds the lock (no pile-up).
    ac_changed = False
    if run_autocomplete and not _sweep_pass_lock.locked():
        async with _sweep_pass_lock:
            ac_changed = await asyncio.to_thread(_autocomplete_exited_subagents)
    if ac_changed:
        await _save_agent_state_async()
        await _event_bus.publish(AGENT_SWEEP, {})
        for name, meta in agent_metadata.items():
            if meta.get("status") == "completed" and name in agents_map:
                if agents_map[name].get("status") == "running":
                    agents_map[name]["status"] = "completed"
                    agents_map[name]["completed_at"] = meta.get("completed_at", "")
    # Drain needle-close queue (→2207) — runs unconditionally so items queued
    # by _autocomplete_exited_subagents are never stranded here.
    _snap_needle_closes = _pending_needle_closes[:]
    _pending_needle_closes.clear()
    for _nid in _snap_needle_closes:
        asyncio.create_task(_close_task_for_autocomplete(_nid))

    rc_changed = _recover_bulk_cancelled_agents()
    if rc_changed:
        await _save_agent_state_async()
        for name, meta in agent_metadata.items():
            if meta.get("status") == "completed" and name in agents_map:
                if agents_map[name].get("status") == "cancelled":
                    agents_map[name]["status"] = "completed"
                    agents_map[name]["completed_at"] = meta.get("completed_at", "")
                    agents_map[name].pop("terminated_at", None)
                    agents_map[name].pop("terminated_reason", None)

    wf_changed = _reconcile_workflow_step_agents()
    if wf_changed:
        await _save_agent_state_async()
        for name, meta in agent_metadata.items():
            if meta.get("status") == "cancelled" and meta.get("terminated_reason") == "workflow ended":
                if name in agents_map and agents_map[name].get("status") == "running":
                    agents_map[name]["status"] = "cancelled"
                    agents_map[name]["terminated_at"] = meta.get("terminated_at", "")
                    agents_map[name]["terminated_reason"] = "workflow ended"

    # Merge live Claude Code sessions inferred from transcript file mtimes.
    loop = asyncio.get_running_loop()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    await loop.run_in_executor(None, _infer_cc_sessions, agents_map, cutoff)

    all_agents = list(agents_map.values())

    async with _enrich_async_lock:
        enriched = await asyncio.to_thread(
            _run_enrich_pipeline,
            all_agents,
            deleted_names,
            now_for_sweep,
            None,   # user_spawned_filter — caller applies per-request
            None,   # filter_status
            None,   # filter_source
            None,   # limit
        )

    return {
        "daemon_running": daemon_running,
        "status": sanitize_for_json(ps_result.get("raw", "unknown")),
        "agents": enriched,
        "avg_min_per_dollar": _avg_minutes_per_dollar(),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _agents_snapshot_loop() -> None:
    """Background task: refresh the agent snapshot every 500 ms (→1219).

    The snapshot itself refreshes every 500 ms, but the GIL-heavy autocomplete
    sweep is throttled to once per _AUTOCOMPLETE_MIN_INTERVAL here so the 2/sec
    cadence does not starve the event loop (→2018). The reconcile loop (60 s)
    is the slower backstop for the same sweep.

    →2224: each scan is wrapped in a _SCAN_TIMEOUT_SECONDS hard timeout so a
    wedged scan cannot block the loop forever.
    →2225: when the timeout fires, logs the ISO timestamp and how many agents
    were processed before cancellation.
    →2226: if the previous scan is still in flight, the current cycle marks
    itself skipped instead of queuing behind it.
    """
    global _cached_snapshot, _last_autocomplete_mono, _snapshot_scan_active
    while True:
        try:
            # →2226: skip this cycle if the previous scan hasn't finished.
            if _snapshot_scan_active:
                logger.warning(
                    "scan.skipped ts=%s reason=previous_scan_running",
                    datetime.now(timezone.utc).isoformat(),
                )
                await asyncio.sleep(0.5)
                continue

            _snapshot_scan_active = True
            try:
                now = asyncio.get_running_loop().time()
                do_autocomplete = (now - _last_autocomplete_mono) >= _AUTOCOMPLETE_MIN_INTERVAL
                # →2224: cancel the scan if it exceeds the hard timeout.
                snapshot = await asyncio.wait_for(
                    _compute_agents_snapshot_async(run_autocomplete=do_autocomplete),
                    timeout=_SCAN_TIMEOUT_SECONDS,
                )
                if do_autocomplete:
                    _last_autocomplete_mono = now
                async with _snapshot_lock:
                    _cached_snapshot = snapshot
            except asyncio.TimeoutError:
                # →2225: log timestamp and how many agents were reached before cancel.
                logger.warning(
                    "scan.timeout ts=%s agents_processed=%d timeout_s=%.1f",
                    datetime.now(timezone.utc).isoformat(),
                    _scan_agents_processed,
                    _SCAN_TIMEOUT_SECONDS,
                )
            except Exception:
                logger.exception("agents snapshotter iteration failed")
            finally:
                _snapshot_scan_active = False
        except Exception:
            logger.exception("agents snapshotter outer loop failed")
            _snapshot_scan_active = False
        await asyncio.sleep(0.5)


async def _merge_debt_tick_loop() -> None:
    """Background task: refresh merge-debt count every 60 s (→1555).

    Runs ``services.merge_debt.scan_merge_debt`` in a thread so the git
    subprocess calls don't block the event loop.
    """
    global _cached_merge_debt
    while True:
        try:
            from services.merge_debt import scan_merge_debt
            result = await asyncio.get_running_loop().run_in_executor(
                None, scan_merge_debt
            )
            async with _merge_debt_lock:
                _cached_merge_debt = result
        except Exception:
            logger.exception("merge_debt_tick_loop iteration failed")
        await asyncio.sleep(60)


def _infer_cc_sessions(agents_map: dict, cutoff: datetime) -> None:
    """Scan JSONL session files and inject inferred CC session rows.

    This runs in a thread (via run_in_executor) to avoid blocking the event
    loop with synchronous glob/stat calls (->2165).
    """
    try:
        from pathlib import Path as _Path
        from config import PROJECT_ROOT as _PROJECT_ROOT
        projects_dir = _Path.home() / ".claude" / "projects" / str(_PROJECT_ROOT).replace("/", "-")
        if not projects_dir.is_dir():
            return
        for jsonl in projects_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                continue
            inferred_name = f"claude-code-{jsonl.stem[:10]}"
            if inferred_name in agents_map:
                continue
            agents_map[inferred_name] = {
                "name": inferred_name,
                "source": "claude-code",
                "status": "running",
                "spawned_at": mtime.isoformat(),
                "last_heartbeat_at": mtime.isoformat(),
                "model": "claude-code",
                "budget": "0",
                "description": "Claude Code session (inferred from transcript mtime)",
            }
    except Exception:
        pass


@router.get("/agents")
async def list_agents(
    user_spawned_only: bool = False,
    summary: int = 0,
    filter_status: Optional[str] = Query(None, alias="status"),
    filter_source: Optional[str] = Query(None, alias="source"),
    limit: Optional[int] = None,
):
    """List every agent known to yourOS.

    Pass ``user_spawned_only=true`` to get just the rows the Agents page
    shows. This applies the same filter as ``isUserSpawnedAgent`` in
    ``app/src/lib/agentUtils.ts`` and lives in
    ``services.agent_filters.is_user_spawned_agent`` so CLI status loops
    and shell scripts never have to re-implement it.

    Compact-mode params (for hook polling; kept backwards-compatible,
    no params = full behavior):
      - ``summary=1``: return a trimmed ``agents`` array containing only
        ``{name, source, status, spawned_at, transcript_bytes, last_heartbeat_at}``.
        Drops transcripts, budget_details, tokens_used, cost_estimate, etc.
        Full-response fields ``daemon_running`` and ``active`` are omitted
        so the payload stays under ~5KB even with a few dozen rows.
      - ``status=<str>``: server-side filter on the final ``status`` field
        (e.g. ``running``). Applied after all merge/sweep passes so the
        caller sees the same effective status the UI would. Exposed as
        the ``status`` query-string key but bound internally to
        ``filter_status`` so it does not collide with FastAPI's
        status-code handling.
      - ``source=<str>``: server-side filter on ``source``
        (e.g. ``claude-code``). Bound internally to ``filter_source``
        for the same reason.
      - ``limit=<n>``: cap the returned agent list at N rows (applied last,
        after filters and sort-by-spawned_at so the oldest rows win).
    """
    # Read from the background snapshot cache (→1219). Latency: <10 ms.
    async with _snapshot_lock:
        snapshot = dict(_cached_snapshot)
    if snapshot.get("computed_at") is None:
        # Cold cache: single-flight the compute so concurrent cold-cache polls
        # (dashboard storm at startup) collapse into ONE compute instead of
        # each running its own, exhausting the thread pool and wedging the
        # event loop (→1687/→1738). Double-checked: a prior holder may have
        # filled the cache while we waited on the compute lock.
        async with _snapshot_compute_lock:
            async with _snapshot_lock:
                snapshot = dict(_cached_snapshot)
            if snapshot.get("computed_at") is None:
                computed = await _compute_agents_snapshot_async()
                async with _snapshot_lock:
                    _cached_snapshot.update(computed)
                    snapshot = dict(_cached_snapshot)
    all_agents = list(snapshot.get("agents", []))
    # Overlay live statuses from agent_metadata (snapshot is up to 500ms stale).
    # This ensures /complete and other status mutations are immediately visible.
    # Exception: don't let agent_metadata "running" overwrite a terminal status
    # that the kernel fleet computed in the snapshot (e.g., kernel says completed
    # but metadata still shows running from before the kernel check ran).
    snapshot_names: set = set()
    for _a in all_agents:
        _n = _a.get("name")
        snapshot_names.add(_n)
        _live = agent_metadata.get(_n)
        if _live is not None:
            _live_status = _live.get("status")
            if _live_status is not None:
                _snap_status = _a.get("status")
                if _live_status != "running" or _snap_status not in _TERMINAL_STATUSES:
                    _a["status"] = _live_status
    # Include agents registered since the last snapshot run.
    # Sanitize string fields here because these rows bypass _run_enrich_pipeline.
    for _n, _meta in list(agent_metadata.items()):
        if _n not in snapshot_names:
            _row = dict(_meta, name=_n)
            for _f in _SANITIZE_FIELDS:
                _v = _row.get(_f)
                if isinstance(_v, str):
                    _row[_f] = sanitize_for_json(_v)
            all_agents.append(_row)
    daemon_running = snapshot.get("daemon_running", False)
    deleted_names = await asyncio.to_thread(_load_deleted_agents)
    agents = [a for a in all_agents if a.get("name") not in deleted_names]
    # →2539: Dedupe helper spawns. When a working agent spawns helpers via
    # the Agent tool, _link_session_jsonl links every helper to the same
    # parent session JSONL (freshest *.jsonl in the project dir at register
    # time). All those helper rows end up pointing at the same transcript.
    # Fix: for each non-per-agent transcript_path, find the oldest running
    # agent — the parent. Any running agent that shares the same path but
    # registered later is a helper spawn. Tag it with is_helper_spawn=True
    # so is_user_spawned_agent can filter it from the Agents page.
    # Terminal agents are never suppressed so history is preserved.
    _shared_path_earliest: dict[str, str] = {}
    for _a in agents:
        _tp = _a.get("transcript_path") or ""
        if not _tp or _is_per_agent_transcript_path(_tp):
            continue
        if _a.get("status", "running") in _TERMINAL_STATUSES:
            continue
        _sa = _a.get("spawned_at") or ""
        if _tp not in _shared_path_earliest or _sa < _shared_path_earliest[_tp]:
            _shared_path_earliest[_tp] = _sa
    for _a in agents:
        _tp = _a.get("transcript_path") or ""
        if not _tp or _is_per_agent_transcript_path(_tp):
            continue
        if _a.get("status", "running") in _TERMINAL_STATUSES:
            continue
        _earliest = _shared_path_earliest.get(_tp)
        if _earliest is not None and (_a.get("spawned_at") or "") > _earliest:
            _a["is_helper_spawn"] = True
    if user_spawned_only:
        from services.agent_filters import is_user_spawned_agent
        agents = [a for a in agents if is_user_spawned_agent(a)]
    if filter_status:
        agents = [a for a in agents if a.get("status") == filter_status]
    if filter_source:
        agents = [a for a in agents if a.get("source") == filter_source]
    if limit is not None and limit >= 0:
        # Newest-first so limit=N returns the N most recently spawned agents.
        # With many historical rows (683+), ascending sort returned only ancient
        # rows and made any agent spawned after the 200th-oldest invisible (→1238).
        agents = sorted(agents, key=lambda a: a.get("spawned_at") or "", reverse=True)[:limit]
    # →1549: annotate every agent row with per_agent_transcript_bytes (per-agent
    # on-disk JSONL size) and kernel_event_index (the pre-existing shared session
    # JSONL size, kept for backward compat). transcript_bytes is left as-is so
    # existing consumers (ghost detection, stall detection, frontend) are unchanged.
    # →1702: snapshot agents already have both fields from _run_enrich_pipeline.
    # Only compute here for rows added since the last snapshot (registered after
    # the last 500ms background cycle) — typically 0-1 rows per request.
    await asyncio.to_thread(_fill_transcript_bytes, agents)
    if summary:
        compact_keys = ("name", "source", "status", "spawned_at", "transcript_bytes",
                        "kernel_event_index", "per_agent_transcript_bytes",
                        "last_heartbeat_at", "description", "model")
        return {"agents": [{k: a.get(k) for k in compact_keys if a.get(k) is not None} for a in agents]}
    from services.agent_filters import is_user_spawned_agent as _is_user_spawned
    try:
        from services.build_queue import all_build_states as _all_build_states
        _bstates = _all_build_states()
        if _bstates:
            for _agent_row in agents:
                _bs = _bstates.get(_agent_row.get("name", ""))
                if _bs:
                    _agent_row["build_state"] = _bs
    except Exception:
        pass
    # Annotate each terminal agent row with a derived badge field.
    for _agent_row in agents:
        _badge = _compute_agent_badge(_agent_row)
        if _badge is not None:
            _agent_row["badge"] = _badge
    async with _merge_debt_lock:
        _md = dict(_cached_merge_debt)
    return {
        "daemon_running": daemon_running,
        "status": snapshot.get("status", "unknown"),
        "active": [a["name"] for a in agents if a.get("status") == "running" and _is_user_spawned(a)],
        "agents": agents,
        "avg_min_per_dollar": snapshot.get("avg_min_per_dollar", 0.0),
        "merge_debt_count": _md.get("count", 0),
        "merge_debt_items": _md.get("items", []),
    }


import shutil
CLAUDE_BIN = shutil.which("claude") or "claude"

MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
    "haiku": "claude-haiku-4-5-20251001",
}


def _spawn_quick_mode(body: "AgentSpawn") -> bool:
    """Return True when the spawn target opts into quick mode.

    Quick mode is an agentfile-level opt-in: ``LIMIT quick_mode true``
    inside the matching .agent file flips this on for every spawn that
    resolves to that agent. We check both the explicit ``template`` name
    (Tasks page "Implement with saa" flow) and the agent ``name`` (direct
    agentfile lookup) so either path gets the speedup.

    Never raises. Any lookup error falls back to False so the spawn stays
    on the safe full-mailbox path.
    """
    try:
        from services.agentfile_parser import (
            get_agent_config,
            get_agent_config_by_template,
        )
    except Exception:
        return False

    try:
        template_raw = (getattr(body, "template", None) or "").strip().lower()
        # ``saa`` is Tori's muscle memory shortcut for the full builder
        # pattern (plan, build, test, verify). The long mailbox contract
        # is part of that full-fidelity experience, so we never flip saa
        # into quick mode even if the underlying builder.agent file opts
        # in. Tasks page "Implement with saa" keeps its old behaviour.
        if template_raw == "saa":
            return False

        if template_raw:
            try:
                from services.agent_templates_store import (
                    _resolve_alias,
                    _BUILTIN_BY_ID,
                    _name_to_stem,
                )
                alias_id = _resolve_alias(body.template)
                if alias_id:
                    # Two stems are tried because marketplace templates
                    # live at ``agents/marketplace/<name_to_stem>.agent``
                    # and their file stem does NOT match the built-in id
                    # prefix (Roadmap -> ``roadmap.agent``, not
                    # ``pm-roadmap``). Without this second stem, the
                    # ``LIMIT quick_mode true`` flag in the agentfile was
                    # silently ignored so the Roadmap spawn ran the full
                    # mailbox block and took minutes instead of seconds.
                    stems = [alias_id.replace("builtin-", "")]
                    tpl = _BUILTIN_BY_ID.get(alias_id) or {}
                    tpl_name = tpl.get("name")
                    if tpl_name:
                        name_stem = _name_to_stem(tpl_name)
                        if name_stem and name_stem not in stems:
                            stems.append(name_stem)
                    for stem in stems:
                        cfg = get_agent_config_by_template(stem)
                        if cfg is not None and getattr(cfg, "quick_mode", False):
                            return True
            except Exception:
                pass
            cfg = get_agent_config_by_template(body.template)
            if cfg is not None and getattr(cfg, "quick_mode", False):
                return True
        cfg = get_agent_config(body.name)
        if cfg is not None and getattr(cfg, "quick_mode", False):
            return True
    except Exception:
        return False
    return False


# Agent statuses that indicate "live work is happening on behalf of a task".
# ``running`` is the default spawn state. ``spawned`` is written by a few
# legacy paths. ``in_progress`` is used by claude-code hook rows that
# register an active subagent. Agents with ``completed_at`` set are NOT
# live even if their ``status`` key was not updated (e.g. a stale row).
_LIVE_AGENT_STATUSES = {"running", "spawned", "in_progress"}
# Counts how many times the ostk-run spawn path fell back to the custom launcher.
# Observable at runtime: routers.agents._ostk_run_fallback_count["count"].
_ostk_run_fallback_count: dict = {"count": 0}

# →2603: captured at import time so the guard below can tell a REAL
# asyncio.create_subprocess_exec apart from a test's mock. Every existing
# test that exercises the bespoke spawn path patches the attribute on the
# asyncio module (patch("asyncio.create_subprocess_exec", ...) or
# monkeypatch.setattr(agents_mod.asyncio, ...)), so identity with this
# capture means "nothing intercepts the exec".
_REAL_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec


def _pytest_blocks_real_spawn() -> bool:
    """→2603: True when the bespoke spawn path must NOT exec a real process.

    Backend tests drive POST /api/agents/spawn in-process (ASGITransport),
    so an unmocked run of the bespoke path execs a real `claude --print`
    from inside pytest. Those processes start in a pytest tmp cwd with no
    working shell (Bash blocked by hooks, no ostk MCP, no ToolSearch), and
    their mailbox bootstrap tells them to register via curl — so they
    delegate to throwaway Agent helpers (run-curl-command-*,
    register-agent-with-new-name, load-shell-tool-via-toolsearch) that the
    global register-agent.sh hook publishes to the live Agents page.

    Blocks only when ALL of:
      * PYTEST_CURRENT_TEST is set (pytest sets it per test, cleared after);
      * asyncio.create_subprocess_exec is unpatched (tests that mock the
        exec keep exercising the deeper spawn logic unchanged);
      * YOUROS_SPAWN_ALLOW_REAL_IN_TESTS is not set (escape hatch for a
        deliberate end-to-end test).
    """
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        and asyncio.create_subprocess_exec is _REAL_CREATE_SUBPROCESS_EXEC
        and not os.environ.get("YOUROS_SPAWN_ALLOW_REAL_IN_TESTS")
    )


def _is_agent_genuinely_live(meta: dict) -> bool:
    """Return True only when the agent row has evidence of being alive.

    A status string in _LIVE_AGENT_STATUSES is necessary but not
    sufficient.  Stale/abandoned rows keep their status string after the
    process dies, which causes their needle_ids to stay overlaid as
    in_progress forever (→1930, →1804, →1754).

    An agent is considered genuinely live when AT LEAST ONE of:
      A. Its recorded pid passes os.kill(pid, 0).  A dead pid is
         definitive evidence of death and short-circuits to False
         immediately; no timestamp check is done.
      B. Its last_heartbeat_at or spawned_at is within
         STALE_AGENT_TIMEOUT_SECONDS of now.
      C. Its spawned_at is within SPAWN_GRACE_PERIOD_SECONDS of now.
         This is checked before Signal A so that an immediately-exiting
         subprocess (e.g. empty-transcript fast exit) does not cause the
         just-spawned task linkage to disappear (→1950/→2020). Stale rows
         are unaffected because their spawned_at is outside the window.

    Agents with no pid and no timestamps (bare rows) return False so
    they do not pin tasks indefinitely.
    """
    # Signal A first: a recorded pid that is definitively dead is conclusive
    # evidence of death and short-circuits to False, even within the spawn
    # grace window. A dead pid means the subprocess is gone for good; the
    # grace window (Signal C) only exists to cover rows that have NOT yet
    # proven death (no pid recorded yet, or a still-live pid). Checking the
    # dead pid here keeps stale/abandoned rows (→1930, →1804, →1754) from
    # being pinned as in_progress just because their spawned_at is recent.
    pid = meta.get("pid")
    if pid:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            pid_int = None
        if pid_int is not None:
            if _is_pid_alive(pid_int):
                return True  # Signal A: live pid.
            return False  # Signal A: dead pid — conclusive, skip Signal B/C.

    # Signal C: spawn grace window — only reached when no pid proved death
    # (no pid recorded, or an unparseable pid). Keeps a just-spawned row's
    # task linkage visible immediately after /spawn returns 200, even when
    # the subprocess exits with an empty transcript before any heartbeat
    # arrives (→1950/→2020). Stale rows with old spawned_at fall through.
    _spawned_raw = meta.get("spawned_at")
    if isinstance(_spawned_raw, str):
        _spawned_ts = _parse_iso(_spawned_raw)
        if _spawned_ts is not None:
            _grace_now = datetime.now(timezone.utc)
            if (_grace_now - _spawned_ts).total_seconds() <= SPAWN_GRACE_PERIOD_SECONDS:
                return True  # Signal C: within spawn grace window.

    # No pid recorded: fall back to heartbeat/spawn recency.
    now = datetime.now(timezone.utc)
    for field in ("last_heartbeat_at", "spawned_at"):
        raw = meta.get(field)
        if not isinstance(raw, str):
            continue
        ts = _parse_iso(raw)
        if ts is None:
            continue
        if (now - ts).total_seconds() <= STALE_AGENT_TIMEOUT_SECONDS:
            return True  # Signal B: recent timestamp.

    return False


def get_running_task_ids() -> set[str]:
    """Return the set of task ids that have at least one live agent.

    "Live" means an agent row in ``agent_metadata`` whose ``status`` is
    in ``_LIVE_AGENT_STATUSES``, which has no ``completed_at`` timestamp,
    AND which passes ``_is_agent_genuinely_live`` (live pid or recent
    heartbeat). The Tasks list endpoint uses this to force a task's
    effective status to ``in_progress`` whenever any agent spawned for it
    is still working. See AgentSpawn.task_id in models/schemas.py for
    how the link is recorded.
    """
    live: set[str] = set()
    for _name, meta in agent_metadata.items():
        if not isinstance(meta, dict):
            continue
        tid = meta.get("task_id")
        if not tid:
            continue
        if meta.get("completed_at"):
            continue
        status = str(meta.get("status") or "").lower()
        if status in _LIVE_AGENT_STATUSES and _is_agent_genuinely_live(meta):
            live.add(str(tid))
    return live


def get_running_needle_ids() -> set[str]:
    """Return the set of needle ids that have at least one live agent.

    Mirrors get_running_task_ids() for ostk needles. The task list
    endpoint overlays in_progress status on a needle when a live agent
    carries its needle_id, without writing back to issues.jsonl.

    Checks both ``needle_id`` (primary, first match) and ``needle_ids``
    (all →NNN tokens extracted at register time, →1204).

    Only counts an agent as live if it passes ``_is_agent_genuinely_live``
    (live pid or recent heartbeat), so stale/abandoned rows with dead pids
    no longer pin tasks as in_progress indefinitely.
    """
    live: set[str] = set()
    for _name, meta in agent_metadata.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("completed_at"):
            continue
        status = str(meta.get("status") or "").lower()
        if status not in _LIVE_AGENT_STATUSES:
            continue
        if not _is_agent_genuinely_live(meta):
            continue
        nid = meta.get("needle_id")
        if nid:
            live.add(str(nid))
        for extra_nid in meta.get("needle_ids") or []:
            live.add(str(extra_nid))
    return live


import re as _needle_re

# Matches →NNN needle references in text (arrow character + digits).
_ARROW_NEEDLE_RE = _needle_re.compile(r"→(\d{1,6})")
# Matches a trailing -NNN suffix in an agent name (2-5 digits, no hex).
# Used as a fallback when no arrow-prefixed reference is found in text.
_NAME_NEEDLE_SUFFIX_RE = _needle_re.compile(r"-(\d{2,5})$")
# Matches the second segment in names like "fix-1062-description-hash".
# The needle ID follows the action verb before any descriptive words.
_NAME_NEEDLE_SECOND_SEG_RE = _needle_re.compile(r"^[a-z_]+-(\d{3,6})-")


def _infer_needle_id(
    name: str,
    task: str,
    description: str,
    prompt: str,
    issues_path: Optional[Path] = None,
) -> Optional[str]:
    """Extract a needle ID from agent text fields or name, best-effort.

    Priority:
    1. Arrow-prefixed →NNN in task, description, or prompt (most reliable).
    2. Second segment in names like "fix-1062-description-hash" (verified).
    3. Trailing -NNN suffix in agent name (verified).

    Returns a bare numeric string (e.g. "968") or None when nothing is found.
    Closed or shelved needles are not excluded here; the overlay in tasks.py
    respects terminal statuses at render time.
    """
    for text in (task, description, prompt):
        if not text:
            continue
        m = _ARROW_NEEDLE_RE.search(text)
        if m:
            return m.group(1)
    if name:
        if issues_path is None:
            issues_path = Path(ostk.cwd) / ".ostk" / "needles" / "issues.jsonl"

        def _verify_candidate(candidate: str) -> Optional[str]:
            if not issues_path.exists():
                return None
            arrow_form = f"→{candidate}"
            try:
                for line in issues_path.read_text().splitlines():
                    entry = json.loads(line)
                    raw_id = str(entry.get("id", ""))
                    if raw_id == arrow_form or raw_id.lstrip("→") == candidate:
                        return candidate
            except Exception:
                pass
            return None

        for pattern in (_NAME_NEEDLE_SECOND_SEG_RE, _NAME_NEEDLE_SUFFIX_RE):
            m = pattern.search(name)
            if m:
                result = _verify_candidate(m.group(1))
                if result is not None:
                    return result
    return None


def _extract_all_needle_ids(
    task: str = "",
    description: str = "",
    prompt: str = "",
) -> list[str]:
    """Extract every unique →NNN needle ID from agent text fields, in order.

    Unlike _infer_needle_id which returns only the first match, this
    returns all unique →NNN tokens. Used at register/spawn time to
    populate needle_ids so get_running_needle_ids() shows in_progress
    for every referenced needle, not just the first (→1204).
    """
    seen: dict[str, None] = {}
    for text in (task, description):
        if not text:
            continue
        for m in _ARROW_NEEDLE_RE.finditer(text):
            seen.setdefault(m.group(1), None)
    # Only scan prompt when both task and description are absent entirely.
    # If task or description is present (even with no needle IDs), the
    # agent's "what I'm doing" is captured there and the prompt is just
    # the instructions brief, which may contain many →NNN IDs as context.
    # Extracting those would overlay unrelated tasks as in_progress.
    if not task and not description and prompt:
        for m in _ARROW_NEEDLE_RE.finditer(prompt):
            seen.setdefault(m.group(1), None)
    return list(seen.keys())


def _fire_set_task_in_progress(needle_id: str) -> None:
    """Schedule a persistent in_progress write for *needle_id*, non-blocking.

    Wraps ostk.set_task_in_progress in a create_task so the spawn/register
    hot path is never delayed. Swallows all errors so a JSONL write failure
    never disrupts agent registration.
    """
    if not needle_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_set_task_in_progress_async(needle_id))
    except RuntimeError:
        pass


async def _set_task_in_progress_async(needle_id: str) -> None:
    try:
        await ostk.set_task_in_progress(needle_id)
    except Exception:
        pass


async def _close_task_for_autocomplete(needle_id: str) -> None:
    """Close the task/needle for an agent completed by the idle sweep (→2207)."""
    try:
        _arrow_n = f"→{needle_id.lstrip('→')}"
        await ostk.close_task(_arrow_n, closed_reason="completed")
    except Exception:
        pass


def _fire_release_needle_if_orphaned(needle_id: str) -> None:
    """Synchronously reset needle_id to open if no other agent claims to hold it.

    Called from _set_agent_status when an agent transitions to a terminal status.
    Done synchronously (no create_task) so no extra event loop cycles are added.

    Checks by STATUS only (skips _is_agent_genuinely_live / os.kill) to avoid
    iterating all live agents' PIDs — that could add 100+ ms of syscall overhead.
    Stale agents that claim a needle but have dead PIDs will be swept by the
    stale-agent loop, which will call _fire_release_needle_if_orphaned again.
    """
    if not needle_id:
        return
    bare = needle_id.lstrip("→").strip()
    for _name, meta in agent_metadata.items():
        if not isinstance(meta, dict):
            continue
        status = str(meta.get("status") or "").lower()
        if status not in _LIVE_AGENT_STATUSES:
            continue
        nid = str(meta.get("needle_id") or "")
        if nid and (nid == needle_id or nid.lstrip("→").strip() == bare):
            return
        for extra_nid in meta.get("needle_ids") or []:
            s = str(extra_nid)
            if s == needle_id or s.lstrip("→").strip() == bare:
                return
    try:
        ostk.release_needle_sync(needle_id)
    except Exception:
        pass


async def _release_needle_if_orphaned_async(needle_id: str) -> None:
    """Async version — used by tests to verify orphan-check logic."""
    live = get_running_needle_ids()
    bare = needle_id.lstrip("→").strip()
    if needle_id in live or bare in live:
        return
    try:
        await ostk.release_needle(needle_id)
    except Exception:
        pass


def _build_spec_ac_block(task_id: str, docs: list[dict]) -> str:
    """Return an AC injection block if any doc in *docs* references *task_id*.

    Normalises arrow-prefixed IDs (→950 == 950) on both sides so the
    lookup works regardless of whether the spec was created via
    POST /specs/from-task (stores bare ID) or via ostk decompose (may
    store arrow-prefixed). Returns an empty string when no match is found
    so callers can do a simple truthiness check.
    """
    norm = task_id.lstrip("→").strip()
    for doc in docs:
        doc_ids = {t.lstrip("→").strip() for t in doc.get("task_ids", [])}
        if norm not in doc_ids:
            continue
        ac = doc.get("acceptance_criteria", [])
        if not ac:
            break
        lines = "\n".join(
            f"- [{'x' if c.get('checked') else ' '}] {c.get('text', '')}"
            for c in ac
        )
        title = doc.get("title", "Spec")
        return (
            f"## Spec: {title}\n\n"
            f"These are the acceptance criteria your solution must satisfy:\n\n"
            f"{lines}\n\n"
            f"Treat unchecked items as your definition of done."
        )
    return ""


# →2640 fix 5: per-process cache for the subscription check. Dict used so it
# can be cleared in tests without monkey-patching the function itself.
_HOST_SUBSCRIPTION_CACHE: dict = {}


def _host_has_claude_subscription() -> bool:
    """Return True if this host authenticates via a claude.ai subscription.

    Reads ~/.claude/settings.json once per process (result cached in
    _HOST_SUBSCRIPTION_CACHE). If the file contains an "apiKeyHelper" field,
    the host uses an external credential program and is NOT a subscription
    host: return False so the caller preserves ANTHROPIC_API_KEY in the
    spawn env. Any read/parse error defaults to True (subscription) so the
    existing strip behaviour is preserved on unknown hosts.
    """
    if "result" in _HOST_SUBSCRIPTION_CACHE:
        return _HOST_SUBSCRIPTION_CACHE["result"]

    result = True  # safe default: strip the key (existing behaviour)
    try:
        settings_path = Path("~/.claude/settings.json").expanduser()
        if settings_path.exists():
            data = json.loads(settings_path.read_text())
            if "apiKeyHelper" in data:
                result = False
    except Exception:
        pass

    _HOST_SUBSCRIPTION_CACHE["result"] = result
    return result


@router.post("/agents/spawn")
async def spawn_agent(body: AgentSpawn, request: Request = None, response: Response = None):
    # →1895/→2945: the native subprocess path at the bottom of this endpoint
    # goes through the RuntimeProvider seam. The runtime comes from the user's
    # saved ``default_provider`` setting; this endpoint holds no vendor branch.
    from services.rate_limit import rate_limit_check
    if request is not None:
        rate_limit_check(request, "agents.spawn")

    # Burst-rate throttle: max MYOS_SPAWN_BURST_LIMIT (default 3) spawns per 30s.
    # Excess spawns queue here until a slot opens (or 429 after 90s).
    from services.spawn_throttle import acquire_spawn_slot as _acquire_spawn_slot
    _throttle_wait = await _acquire_spawn_slot(body.name)
    if _throttle_wait > 0 and response is not None:
        response.headers["X-Spawn-Throttled"] = "1"

    import time as _time
    _loop_t0 = _time.monotonic()
    await asyncio.sleep(0)
    _loop_latency = _time.monotonic() - _loop_t0
    if _loop_latency > 2.0:
        logger.warning(
            "spawn.health_gate.reject name=%s latency=%.2fs",
            body.name, _loop_latency,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"Backend is overloaded (event loop latency: "
                f"{_loop_latency:.1f}s). Try again in a moment or "
                f"reduce the number of running agents."
            ),
        )
    elif _loop_latency > 0.5:
        logger.warning(
            "spawn.health_gate.slow name=%s latency=%.2fs",
            body.name, _loop_latency,
        )

    # Receipts gate: warn if the spawn brief uses a completion trigger word
    # ("done", "fixed", …) without inline evidence. Warn-only for now (→1554).
    _brief_warning = None
    from services.settings_store import settings_store as _settings_store_rg
    if _settings_store_rg.get("chat_receipts_gate_enabled", True):
        from services.receipts_gate import check_brief_receipts as _check_brief_receipts
        _brief_warning = _check_brief_receipts(body.prompt or "")
        if _brief_warning is not None:
            logger.warning(
                "spawn.brief_receipts_warning name=%s trigger_word=%s",
                body.name, _brief_warning.trigger_word,
            )

    # AC3 EXIT CRITERIA - conditions to delete the custom spawn fallback (agents.py ~4546-4592):
    #   (i)  OSTK_RUN_FALLBACK rate stays at or near zero across a full release cycle.
    #   (ii) Worktree-isolation parity confirmed for ostk-run path (all 3 isolation modes work).
    #   (iii) Scaffold-commit watcher gets worktree_path metadata via ostk-run (agents.py ~1463).
    #   (iv) OSTK_PROJECT_ROOT/short-cwd handling parity confirmed for ostk-run path.
    #   (v)  Supervised verification: spawn 3 different agent types, all land via ostk-run, zero fallback.
    # Only after ALL five hold: delete the custom spawn path and this comment block.
    # See spec AC3 in ~/.youros/specs/adopt-claude-code-s-good-ideas-into-myos-as-vendor-agnostic-abstractions.md
    #
    # --- ostk run path: env-level canonical (YOUROS_SPAWN_USE_OSTK_RUN=1, →1305) or per-request opt-in ---
    # YOUROS_SPAWN_USE_OSTK_RUN=1 makes `ostk run <Agentfile>` the default for every spawn.
    # The bespoke claude-code subprocess path is the fallback when:
    #   (a) flag is off and use_ostk_run=False (existing default — no behaviour change)
    #   (b) flag is on but no agentfile resolves for this name  → silent fallback to bespoke
    #   (c) flag is on but ostk run itself errors               → silent fallback to bespoke
    # Explicit use_ostk_run=True (per-request) always routes here; errors raise HTTP 5xx.
    #
    # Pre-flight status (→1305): ostk run path skips the bespoke worktree dance,
    # so three features established in the bespoke path are NOT yet preserved:
    #   1. Worktree isolation (worktree creation loop ~line 4600): ostk run uses its
    #      own ISOLATION directive; our .claude/worktrees/agent-* dance is bypassed.
    #   2. Scaffold-commit watcher (_worktree_has_new_work, line 1314): relies on
    #      worktree_path in agent_metadata, which is only set by the bespoke path.
    #   3. OSTK_PROJECT_ROOT short-cwd trick (commit a5b64c7, →1148): short-cwd is
    #      computed during worktree creation; ostk run inherits the server's cwd.
    # These gaps are acceptable for read-only/research pilots (no file writes, no
    # commits). Full adoption for code-edit agents requires extending run_agentfile
    # or cherry-picking the isolation env-injection. See plan Tier 2.2.
    # AC3: ostk-run is the default spawn path for all agent types.
    # YOUROS_SPAWN_FORCE_CUSTOM=1 is the kill-switch: forces the custom path, skips ostk-run entirely.
    # YOUROS_SPAWN_USE_OSTK_RUN is preserved for backward compat (now a no-op since default is on).
    _force_custom = os.environ.get("YOUROS_SPAWN_FORCE_CUSTOM", "").strip() in ("1", "true", "yes")
    _env_use_ostk_run = os.environ.get("YOUROS_SPAWN_USE_OSTK_RUN", "").strip() in ("1", "true", "yes")
    _req_use_ostk_run = getattr(body, "use_ostk_run", False)
    # Comprehensive/saa builds are code-edit agents: they require the three
    # features the ostk-run path explicitly does NOT preserve (worktree
    # isolation, scaffold-commit watcher, short-cwd) PLUS the spawn-lock dedup
    # that yields HTTP 409 for a duplicate task, the unmerged-branch auto-suffix
    # that keeps the comprehensive build button working, and the
    # BUILD_CONCURRENCY queue that returns build_state="queued" at the limit.
    # The ostk-run early-return would short-circuit all of those, so
    # comprehensive/saa spawns always take the bespoke path.
    # A per-request use_ostk_run=True still forces ostk-run (explicit opt-in wins).
    _is_comprehensive_template = str(body.template or "").lower() in ("comprehensive", "saa")
    _use_ostk_run = not _force_custom and (
        _req_use_ostk_run or not _is_comprehensive_template
    )
    if _use_ostk_run:
        # _ostk_fallback_ok: False when the caller explicitly requested ostk-run (per-request opt-in).
        # When False and ostk-run fails, raise HTTP error instead of falling back.
        # When True, fall back with loud logging so fallbacks are never silent.
        _ostk_fallback_ok = not _req_use_ostk_run
        _ostk_ran = False
        _ostk_result = None
        _ostk_dry_run = getattr(body, "dry_run", False)
        _ostk_agentfile_path = None
        _ostk_wt_path: Optional[str] = None
        _ostk_wt_branch: Optional[str] = None
        try:
            from services.agentfile_parser import (
                _find_any_agentfile as _ostk_find_agentfile,
                get_template_aliases as _ostk_get_aliases,
            )
            _ostk_stem = body.template or body.name
            if body.template:
                _ostk_aliases = _ostk_get_aliases()
                _ostk_stem = _ostk_aliases.get(body.template, body.template)
            _ostk_agentfile_path = _ostk_find_agentfile(_ostk_stem)
            if _ostk_agentfile_path is None:
                if _ostk_fallback_ok:
                    logger.warning(
                        "OSTK_RUN_FALLBACK name=%s reason=no_agentfile stem=%s",
                        body.name, _ostk_stem,
                    )
                    _ostk_run_fallback_count["count"] += 1
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"ostk run: no agentfile found for '{_ostk_stem}'. "
                            f"Check that agents/{_ostk_stem}.agent exists."
                        ),
                    )
            else:
                # →2944 (rows →1885/→1886/→1887): the ostk-run default path keeps
                # the same safety rails as the bespoke path. Resolve isolation the
                # same way (agentfile ISOLATION directive first, then the verb
                # heuristic), enforce the same locks contract, fork the same
                # worktree, and run ostk from the short worktree cwd with the
                # same env the bespoke subprocess would get.
                from services.spawn_isolation import (
                    acquire_spawn_locks as _ostk_acquire_locks,
                    decide_isolation as _ostk_decide_isolation,
                    release_spawn_locks as _ostk_release_locks,
                    validate_locks_for_spawn as _ostk_validate_locks,
                )
                if not body.isolation:
                    # Honour the agentfile's ISOLATION directive before verb
                    # detection, mirroring the template pre-resolution in
                    # _legacy_bespoke_spawn: "nono" is an explicit opt-out;
                    # "none" (no directive) with no caller locks defaults to
                    # the main checkout so lock-less template spawns keep
                    # working exactly as they do on the bespoke path.
                    try:
                        from services.agentfile_parser import (
                            get_agent_config_by_template as _ostk_get_cfg,
                        )
                        _ostk_cfg = _ostk_get_cfg(_ostk_stem)
                        if _ostk_cfg is not None and _ostk_cfg.isolation == "nono":
                            body.isolation = _ostk_cfg.isolation
                        elif (
                            _ostk_cfg is not None
                            and _ostk_cfg.isolation == "none"
                            and not body.locks
                        ):
                            body.isolation = "none"
                    except Exception:
                        pass
                if body.follow_on and not body.isolation:
                    body.isolation = "none"
                body.isolation = _ostk_decide_isolation(
                    description=body.description,
                    prompt=body.prompt,
                    explicit=body.isolation,
                    agent_name=body.name,
                )
                _ostk_locks_ok, _ostk_locks_err = _ostk_validate_locks(
                    isolation=body.isolation or "none",
                    locks=body.locks,
                )
                if not _ostk_locks_ok:
                    raise HTTPException(status_code=400, detail=_ostk_locks_err)
                if not _ostk_dry_run:
                    _ostk_lock_ok, _ostk_lock_keys, _ostk_contenders = _ostk_acquire_locks(
                        spawn_id=body.name,
                        locks=body.locks,
                    )
                    if not _ostk_lock_ok:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": "lock_conflict",
                                "message": (
                                    "Another spawn is already holding one of the paths "
                                    "this spawn asked to edit. Wait for it to finish or "
                                    "retry with a different path."
                                ),
                                "conflicts": [
                                    {
                                        "requested": requested,
                                        "held_by_spawn": holder_id,
                                        "held_path": holder_raw,
                                    }
                                    for (requested, holder_id, holder_raw) in _ostk_contenders
                                ],
                            },
                        )
                _ostk_cwd: Optional[str] = None
                # YOUROS_AGENT_NAME routes hook-fired heartbeats to this row.
                # It was already listed in env_passthrough here but nothing
                # ever set it, so the passthrough forwarded nothing.
                _ostk_env: dict = {"YOUROS_AGENT_NAME": body.name}
                try:
                    if body.isolation == "worktree" and not _ostk_dry_run:
                        (
                            _ostk_cwd,
                            _ostk_wt_path,
                            _ostk_wt_branch,
                            _ostk_wt_env,
                        ) = await _provision_worktree_isolation(body.name)
                        _ostk_env.update(_ostk_wt_env)
                    _ostk_result = await ostk.run_agentfile(
                        str(_ostk_agentfile_path),
                        env_passthrough=sorted(
                            {"ANTHROPIC_API_KEY", *_ostk_env.keys()}
                        ),
                        dry_run=_ostk_dry_run,
                        cwd=_ostk_cwd,
                        env=_ostk_env,
                    )
                    _ostk_ran = True
                except HTTPException:
                    # Worktree provisioning failed. The bespoke path would
                    # fail the same way, so a fallback would not help;
                    # release the locks this branch acquired and surface it.
                    _ostk_release_locks(spawn_id=body.name)
                    raise
        except HTTPException:
            raise
        except Exception as _ostk_exc:
            if _ostk_fallback_ok:
                logger.warning(
                    "OSTK_RUN_FALLBACK name=%s reason=error error=%s",
                    body.name, _ostk_exc,
                )
                _ostk_run_fallback_count["count"] += 1
            else:
                try:
                    from services.spawn_isolation import (
                        release_spawn_locks as _ostk_release_locks_err,
                    )
                    _ostk_release_locks_err(spawn_id=body.name)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=500,
                    detail=f"ostk run failed: {_ostk_exc}",
                )
        if _ostk_ran:
            logger.info(
                "spawn.ostk_run name=%s agentfile=%s dry_run=%s exit_code=%s via_env=%s",
                body.name, _ostk_agentfile_path, _ostk_dry_run,
                _ostk_result.get("exit_code"), _env_use_ostk_run,
            )
            if not _ostk_dry_run:
                # →2944 (row →1886): record the same spawn metadata the
                # bespoke path writes so the scaffold-commit watcher,
                # /recover (max_recoveries), worktree unlock on terminal
                # status, and needle release all see ostk-run agents.
                _ostk_model = MODEL_MAP.get(body.model, body.model)
                spawn_meta = _compose_spawn_meta(
                    body,
                    model=_ostk_model,
                    pid=(_ostk_result or {}).get("pid"),
                    worktree_path=_ostk_wt_path,
                    worktree_branch=_ostk_wt_branch,
                )
                agent_metadata[body.name] = spawn_meta
                await _save_agent_state_async()
                _set_agent_status(body.name, "running")
                _ostk_spawn_nid = spawn_meta.get("needle_id")
                if _ostk_spawn_nid:
                    _fire_set_task_in_progress(_ostk_spawn_nid)
                try:
                    await ostk._run(
                        "os", "audit", "--event", "agent.spawned",
                        "--data", json.dumps(
                            {
                                "name": body.name,
                                "model": _ostk_model,
                                "budget": str(body.budget),
                            }
                        ),
                    )
                except Exception:
                    pass
            return {
                "result": f"Agent '{body.name}' spawned via ostk run",
                "name": body.name,
                "status": "dry_run" if _ostk_dry_run else "running",
                "isolation": body.isolation,
                "worktree_path": _ostk_wt_path,
                "ostk_run": _ostk_result,
                "brief_warning": _brief_warning.message if _brief_warning else None,
            }
        # _ostk_ran is False: no agentfile found or ostk errored with fallback allowed.
        # Fall through to the RuntimeProvider spawn path below.

    # -----------------------------------------------------------------------
    # Native subprocess path, reached through the RuntimeProvider seam (→2945)
    # -----------------------------------------------------------------------
    # The runtime is picked by default_provider() from the user's saved
    # ``default_provider`` setting (YOUROS_RUNTIME is a test-only override).
    # The provider gets our in-process spawn internals injected: the Claude
    # provider runs them as its own spawn implementation, and a runtime
    # without agent support refuses loudly (SpawnNotSupportedError) instead
    # of silently spawning a vendor the user did not pick.
    from services.runtime_provider import (
        SpawnNotSupportedError,
        SpawnRequest,
        SpawnResult,
        default_provider,
    )

    async def _native_spawn_internals(req: SpawnRequest) -> SpawnResult:
        # Map the provider-neutral SpawnRequest back onto the AgentSpawn body
        # the in-process spawn implementation expects.
        body.name = req.name
        body.prompt = req.prompt
        body.model = req.model
        body.budget = req.budget
        body.template = req.template
        body.task = req.task
        body.isolation = req.isolation
        body.token_limit = req.token_limit
        
        # Extract from extra dict
        body.description = req.extra.get("description")
        body.task_id = req.extra.get("task_id")
        body.needle_id = req.extra.get("needle_id")
        body.locks = req.extra.get("locks")
        
        legacy_result = await _legacy_bespoke_spawn(body, request, response, _brief_warning)

        return SpawnResult(
            name=legacy_result.get("name", req.name),
            pid=legacy_result.get("pid"),
            status=legacy_result.get("status", "running"),
            transcript_path=legacy_result.get("transcript"),
            detail=legacy_result,
        )

    req = SpawnRequest(
        name=body.name,
        prompt=body.prompt,
        model=body.model,
        budget=body.budget,
        template=body.template,
        task=body.task,
        isolation=body.isolation,
        token_limit=body.token_limit,
        extra={
            "description": body.description,
            "task_id": body.task_id,
            "needle_id": body.needle_id,
            "locks": body.locks,
        }
    )
    provider = default_provider(spawn_fn=_native_spawn_internals)
    try:
        result = await provider.spawn_subagent(req)
    except SpawnNotSupportedError as exc:
        # The active runtime cannot start agents. Say so in plain language
        # instead of quietly spawning a different vendor.
        raise HTTPException(status_code=501, detail=str(exc))

    # Return the dictionary from _legacy_bespoke_spawn if it was executed.
    if "result" in result.detail:
        return result.detail

    return {
        "result": f"Agent '{result.name}' spawned",
        "name": result.name,
        "pid": result.pid,
        "status": result.status,
        "transcript": result.transcript_path,
        "brief_warning": result.detail.get("brief_warning"),
    }


async def _provision_worktree_isolation(
    name: str,
) -> "tuple[str, Optional[str], Optional[str], dict]":
    """Fork a git worktree for an agent spawn and compute its env rails (→2944).

    Extracted verbatim from the bespoke spawn path so the ostk-run default
    path gets the exact same isolation: the short-cwd trick,
    OSTK_PROJECT_ROOT/OSTK_ROOT, CLAUDE_PROJECT_DIR, YOUROS_AGENT_NAME,
    OSTK_SOCKET, the .claude/.mcp.json sync, and the socket/needles
    symlinks. Returns ``(spawn_cwd, worktree_path, worktree_branch,
    env_overrides)``; raises HTTPException(500) when the fork fails,
    exactly like the bespoke path always has.
    """
    from config import PROJECT_ROOT

    env_overrides: dict = {}
    _spawn_cwd = str(PROJECT_ROOT)
    _worktree_path: Optional[str] = None
    _worktree_branch: Optional[str] = None
    # Cap the worktree id so the resulting
    #   <project_root>/.claude/worktrees/agent-<id>/.ostk/ostk.sock
    # path stays under macOS sun_path (104).  Long agent names
    # (Claude Code derives names from the spawn description, so a
    # verbose description can produce 40+ char names) otherwise
    # overflow, the ostk MCP server's bind() fails, and the
    # subagent silently registers only the static tool surface
    # (no bash/read/fs_ops).  See feedback memory entry
    # subagent_mcp_must_have_realtime_backup for full context.
    from services.spawn_isolation import short_worktree_id as _short_wt_id
    _wt_id = _short_wt_id(name)
    _wt_branch = f"worktree-agent-{_wt_id}"
    _wt_path = PROJECT_ROOT / ".claude" / "worktrees" / f"agent-{_wt_id}"
    try:
        from services.spawn_isolation import (
            create_worktree as _create_worktree,
            short_cwd_for_worktree as _short_cwd_for_worktree,
            sync_claude_dir_to_worktree as _sync,
        )
        _wt_ok, _wt_err = await _create_worktree(
            project_root=PROJECT_ROOT,
            agent_name=name,
            branch=_wt_branch,
            wt_path=_wt_path,
        )
        if _wt_ok:
            # Cap cwd so <cwd>/.ostk/ostk.sock fits macOS sun_path (104).
            # Long agent names + nested .claude/worktrees/agent-<name>/
            # otherwise blow past the limit, the ostk daemon's bind()
            # fails, and the MCP server falls back to degraded mode
            # without bash/read/fs_ops — which silently pushes the
            # subagent onto native tools and reintroduces the cwd-leak.
            _spawn_cwd = _short_cwd_for_worktree(_wt_path)
            # Also set OSTK_PROJECT_ROOT and OSTK_ROOT to the short path
            # so the daemon uses it to compute the socket path instead of
            # walking up from getcwd() (macOS resolves symlinks in cwd via
            # getcwd(), defeating the short-cwd approach for socket binding).
            # The short path is a /tmp symlink that resolves to the real
            # worktree, so all file I/O still reaches the correct checkout.
            # CLAUDE_PROJECT_DIR must stay as the real path so Claude Code
            # hooks and the worktree guard resolve to the correct checkout.
            env_overrides["OSTK_PROJECT_ROOT"] = _spawn_cwd
            env_overrides["OSTK_ROOT"] = _spawn_cwd
            # CLAUDE_PROJECT_DIR is inherited from the parent env
            # (the spawn env starts as a copy of os.environ), which points
            # to the parent repo. Without this override, hooks and
            # Claude Code itself resolve file paths against the parent
            # checkout, causing all edits to land there instead of the
            # worktree. This is the cwd-leak root cause (→932, →916).
            env_overrides["CLAUDE_PROJECT_DIR"] = str(_wt_path)
            # heartbeat-agent.sh reads YOUROS_AGENT_NAME to route its
            # hook-fired heartbeats to the correct registered row.
            # Without this the hook derives a session_id-based name
            # that never matches the custom-named agent row, leaving
            # last_heartbeat_at null for the entire run.
            env_overrides["YOUROS_AGENT_NAME"] = name
            _worktree_path = str(_wt_path)
            _worktree_branch = _wt_branch
            # L2.3 (→902): sync .claude/ into the new worktree so hook
            # edits do not leak across sessions.
            await _sync(PROJECT_ROOT / ".claude", _wt_path / ".claude")
            # Copy .mcp.json so subagents get the ostk MCP server (→952).
            _mcp_src = PROJECT_ROOT / ".mcp.json"
            if _mcp_src.exists() and _wt_path.is_dir():
                import shutil as _shutil
                _shutil.copy2(_mcp_src, _wt_path / ".mcp.json")
            # Anchor the ostk MCP root to this worktree. Without .ostk/
            # here the server traverses up to .claude/worktrees/.ostk/
            # (the shared parent state) and roots all bash calls to
            # .claude/worktrees/, where git writes land on parent main
            # instead of the worktree branch. (→932)
            (_wt_path / ".ostk").mkdir(parents=True, exist_ok=True)
            # Symlink the main daemon socket into the worktree's .ostk/
            # so ostk kernel serve bridges to the running daemon instead
            # of spawning a new one. Without this symlink the worktree
            # kernel starts in standalone mode, fails to initialize, and
            # only registers static tools (context/search/nudge) while
            # fs/shell/bash stay missing — leaving the subagent with no
            # way to write files when the hook blocks native fallbacks.
            _main_sock = PROJECT_ROOT / ".ostk" / "ostk.sock"
            _wt_sock = _wt_path / ".ostk" / "ostk.sock"
            # Point OSTK_SOCKET directly at the main daemon socket so the
            # subagent's ostk CLI connects there without any path computation.
            # agent_loop.rs resolve_socket_path() checks OSTK_SOCKET first,
            # bypassing the <cwd>/.ostk/ostk.sock fallback that would exceed
            # macOS sun_path (104) for long worktree names. This is the
            # primary mitigation for the degraded-MCP mode bug (→1177).
            env_overrides["OSTK_SOCKET"] = str(_main_sock)
            logger.info(
                "spawn.ostk_socket_env.set name=%s socket=%s",
                name, _main_sock,
            )
            if not _wt_sock.exists():  # always symlink, even if daemon not yet running
                try:
                    os.symlink(str(_main_sock), str(_wt_sock))
                    logger.info(
                        "spawn.ostk_sock_symlink.created name=%s target=%s",
                        name, _main_sock,
                    )
                except Exception as _sym_exc:
                    logger.warning(
                        "spawn.ostk_sock_symlink.failed name=%s err=%s",
                        name, _sym_exc,
                    )
            # Symlink needles/ to the main repo's shared needle store
            # so `ostk work add` from the worktree writes to the same
            # issues.jsonl the backend reads. Without this symlink, when
            # the daemon is not running, ostk falls back to direct file
            # I/O and fails with "issues.lock: No such file or directory"
            # because .ostk/needles/ does not exist in the worktree.
            # Needles filed from the worktree then never appear in the UI.
            # (→1143)
            _main_needles = PROJECT_ROOT / ".ostk" / "needles"
            _wt_needles = _wt_path / ".ostk" / "needles"
            if _main_needles.exists() and not _wt_needles.exists():
                try:
                    os.symlink(str(_main_needles), str(_wt_needles))
                    logger.info(
                        "spawn.ostk_needles_symlink.created name=%s target=%s",
                        name, _main_needles,
                    )
                except Exception as _sym_exc:
                    logger.warning(
                        "spawn.ostk_needles_symlink.failed name=%s err=%s",
                        name, _sym_exc,
                    )
        else:
            logger.warning(
                "spawn.worktree.fork_failed name=%s err=%s",
                name, _wt_err,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "worktree_creation_failed",
                    "message": (
                        "Could not create a git worktree for this agent. "
                        f"Reason: {_wt_err or 'unknown'}. "
                        "Fix the underlying git error and retry."
                    ),
                },
            )
    except HTTPException:
        raise
    except Exception as _wt_exc:
        logger.warning(
            "spawn.worktree.fork_exception name=%s err=%s",
            name, _wt_exc,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "worktree_creation_exception",
                "message": (
                    "An unexpected error prevented worktree creation for this agent. "
                    f"Error: {_wt_exc}. "
                    "Fix the underlying error and retry."
                ),
            },
        )
    return _spawn_cwd, _worktree_path, _worktree_branch, env_overrides


def _compose_spawn_meta(
    body: "AgentSpawn",
    *,
    model: str,
    pid: "Optional[int]" = None,
    transcript_path: "Optional[str]" = None,
    worktree_path: "Optional[str]" = None,
    worktree_branch: "Optional[str]" = None,
) -> dict:
    """Build the agent_metadata row for a fresh spawn (→2944, shared).

    Extracted from the bespoke spawn path so the ostk-run default path
    records the exact same metadata: the scaffold-commit watcher reads
    ``worktree_path``, /recover reads prompt/model/budget/recovery_count,
    and the terminal-status cleanup reads isolation + needle ids.
    """
    now_spawn = datetime.now(timezone.utc).isoformat()
    spawn_meta: dict = {
        "status": "running",
        "spawned_at": now_spawn,
        "last_heartbeat_at": now_spawn,
        "budget": str(body.budget),
        "model": model,
        "tokens_used": 0,
    }
    if pid is not None:
        spawn_meta["pid"] = pid
    if body.token_limit is not None:
        spawn_meta["token_limit"] = body.token_limit
    # Record the resolved template name so completion hooks (e.g. the
    # Roadmap output capture) can detect which marketplace template
    # produced the final answer. Stored raw; downstream comparisons
    # lowercase and strip before matching.
    if body.template:
        spawn_meta["template"] = body.template
        # Resolve the template's ``produces_doc`` flag at spawn time and
        # stamp it into metadata so the /complete hook does not need to
        # re-look up the template (which may have been edited between
        # spawn and complete).
        try:
            from services.agent_templates_store import agent_templates_store
            tpl = agent_templates_store.get_by_name_or_alias(body.template)
            if tpl and tpl.get("produces_doc"):
                spawn_meta["template_produces_doc"] = True
        except Exception:
            pass
    # Persist the caller-supplied human-readable task and description
    # so the Agents page can show a friendly title instead of the
    # opaque internal name.
    if body.task:
        spawn_meta["task"] = body.task
    if body.description:
        spawn_meta["description"] = body.description
    # Originating task linkage: persist the task_id into metadata so
    # that the task list endpoint can cheaply compute "is a live
    # agent working on this task?".
    if body.task_id:
        spawn_meta["task_id"] = body.task_id
    if body.needle_id:
        spawn_meta["needle_id"] = body.needle_id
    else:
        _inferred_nid = _infer_needle_id(
            body.name or "",
            body.task or "",
            body.description or "",
            body.prompt or "",
        )
        if _inferred_nid:
            spawn_meta["needle_id"] = _inferred_nid
    # Auto-claim: store all →NNN tokens so in_progress is shown for
    # every referenced needle, not just the first one (→1204).
    _all_nids = _extract_all_needle_ids(
        body.task or "",
        body.description or "",
        body.prompt or "",
    )
    if _all_nids:
        spawn_meta["needle_ids"] = _all_nids
    # Worktree isolation: record the fork location so /cleanup and
    # the pre-merge gate can find the branch later. Keys are only
    # set when the fork actually succeeded.
    if body.isolation == "worktree" and worktree_path:
        spawn_meta["worktree_path"] = worktree_path
        spawn_meta["worktree_branch"] = worktree_branch
        spawn_meta["isolation"] = "worktree"
    else:
        spawn_meta["isolation"] = body.isolation or "none"
    # Always stamp a real source so the audit-log "source=audit"
    # placeholder never wins on the list endpoint.
    spawn_meta["source"] = body.source or "api"
    # Preserve recovery_count across re-spawns so the cap is tracked.
    existing_meta = agent_metadata.get(body.name) or {}
    if existing_meta.get("recovery_count"):
        spawn_meta["recovery_count"] = existing_meta["recovery_count"]
    # Workflow linkage: carry forward from caller or existing metadata.
    workflow_run_id = body.workflow_run_id or existing_meta.get("workflow_run_id")
    if workflow_run_id:
        spawn_meta["workflow_run_id"] = workflow_run_id
    # Record the transcript path so a subsequent /register call can
    # preserve it instead of falling back to auto-discovery. The
    # ostk-run path passes None (ostk owns the transcript there).
    if transcript_path:
        spawn_meta["transcript_path"] = transcript_path
    # Store the original prompt (capped at 4000 chars) and locks so
    # ghost-retry and /recover can re-spawn with the same task.
    if body.prompt:
        spawn_meta["prompt"] = body.prompt[:4000]
    if body.locks:
        spawn_meta["locks"] = list(body.locks)
    # Spawn provenance: persist so GET /agents exposes them and the
    # cancel-all guard can read user_authored.
    if body.originating_session_id:
        spawn_meta["originating_session_id"] = body.originating_session_id
    if body.originating_user_message_id:
        spawn_meta["originating_user_message_id"] = body.originating_user_message_id
    spawn_meta["user_authored"] = (
        bool(body.user_authored)
        if body.user_authored is not None
        else bool(body.originating_session_id and body.originating_user_message_id)
    )
    if body.notify:
        spawn_meta["notify"] = body.notify
    return spawn_meta


async def _legacy_bespoke_spawn(body: AgentSpawn, request: Request, response: Response, _brief_warning):
    # Decide isolation BEFORE any I/O so a later worktree fork can honor
    # the result. decide_isolation respects an explicit caller value and
    # otherwise picks "worktree" for code-edit verbs, "none" for
    # research-only verbs. See services/spawn_isolation.py.
    from services.spawn_isolation import (
        acquire_spawn_locks as _acquire_spawn_locks,
        decide_isolation as _decide_isolation,
        release_spawn_locks as _release_spawn_locks,
        validate_locks_for_spawn as _validate_locks_for_spawn,
    )
    # If the caller named a template, honour the agentfile's ISOLATION
    # directive before verb detection runs. Without this, prompts containing
    # code-edit verbs ("build", "create", "add") make decide_isolation pick
    # "worktree", which then fails lock-validation with HTTP 400 because the
    # frontend never passes locks for template spawns. Templates like roadmap
    # declare ISOLATION nono, explicitly opting out of worktree isolation.
    # This block was introduced in 4b6af76 and accidentally dropped by
    # dafd9f3 (file-upload feature). Keep it above _decide_isolation always.
    # CRITICAL-BLOCK-DO-NOT-REMOVE: isolation pre-resolution for template spawns
    # Without this, prompts with code-edit verbs ("build", "create", "add") make
    # decide_isolation pick "worktree" for template spawns. validate_locks_for_spawn
    # then rejects with HTTP 400 (frontend never sends locks for templates), the
    # optimistic placeholder is removed, and the agent never appears in /api/agents.
    # This block was introduced in 4b6af76 and accidentally dropped by dafd9f3
    # (file-upload feature, large agents.py rewrite). Keep it above _decide_isolation.
    # Regression test: api/tests/test_spawn_template_isolation.py
    if body.template and not body.isolation:
        try:
            from services.agentfile_parser import (
                get_agent_config_by_template as _pre_get_tpl_cfg,
            )
            from services.agent_templates_store import _BUILTIN_BY_ID, _resolve_alias
            _pre_alias = _resolve_alias(body.template)
            if _pre_alias:
                _pre_tpl_meta = _BUILTIN_BY_ID.get(_pre_alias, {})
                _pre_stem = (
                    _pre_tpl_meta.get("name", body.template)
                    .lower()
                    .replace(" ", "-")
                )
            else:
                _pre_stem = body.template.lower().replace(" ", "-")
            _pre_cfg = _pre_get_tpl_cfg(_pre_stem)
            if _pre_cfg is not None and _pre_cfg.isolation == "nono":
                body.isolation = _pre_cfg.isolation
            elif _pre_cfg is not None and _pre_cfg.isolation == "none" and not body.locks:
                # Template defaults to no-isolation (no ISOLATION directive in the
                # agentfile), and the caller sent no locks. Without this branch,
                # verb detection returns "worktree" for edit prompts, then
                # validate_locks_for_spawn rejects with 400 — the agent never
                # registers. UI template-card spawns never send locks. Task-based
                # spawns (comprehensive-build promotions) DO send locks, so they
                # skip this branch and proceed through verb detection normally,
                # preserving the auto-suffix check for re-spawned branches.
                # Regression introduced by 223a4e9 (narrowed to == "nono").
                body.isolation = "none"
        except Exception:
            pass
    # Follow-on content spawns (email drafts, slide decks, flashcards) must not
    # get worktree isolation. The verb heuristic in decide_isolation misreads
    # "write an email" / "create a slide deck" as code-edit tasks because
    # "write" and "create" are in CODE_EDIT_VERBS. When follow_on=True the
    # caller guarantees this is a content-generation task, so we force
    # isolation="none" here before the heuristic runs. Introduced for →1096.
    if body.follow_on and not body.isolation:
        body.isolation = "none"
    body.isolation = _decide_isolation(
        description=body.description,
        prompt=body.prompt,
        explicit=body.isolation,
        agent_name=body.name,
    )

    # When the expected worktree branch already has unmerged commits from a
    # prior run, auto-suffix the agent name so this spawn gets a fresh branch.
    # Without this, create_worktree() refuses to overwrite the branch (data
    # safety guard) and the spawn fails with a 500 -- making the "comprehensive
    # build" button appear broken even though the previous run's work is intact.
    if body.isolation == "worktree":
        try:
            from config import PROJECT_ROOT as _PR_unmerged
            from services.spawn_isolation import (
                branch_has_unmerged_commits as _branch_has_unmerged,
            )
            _candidate_branch = f"worktree-agent-{body.name}"
            if await _branch_has_unmerged(str(_PR_unmerged), _candidate_branch):
                import random as _random
                import string as _string_mod
                _sfx = "".join(
                    _random.choices(_string_mod.ascii_lowercase + _string_mod.digits, k=4)
                )
                _old_name = body.name
                body.name = f"{body.name}-r{_sfx}"
                logger.info(
                    "spawn.auto_suffix.unmerged_branch old=%s new=%s branch=%s",
                    _old_name, body.name, _candidate_branch,
                )
        except Exception as _sfx_exc:
            logger.debug("spawn.auto_suffix.check_failed err=%s", _sfx_exc)

    # Mandatory lock-on-spawn: edit-capable spawns (isolation resolved
    # to "worktree") MUST declare which paths they will touch so
    # parallel spawns cannot race on the same files. Read-only spawns
    # may pass ["*"] as the "won't edit anything" opt-out, or omit the
    # field. See services/spawn_isolation.py for the full contract.
    _locks_ok, _locks_err = _validate_locks_for_spawn(
        isolation=body.isolation or "none",
        locks=body.locks,
    )
    if not _locks_ok:
        raise HTTPException(status_code=400, detail=_locks_err)
    _lock_ok, _acquired_lock_keys, _lock_contenders = _acquire_spawn_locks(
        spawn_id=body.name,
        locks=body.locks,
    )
    if not _lock_ok:
        # Surface each contending spawn so the caller can decide whether
        # to wait, widen their own lock scope, or re-aim at different
        # files. Plain language: no jargon, names the field, names the
        # holder.
        detail = {
            "error": "lock_conflict",
            "message": (
                "Another spawn is already holding one of the paths this "
                "spawn asked to edit. Wait for it to finish or retry with "
                "a different path."
            ),
            "conflicts": [
                {
                    "requested": requested,
                    "held_by_spawn": holder_id,
                    "held_path": holder_raw,
                }
                for (requested, holder_id, holder_raw) in _lock_contenders
            ],
        }
        raise HTTPException(status_code=409, detail=detail)

    from config import PROJECT_ROOT
    from services.policy_enforcement import (
        check_budget,
        check_approval_required,
        get_isolation_level,
        isolation_to_permission_mode,
    )

    # Enterprise policy checks: budget limit and approval threshold
    # NOTE: _release_spawn_locks is called before every raise here because these
    # checks run OUTSIDE the inner try/except that would otherwise release the
    # locks we just acquired. Without the explicit release the locks are orphaned
    # until the TTL sweep fires (8–15 min for a typical $2 budget).
    allowed, reason = check_budget(body.budget)
    if not allowed:
        try:
            _release_spawn_locks(spawn_id=body.name)
        except Exception:
            pass
        raise HTTPException(status_code=403, detail=reason)

    if check_approval_required(body.budget):
        from services import enterprise_store as _es
        _threshold = _es.get_policies().get("require_approval_above", 5.0)
        try:
            _release_spawn_locks(spawn_id=body.name)
        except Exception:
            pass
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent budget ${body.budget:.2f} requires admin approval "
                f"(limit: ${_threshold:.2f}). Ask an admin to approve or "
                f"raise the threshold in Settings > Enterprise > Policies."
            ),
        )

    # Resolve the final model id. ``model_tier`` takes precedence over
    # ``model`` when both are set. ``resolve_model`` handles unknown
    # tiers and full model ids.
    from services.model_routing import resolve_model as _resolve_model
    if getattr(body, "model_tier", None):
        model = _resolve_model(body.model_tier)
    else:
        model = MODEL_MAP.get(body.model, body.model)
    # Clean slate: purge every chat artifact from a previous run of an
    # agent with this same name BEFORE any new state lands on disk. Without
    # this purge the UI's inline chat merges stale nudges/replies from
    # ``.ostk/nudges/{name}/`` with the new run, so when Tori re-spawns a
    # Roadmap (or any template) agent she sees messages that belong to the
    # prior demo run. Also clears the in-memory session dicts so the first
    # ``GET /nudges`` poll returns empty instead of the prior session's
    # trailing entries.
    try:
        await ostk.purge_agent_chat_state(body.name)
    except Exception as _purge_exc:
        # Never let a purge failure block a spawn. A fresh spawn that
        # happens to carry one stale message is better than no agent
        # at all. Log so the operator can investigate later.
        logger.warning(
            "spawn.purge_chat_state.failed name=%s err=%s",
            body.name, _purge_exc,
        )
    nudge_history.pop(body.name, None)
    nudge_replies.pop(body.name, None)
    # Drop any cached resolver result so the next /transcript or /agents
    # list picks up the fresh state instead of returning the prior run's
    # JSONL file from the 30 second cache.
    _resolve_cache.pop(body.name, None)

    # Un-delete: if this name was previously deleted, remove it from
    # deleted_agents.json so the new row is visible in GET /api/agents.
    # Mirrors the same pattern in fleet_spawn_agents (see ~line 3442).
    _del_names = _load_deleted_agents()
    if body.name in _del_names:
        _del_names.discard(body.name)
        _save_deleted_agents(_del_names)

    # Build queue gate: comprehensive builds are limited to BUILD_CONCURRENCY
    # simultaneous runs. Excess arrivals are queued FIFO and promoted when a
    # running build finishes. The spawn lock is already acquired so queued
    # builds hold their task-specific lock until they eventually start.
    _build_state: Optional[str] = None  # set to "running"/"queued" for comprehensive builds
    _is_comprehensive = str(body.template or "").lower() in ("comprehensive", "saa")
    if _is_comprehensive:
        try:
            from services.build_queue import try_start_build as _try_start_build
            _build_state = _try_start_build(
                spawn_id=body.name,
                task_id=body.task_id or "",
                spawn_kwargs=body.model_dump(),
            )
            if _build_state == "queued":
                # Register a placeholder row so the agent is visible in the UI
                # with status "queued" before the subprocess starts.
                _now_q = datetime.now(timezone.utc).isoformat()
                agent_metadata[body.name] = {
                    "spawned_at": _now_q,
                    "status": "queued",
                    "source": body.source or "api",
                    "model": body.model,
                    "budget": body.budget,
                    "task_id": body.task_id,
                    "template": body.template,
                    "build_state": "queued",
                    "label": f"comprehensive/{body.task_id}" if body.task_id else body.name,
                }
                await _save_agent_state_async()
                logger.info("spawn.build_queue.queued name=%s", body.name)
                return {
                    "result": "queued",
                    "agent_name": body.name,
                    "build_state": "queued",
                    "status": "queued",
                    "message": "Build is waiting for a slot. It will start automatically.",
                }
        except Exception as _bq_exc:
            logger.warning("spawn.build_queue_check_failed name=%s err=%s", body.name, _bq_exc)
            # Fail-open: on queue error, proceed with normal spawn.

    transcript_path = PROJECT_ROOT / "transcripts" / f"{body.name}.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    # Prepend past memory context so the agent picks up where it left off.
    memory_ctx = agent_memory_svc.get_context(body.name)
    prompt_with_memory = (memory_ctx + body.prompt) if memory_ctx and body.prompt else body.prompt

    # Prepend shared workspace summary so agents can see findings from peers.
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
    #
    # Speed path: when the target agentfile opts in with
    # ``LIMIT quick_mode true``, we use the compact mailbox block (<800
    # chars) so the first-byte latency on short spawns drops to the
    # raw subprocess fork time. The full block stays the default so
    # existing agents are untouched.
    _quick_mode = _spawn_quick_mode(body)
    if _quick_mode:
        mailbox_block = agent_mailbox_instruction_short(body.name)
    else:
        mailbox_block = agent_mailbox_instruction(body.name)
    if _demo_mode_active():
        mailbox_block += _DEMO_BUILD_RULE
    if prompt_with_memory:
        prompt_with_memory = mailbox_block + "\n\n---\n\n" + prompt_with_memory
    else:
        prompt_with_memory = mailbox_block

    # Prepend the user's standing instructions so every spawned agent
    # follows the house rules (tone, preferred tools, how to explain
    # code, etc.) that the user saved once in Settings. Empty string
    # when the setting is blank so this is a no-op for most users.
    try:
        from services.settings_store import settings_store as _settings_store
        _standing = str(_settings_store.get("standing_instructions", "") or "").strip()
    except Exception:
        _standing = ""
    if _standing:
        _standing_block = (
            "STANDING INSTRUCTIONS (from the user, always apply):\n"
            f"{_standing}"
        )
        prompt_with_memory = _standing_block + "\n\n---\n\n" + prompt_with_memory

    # When the spawn is linked to a spec, append a one-time claim instruction
    # so the agent registers itself before starting work. Covers terminal
    # Claude/Gemini sessions not launched through the wrapper CLI. (→1425)
    if body.spec_id:
        _claim_block = build_spec_claim_block(body.spec_id, body.name)
        if _claim_block:
            prompt_with_memory = prompt_with_memory + "\n\n---\n\n" + _claim_block

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
        # Resolve aliases: "saa" -> "builder" agentfile, "PRD Draft" -> "PRD",
        # etc. The agent_templates_store knows the full alias table.
        resolved_template = body.template
        try:
            from services.agent_templates_store import _resolve_alias, _BUILTIN_BY_ID
            alias_id = _resolve_alias(body.template)
            if alias_id:
                # Map builtin-builder -> "builder" (agentfile name stem),
                # builtin-diagnose -> "diagnose", etc.
                tpl = _BUILTIN_BY_ID.get(alias_id, {})
                # Derive the agentfile stem from the display name, not the id.
                # "builtin-pm-roadmap" → id-stem "pm-roadmap" but file is "roadmap.agent".
                # "Roadmap".lower().replace(" ", "-") → "roadmap" which matches.
                # Previously used alias_id.replace("builtin-", "") which broke every
                # category-prefixed id (pm-*, sales-*, writer-*, eng-*, home-*).
                resolved_template = tpl.get("name", body.template).lower().replace(" ", "-")
        except Exception:
            pass

        template_config = get_agent_config_by_template(resolved_template)
        if template_config is None:
            available = list_available_templates()
            available_str = ", ".join(available) if available else "none found"
            try:
                _release_spawn_locks(spawn_id=body.name)
            except Exception:
                pass
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

        # Inject attached files as leading context (→1070)
        try:
            from routers.agent_uploads import build_files_context
            from services.agent_templates_store import agent_templates_store as _ats
            _tpl_rec = _ats.get_by_name_or_alias(body.template)
            if _tpl_rec is None:
                # Fall back to ID-based lookup when name resolution fails
                _tpl_rec = _ats.get_by_id(body.template)
            if _tpl_rec and _tpl_rec.get("attached_files"):
                _file_ctx = build_files_context(
                    _tpl_rec["id"], _tpl_rec["attached_files"]
                )
                if _file_ctx:
                    prompt_with_memory = _file_ctx + "\n\n---\n\n" + prompt_with_memory
        except Exception:
            pass  # file injection is best-effort; never block a spawn

        # KNOWLEDGE grounding: prepend tagged source excerpts (→1530)
        try:
            if getattr(template_config, "knowledge_tags", None):
                from services.source_library import get_knowledge_excerpts
                excerpts_block = get_knowledge_excerpts(
                    template_config.knowledge_tags,
                    body.prompt or "",
                )
                if excerpts_block:
                    prompt_with_memory = excerpts_block + "\n\n---\n\n" + prompt_with_memory
        except Exception:
            pass  # grounding is best-effort; never block a spawn
    else:
        agent_config = get_agent_config(body.name)
        quality_instructions = build_quality_gate_instructions(agent_config)
        if quality_instructions:
            prompt_with_memory = prompt_with_memory + "\n\n---\n\n" + quality_instructions

    # When the spawn is tied to a task and that task has a linked spec,
    # inject the spec's acceptance criteria directly into the prompt so
    # the builder can see the definition of done without hunting for it.
    if body.task_id:
        try:
            _docs = await ostk.list_docs()
            _ac_block = _build_spec_ac_block(body.task_id, _docs)
            if _ac_block:
                prompt_with_memory = prompt_with_memory + "\n\n---\n\n" + _ac_block
        except Exception:
            pass  # spec lookup is best-effort; never block a spawn

    # When the spawn is explicitly linked to a spec (spec_id set), flip
    # that spec's frontmatter status from "spec" (ready) to "building"
    # so the Specs page immediately shows the in-flight state. This covers
    # implementation paths that do not go through the "Build it" button
    # (e.g. a worktree agent spawned to work on a spec-linked needle).
    # Best-effort: a write failure must not block the spawn. (→1420)
    if body.spec_id:
        try:
            from routers.specs import _set_spec_status
            _set_spec_status(body.spec_id, "building")
        except Exception:
            logger.warning(
                "spawn_agent: failed to flip spec %s to building: %s",
                body.spec_id,
                "see traceback in DEBUG logs",
                exc_info=True,
            )

    # Map isolation level to Claude CLI permission mode
    _perm_mode = isolation_to_permission_mode(get_isolation_level())

    cmd = [
        CLAUDE_BIN, "--print",
        "--model", model,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", _perm_mode,
    ]

    try:
        _spawn_env = {**os.environ}
        # →2640 fix 5: only drop the API key when the host authenticates via
        # the stored claude.ai subscription. On hosts that use an apiKeyHelper
        # (external credential program in ~/.claude/settings.json), stripping
        # the key leaves the subagent with no auth signal and it dies before
        # registering, creating another ghost source.
        if _host_has_claude_subscription():
            # Drop the API key so subagents authenticate via the stored claude.ai
            # subscription instead of billing against the key's spending limit.
            _spawn_env.pop("ANTHROPIC_API_KEY", None)
        try:
            from services.tracing import get_trace_id as _get_trace_id
            _tid = _get_trace_id()
            if _tid:
                _spawn_env["TORIOS_TRACE_ID"] = _tid
        except Exception:
            pass
        # When isolation=="worktree", fork a git worktree for this agent
        # and run the subprocess there so its commits stay isolated from
        # the main tree (see spawn_burst_commit_contamination memory
        # entry). create_worktree() handles the re-spawn case: it removes
        # any prior worktree/branch with the same name before creating so
        # repeated spawns do not silently fall back to the parent checkout.
        # On fork failure, fall back to the main worktree — never fail the
        # spawn for a tooling hiccup.
        _spawn_cwd = str(PROJECT_ROOT)
        _worktree_path: Optional[str] = None
        _worktree_branch: Optional[str] = None
        if body.isolation == "worktree":
            # →2944 (rows →1885/→1887): the worktree fork, short-cwd, and env
            # injection live in _provision_worktree_isolation so the ostk-run
            # default spawn path shares these exact rails.
            (
                _spawn_cwd,
                _worktree_path,
                _worktree_branch,
                _wt_env_overrides,
            ) = await _provision_worktree_isolation(body.name)
            _spawn_env.update(_wt_env_overrides)
        # →1225: Remap absolute main-checkout paths in prompt to worktree paths.
        # Briefs from the parent session may include absolute paths that point
        # to the main checkout. Replacing them ensures fs_ops writes land in
        # the agent's worktree, not on main.
        if _worktree_path and prompt_with_memory:
            _main_prefix = str(PROJECT_ROOT) + "/"
            _wt_prefix = _worktree_path + "/"
            if _main_prefix in prompt_with_memory:
                prompt_with_memory = prompt_with_memory.replace(_main_prefix, _wt_prefix)
                logger.info(
                    "spawn.prompt_path_remap name=%s rewrote main-checkout paths to worktree",
                    body.name,
                )
        # →1240: Inject worktree cwd header so agents always pass cwd= to bash.
        # mcp__ostk__bash routes through the MAIN ostk daemon (OSTK_SOCKET
        # points to the shared main socket). sh_run.rs defaults cwd to the
        # daemon's project_root (= main checkout) when no cwd arg is given.
        # Agents that call bash without cwd= commit and write to main instead
        # of their worktree branch. The header below is prepended to every
        # worktree agent prompt so the instruction is visible before any task.
        if _worktree_path:
            _wt_cwd_header = (
                f"[WORKTREE CWD →1240] Your git worktree is: {_worktree_path}\n"
                f"REQUIRED — every mcp__ostk__bash call MUST include "
                f'cwd="{_worktree_path}". Without it, bash runs in the MAIN '
                f"repo and commits land on main instead of your branch.\n"
                f"REQUIRED — every mcp__ostk__fs_ops call MUST use absolute "
                f"paths starting with {_worktree_path}/ (paths in this prompt "
                f"are already remapped).\n"
                f"[WORKTREE COMMIT →2503] Your work only counts once it is "
                f"committed on your worktree branch. REQUIRED — run "
                f'git add -A && git commit (with cwd="{_worktree_path}") '
                f"BEFORE starting long verify steps (pytest, tsc, vitest), "
                f"then add follow-up commits as needed. A failed quality gate "
                f"still leaves a recoverable commit. NEVER end your session or "
                f"POST /complete while the worktree has uncommitted changes — "
                f"an exit with a dirty worktree is a failed run."
            )
            prompt_with_memory = (
                _wt_cwd_header + "\n\n---\n\n" + (prompt_with_memory or "")
            ).strip() or None
            logger.info(
                "spawn.worktree_cwd_header.injected name=%s worktree=%s",
                body.name, _worktree_path,
            )
        # →2603: never exec a real claude subprocess from inside pytest.
        # Raised before any on-disk artifact (transcript, prompt temp file)
        # is created. The HTTPException propagates via the explicit
        # `except HTTPException: raise` handler below, so callers see the
        # 503 and its detail unchanged.
        if _pytest_blocks_real_spawn():
            logger.warning(
                "spawn.blocked_in_pytest name=%s — bespoke path reached with an "
                "unmocked create_subprocess_exec under pytest (→2603)",
                body.name,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Refusing to launch a real agent subprocess inside pytest "
                    "(→2603). Mock asyncio.create_subprocess_exec in the test, "
                    "or set YOUROS_SPAWN_ALLOW_REAL_IN_TESTS=1 for a "
                    "deliberate end-to-end run."
                ),
            )
        # Create the transcript file immediately so the resolver can find it
        # even before the subprocess writes its first byte. The _drain_stdout
        # coroutine below writes+flushes each chunk so the file grows in real
        # time during the run.
        transcript_path.touch()
        # Write the prompt to a temp file BEFORE spawning the subprocess.
        # claude --print has a 3-second stdin timeout. Under event-loop
        # contention (large agent lists, stale worktrees) the async
        # stdin.write can miss that window, producing 0-byte transcripts.
        # A pre-written file makes the data available the instant the
        # CLI starts reading, eliminating the race entirely.
        import tempfile as _tempfile
        _prompt_fh = None
        _prompt_temp_path = None
        _stdin_source = asyncio.subprocess.DEVNULL
        if prompt_with_memory:
            _fd, _prompt_temp_path = _tempfile.mkstemp(
                prefix=f"spawn-{body.name}-", suffix=".prompt"
            )
            os.write(_fd, prompt_with_memory.encode())
            os.close(_fd)
            _prompt_fh = open(_prompt_temp_path, "rb")
            _stdin_source = _prompt_fh

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=_stdin_source,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_spawn_cwd,
            env=_spawn_env,
            start_new_session=True,
        )

        if _prompt_fh is not None:
            _prompt_fh.close()
        if _prompt_temp_path:
            try:
                os.unlink(_prompt_temp_path)
            except Exception:
                pass

        active_agents[body.name] = proc

        # Continuously drain stderr to a sibling log file. Without this,
        # the stderr=PIPE buffer fills (~64KB on darwin) and the subprocess
        # blocks on its next stderr write. claude --print writes plugin
        # sync, telemetry, and warning messages to stderr during start-up,
        # so a busy run hits the buffer cap in seconds. When that happens
        # the child stalls forever, transcript stays 0 bytes, and the 90s
        # demo wall clock force-completes the agent with no output. This
        # was the root cause of the e2e-roadmap-probe series hanging at
        # 0 bytes through 70s+ even after stdin EOF was fixed.
        # Also retains the fast-exit log: if the child dies in under 5s
        # we mirror the captured stderr into the transcript so the operator
        # can see the CLI error message without re-running under strace.
        stderr_log_path = transcript_path.with_suffix(transcript_path.suffix + ".stderr.log")

        async def _drain_stderr(p, name: str, tpath: Path, slog: Path, template: str = "") -> None:
            buffered = bytearray()
            try:
                with open(str(slog), "wb") as sfh:
                    while True:
                        if p.stderr is None:
                            break
                        chunk = await p.stderr.read(4096)
                        if not chunk:
                            break
                        try:
                            sfh.write(chunk)
                            sfh.flush()
                        except Exception:
                            pass
                        if len(buffered) < 8192:
                            buffered.extend(chunk[: 8192 - len(buffered)])
            except Exception as exc:
                logger.warning("spawn.stderr_drain name=%s err=%s", name, exc)
            try:
                rc = p.returncode
                if rc is None:
                    try:
                        await asyncio.wait_for(p.wait(), timeout=2.0)
                        rc = p.returncode
                    except Exception:
                        rc = None
                if rc not in (None, 0) and buffered:
                    try:
                        with open(str(tpath), "a") as fh:
                            fh.write(
                                f"\n--- subprocess exited {rc} with stderr (head) ---\n"
                            )
                            fh.write(buffered.decode("utf-8", errors="replace"))
                    except Exception:
                        pass
                    logger.warning(
                        "spawn.fast_exit name=%s rc=%s stderr=%r",
                        name, rc, bytes(buffered)[:400],
                    )
                if rc not in (None, 0):
                    _fail_meta = agent_metadata.get(name)
                    if _fail_meta and _fail_meta.get("status") == "running":
                        _set_agent_status(name, "failed", completed_at=datetime.now(timezone.utc).isoformat(), error=f"subprocess exited {rc}")
                        await _save_agent_state_async()
                        logger.info("spawn.marked_failed name=%s rc=%s", name, rc)
                # Quick-mode roadmap agents write JSON to stdout (transcript)
                # and exit without calling /complete, so _save_agent_output_to_files
                # is never triggered by the normal /complete path. Fire it here
                # once the subprocess exits cleanly.
                if rc == 0 and _is_roadmap_agent(name, template):
                    try:
                        content = tpath.read_text(encoding="utf-8", errors="replace").strip()
                        if content:
                            _save_agent_output_to_files(name, content)
                        _now_c = datetime.now(timezone.utc).isoformat()
                        _m = agent_metadata.get(name)
                        if _m and _m.get("status") == "running":
                            _m["status"] = "completed"
                            _m["completed_at"] = _now_c
                            await _save_agent_state_async()
                    except Exception as _qc_exc:
                        logger.warning("roadmap.quick_complete name=%s err=%s", name, _qc_exc)
            except Exception:
                pass
            # Release every path lock this spawn acquired. Always runs
            # after the subprocess exits, regardless of return code, so
            # a failed edit spawn does not orphan its locks. The
            # registry is the source of truth; ostk lock release fires
            # as a best-effort side effect. See services/spawn_isolation.
            try:
                _released = _release_spawn_locks(spawn_id=name)
                if _released:
                    logger.info(
                        "spawn.locks.released name=%s keys=%s",
                        name, _released,
                    )
            except Exception as _rel_exc:
                logger.warning(
                    "spawn.locks.release_failed name=%s err=%s",
                    name, _rel_exc,
                )
            # Clean up the git worktree so the next re-spawn of this agent
            # name gets a clean slate. Without this, `git worktree add -b
            # worktree-agent-<name>` fails on the second spawn because the
            # branch / locked worktree still exists, and the handler silently
            # falls back to the parent checkout where all agents race.
            try:
                _meta_snap = agent_metadata.get(name) or {}
                _cleanup_wt_path = _meta_snap.get("worktree_path")
                _cleanup_wt_branch = _meta_snap.get("worktree_branch")
                if _cleanup_wt_path and _cleanup_wt_branch:
                    from services.spawn_isolation import remove_worktree as _remove_wt
                    await _remove_wt(
                        project_root=PROJECT_ROOT,
                        wt_path=_cleanup_wt_path,
                        branch=_cleanup_wt_branch,
                    )
            except Exception as _cleanup_exc:
                logger.warning(
                    "spawn.worktree.cleanup_failed name=%s err=%s",
                    name, _cleanup_exc,
                )
            # Build queue: free this build's slot and promote the next queued
            # build if one is waiting. Only runs for comprehensive/saa builds.
            try:
                if (template or "").lower() in ("comprehensive", "saa"):
                    from services.build_queue import finish_build as _finish_build
                    _next_bq = _finish_build(spawn_id=name)
                    if _next_bq is not None:
                        logger.info(
                            "spawn.build_queue.promoting spawn_id=%s task_id=%s",
                            _next_bq.spawn_id, _next_bq.task_id,
                        )
                        from models.schemas import AgentSpawn as _AgentSpawnCls

                        async def _spawn_queued_next(_entry=_next_bq) -> None:
                            try:
                                _nb = _AgentSpawnCls(**_entry.spawn_kwargs)
                                await spawn_agent(_nb, request=None)
                            except Exception as _sq_exc:
                                logger.error(
                                    "spawn.build_queue.promote_failed spawn_id=%s err=%s",
                                    _entry.spawn_id, _sq_exc,
                                )
                                # Return the entry to the front of the queue so
                                # the phantom running slot is freed and the build
                                # retries when the next slot opens. Without this
                                # the entry stays in _running forever, permanently
                                # blocking one concurrency slot (→2497).
                                try:
                                    from services.build_queue import return_to_queue as _return_to_queue
                                    _return_to_queue(_entry)
                                except Exception:
                                    pass
                        asyncio.create_task(_spawn_queued_next())
            except Exception as _bq_finish_exc:
                logger.warning(
                    "spawn.build_queue.finish_failed name=%s err=%s",
                    name, _bq_finish_exc,
                )

        async def _drain_stdout(p, name: str, tpath: Path) -> None:
            """Drain subprocess stdout to the transcript file, flushing after each chunk.

            Subprocess output is stream-json (--output-format stream-json --verbose).
            JSON events are parsed so only model text content is written to the
            transcript; hook/system events update the watchdog timer without
            polluting the transcript file with raw JSON.

            Three-threshold watchdog:
              Phase 1 — no bytes at all: _STDOUT_FIRST_BYTE_LIMIT_SECONDS (45s).
                A subprocess that never writes is wedged at startup.
              Phase 2 — bytes (hook events) but no model output:
                _STDOUT_API_HANG_LIMIT_SECONDS (120s). Hooks prove the process
                started; the extended window covers the API round-trip itself.
              Phase 3 — had model output, now silent:
                _STDOUT_SILENCE_LIMIT_SECONDS (300s). Mid-stream stall.
            """
            _had_any_byte = False
            _had_model_output = False
            _had_text_output = False  # True only when assistant text is written to transcript
            _last_any_byte_at = [time.monotonic()]
            _first_any_byte_at = [time.monotonic()]  # set once; Phase 2 uses this
            _last_model_output_at = [time.monotonic()]
            _open_tool_calls = [0]  # incremented on tool_use, decremented on tool_result

            # stream-json event types that indicate model activity (excluding
            # "assistant" which is handled separately to extract nested text)
            _MODEL_EVENT_TYPES = frozenset(("tool_result", "thinking"))

            async def _heartbeat_loop() -> None:
                while True:
                    await asyncio.sleep(_TRANSCRIPT_FLUSH_INTERVAL)
                    if p.returncode is not None:
                        break
                    now = time.monotonic()
                    if not _had_any_byte:
                        silent_for = now - _last_any_byte_at[0]
                        limit = _STDOUT_FIRST_BYTE_LIMIT_SECONDS
                        hang_kind = "startup (no first byte)"
                    elif not _had_model_output:
                        # Use _first_any_byte_at so periodic hook events
                        # (heartbeat hooks, tool events) don't reset the clock
                        # and let an API-hung subprocess survive indefinitely.
                        silent_for = now - _first_any_byte_at[0]
                        limit = _STDOUT_API_HANG_LIMIT_SECONDS
                        hang_kind = "api-hang (hooks only, no model output)"
                    else:
                        silent_for = now - _last_model_output_at[0]
                        limit = _STDOUT_SILENCE_LIMIT_SECONDS
                        hang_kind = "mid-stream"
                    if silent_for > limit:
                        if _open_tool_calls[0] > 0:
                            # Subprocess is legitimately waiting for a tool result;
                            # suppress the kill and let the clock reset when the
                            # tool_result event arrives.
                            continue
                        try:
                            with open(str(tpath), "a") as fh:
                                fh.write(
                                    f"\nAgent '{name}' subprocess silent for "
                                    f"{int(silent_for)}s ({hang_kind}) - "
                                    f"killing wedged process.\n"
                                )
                            logger.warning(
                                "spawn.silent_kill name=%s silent_s=%.0f hang_kind=%s",
                                name, silent_for, hang_kind,
                            )
                            import signal as _sig
                            try:
                                _pgid = os.getpgid(p.pid)
                                _own_pgid = os.getpgid(os.getpid())
                                if _pgid != _own_pgid:
                                    os.killpg(_pgid, _sig.SIGKILL)
                                else:
                                    p.kill()
                            except (ProcessLookupError, OSError):
                                try:
                                    p.kill()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        break
                    try:
                        ts = datetime.now(timezone.utc).isoformat()
                        with open(str(tpath), "ab") as _fh:
                            _fh.write(f"\n[heartbeat ts={ts}]\n".encode())
                            _fh.flush()
                    except Exception:
                        pass

            _hb_task = asyncio.create_task(_heartbeat_loop())
            try:
                with open(str(tpath), "ab") as tfh:
                    _json_buf = b""
                    while True:
                        if p.stdout is None:
                            break
                        chunk = await p.stdout.read(4096)
                        if not chunk:
                            break
                        if not _had_any_byte:
                            _first_any_byte_at[0] = time.monotonic()
                        _had_any_byte = True
                        _last_any_byte_at[0] = time.monotonic()
                        _json_buf += chunk
                        # Process complete newline-delimited JSON events
                        while b"\n" in _json_buf:
                            line, _json_buf = _json_buf.split(b"\n", 1)
                            if not line.strip():
                                continue
                            if len(line) > 1_000_000:
                                # Pathologically large stream line: skip parsing entirely to avoid
                                # deep-recursion / huge-traceback event-loop wedges (->2018).
                                continue
                            try:
                                event = json.loads(line.decode("utf-8", errors="replace"))
                                etype = event.get("type")
                                if etype == "assistant":
                                    # stream-json wraps text in assistant.message.content[]
                                    for block in event.get("message", {}).get("content", []):
                                        btype = block.get("type")
                                        if btype == "text":
                                            text = block.get("text", "")
                                            if text:
                                                _had_model_output = True
                                                _had_text_output = True
                                                _last_model_output_at[0] = time.monotonic()
                                                try:
                                                    tfh.write(text.encode("utf-8", errors="replace"))
                                                    tfh.flush()
                                                except Exception:
                                                    pass
                                        elif btype in ("tool_use",):
                                            _had_model_output = True
                                            _last_model_output_at[0] = time.monotonic()
                                            _open_tool_calls[0] += 1
                                elif etype in _MODEL_EVENT_TYPES:
                                    # tool_result etc: update watchdog, skip raw JSON in transcript
                                    _had_model_output = True
                                    _last_model_output_at[0] = time.monotonic()
                                    if etype == "tool_result":
                                        _open_tool_calls[0] = max(0, _open_tool_calls[0] - 1)
                                # system/hook events: _had_any_byte already set; skip transcript
                            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError):
                                # Non-JSON / pathological line. RecursionError + ValueError are caught here
                                # so a deeply-nested payload can never escape to uvloop's default exception
                                # handler and wedge the event loop with a huge traceback write (->2018).
                                if line.strip() and len(line) <= 1_000_000:
                                    try:
                                        tfh.write(line + b"\n")
                                        tfh.flush()
                                    except Exception:
                                        pass
                                    _had_model_output = True
                                    _had_text_output = True
                                    _last_model_output_at[0] = time.monotonic()
                    # Flush any partial line left in the buffer
                    if _json_buf.strip():
                        try:
                            event = json.loads(_json_buf.decode("utf-8", errors="replace"))
                            etype = event.get("type")
                            if etype == "assistant":
                                for block in event.get("message", {}).get("content", []):
                                    if block.get("type") == "text":
                                        text = block.get("text", "")
                                        if text:
                                            _had_model_output = True
                                            _had_text_output = True
                                            try:
                                                tfh.write(text.encode("utf-8", errors="replace"))
                                                tfh.flush()
                                            except Exception:
                                                pass
                                    elif block.get("type") in ("tool_use",):
                                        _had_model_output = True
                            elif etype in _MODEL_EVENT_TYPES:
                                _had_model_output = True
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            try:
                                tfh.write(_json_buf + b"\n")
                                tfh.flush()
                            except Exception:
                                pass
                            _had_model_output = True
                            _had_text_output = True
                # All stdout consumed. Write a diagnostic note when:
                #   - No model output at all → "no stdout output" (existing behaviour).
                #   - Had tool calls but no text → new: "tools only, no text summary".
                # Both cases keep transcript_bytes > 0 so ghost-detection doesn't fire,
                # and give the user a useful explanation instead of a silent heartbeat log.
                try:
                    if not _had_model_output:
                        _rc = getattr(p, "returncode", None)
                        _rc_str = str(_rc) if _rc is not None else "unknown"
                        with open(str(tpath), "a") as fh:
                            fh.write(
                                f"Agent '{name}' subprocess exited (rc={_rc_str})"
                                f" with no stdout output.\n"
                            )
                        logger.warning(
                            "spawn.empty_transcript name=%s rc=%s",
                            name, _rc,
                        )
                    elif not _had_text_output:
                        # Agent ran tool calls but never emitted assistant text.
                        # The transcript shows only heartbeats — add a note so
                        # the user understands why and where to find results.
                        _rc = getattr(p, "returncode", None)
                        _rc_str = str(_rc) if _rc is not None else "unknown"
                        with open(str(tpath), "a") as fh:
                            fh.write(
                                f"\nAgent '{name}' completed via tool calls (rc={_rc_str})"
                                f" without emitting a text summary.\n"
                                f"Results may be in agent memory:"
                                f" GET /api/agents/{name}/memory\n"
                            )
                        logger.info(
                            "spawn.tools_only_transcript name=%s rc=%s",
                            name, _rc,
                        )
                except Exception:
                    pass
            except Exception as exc:
                logger.warning("spawn.stdout_drain name=%s err=%s", name, exc)
            finally:
                _hb_task.cancel()
                try:
                    await asyncio.wait_for(_hb_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        try:
            asyncio.create_task(
                _drain_stderr(proc, body.name, transcript_path, stderr_log_path, body.template or "")
            )
        except Exception:
            # If we cannot schedule the drain task, the subprocess will
            # never trigger the drain's release path. Release the locks
            # now so the next spawn on the same path can proceed. The
            # drain-path release is still the preferred path on the
            # happy case; this is belt-and-suspenders.
            try:
                _release_spawn_locks(spawn_id=body.name)
            except Exception:
                pass
        try:
            asyncio.create_task(
                _drain_stdout(proc, body.name, transcript_path)
            )
        except Exception:
            pass
        # →2640 fix 4: arm the startup-deadline watchdog so a process that
        # hangs on a network call with a 0-byte transcript is killed after
        # STARTUP_DEADLINE_SECONDS instead of ghosting as "running" forever.
        # Strong-ref pattern mirrors _unlock_worktree_tasks (→2627): hold
        # the task in a module-level set so asyncio GC cannot drop it mid-run,
        # and discard on done so the set does not grow unboundedly.
        try:
            _wd_task = asyncio.create_task(
                _startup_deadline_watchdog(proc, body.name, transcript_path)
            )
            _startup_watchdog_tasks.add(_wd_task)
            _wd_task.add_done_callback(_startup_watchdog_tasks.discard)
        except Exception:
            pass
        # Kick off the ack bot so inline chat gets a warm acknowledgment
        # within two seconds even when the subagent is locked inside a
        # long tool call. The bot polls /nudges on its own cadence and
        # tears itself down when the agent status flips to terminal.
        try:
            chat_ack_bot.start(body.name)
        except Exception as _ack_exc:
            logger.warning("failed to start ack bot for %s: %s", body.name, _ack_exc)
        # →2944 (row →1886): metadata composition is shared with the ostk-run
        # default path via _compose_spawn_meta so both spawn flavors write
        # the same row (scaffold-commit watcher, /recover, cleanup).
        spawn_meta: dict = _compose_spawn_meta(
            body,
            model=model,
            pid=proc.pid,
            transcript_path=str(transcript_path),
            worktree_path=_worktree_path,
            worktree_branch=_worktree_branch,
        )
        agent_metadata[body.name] = spawn_meta
        await _save_agent_state_async()
        _set_agent_status(body.name, "running")

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

        # Originating task linkage is stored via spawn_meta["task_id"]
        # above. The effective ``in_progress`` label on the task is
        # computed on read (see routers.tasks.list_tasks and the
        # ``get_running_task_ids`` helper in this module) for
        # backward-compat overlay. Additionally, if a needle_id was
        # resolved, we persistently write in_progress to issues.jsonl
        # so the status survives across agent completion and is visible
        # to ostk CLI readers, not just the API overlay. The needle
        # stays in_progress until the branch merges to main, at which
        # point the auto-merge path calls close_task (→1714).
        _spawn_nid = spawn_meta.get("needle_id")
        if _spawn_nid:
            _fire_set_task_in_progress(_spawn_nid)

        return {
            "result": f"Agent '{body.name}' spawned",
            "name": body.name,
            "pid": proc.pid,
            "transcript": str(transcript_path),
            "build_state": _build_state,
            "brief_warning": _brief_warning.message if _brief_warning else None,
        }
    except HTTPException:
        # Preserve explicit HTTPException codes raised inside the try
        # block (template lookup 400s, etc.). Without this the catch-all
        # below would clobber the real status code.
        try:
            _release_spawn_locks(spawn_id=body.name)
        except Exception:
            pass
        raise
    except FileNotFoundError as fnf:
        # ENOENT inside spawn is almost always a stale CLAUDE_BIN path:
        # ``tori()`` puts a tmp dir first on PATH at shell init, the
        # backend imports this module and caches CLAUDE_BIN from that
        # PATH, then the tmp dir gets cleaned up. The subprocess exec
        # then fails with the opaque "[Errno 2] No such file or
        # directory" and no filename. Surface the real path and retry
        # once with a freshly resolved claude binary.
        logger.exception(
            "spawn.enoent name=%s cmd0=%s cwd=%s filename=%s",
            body.name,
            cmd[0] if cmd else None,
            str(PROJECT_ROOT),
            getattr(fnf, "filename", None),
        )
        missing = getattr(fnf, "filename", None) or (cmd[0] if cmd else "unknown")
        import shutil as _shutil_retry
        fresh_claude = _shutil_retry.which("claude")
        if (
            fresh_claude
            and cmd
            and (cmd[0] == CLAUDE_BIN or "claude" in str(cmd[0]).lower())
            and fresh_claude != cmd[0]
        ):
            logger.warning(
                "spawn.claude_bin_stale old=%s fresh=%s name=%s",
                cmd[0], fresh_claude, body.name,
            )
            try:
                cmd[0] = fresh_claude
                # Update module-level CLAUDE_BIN so later spawns in this
                # process hit the fresh path directly instead of retry.
                globals()["CLAUDE_BIN"] = fresh_claude
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=open(str(transcript_path), "w"),
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(PROJECT_ROOT),
                    start_new_session=True,
                )
                active_agents[body.name] = proc
                now_iso = datetime.now(timezone.utc).isoformat()
                agent_metadata[body.name] = {
                    "status": "running",
                    "spawned_at": now_iso,
                    "last_heartbeat_at": now_iso,
                    "budget": str(body.budget),
                    "model": model,
                    "pid": proc.pid,
                    "tokens_used": 0,
                    "source": body.source or "api",
                    "recovery_note": "claude_bin_stale_retry",
                }
                await _save_agent_state_async()
                return {
                    "result": f"Agent '{body.name}' spawned (retry)",
                    "name": body.name,
                    "pid": proc.pid,
                    "transcript": str(transcript_path),
                    "brief_warning": _brief_warning.message if _brief_warning else None,
                }
            except Exception as retry_exc:
                logger.exception(
                    "spawn.retry_failed name=%s err=%s",
                    body.name, retry_exc,
                )
        try:
            _release_spawn_locks(spawn_id=body.name)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=(
                f"Spawn failed: file not found ({missing}). "
                f"Check that the claude CLI is installed and on PATH."
            ),
        )
    except Exception as e:
        # Log with full traceback so the operator can diagnose without
        # re-running under strace. Include the exception type so a bare
        # OSError with empty message still identifies the failure class.
        logger.exception("spawn.failed name=%s err=%s", body.name, e)
        # Release path locks so a retry with the same paths can
        # acquire. The drain-task release fires on a live subprocess;
        # if we never got that far the registry would leak without this.
        try:
            _release_spawn_locks(spawn_id=body.name)
        except Exception:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__,
        )


@router.get("/agents/fleets")
async def list_fleets():
    """Return the built-in fleet templates."""
    from services.fleet_templates import list_fleet_templates
    return {"fleets": list_fleet_templates()}


@router.post("/agents/fleets/{fleet_id}/prewarm")
async def prewarm_fleet(fleet_id: str):
    """Warm up everything that slows down the first spawn of a fleet.

    Demo-critical: when Tori clicks the fleet template button on stage,
    every agent spawn would otherwise pay for a cold ``claude auth
    status`` probe (up to ~1.5 s), plus the template lookup, plus the
    shared agent workspace mkdir. This endpoint does all of that once in
    advance so the visible ``/fleets/spawn`` latency drops to the pure
    subprocess fork time.

    Returns a small readiness dict. Safe to call on every Agents page
    mount. Never spawns actual agents.
    """
    from pathlib import Path
    from services.fleet_templates import list_fleet_templates
    from services.claude_code_provider import (
        is_claude_code_available,
        has_cached_auth_status,
        _find_claude_binary,
    )

    templates = list_fleet_templates()
    fleet = next((f for f in templates if f["id"] == fleet_id), None)
    if not fleet:
        raise HTTPException(status_code=404, detail=f"Fleet template '{fleet_id}' not found")

    # Warm the auth-status cache. The very first spawn otherwise blocks
    # on `claude auth status` which adds ~1.5 s per member on a cold run.
    # Skip the force-probe when we already have a fresh cached result so
    # hitting Prewarm twice in a row stays cheap. Tori can still force a
    # refresh via Settings (that path still passes force=True explicitly).
    if has_cached_auth_status():
        auth_ok = await is_claude_code_available()
    else:
        auth_ok = await is_claude_code_available(force=True)
    binary_path = _find_claude_binary()

    # Touch the shared agent workspace dir so the first member does not
    # pay for the mkdir. Harmless if the dir already exists.
    try:
        workspace_dir = youros_home() / "agent_workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Never let a fs hiccup fail the prewarm. The spawn path will
        # retry the mkdir itself.
        pass

    return {
        "ready": True,
        "fleet_id": fleet_id,
        "member_count": len(fleet.get("members", [])),
        "auth_ok": auth_ok,
        "binary_path": binary_path,
    }


@router.post("/agents/ack-bot/backfill")
async def backfill_ack_bots():
    """Start an ack bot for every running registered subagent.

    Used to enable the inline chat ack bot feature for agents that
    were spawned before the ack bot code landed. Idempotent: running
    this twice in a row on the same fleet is safe and returns the
    same agents under ``already_active`` the second time.

    Returns a dict with two lists:

    * ``started``: agents that did not have a bot and now do.
    * ``already_active``: agents that already had a running bot.
    """
    result = chat_ack_bot.start_for_running_agents()
    return result


def _link_session_jsonl(name: str, meta: dict, register_time_iso: str) -> bool:
    """→1475: find the Claude Code session JSONL for a registered agent and store it.

    Scans ~/.claude/projects/<encoded-cwd>/ for *.jsonl files (main session files,
    NOT subagent files in subdirectories) that were written within a 30s window
    before the register call. Stores the best match as meta["transcript_path"] so
    _resolve_transcript_source and _get_transcript_metrics return real byte counts.

    Returns True if a path was found and stored; False otherwise.
    Callers set meta["transcript_uuid_pending"] = True on False to trigger retry
    from the heartbeat endpoint.
    """
    from config import PROJECT_ROOT

    cwd = meta.get("worktree_path") or str(PROJECT_ROOT)
    projects_dir = _claude_code_projects_dir()
    encoded = str(cwd).replace("/", "-").lstrip("-")
    project_dir = projects_dir / f"-{encoded}"
    if not project_dir.exists():
        return False

    register_dt = _parse_iso(register_time_iso)
    # 30-second grace window: the session file may have been created slightly
    # before the agent's /register call arrives.
    cutoff = (register_dt - timedelta(seconds=30)).timestamp() if register_dt else 0.0

    best: Optional[Path] = None
    best_mtime = 0.0
    try:
        for p in project_dir.glob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size == 0:
                continue
            if st.st_mtime < cutoff:
                continue
            if st.st_mtime > best_mtime:
                best_mtime = st.st_mtime
                best = p
    except OSError:
        return False

    if best is None:
        return False

    meta["transcript_path"] = str(best)
    # →2893: provenance marker. This path is a HEURISTIC link to a shared
    # top-level session JSONL (often the orchestrator's own conversation),
    # stored so byte-count metrics have something to read. It is not
    # attribution: the transcript endpoint must never serve it as the
    # agent's own log.
    meta["transcript_path_source"] = "session-link"
    meta.pop("transcript_uuid_pending", None)
    return True


@router.post("/agents/register")
async def register_agent(body: AgentSpawn, request: Request = None):
    """Register an external agent (e.g., Claude Code subagent) without spawning a process.

    This lets yourOS track agents that are managed by another system. Agents
    should call this BEFORE they start work so they show up as "running"
    in the Agents page in real time. The default status is "running" so a
    simple register call is enough to make the agent visible immediately.
    """
    # Require at least one of task, description, or prompt so mystery rows
    # with no purpose can never be created (regression guard for the
    # stronger-set-selection orphan incident).
    if not (body.task or body.description or body.prompt):
        raise HTTPException(
            status_code=400,
            detail="register requires task, description, or prompt",
        )

    # Require source so every registered agent declares where it came from.
    if not body.source:
        raise HTTPException(
            status_code=400,
            detail="register requires source (e.g. 'claude-code')",
        )

    # →2895: accept an optional ``log_path`` field — the caller's own log
    # file, known at spawn time (the subagents/ JSONL the harness streams
    # to). AgentSpawn is a shared schema that ignores unknown fields, so
    # lift it from the raw body. It is stored as transcript_path with the
    # →2893 "caller" provenance marker: explicit attribution beats every
    # heuristic, so liveness checks and the transcript endpoint trust it
    # even when name matching would have failed.
    _caller_log_path = body.transcript_path
    if not _caller_log_path and request is not None:
        try:
            _raw_body = await request.json()
            if isinstance(_raw_body, dict):
                _lp = _raw_body.get("log_path")
                if isinstance(_lp, str) and _lp.strip():
                    _caller_log_path = _lp.strip()
        except Exception:
            pass

    resolved_model = MODEL_MAP.get(body.model, body.model)
    # Default status to "running" so newly registered agents appear in the UI
    # immediately. Callers may pass an explicit status to override.
    status = body.status or "running"
    now_iso = datetime.now(timezone.utc).isoformat()
    # Merge-with-hook-preregister guard. The Claude Code PreToolUse hook
    # (.claude/hooks/register-agent.sh) already POSTs /register under a
    # slug-of-description name BEFORE the subagent boots. The subagent's
    # own prompt then tells it to POST /register again under a different
    # name from its prompt body. Without this guard that second call
    # would create a SECOND row with the same source and a near-identical
    # purpose, producing the duplicate "Active Sessions" entries the user
    # sees. If we can find a recent hook-preregister that matches the
    # same parent session window, merge into it: return the existing row
    # and skip inserting a duplicate.
    if (
        body.name not in agent_metadata
        and body.source == "claude-code"
        and (status or "running") == "running"
        and not body.hook_preregister
    ):
        merge_target = _find_recent_hook_preregister(
            now_iso,
            body_name=body.name or "",
            body_description=body.description or "",
            body_prompt=body.prompt or "",
        )
        if merge_target is not None:
            existing_name, existing_meta = merge_target
            # Refresh heartbeat so the merged row does not get swept as
            # stale while the subagent is doing real work.
            existing_meta["last_heartbeat_at"] = now_iso
            # →2895: a caller-provided log path applies to the merged row
            # too — the hook slug row it merges into has, at best, an
            # autodiscovered guess.
            if _caller_log_path:
                existing_meta["transcript_path"] = _caller_log_path
                existing_meta["transcript_path_source"] = "caller"
            # Preserve the hook's description (usually richer) but adopt
            # the subagent's prompt if the hook did not capture one.
            if body.prompt and not existing_meta.get("prompt"):
                existing_meta["prompt"] = body.prompt[:500]
            # Subagent-name-wins: if the subagent's chosen name differs
            # from the hook-preregister slug, rekey the merged row under
            # the subagent's name so GET /api/agents surfaces row.name
            # as the real subagent name (what the UI renders) instead of
            # the generic Task-description slug. Keep the hook slug as an
            # alias so late heartbeat/status/complete calls under either
            # name still resolve. This inverts the earlier merge direction
            # (hook name wins) which hid subagents from the Agents page
            # under an unrecognised slug, the demo-blocking visibility bug.
            # The merged row is tagged with ``hook_preregister_name`` so
            # audit tooling can still trace the original preregistration.
            if body.name and body.name != existing_name:
                existing_meta["hook_preregister_name"] = existing_name
                # Drop the explicit hook_preregister flag so a second
                # subagent self-register later cannot re-merge into this
                # row (it is no longer "awaiting a subagent"; one arrived).
                existing_meta.pop("hook_preregister", None)
                # Move the metadata entry: new key wins, old key is
                # removed so the list endpoint does not render two rows.
                agent_metadata.pop(existing_name, None)
                agent_metadata[body.name] = existing_meta
                # Alias the hook slug -> subagent name so calls still
                # landing on the old key keep resolving.
                agent_aliases[existing_name] = body.name
                # Remove any stale alias under body.name that might have
                # pointed at the old key.
                if agent_aliases.get(body.name) == existing_name:
                    agent_aliases.pop(body.name, None)
                canonical_name = body.name
            else:
                # Names already match (or body.name is empty): preserve
                # the pre-existing behaviour, alias body.name -> row.
                agent_aliases[body.name] = existing_name
                agent_metadata[existing_name] = existing_meta
                canonical_name = existing_name
            await _save_agent_state_async()
            _fire_delta(canonical_name, existing_meta.get("status", "running"))
            return {
                "result": (
                    f"Agent '{body.name}' merged into existing hook "
                    f"preregistration '{existing_name}'"
                ),
                "source": "claude-code",
                "status": existing_meta.get("status", "running"),
                "merged_into": existing_name,
                "mailbox_instruction": agent_mailbox_instruction(canonical_name),
                "mailbox_check_interval_seconds": MAILBOX_CHECK_INTERVAL_SECONDS,
            }

    # Preserve spawned_at across re-registers so an agent that calls
    # register again (for a heartbeat-like ping) does not lose its
    # original start time and its duration stays accurate.
    existing = agent_metadata.get(body.name) or {}
    # Accept caller-supplied spawned_at (first registration sets it; re-registers
    # keep the original). Fall back to the existing record, then now.
    spawned_at = existing.get("spawned_at") or body.spawned_at or now_iso

    # Never downgrade a terminal status back to "running". If the agent
    # already completed or failed, a stale re-register call (e.g. from a
    # zombie process that kept running) must not reset the status. Doing so
    # would allow /complete to bypass its idempotency guard and emit another
    # agent.completed row -- the root cause of the summarizer-bot storm.
    # Uses the module-level _TERMINAL_STATUSES (->2625): a local copy of the
    # list drifted once already and is banned by the drift-guard test.
    existing_status = existing.get("status", "")
    # Reject re-registration of a name that already holds an EXPLICIT
    # terminal status as running. A Claude Code subprocess that keeps
    # heartbeating after a user cancel must NOT resurrect the same row:
    # the cancelled row stays cancelled and the new work gets a new row.
    #
    # →2956 exception — self-reclaim: a terminal status a SWEEP inferred
    # (terminated_stale / completed_timeout, or any status stamped
    # flagged_by=idle_sweep/stale_sweep) is a guess, not a fact — the
    # same doctrine as the →2896 heartbeat revive. The agent itself
    # re-registering under its OWN name is proof the guess was wrong:
    # reclaim the row in place (history preserved) instead of forcing a
    # numbered '-retry-N' / '-rN' copy that breaks byte-count tracking
    # (→2936) and leaves dead duplicate rows behind (saa-2945 became
    # '-retry-1', saa-2946 became '-r2').
    _reclaimed_row = False
    if existing_status in _TERMINAL_STATUSES and status == "running":
        _sweep_inferred = (
            existing_status in ("terminated_stale", "completed_timeout")
            or existing.get("flagged_by") in ("idle_sweep", "stale_sweep")
        )
        if not _sweep_inferred:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent '{body.name}' already terminated with status "
                    f"'{existing_status}'. Register under a fresh name "
                    "(e.g. append '-retry-XXXX') so the terminal row is "
                    "preserved and the new work gets its own row."
                ),
            )
        _reclaimed_row = True
        logger.info(
            "register.self_reclaim name=%s from_status=%s flagged_by=%s",
            body.name, existing_status, existing.get("flagged_by"),
        )

    # →2956 (3): a deleted name is not a permanent blacklist. When a live
    # agent registers it again as running, clear the tombstone so the row
    # is visible in GET /api/agents and its /complete is honored again.
    # The deleted-agents guard keeps its original protection: a zombie
    # /complete with no live re-registered row is still refused (see
    # mark_agent_complete).
    if status == "running":
        _del_names = _load_deleted_agents()
        if body.name in _del_names:
            _del_names.discard(body.name)
            _save_deleted_agents(_del_names)
            logger.info("register.tombstone_cleared name=%s", body.name)

    # When an agent was REST-spawned (has a pid), preserve the spawn-time model
    # rather than letting the agent's own /register call overwrite it. The
    # mailbox template used to hardcode "sonnet", so old agents would overwrite
    # haiku/opus spawn-time assignments on first register.
    spawn_time_model = existing.get("model") if existing.get("pid") else None
    model = spawn_time_model or resolved_model
    # When an agent was REST-spawned (has a pid), preserve the spawn-time source
    # rather than letting the agent's self-register overwrite it. Bridge-spawned
    # agents (source="task-bridge") self-register with source="claude-code", which
    # makes them eligible for _autocomplete_exited_subagents — the sweep that
    # prematurely marked onboarding-tracking-step-folder-025d66 "completed" while
    # its subprocess was still running (→1227, Bug 1).
    spawn_time_source = existing.get("source") if existing.get("pid") else None
    source = spawn_time_source or body.source
    record: dict = {
        "spawned_at": spawned_at,
        "budget": str(body.budget),
        "model": model,
        "source": source,
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
    if body.hook_preregister:
        # Persist so _find_recent_hook_preregister can find this row
        # when the subagent self-registers a few seconds later.
        record["hook_preregister"] = True
    elif existing.get("hook_preregister"):
        # Preserve the flag on idempotent re-registers from the hook.
        record["hook_preregister"] = True
    if body.task:
        record["task"] = body.task
    if body.description:
        record["description"] = body.description
    if body.prompt:
        record["prompt"] = body.prompt[:500]
    # Record the template name (if caller specified one) so completion
    # hooks can detect Roadmap/opt-in templates. Also resolve the
    # ``produces_doc`` flag at register time so /complete does not have
    # to re-look up the template (which may have been edited between
    # register and complete). Same path used by the spawn handler.
    if body.template:
        record["template"] = body.template
        try:
            from services.agent_templates_store import agent_templates_store
            tpl = agent_templates_store.get_by_name_or_alias(body.template)
            if tpl and tpl.get("produces_doc"):
                record["template_produces_doc"] = True
        except Exception:
            pass
    # Preserve template_produces_doc across re-registers so a pre-existing
    # spawn does not lose its opt-in if the caller re-registers without
    # the template field.
    elif existing.get("template_produces_doc"):
        record["template_produces_doc"] = existing["template_produces_doc"]
        if existing.get("template"):
            record["template"] = existing["template"]
    if _caller_log_path:
        record["transcript_path"] = _caller_log_path
        # →2893: provenance marker. A caller-provided path (transcript_path
        # or the →2895 log_path alias) is the only transcript_path the
        # transcript endpoint may trust unconditionally. Heuristic paths
        # (session-link, autodiscovered) are guesses, not attribution.
        record["transcript_path_source"] = "caller"
    elif existing.get("transcript_path"):
        # The spawn endpoint already stamped the correct transcript path
        # into the agent record. Preserve it so that a bridge-spawned
        # agent's /register call (step 0 of the mailbox boot) does not
        # overwrite the real path with whatever auto-discovery finds.
        # Auto-discovery scans the parent session's task scratch dir, so
        # it can pick up a Monitor's .output file instead of this agent's
        # own transcript -- the exact bug this fixes.
        record["transcript_path"] = existing["transcript_path"]
        # Carry the provenance marker forward with the path (→2893).
        if existing.get("transcript_path_source"):
            record["transcript_path_source"] = existing["transcript_path_source"]
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
            record["transcript_path_source"] = "autodiscovered"  # →2893
    # Workflow linkage: preserve the run ID so the reconcile pass can find
    # this agent later when the workflow finishes. Carry forward any existing
    # workflow_run_id on re-register so it is never lost.
    workflow_run_id = body.workflow_run_id or existing.get("workflow_run_id")
    if workflow_run_id:
        record["workflow_run_id"] = workflow_run_id
    # Fleet linkage: re-register must never clobber the fleet_id /
    # fleet_name / role fields a member was spawned with, otherwise the
    # narrowed .md opt-in gate stops treating the member as a fleet agent
    # and its /complete falls through to the solo no-op.
    for fleet_field in ("fleet_id", "fleet_name", "role"):
        if existing.get(fleet_field) and fleet_field not in record:
            record[fleet_field] = existing[fleet_field]
    # Worktree isolation: the spawn endpoint writes worktree_path,
    # worktree_branch, and isolation into the metadata row BEFORE the
    # agent boots. The agent then calls /register as step 0 of its
    # mailbox boot. Without this block the fresh record dict overwrites
    # those fields with nothing, making worktree_path null in /api/agents
    # even though the worktree was created successfully.
    for wt_field in ("worktree_path", "worktree_branch", "isolation"):
        if existing.get(wt_field) and wt_field not in record:
            record[wt_field] = existing[wt_field]
    # Preserve PID from spawn: the spawn endpoint stores proc.pid in metadata
    # before the agent boots. A subsequent /register call (e.g. self-registration)
    # must not drop it. After a backend restart active_agents is cleared, leaving
    # meta.get("pid") as the only liveness guard in _autocomplete_exited_subagents
    # (line: if pid and _is_pid_alive(pid): continue). Without this, the guard is
    # bypassed and agents are prematurely completed (→1227, Bug 2).
    if existing.get("pid") and "pid" not in record:
        record["pid"] = existing["pid"]
    # Task and needle linkage: persist from the request body, or carry
    # forward from existing metadata on re-register. Without this, a
    # spawn-set task_id is erased when the subagent self-registers, and
    # bridge-spawned agents that pass task_id at /register lose it too.
    if body.task_id:
        record["task_id"] = body.task_id
    elif existing.get("task_id"):
        record["task_id"] = existing["task_id"]
    if body.needle_id:
        record["needle_id"] = body.needle_id
    elif existing.get("needle_id"):
        record["needle_id"] = existing["needle_id"]
    else:
        _inferred_nid = _infer_needle_id(
            body.name or "",
            body.task or "",
            body.description or "",
            body.prompt or "",
        )
        if _inferred_nid:
            record["needle_id"] = _inferred_nid
    # Auto-claim: extract ALL →NNN tokens from task description and store
    # as needle_ids so get_running_needle_ids() shows in_progress for every
    # referenced needle, not just the first one (→1204).
    _all_nids = _extract_all_needle_ids(
        body.task or "",
        body.description or "",
        body.prompt or "",
    )
    if _all_nids:
        record["needle_ids"] = _all_nids
    elif existing.get("needle_ids"):
        record["needle_ids"] = existing["needle_ids"]
    # Needle 857: stamp conversational chat mode for claude-code agents so
    # the nudge handler knows it can generate a full LLM reply instead of
    # relying on the ack bot's canned receipts. Preserved on re-register so
    # the flag survives the subagent's own /register call at step 0.
    if body.source == "claude-code":
        record["chat_mode"] = "conversational"
    elif existing.get("chat_mode"):
        record["chat_mode"] = existing["chat_mode"]
    if _reclaimed_row:
        # →2956: reclaim bookkeeping. The fresh record dict already
        # dropped the sweep's terminal fields (completed_at,
        # terminated_at / terminated_reason, failed_at, flagged_by and
        # the synthetic sweep summary); carry the agent's live context
        # forward and count the reclaim so the board can see how often
        # its guesses were wrong.
        record["reclaim_count"] = int(existing.get("reclaim_count") or 0) + 1
        record["reclaimed_at"] = now_iso
        for _keep_field in (
            "current_step", "current_step_updated_at",
            "pending_summary", "pending_summary_at", "revival_count",
        ):
            if existing.get(_keep_field) and _keep_field not in record:
                record[_keep_field] = existing[_keep_field]
        # (4) Reclaiming the base row also cleans up the dead numbered
        # copies the old 409 path forced this agent to mint.
        _cleanup_dead_numbered_copies(body.name)
    agent_metadata[body.name] = record
    # Persistently mark the needle in_progress when an agent registers
    # for it (→1714). Fire-and-forget so register latency is unaffected.
    _reg_nid = record.get("needle_id")
    if _reg_nid and status == "running":
        _fire_set_task_in_progress(_reg_nid)
    # →1475: link session JSONL at register time so transcript_bytes is populated
    # immediately. If no file found yet (timing race), mark pending for retry.
    if source == "claude-code" and not record.get("transcript_path"):
        if not _link_session_jsonl(body.name, record, now_iso):
            record["transcript_uuid_pending"] = True
    await _save_agent_state_async()
    _set_agent_status(body.name, status)

    # →2946: fresh registrations announce themselves on the consolidated
    # bus so SSE consumers of GET /api/events see agent.spawned directly.
    if status == "running" and not existing:
        _fire_event(AGENT_SPAWNED, {"name": body.name, "task": body.task or ""})

    # Start the ack bot on first registration so Tori's inline chat
    # gets a warm acknowledgment within two seconds even when the
    # subagent is inside a long tool call and cannot poll /nudges. The
    # bot is a no-op for terminal registrations (it exits immediately).
    if status == "running":
        try:
            chat_ack_bot.start(body.name)
        except Exception as _ack_exc:
            logger.warning("failed to start ack bot for %s: %s", body.name, _ack_exc)

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
        _emit_audit_event("agent.spawned", audit_data)
        trace_event(
            "agent_spawned",
            name=body.name,
            source=getattr(body, "source", ""),
            model=getattr(body, "model", ""),
        )
    except Exception:
        pass

    # Return the mailbox contract so the caller (a Claude Code subagent
    # calling /register at step 0) learns the polling rule from the API
    # itself, not from the parent session's prompt. Without this block
    # the subagent may never know it should poll /nudges and Tori's
    # follow up messages pile up unseen. Regression guard for needle
    # 240. Keyed under ``mailbox_instruction`` so old callers that only
    # read ``result`` still work.
    try:
        if _time_primitive is not None:
            _time_primitive.start(op_id=body.name, op_kind="agent_spawn", hint_sec=None)
    except Exception:
        pass

    return {
        "result": f"Agent '{body.name}' registered",
        "source": "claude-code",
        "status": status,
        "mailbox_instruction": agent_mailbox_instruction(body.name),
        "mailbox_check_interval_seconds": MAILBOX_CHECK_INTERVAL_SECONDS,
    }


# Directory where user-facing generated files (like roadmap.md from the
# Roadmap template) are written. Scanned by /docs/recent so these files
# show up on the Files page without needing to live inside the repo.
# Dynamically resolve via services.files_dir.get_files_dir() so
# Settings page changes take effect without a restart. Tests patch
# this name via ``patch.object(module, "MYOS_FILES_DIR", ...)`` which
# sets a real attribute and shadows ``__getattr__`` below.
def __getattr__(name):  # PEP 562
    if name == "MYOS_FILES_DIR":
        from services.files_dir import get_files_dir
        return get_files_dir()
    raise AttributeError(name)

# Minimum summary length (characters, after strip) that qualifies as a
# real artifact. Short summaries like "Done" or "ok" would clutter the
# Files tab with acks, so we skip them.
_MIN_ARTIFACT_SUMMARY_CHARS = 50


def _is_test_artifact_agent_name(agent_name: str) -> bool:
    """True when the agent name matches the shared infra-noise pattern.

    Catches demo-smoke-*, build-NNN, overnight-*, fix-*, diagnose-*,
    smoke-*, verify-*, harden-*, cleanup-*, template-*, fleet-build-*,
    and the other machine-generated prefixes. Reuses the exact regex
    added by the briefing-filter-by-name-too work so the Files tab and
    the Review failed list agree on which runs are infra noise. Lazy
    import keeps routers.agents free of a circular dependency on
    services.briefing at module-load time.
    """
    try:
        from services.briefing import _INFRA_AGENT_NAME_RE

        return bool(_INFRA_AGENT_NAME_RE.match(agent_name or ""))
    except Exception:
        import re as _re

        fallback = _re.compile(
            r"^(demo-smoke-|build-\d+|test-|smoke-|overnight-|fix-|diagnose-|"
            r"verify-|harden-|dedupe-|cleanup-|backfill-|backend-|template-|"
            r"tasks-|usage-|github-|calendar-|drive-|inline-|onboarding-|"
            r"chat-|workflow-|spec-|fleet-build-|dupe-guard-|stale-complete-|"
            r"reap-|ghost-)",
            _re.IGNORECASE,
        )
        return bool(fallback.match(agent_name or ""))


def _slugify_agent_name(agent_name: str) -> str:
    """Turn an agent name into a filesystem-safe slug for Files artifacts."""
    import re as _re

    slug = _re.sub(r"[^a-zA-Z0-9_-]+", "-", (agent_name or "").strip().lower())
    slug = _re.sub(r"-+", "-", slug).strip("-")
    return slug or "agent"


def _artifact_timestamp(now: Optional[datetime] = None) -> str:
    """Return an ISO-ish timestamp safe for filenames (colons replaced)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M")


def _is_roadmap_agent(agent_name: str, template_raw: str) -> bool:
    """True only when an agent was spawned from the Roadmap marketplace
    template.

    Strictly template-driven. Name-based matching was removed because
    diagnose subagents whose names happen to contain "roadmap" (like
    ``roadmap-e2e-task-leak``) were being misclassified as Roadmap
    runs, bypassing the Files-tab opt-in gate and writing a .md with
    ``kind: roadmap`` front matter. Real Roadmap runs always carry
    ``template: "Roadmap"`` (or one of the builtin equivalents) in
    ``agent_metadata`` at spawn time. If that metadata is missing, this
    is not a Roadmap run.

    The ``agent_name`` argument is kept for signature compatibility with
    existing call sites but is intentionally unused. Do not reintroduce
    a name-based check; diagnostic agents with "roadmap" in their name
    will slip through again.
    """
    template_lc = (template_raw or "").strip().lower()
    if not template_lc:
        return False
    return (
        template_lc == "roadmap"
        or template_lc in {"pm-roadmap", "builtin-pm-roadmap"}
    )


def _should_persist_agent_doc(
    agent_name: str,
    agent_meta: Optional[dict],
) -> bool:
    """Single source of truth for "does this agent run produce a ``.md``
    artifact in ``~/.youros/files/``?"

    Called by both the live ``/complete`` writer
    (:func:`_save_agent_output_to_files`) and the boot-time
    :func:`_retroactively_save_agent_summaries` sweep so the two paths
    cannot drift. Returns True only when at least one explicit opt-in
    signal is present in ``agent_meta``:

      * ``fleet_id`` — a fleet member completing.
      * Roadmap template match via :func:`_is_roadmap_agent`.
      * ``template_produces_doc`` — set at spawn time when the
        template's ``produces_doc`` flag is true.

    Also rejects infra-noise names (``demo-smoke-*``, ``diagnose-*``,
    ``fix-*``, etc.) that do not carry a ``fleet_id``. Fleet members
    keep the ``fleet-build-*`` prefix so the infra-name filter alone
    would drop them; ``fleet_id`` is the exemption.
    """
    meta = agent_meta or {}
    template_raw = str(meta.get("template") or "").strip()
    fleet_id = str(meta.get("fleet_id") or "").strip()
    template_produces_doc = bool(meta.get("template_produces_doc"))
    is_roadmap = _is_roadmap_agent(agent_name, template_raw)

    if _is_test_artifact_agent_name(agent_name) and not fleet_id:
        return False
    # Default opt-out: every completed non-infra run produces a summary doc.
    # Fleet members, Roadmap, and template_produces_doc were the prior opt-in
    # signals; they remain sufficient but are no longer required (→2485).
    return True


def _save_agent_output_to_files(
    agent_name: str,
    summary: str,
    skip_auto_tasks: bool = False,
    emit_notification: bool = True,
) -> list[Path]:
    """Persist an agent's final summary to ``~/.youros/files/`` so it shows
    up on the Files tab as a Recent Document.

    Only runs that make sense as a document land here. The narrow rule
    is: a solo agent editing code should NOT produce a .md. Only fleets,
    workflows, Roadmap, and templates that opt in via ``produces_doc``
    drop a file plus auto-tasks.

    Opt-in signals, any one is sufficient:
      * ``fleet_id`` in agent_metadata (fleet member completing).
      * :func:`_is_roadmap_agent` matches the Roadmap template.
      * ``template_produces_doc`` in agent_metadata (set at spawn time
        when the template's ``produces_doc`` flag is true).

    Without one of those, a plain solo agent's /complete is a silent
    no-op here. Workflows write their own rollup artifact through
    :mod:`services.workflows` and do not rely on this hook.

    Skips silently when:
      * Summary is missing or shorter than
        :data:`_MIN_ARTIFACT_SUMMARY_CHARS` (avoids "Done" acks).
      * Agent name matches the shared infra-noise regex (demo-smoke-*,
        build-NNN, fix-*, etc). Those are ops runs, not user work.
      * None of the opt-in signals above are present.

    Best-effort: a filesystem error returns whatever paths we did manage
    to write so the ``/complete`` endpoint never fails because of this
    hook. Returns the list of paths written (generic artifact first,
    optional roadmap.md compat copy second).

    Also fires ``services.automation_outputs.auto_create_tasks`` in the
    background for any next-step bullets the summary contains so
    actionable follow-ups show up in the Tasks list without requiring
    the user to re-read the artifact.
    """
    if not summary:
        return []
    summary_clean = summary.strip()
    if len(summary_clean) < _MIN_ARTIFACT_SUMMARY_CHARS:
        return []

    meta = agent_metadata.get(agent_name, {}) or {}
    template_raw = str(meta.get("template") or "").strip()
    is_roadmap = _is_roadmap_agent(agent_name, template_raw)
    fleet_id = str(meta.get("fleet_id") or "").strip()
    fleet_name = str(meta.get("fleet_name") or "").strip()

    # Combined opt-in + infra-name gate. Delegated to
    # :func:`_should_persist_agent_doc` so the retroactive sweep path
    # shares the same rules. Without one of ``fleet_id``, a Roadmap
    # template, or ``template_produces_doc``, this is a silent no-op.
    # Regression guards:
    #   * Website Builder "fleet finished but Files tab empty"
    #   * ``roadmap-e2e-task-leak`` diagnose agent misclassified by name
    #   * ``settings-cost-tracking-rename`` landing via retroactive sweep
    if not _should_persist_agent_doc(agent_name, meta):
        return []

    written: list[Path] = []
    try:
        MYOS_FILES_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        slug = _slugify_agent_name(agent_name)
        timestamp = _artifact_timestamp(now)
        target = MYOS_FILES_DIR / f"{slug}-{timestamp}.md"

        # Same-minute reruns of the same agent must not collide. Append
        # a numeric suffix until we land on a free filename. Keeps both
        # runs visible on the Files tab.
        if target.exists():
            for i in range(2, 100):
                candidate = MYOS_FILES_DIR / f"{slug}-{timestamp}-{i}.md"
                if not candidate.exists():
                    target = candidate
                    break

        heading_words = [w for w in slug.split("-") if w]
        heading = " ".join(w.capitalize() for w in heading_words) or "Agent Output"

        # Parse next-step bullets once so the file and auto-task pass
        # agree on what to surface.
        from services import automation_outputs as _auto_outputs
        next_steps = _auto_outputs.parse_next_steps(summary_clean)
        automation_kind = "fleet" if fleet_id else "agent"

        fm_lines = [
            "---",
            f"source: {agent_name}",
            f"template: {template_raw or 'none'}",
            f"generated_at: {now_iso}",
            f"summary_length: {len(summary_clean)}",
            f"kind: {'roadmap' if is_roadmap else f'{automation_kind}-output'}",
        ]
        if fleet_id:
            fm_lines.append(f"fleet_id: {fleet_id}")
        if fleet_name:
            fm_lines.append(f"fleet_name: {fleet_name}")
        fm_lines.append("---\n")
        front_matter = "\n".join(fm_lines) + "\n"

        body_parts = [front_matter, f"# {heading}\n\n", summary_clean.rstrip() + "\n"]
        # Only append a rendered "Next steps" section when the body does
        # not already contain one. Avoids the double-render that happened
        # when the demo-timeout placeholder embedded its own section and
        # this writer re-parsed and re-emitted the same bullets.
        import re as _re_ns
        already_has_section = bool(
            _re_ns.search(r"(?mi)^##\s*next\s*steps", summary_clean)
        )
        if next_steps and not already_has_section:
            body_parts.append("\n## Next steps\n\n")
            for item in next_steps:
                body_parts.append(f"- [ ] {item}\n")
        # Roadmap agents write ONLY to the stable roadmap.md path (below).
        # The timestamped <agentname>-<ts>.md copy used to land alongside
        # it, which put two copies of the same roadmap on the Files page
        # (one labelled by the stable name, one labelled by the random
        # agent-name slug). Tori only wants one. Skip the timestamped
        # write for roadmaps; the stable write on the next branch is the
        # single source of truth.
        if not is_roadmap:
            target.write_text("".join(body_parts))
            written.append(target)
            try:
                from services.provenance import write_sidecar as _write_sidecar
                _write_sidecar(
                    target,
                    agent_name=agent_name,
                    task_id=str(meta.get("task_id") or "") or None,
                    prompt_summary=summary_clean[:200],
                )
            except Exception as _prov_exc:
                logger.warning("provenance sidecar write failed for %s: %s", target, _prov_exc)

        # Fire-and-forget task auto-creation for any next-step bullets
        # we just surfaced. Runs on the current event loop so
        # schedule_auto_labels and ostk both have the running-loop
        # context they expect. Never blocks /complete.
        #
        # ``skip_auto_tasks`` is the opt-out used by the demo-timeout
        # placeholder path. Those placeholders are filler artifacts and
        # must never create follow-up tasks.
        #
        # Roadmap outputs are also opted out: Tori wants to preview the
        # roadmap in chat first and explicitly say "create tasks from
        # this roadmap" before any tasks get generated. The chat
        # notification below carries the link and the prompt.
        suppress_auto_tasks = skip_auto_tasks or is_roadmap
        if next_steps and not suppress_auto_tasks:
            try:
                asyncio.create_task(
                    _auto_outputs.auto_create_tasks(
                        next_steps,
                        source_name=agent_name,
                        automation_kind=automation_kind,
                    )
                )
            except Exception:
                pass

        # Backward compat: the Roadmap template has always written to a
        # stable path so chat can reference "the roadmap.md" by name.
        # Keep that shortcut working alongside the timestamped copy.
        if is_roadmap:
            roadmap_target = MYOS_FILES_DIR / "roadmap.md"
            roadmap_front_matter = (
                "---\n"
                f"source: {agent_name}\n"
                f"generated_at: {now_iso}\n"
                "kind: roadmap\n"
                "---\n\n"
                "# Roadmap\n\n"
            )
            roadmap_target.write_text(roadmap_front_matter + summary_clean + "\n")
            written.append(roadmap_target)
            try:
                from services.provenance import write_sidecar as _write_sidecar
                _write_sidecar(
                    roadmap_target,
                    agent_name=agent_name,
                    task_id=str(meta.get("task_id") or "") or None,
                    prompt_summary=summary_clean[:200],
                )
            except Exception as _prov_exc:
                logger.warning("provenance sidecar write failed for %s: %s", roadmap_target, _prov_exc)

            # Bell-icon notification: tell Tori the roadmap landed, link
            # to the Files page. This replaces the previous chat-bubble
            # post so the chat thread stays clean. The bell badge plus
            # web push handle delivery. Gated on ``emit_notification`` so
            # the boot-time retro-save sweep never fires a fresh bell
            # notification for a Roadmap run that completed on a prior
            # session.
            if emit_notification:
                try:
                    from services.notifications import (
                        notifications_service as _notif,
                    )

                    _notif.add(
                        type="roadmap_ready",
                        title="Roadmap ready",
                        body=(
                            "Open roadmap.md. Type 'create tasks' in chat "
                            "to break it down."
                        ),
                        action_label="Open roadmap",
                        action_url=f"/files?path={roadmap_target.as_posix()}",
                        metadata={
                            "roadmap_path": roadmap_target.as_posix(),
                            "source_agent": agent_name,
                            "kind": "roadmap_ready",
                        },
                        target=f"roadmap:{roadmap_target.as_posix()}",
                    )
                except Exception:
                    # Notification is best-effort. A storage error must
                    # never block /complete.
                    pass
    except Exception:
        return written
    return written


def _retroactively_save_agent_summaries(limit: int = 50) -> int:
    """Walk ``~/.youros/agent_memory/*.json`` and write the latest summary
    for every opt-in agent as a Files artifact, if it is not already
    on disk. Runs once at module import so existing IA review / PRD /
    custom-build outputs show up on the Files tab retroactively.

    The sweep shares the exact same opt-in gate as the live
    ``/complete`` writer via :func:`_should_persist_agent_doc`.
    Historical runs without a ``fleet_id``, Roadmap template, or
    ``template_produces_doc`` flag in their persisted metadata are
    skipped. Without this shared gate the sweep was bypassing the
    opt-in rule entirely and stamping ``template: retroactive`` on
    every solo diagnose / rename / cleanup agent, polluting the Files
    tab with runs that have no business being surfaced as user docs.

    Returns the number of artifact files newly written.
    """
    import json as _json

    try:
        from services.agent_memory import AGENT_MEMORY_DIR
    except Exception:
        return 0
    if not AGENT_MEMORY_DIR.exists():
        return 0

    try:
        MYOS_FILES_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0

    existing_slugs: set[str] = set()
    try:
        for p in MYOS_FILES_DIR.glob("*.md"):
            existing_slugs.add(p.stem)
    except Exception:
        pass

    written_count = 0
    try:
        entries = sorted(
            AGENT_MEMORY_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
    except Exception:
        entries = []

    for path in entries:
        agent_name = path.stem
        # Share the /complete writer's gate exactly. Gets the agent's
        # real metadata (fleet_id, template, produces_doc) out of the
        # in-memory dict loaded from AGENT_STATE_PATH at module import.
        # If metadata is missing entirely, the gate returns False and we
        # skip. Diagnose / rename / fix / cleanup names are also rejected
        # inside the helper via the shared infra-name regex.
        meta = agent_metadata.get(agent_name, {}) or {}
        if not _should_persist_agent_doc(agent_name, meta):
            continue
        slug = _slugify_agent_name(agent_name)
        try:
            data = _json.loads(path.read_text())
        except Exception:
            continue
        summaries = data.get("summaries") or []
        if not summaries:
            continue
        last = summaries[-1]
        text = (last.get("text") or "").strip()
        if len(text) < _MIN_ARTIFACT_SUMMARY_CHARS:
            continue
        saved_at = last.get("saved_at") or datetime.now(timezone.utc).isoformat()
        try:
            dt = datetime.fromisoformat(saved_at)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
        timestamp = _artifact_timestamp(dt)
        target_stem = f"{slug}-{timestamp}"
        if target_stem in existing_slugs:
            continue
        target = MYOS_FILES_DIR / f"{target_stem}.md"
        if target.exists():
            continue

        # Reflect the real run shape in front matter. Roadmap-template
        # replays write ``kind: roadmap``; fleet replays write
        # ``kind: fleet-output``; produces_doc replays write
        # ``kind: agent-output``. ``template: retroactive`` is kept as a
        # secondary marker so the sweep's output is distinguishable from
        # a first-run write, but the primary template field now carries
        # the real template name when we know it.
        template_raw = str(meta.get("template") or "").strip()
        fleet_id = str(meta.get("fleet_id") or "").strip()
        is_roadmap = _is_roadmap_agent(agent_name, template_raw)
        kind = "roadmap" if is_roadmap else (
            "fleet-output" if fleet_id else "agent-output"
        )
        heading_words = [w for w in slug.split("-") if w]
        heading = " ".join(w.capitalize() for w in heading_words) or "Agent Output"
        fm_lines = [
            "---",
            f"source: {agent_name}",
            f"template: {template_raw or 'retroactive'}",
            "retroactive: true",
            f"generated_at: {saved_at}",
            f"summary_length: {len(text)}",
            f"kind: {kind}",
        ]
        if fleet_id:
            fm_lines.append(f"fleet_id: {fleet_id}")
        fm_lines.append("---\n")
        front_matter = "\n".join(fm_lines) + "\n" + f"# {heading}\n\n"
        try:
            target.write_text(front_matter + text + "\n")
            written_count += 1
            existing_slugs.add(target_stem)
        except Exception:
            continue
    return written_count


# Retro-save once at import. Best-effort: must never break boot. Skip
# under an env toggle so test runs that pre-populate AGENT_MEMORY_DIR
# with fixtures are not surprised by the sweep rewriting their files.
import os as _os_artifacts

if _os_artifacts.environ.get("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "").lower() not in ("1", "true", "yes"):
    try:
        _retroactively_save_agent_summaries()
    except Exception:
        pass


@router.post("/agents/{name}/complete")
async def mark_agent_complete(name: str, body: Optional[AgentComplete] = None):
    """Mark an externally managed agent as completed.

    This writes the completion status to the persistent agent metadata store
    so the agent shows as completed in the UI across server restarts, and
    also writes a transcript marker as a belt-and-suspenders signal.

    If ``body.summary`` is provided it is appended to the agent's persistent
    memory so future sessions can pick up where this one left off.
    """
    # Defensive: if this agent was already cancelled by the user, do NOT
    # flip it back to completed. A zombie /complete arriving after an
    # explicit user cancel must not revive the record.
    #
    # IMPORTANT: terminated_stale is NOT treated as a blocker here.
    # The stale sweep fires after 15 minutes of no heartbeat, which can
    # happen legitimately during a long pytest run or tsc build. When the
    # agent finishes and calls /complete, we honor it. The sweep is a
    # best-effort UI cleanup, not an irrevocable kill signal. Regression
    # fix for the e2e-release-smoke incident (2026-04-14): a 2.3-hour
    # agent was swept mid-run, then its /complete was silently ignored,
    # leaving the Agents page showing "stopped" forever.
    #
    # Idempotency: if the agent is already in a terminal completion state
    # (completed or failed), return 200 immediately without writing another
    # audit row. This prevents duplicate agent.completed events when a
    # client retries /complete or races with the stale-sweep path.
    # (cancelled is handled separately above because it must also block
    # flipping the status back.)
    #
    # Race-condition guard: "completing" is a transient sentinel set
    # immediately below (before any awaits) so that a second concurrent
    # request arriving while the AC gate is running sees a non-None
    # terminal-like status and bails early.
    # Route through the alias map so /complete on a subagent's chosen
    # self-register name lands on the hook-preregister row it was
    # merged into.
    name = _resolve_agent_name(name)
    existing_meta = agent_metadata.get(name, {})
    terminal_status = existing_meta.get("status")
    if terminal_status == "cancelled":
        return {
            "result": f"Agent '{name}' was cancelled, complete ignored",
            "status": terminal_status,
        }
    if terminal_status in ("completed", "failed", "completing"):
        return {
            "result": f"Agent '{name}' already {terminal_status}, complete ignored (idempotent)",
            "status": terminal_status,
        }

    # Also guard against re-completing a deleted agent. When an agent is
    # deleted via DELETE /agents/{name}, it is removed from agent_metadata
    # and added to deleted_agents.json. Without this check, subsequent
    # /complete calls would see status=None (no metadata), bypass the guard
    # above, and keep writing new agent.completed audit rows indefinitely.
    deleted_names = _load_deleted_agents()
    if name in deleted_names:
        # →2956 (3): a deleted name must not permanently blacklist the
        # agent. Refuse only when there is NO live re-registered row —
        # that is the original abuse this guard was built for (a zombie
        # /complete after deletion must not upsert rows or keep writing
        # agent.completed audit events). An agent that came back and
        # re-registered (self-reclaim) has a live row here; its
        # /complete is real — honor it and clear the tombstone
        # (saa-2953: this guard refused the recovered summary forever).
        _live_row = agent_metadata.get(name)
        if not _live_row or _live_row.get("status") in _TERMINAL_STATUSES:
            return {
                "result": f"Agent '{name}' was deleted, complete ignored",
                "status": "deleted",
            }
        deleted_names.discard(name)
        _save_deleted_agents(deleted_names)

    # Guard: refuse idle-sweep auto-complete when the backend-spawned subprocess
    # (PID recorded at spawn time) is still alive. Without this, the idle sweep
    # fires after IDLE_COMPLETE_SECONDS of 0-byte transcript while the subprocess
    # is busy with internal tool calls that produce no stdout. mark_agent_complete
    # then writes the "registered externally" stub; the subprocess appends its real
    # output afterward; and _drain_stderr cleanup removes the worktree (0 commits
    # ahead of main) before the parent can merge — silently discarding all work.
    # os.kill(pid, 0) is a POSIX existence check: succeeds if the process is alive,
    # raises ProcessLookupError if it is dead.
    _spawn_pid = existing_meta.get("pid")
    if _spawn_pid is not None:
        try:
            os.kill(int(_spawn_pid), 0)
            # Subprocess is still running. Defer this /complete so the agent
            # keeps working. Return 200 so the caller (idle sweep) does not retry.
            # →2953: keep the posted summary. Deferring used to drop
            # body.summary entirely, so an agent that reported done while its
            # process was still alive lost its final summary when the PID-exit
            # reconciler later flipped the row to completed. Park it as
            # pending_summary; _set_agent_status attaches it on the completed
            # flip. Repeated deferred posts overwrite it (latest wins). The
            # idle detector's synthesized text is not the agent's own words,
            # so it is never parked.
            if (
                body
                and body.summary
                and body.summary.strip()
                and "auto-completed by heartbeat idle detector" not in body.summary
                and name in agent_metadata
            ):
                agent_metadata[name]["pending_summary"] = body.summary.strip()
                agent_metadata[name]["pending_summary_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                await _save_agent_state_async()
            logger.info(
                "mark_agent_complete.deferred_live_pid name=%s pid=%s",
                name, _spawn_pid,
            )
            return {
                "result": (
                    f"Agent '{name}' subprocess (pid={_spawn_pid}) is still running "
                    "— complete deferred until subprocess exits"
                ),
                "status": "running",
            }
        except (ProcessLookupError, PermissionError, OSError):
            pass  # Process is dead or not ours — proceed with completion
        except (ValueError, TypeError):
            pass  # Malformed PID in metadata — ignore safeguard

    # →2607: unknown name = 404. /complete used to upsert unregistered names
    # as brand-new completed rows; the →2606 containment work traced phantom
    # rows on the live Agents page (identical-task, foo-bar,
    # register-endpoint-contract) to exactly this path. Completion is a state
    # transition on an EXISTING row, never a row factory.
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown agent '{name}'. /complete does not create agent "
                "rows; register first via POST /api/agents/register."
            ),
        )

    # →2896: the client-side idle detector's /complete (register-agent.sh
    # keepalive loop running heartbeat_idle.py) is an inference from log
    # idleness, not a report from the agent itself — it produced one of the
    # three observed false flags. Stamp it revivable so a real heartbeat
    # arriving later flips the row back to running.
    if body and body.summary and "auto-completed by heartbeat idle detector" in body.summary:
        agent_metadata[name]["flagged_by"] = "idle_sweep"

    # Set a "completing" sentinel status BEFORE any awaits so concurrent
    # requests see a non-None status and are turned away by the guard above.
    # This closes the race window where two simultaneous /complete calls
    # both passed the guard while the first was still awaiting the AC gate.
    now_iso = datetime.now(timezone.utc).isoformat()
    _set_agent_status(name, "completing")
    # Persist the sentinel so even a server restart within the AC window
    # does not create a second completion event.
    await _save_agent_state_async()

    # Run quality gate checks from the matching Agentfile.
    # Each command runs in a thread so it never blocks the event loop.
    # Total AC block is capped at 30 seconds; individual commands at 20s.
    #
    # Exception: source="claude-code" (Agent-tool subagents spawned by the
    # main session). Those agents run their own tests inside their task
    # prompt, and the full-repo pytest sweep is too slow for the 30s budget
    # (every subagent ended up with a bogus
    # ``AC timed out: `python3 -m pytest api/tests/ -x -q` `` noise line).
    # The main session is the owner of repo-wide quality gates. Subagents
    # finish instantly here; their /complete just records the result.
    agent_config = get_agent_config(name)
    existing_source = existing_meta.get("source")
    skip_ac_gate = existing_source == "claude-code"
    if agent_config.acceptance_criteria and not skip_ac_gate:
        import subprocess
        from config import PROJECT_ROOT

        gate_failures: list[str] = []

        async def _run_ac(ac_cmd: str) -> Optional[str]:
            """Run one AC command in a thread; return failure string or None."""
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        subprocess.run,
                        ac_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        cwd=str(PROJECT_ROOT),
                        timeout=20,
                    ),
                    timeout=22.0,
                )
                if result.returncode != 0:
                    return f"AC failed: `{ac_cmd}` (exit {result.returncode})"
            except asyncio.TimeoutError:
                return f"AC timed out: `{ac_cmd}`"
            except subprocess.TimeoutExpired:
                return f"AC timed out: `{ac_cmd}`"
            except Exception as e:
                return f"AC error: `{ac_cmd}` ({e})"
            return None

        try:
            ac_tasks = [asyncio.create_task(_run_ac(cmd)) for cmd in agent_config.acceptance_criteria]
            done, pending = await asyncio.wait(ac_tasks, timeout=30.0)
            for t in pending:
                t.cancel()
            for t in done:
                failure = t.result()
                if failure:
                    gate_failures.append(failure)
            for t in pending:
                gate_failures.append("AC timed out (30s budget exceeded)")
        except Exception:
            pass

        if gate_failures:
            # Record the failure but still allow completion
            # (the agent already did the work, blocking helps nobody)
            if name in agent_metadata:
                agent_metadata[name]["gate_results"] = gate_failures
                await _save_agent_state_async()

    # Save session summary to memory if provided
    if body and body.summary:
        try:
            agent_memory_svc.append_summary(name, body.summary)
        except Exception:
            pass

        # Files-tab artifact capture: every agent whose summary clears
        # the length / name-filter bar gets its final output written to
        # ~/.youros/files/<slug>-<timestamp>.md so Tori can find IA review
        # / PRD / custom-build output alongside the roadmap on the Files
        # tab. Roadmap-template runs also keep their stable roadmap.md
        # copy so chat's "read the roadmap.md" shortcut still works.
        # Persist the returned paths so /api/agents can expose them as
        # clickable links on the finished card (→2485).
        # Best-effort; a write failure must never block completion.
        try:
            _artifact_paths = _save_agent_output_to_files(name, body.summary)
            if _artifact_paths and name in agent_metadata:
                agent_metadata[name]["artifacts"] = [
                    str(p) for p in _artifact_paths
                ]
        except Exception:
            pass

        # Round-trip bridge: mirror the completion summary into the nudge
        # replies stream so it shows up in the inline agent chat. Without
        # this bridge the UI never sees the agent's final word, because
        # claude --print is one-shot and agents almost never remember to
        # call POST /reply from inside their tool chain. The summary IS
        # the agent's final user-facing text, so treating it as a reply
        # closes the conversation for the user. We skip this only if the
        # exact same text was already posted as a reply in the last
        # minute, to avoid duplicating a reply the agent DID post.
        try:
            recent_replies = nudge_replies.get(name, [])
            already_posted = False
            if recent_replies:
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
                for r in recent_replies[-5:]:
                    if r.get("message", "").strip() == body.summary.strip():
                        ts = r.get("timestamp", "")
                        try:
                            if datetime.fromisoformat(ts) >= cutoff:
                                already_posted = True
                                break
                        except (ValueError, TypeError):
                            pass
            if not already_posted:
                reply_data = await ostk.append_nudge_reply(name, body.summary)
                nudge_replies.setdefault(name, []).append(reply_data)
                # Wake any long-pollers so the frontend renders the final
                # reply within milliseconds of /complete landing.
                _wake_nudge_waiters(name)
        except Exception:
            # Bridge is best-effort. A filesystem or storage error must
            # not block the completion itself.
            pass

    meta = agent_metadata.get(name, {})
    if meta.get("spawned_at"):
        try:
            start = datetime.fromisoformat(meta["spawned_at"])
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            _save_duration(meta.get("model", ""), float(meta.get("budget", "0")), duration)
        except (ValueError, TypeError):
            pass

    # Persist final completion status. The sentinel "completing" was set
    # before the AC gate; now stamp the real terminal status and timestamp.
    completed_at = datetime.now(timezone.utc).isoformat()
    _completion_summary = (body.summary or "").strip() if body else ""
    if name in agent_metadata:
        agent_metadata[name]["completed_at"] = completed_at
        agent_metadata[name]["status"] = "completed"
        if _completion_summary:
            agent_metadata[name]["summary"] = _completion_summary
            # →2953: this /complete carries a newer summary than any copy
            # parked by an earlier deferred /complete. Drop the parked one
            # so _set_agent_status below does not resurrect it.
            agent_metadata[name].pop("pending_summary", None)
            agent_metadata[name].pop("pending_summary_at", None)
        # Generate actionable_doc for template-run agents so the Recent tab
        # can surface a plain-language one-liner of what the run produced.
        _tpl = str(agent_metadata[name].get("template") or "").strip()
        if _tpl and _completion_summary:
            agent_metadata[name]["actionable_doc"] = _completion_summary
    else:
        # Metadata row vanished while the AC gate ran (cleared mid-flight).
        # →2607: never recreate it — /complete must not upsert rows. The
        # deleted-agents guard above already answers explicit deletions.
        logger.warning(
            "mark_agent_complete.row_vanished_mid_flight name=%s — not recreated",
            name,
        )
    await _save_agent_state_async()

    # Scaffold-only + dirty worktree guard (→1346).
    # Block completion when the agent's worktree has ONLY scaffold commits AND
    # uncommitted/untracked changes. This means the agent exited after writing
    # files but before `git add` + commit — closing the needle here would lose
    # the work. Reset to "running" so the needle stays open, write a warning,
    # and surface the alert so the parent session can intervene.
    _sc_branch = existing_meta.get("worktree_branch")
    _sc_wt_path = existing_meta.get("worktree_path")
    if (
        existing_meta.get("isolation") == "worktree"
        and _sc_branch
        and _sc_wt_path
    ):
        _sc_premature, _sc_reason = _is_scaffold_only_with_dirty_worktree(
            worktree_path=_sc_wt_path,
            branch=_sc_branch,
        )
        if _sc_premature:
            import json as _json_sc
            _warn_path = Path(str(youros_home() / "subagents" / "scaffold-warnings.jsonl"))
            _warn_path.parent.mkdir(parents=True, exist_ok=True)
            _warn_entry = _json_sc.dumps({
                "agent": name,
                "spawned_at": existing_meta.get("spawned_at", ""),
                "worktree_path": _sc_wt_path,
                "branch": _sc_branch,
                "reason": _sc_reason,
                "ts": now_iso,
                "warning": "premature-close blocked: scaffold-only commits + uncommitted changes in worktree",
            })
            try:
                with open(_warn_path, "a") as _wf:
                    _wf.write(_warn_entry + "\n")
            except Exception as _we:
                logger.warning("scaffold_premature_close_guard: failed to write warning: %s", _we)
            # Reset sentinel to "running" so the UI keeps the agent active
            # and the needle stays open.
            _set_agent_status(name, "running")
            await _save_agent_state_async()
            logger.warning(
                "mark_agent_complete.scaffold_premature_close_blocked name=%s reason=%s",
                name, _sc_reason,
            )
            return {
                "result": (
                    f"Agent '{name}' blocked: scaffold-only commits exist but worktree "
                    "has uncommitted changes. Commit the real work before closing. "
                    "Warning written to ~/.youros/subagents/scaffold-warnings.jsonl."
                ),
                "status": "running",
                "scaffold_premature_close": True,
                "reason": _sc_reason,
            }

    # Auto-merge worktree branch onto main when a bridge-spawned agent completes.
    # The PostToolUse complete-agent.sh hook handles this for native Agent-tool spawns
    # (source="claude-code"), but the bridge blocks those calls (exit 2) so PostToolUse
    # never fires — leaving worktree commits stranded until manual cherry-pick. (→999)
    _am_branch = existing_meta.get("worktree_branch")
    if (
        existing_meta.get("isolation") == "worktree"
        and _am_branch
        and existing_meta.get("source") != "claude-code"
    ):
        try:
            from config import PROJECT_ROOT as _am_root
            import subprocess as _am_sub
            _am_result = await asyncio.to_thread(
                _am_sub.run,
                ["git", "merge", "--ff-only", _am_branch],
                cwd=str(_am_root),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if _am_result.returncode == 0:
                if "Already up to date" in _am_result.stdout:
                    logger.info(
                        "mark_agent_complete.auto_merge name=%s branch=%s already_merged",
                        name, _am_branch,
                    )
                else:
                    logger.info(
                        "mark_agent_complete.auto_merge name=%s branch=%s merged",
                        name, _am_branch,
                    )
                    # Close the needle associated with this agent on merge (→1714).
                    _am_nid = existing_meta.get("needle_id")
                    if _am_nid:
                        try:
                            await ostk.close_task(f"→{_am_nid}", closed_reason="completed")
                        except Exception:
                            logger.warning(
                                "mark_agent_complete.auto_merge needle_close_failed name=%s nid=%s",
                                name, _am_nid,
                            )
            else:
                logger.warning(
                    "mark_agent_complete.auto_merge_failed name=%s branch=%s stderr=%s",
                    name, _am_branch, _am_result.stderr.strip(),
                )
        except Exception:
            logger.exception(
                "mark_agent_complete.auto_merge_error name=%s branch=%s",
                name, _am_branch,
            )

    # Near-no-op completion signal (→2141). Compute the committed diff
    # magnitude of the worktree and attach near_noop / work_size to the row.
    # INFORMS, never blocks: an agent dispatched to "build X" that completes
    # with an empty or sub-threshold diff is flagged so the orchestrator can
    # look, but it still completes normally (torios informs, never blocks).
    _nn_meta = agent_metadata.get(name)
    if _nn_meta is not None:
        _attach_near_noop_signal(name, _nn_meta)
        await _save_agent_state_async()

    _set_agent_status(name, "completed")

    # Auto-close the needle(s) associated with this agent on completion (→2042).
    # The auto-merge block above closes the needle only for non-claude-code
    # worktree agents when the ff-merge succeeds. All other agents (especially
    # source="claude-code" subagents, the most common case) have no close path,
    # leaving tasks stuck open/in_progress until the next server restart.
    # close_task is idempotent — a double-close from the merge path is safe.
    _cn_meta = agent_metadata.get(name) or existing_meta
    _cn_nid = _cn_meta.get("needle_id")
    _cn_extra = list(_cn_meta.get("needle_ids") or [])
    _cn_all: list[str] = []
    if _cn_nid:
        _cn_all.append(str(_cn_nid))
    for _n in _cn_extra:
        if str(_n) not in _cn_all:
            _cn_all.append(str(_n))
    if _cn_all:
        for _n in _cn_all:
            try:
                _arrow_n = f"→{_n.lstrip('→')}"
                await ostk.close_task(_arrow_n, closed_reason="completed")
            except Exception:
                pass  # best-effort; never block completion

    # Auto-close the spec builder task if this agent was spawned from a
    # Build it click. The spec_build prompt tells the agent to edit
    # files directly and NOT run `ostk work close` itself, so /complete
    # is the only signal we get. Without this, builder tasks stay open
    # forever and the spec never advances past in-progress. Imported
    # lazily to avoid a cross-router circular import at module load.
    try:
        from routers.specs import close_spec_builder_task
        await close_spec_builder_task(name)
    except Exception:
        logger.exception(
            "mark_agent_complete: spec builder auto-close failed for %s",
            name,
        )

    # Log to audit so the audit_agents() helper also reflects completion
    _emit_audit_event("agent.completed", {"name": name})
    trace_event("agent_completed", agent_name=name)

    # Write a transcript marker so the status check finds it even on
    # legacy rows. IMPORTANT: only write the stub if no real transcript
    # source exists. Otherwise the stub would mask the real JSONL that
    # ``_resolve_transcript_source`` would otherwise return and View
    # Transcript would show "completed (registered externally)" forever.
    from config import PROJECT_ROOT
    transcript = PROJECT_ROOT / "transcripts" / f"{name}.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    # Chat-attribution gate: a cancelled 0-token agent must not leave a
    # stub that reads "completed" on disk. Downstream the inline chat
    # assistant treats transcripts/<name>.md as authoritative work, and a
    # happy-path stub confuses it into crediting work the agent never did.
    _final_meta = agent_metadata.get(name, {})
    if _terminated_without_work(_final_meta):
        should_write_stub = False
    else:
        should_write_stub = not transcript.exists() or transcript.stat().st_size == 0
        # Cleanup: if a previous terminated-without-work stub still lives
        # here from an earlier cancel, overwrite it with the real stub so
        # the stale banner does not mask a now-legitimate completion.
        if not should_write_stub and _transcript_is_stub(transcript):
            should_write_stub = True
    if should_write_stub:
        # Does the resolver already know where the real transcript lives?
        # Run in a thread with a short timeout: scanning 100+ Claude session
        # dirs can take several seconds on large installs, and we must not
        # block the event loop or let the /complete endpoint hang indefinitely.
        try:
            real_source = await asyncio.wait_for(
                asyncio.to_thread(_resolve_transcript_source, name),
                timeout=2.0,
            )
            if real_source is not None and real_source != transcript:
                should_write_stub = False
        except (asyncio.TimeoutError, Exception):
            pass
    if should_write_stub:
        import re as _re
        if _re.match(r"^plan-\d+$", name):
            transcript.unlink(missing_ok=True)
        else:
            transcript.write_text(f"Agent '{name}' completed (registered externally).\n")

    # (→1147) Clean up orphan plan transcripts at completion. Covers:
    # - "no plan needed" responses where transcript has content but no real plan
    # - empty transcripts that were not caught by the should_write_stub path above
    # The should_write_stub path already deletes plan-NNN.md when the file is
    # missing/empty; this catch handles the case where it EXISTS with orphan text.
    _close_orphan_plan_transcript(name)

    # Fire a persistent notification so the bell lights up when an agent finishes.
    # Skip internal housekeeping agents — they are infrastructure noise, not user work.
    if not _is_test_artifact_agent_name(name):
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

        # iMessage completion text-back for agents started via text (→1875).
        # Only fires when the spawn included a notify target; in-app spawns never have one.
        # Summary is already persisted in agent_metadata above (line ~7984) so we read it
        # from there — the function-parameter `body` is shadowed by a local var above.
        _notify = agent_metadata.get(name, {}).get("notify")
        if _notify and _notify.get("kind") == "imessage" and _notify.get("chat_id") is not None:
            try:
                from services.imessage import reply_to_chat_sync
                from services.text_bridge import text_bridge as _text_bridge
                _agent_summary = agent_metadata.get(name, {}).get("summary") or f"Agent '{name}' finished."
                _msg = f"{name}: {_agent_summary}"
                # Pre-register before sending so the self-chat loop guard is armed.
                # Without this, the received echo of the completion text would be picked
                # up by the poller and re-dispatched as a new command (→2489).
                if _text_bridge._imessage_poller is not None:
                    _text_bridge._imessage_poller.mark_sent(_msg)
                await asyncio.to_thread(reply_to_chat_sync, _notify["chat_id"], _msg)
            except Exception as _notify_exc:
                logger.warning("mark_agent_complete: iMessage notify failed: %s", _notify_exc)

    # Drop the stdin writer on completion so future /nudge calls don't try
    # to write to a dead pipe.
    _agent_stdin_writers.pop(name, None)

    # Stop the ack bot: its job ends the moment the real agent reports
    # complete. Leaving it running would ack late-arriving nudges on
    # behalf of a dead agent.
    try:
        chat_ack_bot.stop(name)
    except Exception as _ack_exc:
        logger.warning("failed to stop ack bot for %s: %s", name, _ack_exc)

    try:
        if _time_primitive is not None:
            _time_primitive.finish(op_id=name, status="completed")
    except Exception:
        pass

    return {"result": f"Agent '{name}' marked complete", "status": "completed"}


@router.post("/agents/{name}/gem")
async def save_agent_gem(name: str):
    """Bookmark an agent run as a gem so the user can revisit its output.

    Sets ``is_gem=True`` on the agent metadata row. The Files tab can
    filter by this flag to surface bookmarked runs separately.
    Best-effort: always returns 200 so UI button does not show errors.
    """
    name = _resolve_agent_name(name)
    if name in agent_metadata:
        agent_metadata[name]["is_gem"] = True
        await _save_agent_state_async()
    return {"result": "saved", "name": name}


@router.post("/agents/{name}/heartbeat")
async def heartbeat_agent(name: str, body: Optional[AgentHeartbeat] = None):
    """Refresh an agent's ``last_heartbeat_at`` so the stale sweep does
    not mark it terminated.

    Agents should POST here on a short interval (every minute or so)
    while they are still doing work. The body is optional. If ``step``
    is provided it is stored on the record so the UI can surface the
    current phase the agent is working on.
    """
    # Route through the alias map so a subagent whose self-register was
    # merged into a hook-preregister row still lands on the correct
    # record when it heartbeats under its own chosen name.
    name = _resolve_agent_name(name)
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found. Register first with /api/agents/register.",
        )
    meta = agent_metadata[name]
    # Reject heartbeats against a row that has already reached a terminal
    # state. A zombie subprocess that keeps pinging after a user cancel
    # must not keep refreshing the row: doing so would keep
    # ``last_heartbeat_at`` fresh and could trip fresh-heartbeat fallbacks
    # in the Active tab. The caller should either stop pinging or
    # re-register under a fresh name.
    _HEARTBEAT_TERMINAL = {
        "completed",
        "failed",
        "cancelled",
        "terminated_stale",
        "completed_timeout",
        "killed",
        "stopped",
        "abandoned",
    }
    current_status = meta.get("status", "")
    if current_status in _HEARTBEAT_TERMINAL:
        # →2896: a status a SWEEP inferred (flagged_by stamp) is a guess,
        # not a fact. A real heartbeat carrying a step is proof of life:
        # revive the row instead of forcing a re-register under a retry
        # name. Bodyless pings (the detached register-agent.sh keepalive
        # loop) never revive — that loop outlives dead subagents by design
        # and would flap a genuine zombie row for its whole 45-minute TTL.
        # Explicit terminal statuses (user /complete, /cancel) carry no
        # flagged_by marker and stay final.
        _revivable = (
            meta.get("flagged_by") in ("idle_sweep", "stale_sweep")
            and body is not None
            and bool(body.step and body.step.strip())
            and meta.get("revival_count", 0) < MAX_HEARTBEAT_REVIVALS
        )
        if _revivable:
            meta.pop("flagged_by", None)
            for _flip_field in (
                "completed_at", "terminated_at", "terminated_reason",
                "failed_at", "fail_reason",
            ):
                meta.pop(_flip_field, None)
            _revival_count = meta.get("revival_count", 0) + 1
            _set_agent_status(
                name, "running",
                revival_count=_revival_count,
                revived_at=_now_iso(),
            )
            logger.info(
                "heartbeat.revived name=%s from=%s count=%d",
                name, current_status, _revival_count,
            )
            # Re-claim the task the sweep released when it flipped the row
            # terminal, mirroring what /register does for a fresh row.
            _rev_nid = meta.get("needle_id")
            if _rev_nid:
                _fire_set_task_in_progress(_rev_nid)
        else:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent '{name}' is already in terminal status "
                    f"'{current_status}'. Stop heartbeating or re-register "
                    "under a fresh name."
                ),
            )
    now_iso = _now_iso()
    meta["last_heartbeat_at"] = now_iso
    if body and body.step:
        meta["current_step"] = body.step
        meta["current_step_updated_at"] = now_iso
    # →1475: retry UUID link if it wasn't found at register time
    if meta.get("transcript_uuid_pending"):
        _link_session_jsonl(name, meta, meta.get("spawned_at") or now_iso)
    # →1475/→2895: refresh transcript_bytes from the agent's OWN resolved
    # log. meta["transcript_path"] is often the shared orchestrator session
    # (_link_session_jsonl stores it with source "session-link"); counting
    # that file reports the orchestrator's byte count and lets the
    # orchestrator's activity mask a dead helper. Resolve the agent's own
    # log first; only when no own log exists anywhere fall back to the
    # stored link so the Agents page still shows non-zero bytes (the
    # original →1475 contract). The mtime-advance liveness credit applies
    # to the OWN log only — crediting liveness from the shared session
    # file was part of the →2895 bug.
    _own_log = _resolve_own_log_path_cached(name)
    if _own_log is not None:
        try:
            _st = _own_log.stat()
            meta["transcript_bytes"] = _st.st_size
            _file_mtime_iso = datetime.fromtimestamp(
                _st.st_mtime, tz=timezone.utc
            ).isoformat()
            if now_iso < _file_mtime_iso:
                meta["last_heartbeat_at"] = _file_mtime_iso
        except OSError:
            pass
    else:
        _tp = meta.get("transcript_path")
        if _tp:
            try:
                meta["transcript_bytes"] = os.stat(_tp).st_size
            except OSError:
                pass
    await _save_agent_state_async()
    try:
        if _time_primitive is not None:
            current_step = body.step if body and body.step else None
            _time_primitive.progress(op_id=name, pct=0.5, current_step=current_step)
    except Exception:
        pass
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
    await _save_agent_state_async()
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
    _set_agent_status(name, "recovering", recovery_count=recovery_count + 1, last_recovery_at=_now_iso())
    await _save_agent_state_async()

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
        _set_agent_status(name, "failed", terminated_at=_now_iso(), terminated_reason=f"Recovery spawn failed: {e}")
        await _save_agent_state_async()
        raise HTTPException(status_code=500, detail=f"Recovery spawn failed: {e}")


@router.post("/agents/{name}/cancel")
async def cancel_agent(
    name: str,
    request: Request,
    body: Optional[AgentCancel] = None,
    force: bool = Query(False, description="Pass ?force=1 to override cross-session cancel guard"),
):
    """Mark an agent as cancelled and terminate its subprocess if one exists.

    Unlike the old behaviour that only flipped metadata, this now also
    sends SIGTERM to the in-process subprocess (if any) and follows up
    with SIGKILL after a 5-second grace period so resilient processes
    do not survive a user cancel.

    Cross-session guard: if the agent row carries an originating_session_id
    that differs from the caller's X-Claude-Session-Id header, the request
    is rejected with 403 unless ?force=1 is passed.
    """
    if name not in agent_metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found.",
        )

    # Cross-session cancel guard (→959).
    # Only fires when BOTH sides are known; legacy rows and non-Claude callers
    # pass through unchanged for back-compat.
    spawning_session = agent_metadata[name].get("originating_session_id")
    caller_session = request.headers.get("X-Claude-Session-Id") if request is not None else None
    if spawning_session and caller_session and spawning_session != caller_session:
        if not force:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "cross-session cancel refused; pass ?force=1 to override",
                    "spawning_session_id": spawning_session,
                    "caller_session_id": caller_session,
                },
            )
        logger.warning(
            "cross-session cancel forced: name=%s spawning_session=%s caller_session=%s",
            name, spawning_session, caller_session,
        )

    reason = body.reason if body and body.reason else "user cancelled"
    now_iso = _now_iso()
    meta = agent_metadata[name]
    _set_agent_status(name, "cancelled", terminated_at=now_iso, terminated_reason=reason)
    await _save_agent_state_async()

    # Terminate the subprocess if we hold one.
    proc = active_agents.pop(name, None)
    _agent_stdin_writers.pop(name, None)
    # Stop the ack bot so it does not ack for a cancelled agent.
    try:
        chat_ack_bot.stop(name)
    except Exception as _ack_exc:
        logger.warning("failed to stop ack bot for %s: %s", name, _ack_exc)
    killed = False
    if proc is not None:
        killed = await _terminate_with_sigkill_fallback(proc)
    else:
        # (→1344) The bridge no longer holds a Popen handle for this agent.
        # Fall back to ``meta.get('pid')`` so a cancel from the UI actually
        # stops the subagent process. Without this, the row flips to
        # cancelled but the Claude Code subprocess keeps running silently
        # and continues consuming API quota.
        _pid = meta.get("pid")
        if _pid:
            try:
                killed = await _terminate_pid_with_sigkill_fallback(int(_pid))
            except (TypeError, ValueError):
                killed = False

    # Chat-attribution gate: if this agent never recorded any tokens, any
    # text in transcripts/<name>.md is subprocess stdout that was already
    # mid-stream when we cancelled. Overwrite it with the terminated
    # banner so downstream consumers (inline chat, audit tools) cannot
    # attribute invented completions to this row.
    # Guard: do NOT clobber when (a) the transcript already contains real
    # (non-heartbeat) content, or (b) the agent's worktree branch has
    # commits ahead of main. Either condition means real work was done even
    # if tokens_used stayed 0 (→1041).
    if _terminated_without_work(meta):
        try:
            import re as _re
            from config import PROJECT_ROOT
            _t_path = PROJECT_ROOT / "transcripts" / f"{name}.md"
            _wt_branch = meta.get("worktree_branch") or ""
            if not _transcript_has_real_content(_t_path) and not _worktree_branch_has_commits(_wt_branch):
                if _re.match(r"^plan-\d+$", name):
                    _t_path.unlink(missing_ok=True)
                else:
                    _write_terminated_banner(_t_path, name, reason)
        except Exception:
            pass

    # (→1147) Clean up orphan plan transcripts on cancel, regardless of token count.
    # Covers "no plan needed" responses and zero-token cancels alike. Best-effort.
    _close_orphan_plan_transcript(name)

    # Audit so the audit log reflects the cancel.
    _emit_audit_event("agent.cancelled", {"name": name, "reason": reason})
    trace_event("agent_cancelled", agent_name=name, reason=reason)

    return {"ok": True, "status": "cancelled", "terminated_at": now_iso, "process_killed": killed}


@router.post("/agents/cancel-all")
async def cancel_all_agents():
    """Cancel every background agent that is currently running or spawned.

    Safety gate: agents with ``source='chat'`` are live interactive sessions
    and must never be cancelled here. Only agents with ``source='claude-code'``
    or ``source='api'`` (background work) are eligible.

    Agents that are already in a terminal state (the module-level
    ``_TERMINAL_STATUSES``) are left untouched. A local copy of that list
    lived here until ->2625; it had drifted (missing "abandoned" and
    "completed_timeout"), so bulk cancel overwrote already-finished rows.

    Returns the count of agents cancelled and their names so the frontend can
    show a meaningful confirmation message.
    """
    _BACKGROUND_SOURCES = {"claude-code", "api"}

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    cancelled_names: list[str] = []

    for name, meta in agent_metadata.items():
        status = meta.get("status", "")
        # Agents registered without an explicit source default to "api" (the
        # same fallback used by the GET /agents listing). Using "" here would
        # cause source=None rows to be silently skipped and never cancelled.
        source = meta.get("source") or "api"

        # Skip already-terminal rows.
        if status in _TERMINAL_STATUSES:
            continue

        # Safety gate: never cancel chat sessions.
        if source not in _BACKGROUND_SOURCES:
            continue

        # Provenance guard: never bulk-cancel an agent the user explicitly
        # requested. user_authored=True means a human typed saa/spawned
        # this agent; a peer session has no business killing it.
        if meta.get("user_authored"):
            logger.info(
                "cancel_all.skip_user_authored name=%s originating_session=%s",
                name, meta.get("originating_session_id", ""),
            )
            continue

        # Grace period: skip agents spawned or registered within the last
        # CANCEL_ALL_GRACE_SECONDS. A sibling agent calling /cancel-all as
        # part of a test or feature demo must not wipe freshly-launched
        # workers that have barely had time to register. The grace window
        # is short (30 s) so the user can still stop a rogue agent quickly.
        spawned_raw = meta.get("spawned_at") or meta.get("registered_at")
        if spawned_raw:
            spawned_dt = _parse_iso(spawned_raw)
            if spawned_dt is not None:
                age_seconds = (now_dt - spawned_dt).total_seconds()
                if age_seconds < CANCEL_ALL_GRACE_SECONDS:
                    continue

        _set_agent_status(name, "cancelled", terminated_at=now_iso, terminated_reason="bulk cancel")
        cancelled_names.append(name)

    if cancelled_names:
        await _save_agent_state_async()
        for name in cancelled_names:
            _emit_audit_event(
                "agent.cancelled",
                {"name": name, "reason": "bulk cancel"},
            )
        # Also terminate any in-process subprocess handles so the process does
        # not stay alive and cause GET /agents to flip the status back to
        # "running" on the next poll. Uses the SIGKILL fallback so resilient
        # processes do not survive the cancel.
        for name in cancelled_names:
            proc = active_agents.pop(name, None)
            _agent_stdin_writers.pop(name, None)
            if proc is not None:
                await _terminate_with_sigkill_fallback(proc)
        # Chat-attribution gate: neutralize transcripts for any
        # bulk-cancelled row whose token counter stayed at zero. Without
        # this, a subprocess that was mid-stream when cancelled leaves
        # plausible-looking "Task is complete" stdout in
        # transcripts/<name>.md and the inline chat treats that as real
        # work. See _terminated_without_work for the invariant.
        # Guard (→1189): same two conditions as the single-cancel path:
        # skip the banner when (a) the transcript already has real content or
        # (b) the agent's worktree branch has commits ahead of main. Either
        # means real work was done even when tokens_used stayed 0 (e.g. for
        # bridge-spawned worktree agents that don't report tokens to the API).
        try:
            from config import PROJECT_ROOT
            for name in cancelled_names:
                meta_row = agent_metadata.get(name, {})
                if _terminated_without_work(meta_row):
                    _t_path = PROJECT_ROOT / "transcripts" / f"{name}.md"
                    _wt_branch = meta_row.get("worktree_branch") or ""
                    if (not _transcript_has_real_content(_t_path)
                            and not _worktree_branch_has_commits(_wt_branch)):
                        _write_terminated_banner(_t_path, name, "bulk cancel")
        except Exception:
            pass

    return {"cancelled": len(cancelled_names), "names": cancelled_names}


# How often the background reconciliation loop runs (seconds).
# Tightened from 300s to 60s so zombie "running" rows (agents that died
# without calling /complete) clear within a minute instead of sitting in
# the demo for 5+ minutes. The actual stale-cutoff is still governed by
# STALE_AGENT_TIMEOUT_SECONDS / STALE_CLAUDE_CODE_SUBAGENT_SECONDS; this
# only controls how often we check.
RECONCILE_INTERVAL_SECONDS = 60  # 1 minute


def _reconcile_agents_sync() -> tuple[list[str], int]:
    """Synchronous core of agent reconciliation. Runs in a thread (->2165)."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    reconciled_names: list[str] = []
    still_running = 0

    for name, meta in list(agent_metadata.items()):
        if meta.get("status") != "running":
            continue

        # If we hold a live subprocess handle, the agent is real.
        if _proc_handle_is_alive(name):
            still_running += 1
            continue

        # If the transcript was written recently, the agent is alive.
        if _transcript_recently_active(name, now):
            still_running += 1
            continue

        # Hook-preregistered rows whose transcript we cannot locate must not
        # be stopped here. Their name is description-derived and may not match
        # the subagent's actual transcript first line; the 15-minute stale
        # sweep is the only mechanism allowed to close them. See the matching
        # guard inside _autocomplete_exited_subagents for the full rationale.
        if meta.get("hook_preregister") and _resolve_transcript_source(name) is None:
            still_running += 1
            continue

        # Check heartbeat age.
        last_seen_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        last_seen = _parse_iso(last_seen_raw) if isinstance(last_seen_raw, str) else None
        if last_seen is not None:
            age_seconds = (now - last_seen).total_seconds()
            if age_seconds <= STALE_AGENT_AUTOCOMPLETE_SECONDS:
                still_running += 1
                continue

        # →1678: raw PID-liveness guard. The in-memory proc handle can be lost
        # after a backend restart even though the worker process is alive and
        # working (reparented/orphaned). Same rationale as _recover_stale_agents
        # Case 2c — never stop a live process on a lapsed handle/heartbeat.
        _pid = meta.get("pid")
        if _pid:
            try:
                if _is_pid_alive(int(_pid)):
                    meta["stale_heartbeat"] = True
                    still_running += 1
                    continue
            except (TypeError, ValueError):
                pass

        # No live process, no recent heartbeat, no transcript activity.
        # Mark as stopped.
        _set_agent_status(name, "stopped", terminated_at=now_iso, terminated_reason="reconcile: no live process or recent heartbeat")
        reconciled_names.append(name)

        # Clean up the active_agents dict entry if lingering.
        active_agents.pop(name, None)
        _agent_stdin_writers.pop(name, None)

    return reconciled_names, still_running


@router.post("/agents/reconcile")
async def reconcile_agents():
    """Scan running agent records and mark orphans as stopped.
...
    Returns the count of reconciled (stopped) agents and the count of
    agents that are still legitimately running.
    """
    async with _sweep_pass_lock:
        reconciled_names, still_running = await asyncio.to_thread(_reconcile_agents_sync)

    if reconciled_names:
        await _save_agent_state_async()
        for rname in reconciled_names:
            _emit_audit_event(
                "agent.reconciled",
                {"name": rname, "reason": "no live process or recent heartbeat"},
            )

    return {"reconciled": len(reconciled_names), "still_running": still_running, "names": reconciled_names}


async def _reconcile_loop():
    """Background loop that reaps zombie "running" rows every minute.

    Runs the same three passes GET /api/agents runs so the UI does not have
    to be open for stale rows to clear:

      1. ``_sweep_stale_running_agents``      - demotes running rows whose
         heartbeat has been silent past STALE_AGENT_TIMEOUT_SECONDS (15 min
         for most sources; 8 min for Claude Code subagents) to
         ``terminated_stale`` / ``completed_timeout``.
      2. ``_autocomplete_exited_subagents``   - flips Claude Code subagents
         whose transcript stopped growing (>2 min idle, no live PID) to
         ``completed``.
      3. ``reconcile_agents``                 - backstop for agents with no
         live proc and no recent heartbeat; marks them ``stopped``.

    Writing the reaper into a background task (instead of only piggybacking
    on GET /api/agents) is the whole point: if the Agents page is not open,
    the lazy sweep never fires and zombie rows show up in the nav badge,
    inline-chat running-agents snapshot, and workflow views for far longer
    than any user should have to see them.
    """
    # Wait a bit on startup so agents have time to register.
    await asyncio.sleep(1)
    while True:
        try:
            # →2018: move both sweep functions off the event loop and serialize
            # with the snapshot loop's autocomplete via _sweep_pass_lock. Before
            # this fix, _sweep_stale_running_agents() ran directly on the event
            # loop (blocking it for stat × N agents) and two concurrent
            # asyncio.to_thread(_autocomplete_exited_subagents) calls competed
            # for the GIL, starving all request handling.
            _t0 = time.monotonic()
            async with _sweep_pass_lock:
                stale_changed = await asyncio.to_thread(_sweep_stale_running_agents)
                ac_changed = await asyncio.to_thread(_autocomplete_exited_subagents)
            _dt = time.monotonic() - _t0
            if _dt > 1.0:
                logger.warning("agent sweep/autocomplete pass took %.2fs (N=%d agents)", _dt, len(agent_metadata))

            if stale_changed or ac_changed:
                await _save_agent_state_async()
                await _event_bus.publish(AGENT_SWEEP, {})
            # Drain ghost retry queue populated by _autocomplete_exited_subagents.
            retries = _pending_ghost_retries[:]
            _pending_ghost_retries.clear()
            for _ghost_name in retries:
                asyncio.create_task(_schedule_ghost_retry(_ghost_name))
            # Drain needle-close queue (→2207).
            needle_closes = _pending_needle_closes[:]
            _pending_needle_closes.clear()
            for _nid in needle_closes:
                asyncio.create_task(_close_task_for_autocomplete(_nid))
        except Exception:
            pass
        try:
            _t_rec = time.monotonic()
            rec_result = await reconcile_agents()
            _dt_rec = time.monotonic() - _t_rec
            if _dt_rec > 1.0:
                logger.warning("reconcile_agents took %.2fs (N=%d agents)", _dt_rec, len(agent_metadata))
        except Exception:
            pass
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)


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
    # Tombstone so a rapid re-create does not resurrect stale memory.
    recent_deletes.record_id(f"agent-memory:{name}")
    return {"result": f"Memory cleared for '{name}'"}


@router.post("/agents/{name}/kill")
async def kill_agent(name: str):
    # Always drop the stdin writer on kill so future /nudge calls do not
    # attempt to write to a closing pipe and fall back to file immediately.
    _agent_stdin_writers.pop(name, None)

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


_NUDGE_SIGNAL_DIR = youros_home() / "nudges"


def _touch_nudge_signal(name: str) -> None:
    """Touch ~/.youros/nudges/<name>.signal to let the agent skip ahead.

    The adaptive poll in the mailbox instruction block stats this file
    each cycle. If mtime changed since the last check the agent polls
    /nudges immediately instead of waiting for the current interval.
    This is purely advisory: errors are silently swallowed so a missing
    home directory or read-only filesystem never breaks nudge delivery.
    """
    try:
        _NUDGE_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
        signal_path = _NUDGE_SIGNAL_DIR / f"{name}.signal"
        signal_path.touch()
    except OSError:
        pass


def _build_stdin_envelope(name: str, message: str, kind: str) -> str:
    """Wrap a user message in a strong mailbox envelope for stdin delivery.

    The bare nudge text gets ignored by the LLM under pressure: it is
    mid tool chain, mid pytest run, or mid large file write. Streaming
    "fix the timestamps" by itself reads as ambient noise. The envelope
    promotes the message to an urgent system-style instruction so the
    model is much more likely to:

    1. Stop its current step at the next safe boundary.
    2. POST a /reply within two seconds confirming receipt.
    3. Apply the new instruction to the work in flight rather than
       deferring to end of task.

    For ``kind == "correction"`` we use stronger wording so the model
    treats the message as a course change, not a follow-up. For every
    other kind (default ``user_message``) we use the standard urgent
    block. The envelope is only ever sent over stdin: the on-disk
    nudge keeps the user's raw text so the inline UI renders the
    original message without leak through.
    """
    base_url = "https://127.0.0.1:8000/api/agents"
    reply_curl = (
        f"curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"{base_url}/{name}/reply -H 'Content-Type: application/json' "
        f"-d '{{\"message\": \"<your reply>\"}}'"
    )
    if kind == "correction":
        return (
            "\n=== URGENT CORRECTION FROM THE USER (act immediately) ===\n"
            "The user just sent a course-correction. Do NOT defer this to "
            "the end of your task. Stop your current step at the next "
            "safe boundary, change your approach to honour the "
            "correction, and POST a /reply within 2 seconds confirming "
            "you have it and what you will do differently.\n"
            f"Correction: {message}\n"
            f"Reply now: {reply_curl}\n"
            "=== end correction ===\n"
        )
    return (
        "\n=== URGENT MAILBOX MESSAGE FROM THE USER (act now) ===\n"
        "The user just sent you a message through the inline chat. Do NOT "
        "wait until your current step finishes. POST a /reply within "
        "2 seconds with a warm 1-2 sentence acknowledgement, then "
        "fold the message into the work in flight.\n"
        f"Message: {message}\n"
        f"Reply now: {reply_curl}\n"
        "=== end message ===\n"
    )


def _nudge_delivery_message(delivery: str, name: str) -> str:
    """Return the plain language status line the UI shows to the user.

    The wording is deliberately non technical and tells the user what
    will actually happen next. This is the surface for Tori's feedback
    that silent success on a dead delivery pipe is not acceptable.

    The file_only branch distinguishes two real states:

    * Agent is currently parked on a /nudges long-poll. We just woke
      it with ``_wake_nudge_waiters`` and it will return within tens of
      milliseconds, so the UI can honestly promise "within a second".
    * No parked waiter. The agent is either between polls, mid tool
      chain and not polling at all, or has stopped. We cannot promise
      a couple of seconds. The truth is "up to MAILBOX_SLOW_POLL_SECONDS
      on its next mailbox check".

    We never lie upward. If we do not know the agent is parked, we
    quote the slow cap, not the fast cap.
    """
    if delivery == "stdin":
        return "Sent. The agent should respond shortly."
    if delivery == "file_only":
        if _is_long_poll_parked(name):
            return "Sent. The agent is waiting and will reply within a second."
        # Ack bot present: the user will see a warm ack within two
        # seconds even if the real agent is deep in a tool call. The
        # final answer follows when the agent finishes its step.
        if chat_ack_bot.is_active(name):
            return (
                "Sent. Agent acknowledged within 2s. Full reply arrives "
                "when it finishes its current step."
            )
        return (
            f"Saved. The agent will pick this up on its next mailbox "
            f"check, up to {MAILBOX_SLOW_POLL_SECONDS} seconds from now."
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

    # Tag every UI-driven nudge as a user_message so the agent's
    # mailbox poller can tell it apart from a structured correction
    # written by POST /correct. Callers may override via body.kind to
    # support future channels (workflow, automation, etc.) without
    # widening the surface here. Default keeps existing clients quiet.
    nudge_kind = (body.kind or "user_message").strip() or "user_message"

    # Write the nudge to the filesystem so any watcher can pick it up.
    # A filesystem failure (disk full, permission error, etc.) must not
    # surface as a generic 500. Return a readable 503 so the UI can
    # tell the user what went wrong instead of showing "Internal Server Error".
    try:
        nudge_data = await ostk.write_nudge(name, message, kind=nudge_kind)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not save message for '{name}'. Storage error: {exc}. "
                   "Try again in a moment.",
        ) from exc

    # Touch a per-agent signal file so the mailbox adaptive poll can
    # skip ahead immediately instead of waiting for the current interval
    # to expire. The agent stats this file each cycle: a newer mtime
    # means a nudge arrived and /nudges should be polled right away.
    # We never fail the request if this write errors (it is advisory).
    _touch_nudge_signal(name)

    # Wrap the raw user message in a strong mailbox envelope before
    # writing it to stdin. The bare message gets ignored by the LLM
    # under pressure (mid tool chain, mid pytest run). The envelope
    # promotes it to an urgent system-style instruction so the model
    # is much more likely to break out of its current step, post a
    # /reply within two seconds, and act on the message immediately
    # rather than at the end of its current task. We do NOT change
    # what gets written to disk: the on-disk nudge stays the user's
    # raw text so /nudges and the inline UI render the original
    # message without the envelope leaking through.
    stdin_payload = _build_stdin_envelope(name, message, nudge_kind)

    # Try to deliver the message directly to the agent's stdin. Priority:
    #   1. _agent_stdin_writers: API-spawned subagents whose stdin was kept
    #      open after the initial prompt. The writer may be closing if the
    #      process has exited; detect via is_closing() and fall back.
    #   2. active_agents proc.stdin: legacy path for safety.
    #   3. file_only: the agent registered over HTTP with no proc handle.
    #      Normal for all HTTP-registered claude-code subagents.
    delivery = "file_only"
    writer = _agent_stdin_writers.get(name)
    if writer is not None:
        if writer.is_closing():
            # Pipe already closed (process exited). Drop stale entry.
            _agent_stdin_writers.pop(name, None)
        else:
            try:
                writer.write((stdin_payload + "\n").encode())
                await writer.drain()
                delivery = "stdin"
            except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError):
                # Pipe broke mid-write. Clean up and fall back to file.
                # RuntimeError covers uvloop's "handler is closed" when the
                # underlying transport was torn down between is_closing() and
                # write() (incident 2026-04-18 22:38 UTC, inline chat 500).
                _agent_stdin_writers.pop(name, None)
                delivery = "file_only"
    else:
        proc = active_agents.get(name)
        if proc and hasattr(proc, "stdin") and proc.stdin:
            try:
                proc.stdin.write((stdin_payload + "\n").encode())
                await proc.stdin.drain()
                delivery = "stdin"
            except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError):
                # Legacy path for agents tracked in active_agents. uvloop
                # raises RuntimeError("... handler is closed") when the
                # subprocess has already exited and its stdin transport was
                # torn down. The second message the user sends in the inline
                # chat hits this (incident 2026-04-18 22:38 UTC). Fall back
                # to file_only so the message still lands and the UI shows
                # a 200 with a delivery indicator instead of a generic 500.
                delivery = "file_only"

    delivery_message = _nudge_delivery_message(delivery, name)

    # Track in session history
    if name not in nudge_history:
        nudge_history[name] = []
    record = {
        "message": message,
        "timestamp": nudge_data["timestamp"],
        "source": "ui",
        # Tag the record so the inline UI and the agent's mailbox poll
        # can route on kind. user_message is the default for everything
        # POSTed through /nudge; /correct overrides to "correction".
        "kind": nudge_kind,
        # Legacy field kept for any old clients that still read it.
        "stdin_delivered": delivery == "stdin",
        # New structured delivery fields.
        "delivery": delivery,
        "delivery_message": delivery_message,
    }
    nudge_history[name].append(record)

    # Wake every long-poll /nudges waiter for this agent so agents that
    # are parked on a long poll get the new message immediately instead
    # of waiting out the remaining interval. Safe to call even when no
    # waiter is active: the event just stays set until the next poller
    # drains it.
    _wake_nudge_waiters(name)

    # Before waking the ack bot or the conversational responder, check
    # whether the agent is actually reachable. Terminal agents (completed,
    # cancelled, failed, etc.) and ghost agents (still 'running' but
    # heartbeat stale, i.e. process has gone away) cannot pick up messages.
    # Sending a canned ack or an LLM-generated greeting to these agents
    # produces a dishonest impression that an answer is coming. Instead,
    # write a short honest system reply and return early so neither the
    # ack bot nor the conversational responder fires.
    _agent_is_inactive = (
        str((meta or {}).get("status", "")).lower() in _TERMINAL_STATUSES
        or bool((meta or {}).get("stale_heartbeat"))
    )
    if _agent_is_inactive:
        if str((meta or {}).get("status", "")).lower() in _TERMINAL_STATUSES:
            _inactive_msg = (
                "This agent has finished its task and is no longer active, "
                "so it will not reply here."
            )
        else:
            _inactive_msg = (
                "This agent has not checked in for over 2 minutes and may "
                "have stopped, so it may not reply."
            )
        try:
            _inactive_reply = await ostk.append_nudge_reply(
                name,
                _inactive_msg,
                in_reply_to=record.get("timestamp"),
                kind="system",
            )
        except Exception:
            _inactive_reply = {
                "message": _inactive_msg,
                "timestamp": record.get("timestamp", ""),
            }
        _inactive_reply.setdefault("kind", "system")
        nudge_replies.setdefault(name, []).append(_inactive_reply)
        _wake_nudge_waiters(name)
        return {
            "result": f"Nudge sent to '{name}'",
            "nudge": record,
        }

    # Signal the ack bot immediately so it posts "Got your message"
    # within milliseconds instead of waiting up to ACK_POLL_INTERVAL_SECONDS.
    chat_ack_bot.signal_nudge(name)

    # Needle 857: pause-and-chat. When the agent's metadata marks it as
    # conversational, fire a background LLM call that generates a real
    # answer to the user's message and posts it as kind="conversational".
    # This replaces the canned ack-bot receipt with a substantive reply
    # without requiring any change to the subagent's own prompt or any
    # new endpoint. The call is fire-and-forget: if it fails or times
    # out the ack bot still covers the 2-second receipt promise.
    if (meta or {}).get("chat_mode") == "conversational":
        nudge_ts = record.get("timestamp", "")
        agent_task = (meta or {}).get("task", "")
        session_nudges = list(nudge_history.get(name, []))
        session_replies = list(nudge_replies.get(name, []))
        asyncio.create_task(
            agent_chat_responder.reply_to_nudge(
                name,
                message,
                nudge_ts,
                session_nudges,
                session_replies,
                agent_task=agent_task,
            ),
            name=f"conversational_reply:{name}",
        )

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

    # Default reply kind to "real" so the inline UI can render a
    # substantive subagent reply as the primary bubble while a
    # warm ack bot reply (kind="ack") shows in a lighter tone with
    # the "Agent received your message" badge. Callers may override
    # via body.kind to tag automated replies (e.g. "summary",
    # "system") without widening the surface.
    reply_kind = (body.kind or "real").strip() or "real"

    try:
        reply_data = await ostk.append_nudge_reply(
            name,
            message,
            in_reply_to=body.in_reply_to,
            kind=reply_kind,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not save reply for '{name}'. Storage error: {exc}. "
                   "Try again in a moment.",
        ) from exc

    # Mirror the kind into the in-memory record so /nudges callers
    # see it on the next snapshot regardless of disk read latency.
    reply_data.setdefault("kind", reply_kind)
    if name not in nudge_replies:
        nudge_replies[name] = []
    nudge_replies[name].append(reply_data)

    # Wake long-pollers the moment the data is in memory, before the
    # disk write. _save_agent_state_async runs in a thread pool and
    # acquires _save_state_write_lock; under lock contention that can
    # delay the wake by seconds or more. Moving the wake here means
    # the GET handler always unblocks within milliseconds — the reply
    # is already in nudge_replies so the snapshot recheck sees it.
    _wake_nudge_waiters(name)

    # Feed the rolling reply-latency store when this is a real reply
    # (not an ack-bot ack) and the reply carries an in_reply_to marker
    # we can subtract against. The ack bot reads the aggregate to
    # decide whether its next acknowledgement can honestly quote a
    # "usually under Ns" number. Any skipped path (missing fields,
    # clock skew, ack kind) silently declines to push a sample so
    # the aggregate stays clean.
    if reply_kind == "real":
        from services import chat_ack_bot as _ack_bot_for_latency
        latency = _ack_bot_for_latency._infer_latency_from_reply(
            nudge_replies[name], reply_data
        )
        if latency is not None:
            _ack_bot_for_latency.record_reply_latency(name, latency)

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
            _set_agent_status(name, "completed", completed_at=now_iso)
            meta["revival_reason"] = (
                "Reply arrived after the record was marked terminated_stale. "
                "The agent was still working. Record restored to completed."
            )
            revived = True
        await _save_agent_state_async()

    return {
        "result": f"Reply recorded for '{name}'",
        "reply": reply_data,
        "revived": revived,
    }


def _latest_nudge_timestamp(
    file_nudges: list,
    session_nudges: list,
    file_replies: list,
    session_replies: list,
) -> str:
    """Return the newest timestamp across all four lists, or empty string.

    Used by the long-poll branch of GET /nudges to decide whether new
    data has arrived since the client's last ``since`` marker. Timestamps
    are ISO-8601 strings, lexicographic compare matches chronological
    order for well-formed UTC timestamps, so no parsing is needed.
    """
    latest = ""
    for lst in (file_nudges, session_nudges, file_replies, session_replies):
        for entry in lst:
            ts = entry.get("timestamp", "") if isinstance(entry, dict) else ""
            if ts > latest:
                latest = ts
    return latest


@router.get("/agents/{name}/nudges")
async def list_agent_nudges(
    name: str,
    wait: int = Query(
        0,
        ge=0,
        le=NUDGE_LONG_POLL_MAX_SECONDS,
        description=(
            "Seconds to hold the request open waiting for new nudges "
            "or replies when nothing is newer than ``since``. Zero "
            "returns the current snapshot immediately. Capped server "
            "side at 30 seconds so connections never linger forever."
        ),
    ),
    since: Optional[str] = Query(
        None,
        description=(
            "ISO-8601 timestamp of the most recent nudge or reply the "
            "caller has already seen. If the backend has nothing newer "
            "and wait > 0, the request blocks until something arrives "
            "or the timeout elapses."
        ),
    ),
):
    """List all nudges and replies for an agent.

    Returns four lists:

    * ``nudges``: file-based user messages written by /nudge.
    * ``session_nudges``: in-memory user messages from the current
      session. Same shape as ``nudges``, kept separate so the client
      can deduplicate.
    * ``replies``: file-based replies the agent has posted via /reply.
    * ``session_replies``: in-memory replies from the current session.

    Long-poll contract: when ``wait`` is greater than zero, the handler
    checks whether the latest timestamp is strictly newer than the
    caller's ``since`` marker. If so, it returns right away. Otherwise
    it blocks on an ``asyncio.Event`` that POST /nudge and POST /reply
    set the instant a new message lands, so the caller wakes with
    sub-second latency instead of waiting out the full poll interval.
    The wait is bounded by NUDGE_LONG_POLL_MAX_SECONDS, and the wait
    is fully cancellable so a client disconnect frees the handler.

    Needle 300: heartbeat is NOT refreshed here. The frontend also
    polls this endpoint to show nudge replies, so refreshing here
    would keep dead agents alive forever. Agents refresh their own
    heartbeat via POST /heartbeat, POST /reply, or POST /register.
    """
    async def _snapshot():
        file_nudges = await ostk.list_nudges(name)
        session_nudges = nudge_history.get(name, [])
        file_replies = await ostk.list_nudge_replies(name)
        session_replies = nudge_replies.get(name, [])
        return file_nudges, session_nudges, file_replies, session_replies

    file_nudges, session_nudges, file_replies, session_replies = await _snapshot()

    # Long-poll branch. We only wait when the caller asked for it AND
    # nothing is strictly newer than the marker they provided. If the
    # caller did not pass a ``since`` marker we treat the first fetch
    # as fresh and return immediately: there is nothing to wait for
    # when the client has no state yet.
    if wait > 0 and since is not None:
        latest = _latest_nudge_timestamp(
            file_nudges, session_nudges, file_replies, session_replies,
        )
        if not latest or latest <= since:
            # Arm the waiter BEFORE the recheck so a POST /nudge that
            # lands between our snapshot and the wait() still wakes us.
            event = _get_nudge_waiter(name)
            event.clear()
            # Recheck right after arming to close the race: something
            # may have arrived between _snapshot and clear().
            file_nudges, session_nudges, file_replies, session_replies = await _snapshot()
            latest = _latest_nudge_timestamp(
                file_nudges, session_nudges, file_replies, session_replies,
            )
            if not latest or latest <= since:
                # Mark this request as a parked long-poller. The nudge
                # and reply handlers read this counter to decide whether
                # the UI can promise sub-second delivery. Increment
                # BEFORE awaiting so a nudge that races in during
                # event.wait() still sees us as parked.
                _nudge_parked_count[name] = _nudge_parked_count.get(name, 0) + 1
                try:
                    bounded = min(wait, NUDGE_LONG_POLL_MAX_SECONDS)
                    await asyncio.wait_for(event.wait(), timeout=bounded)
                except asyncio.TimeoutError:
                    # No message arrived before the timeout. Return the
                    # current snapshot so the caller can loop again.
                    pass
                finally:
                    # Always clear so the next poll arms fresh. Safe even
                    # if event was not set (no-op).
                    event.clear()
                    # Decrement parked count. Floor at zero so a double
                    # decrement from a race never produces a negative.
                    current = _nudge_parked_count.get(name, 0)
                    if current <= 1:
                        _nudge_parked_count.pop(name, None)
                    else:
                        _nudge_parked_count[name] = current - 1
                # Pull the freshest data now that the wait is over.
                (
                    file_nudges,
                    session_nudges,
                    file_replies,
                    session_replies,
                ) = await _snapshot()

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


# ── Templates list cache ────────────────────────────────────────────
#
# Parsing every ``.agent`` file on every /agents/templates request is
# the dominant cost of the Templates tab on /agents. The file set
# changes rarely (only when a user creates/edits/deletes a template or
# an agentfile on disk), so we cache the parsed list and invalidate it
# on two signals:
#   (1) a manual invalidation hook the store can call when it writes
#       custom agentfiles, and
#   (2) a signature of (filename, mtime) across AGENTS_DIR so cold
#       starts and direct-file edits still refresh.
#
# Cache hits return the cached list in microseconds; cold misses pay
# the normal parse cost. Tests enforce both the hit path (no re-parse)
# and the invalidation hook.

_templates_cache: dict[str, object] = {
    "signature": None,  # tuple of (name, mtime_ns) pairs, or None
    "agents_dir": None,  # the AGENTS_DIR used when the cache was built
    "list": None,  # the cached list of template dicts
    "build_count": 0,  # how many times we re-parsed from disk
}


def _agents_dir_signature() -> tuple:
    """Build a fast invalidation signature for AGENTS_DIR.

    Returns a tuple of (filename, mtime_ns) pairs for every ``.agent``
    file in AGENTS_DIR. Cheap enough to compute on every request: one
    directory listing plus one stat per file.
    """
    if not AGENTS_DIR.exists():
        return ()
    entries: list[tuple[str, int]] = []
    try:
        with os.scandir(AGENTS_DIR) as it:
            for e in it:
                if e.is_file() and e.name.endswith(".agent"):
                    try:
                        entries.append((e.name, e.stat().st_mtime_ns))
                    except OSError:
                        # Ignore files that vanished between scan and stat.
                        pass
    except OSError:
        return ()
    entries.sort()
    return tuple(entries)


def invalidate_templates_cache() -> None:
    """Drop the cached templates list.

    Called after any store action that may change the rendered templates
    (install, uninstall, create, update, delete). The next /agents/templates
    request repopulates the cache on demand.
    """
    _templates_cache["signature"] = None
    _templates_cache["list"] = None


def _build_templates_list() -> list[dict]:
    """Build the deduped templates list for the Agents page.

    Uses a module-level cache keyed on (AGENTS_DIR, file signatures).
    Cache hits skip all parsing. Invalidation triggers:
      * any ``.agent`` file in AGENTS_DIR has a different mtime
      * AGENTS_DIR itself was swapped (tests use monkeypatch)
      * ``invalidate_templates_cache()`` was called manually

    Rules (tested in tests/test_agent_templates.py):

    1. Alias-only agentfiles (those whose body contains ``ALIAS <target>``)
       are NOT returned as their own rows. Instead, their stem is folded
       into the target row's ``aliases`` list so the UI can render a chip
       like ``alias: elit`` under the target card. Examples: ``saa.agent``
       (alias of ``builder``), ``elit.agent`` (alias of ``explain-plain``).
    2. Fleet member agentfiles (``fleet-<parent>-<role>.agent``, where a
       parent fleet template with id ``fleet-<parent>`` exists in
       ``BUILTIN_FLEET_TEMPLATES``) are NOT returned as rows. They are
       team members spawned through the Fleets panel, not standalone
       templates.
    3. Case-insensitive deduplication by stem: ``Explain-plain`` and
       ``Explain Plain`` would otherwise render as two cards because the
       frontend fetches both ``/agents/templates`` (stems) and
       ``/agents/persona-templates`` (Title Case names). The first
       occurrence by sorted stem wins.

    Alias resolution still works for the chat matcher and spawn path.
    This only changes what appears on the Agents templates grid.
    """
    # Cache check before any import or I/O work. The signature is a
    # cheap directory scan; the previous cold-path was glob + read_text
    # + parse per file, so this shortcut is a large win on every hit.
    signature = _agents_dir_signature()
    cached_sig = _templates_cache.get("signature")
    cached_dir = _templates_cache.get("agents_dir")
    cached_list = _templates_cache.get("list")
    if (
        cached_list is not None
        and cached_sig == signature
        and cached_dir == AGENTS_DIR
    ):
        return cached_list  # type: ignore[return-value]

    from services.agentfile_parser import (
        build_capabilities_summary,
        parse_agentfile,
        AgentfileParseError,
    )
    from services.fleet_templates import BUILTIN_FLEET_TEMPLATES

    if not AGENTS_DIR.exists():
        _templates_cache["signature"] = signature
        _templates_cache["agents_dir"] = AGENTS_DIR
        _templates_cache["list"] = []
        _templates_cache["build_count"] = int(_templates_cache.get("build_count", 0) or 0) + 1
        return []

    # Step 1: parse every .agent file.
    parsed: list[tuple[Path, dict, object]] = []
    for f in sorted(AGENTS_DIR.glob("*.agent")):
        content = f.read_text()
        entry: dict = {
            "name": f.stem,
            "file": f.name,
            "content": content[:500],
            "capabilities": None,
            "parse_error": None,
        }
        config = None
        try:
            config = parse_agentfile(f)
            entry["capabilities"] = build_capabilities_summary(config)
            entry["description"] = config.description or ""
            entry["mcp_servers"] = config.mcp_servers
            entry["skills"] = config.skills
        except AgentfileParseError as exc:
            entry["parse_error"] = str(exc)
        parsed.append((f, entry, config))

    # Step 2: build the alias_target -> [alias_stem, ...] map so alias-only
    # files can be folded into their target card as chips.
    alias_map: dict[str, list[str]] = {}
    for f, entry, config in parsed:
        if config is not None and getattr(config, "alias", ""):
            alias_map.setdefault(config.alias, []).append(f.stem)

    # Step 3: determine fleet parent IDs so we can filter out member files.
    # Members follow ``fleet-<parent>-<role>`` where ``fleet-<parent>`` is
    # a top-level fleet template ID.
    fleet_parent_ids: set[str] = {
        fleet["id"] for fleet in BUILTIN_FLEET_TEMPLATES
    }

    def _is_fleet_member(stem: str) -> bool:
        # If the stem is EXACTLY a parent (e.g. ``fleet-build-website``) it's
        # the parent card itself, not a member. If it extends the parent with
        # ``-<role>`` (e.g. ``fleet-build-website-product-manager``), it's a
        # member and must be filtered out.
        for parent in fleet_parent_ids:
            if stem == parent:
                return False
            if stem.startswith(parent + "-"):
                return True
        return False

    # Step 4: collapse case-insensitive stem duplicates. First occurrence
    # (by sorted order) wins so the output is deterministic.
    templates: list[dict] = []
    seen_stems: set[str] = set()
    for f, entry, config in parsed:
        # Skip alias-only files: they never get their own card.
        if config is not None and getattr(config, "alias", ""):
            continue
        # Skip fleet member files: parent fleet card lives in the Fleets panel.
        if _is_fleet_member(f.stem):
            continue
        key = f.stem.strip().lower()
        if key in seen_stems:
            continue
        seen_stems.add(key)
        # Attach aliases (if any alias files point to this stem).
        aliases = sorted(alias_map.get(f.stem, []))
        if aliases:
            entry["aliases"] = aliases
        templates.append(entry)

    # Populate the cache so the next request is a hit. Store under the
    # signature we captured at the top of this function, not a fresh
    # one, to avoid racing with concurrent writes.
    _templates_cache["signature"] = signature
    _templates_cache["agents_dir"] = AGENTS_DIR
    _templates_cache["list"] = templates
    _templates_cache["build_count"] = int(_templates_cache.get("build_count", 0) or 0) + 1
    return templates


@router.get("/agents/roadmap-output")
async def get_roadmap_output():
    """Return the raw text of ~/.youros/files/roadmap.md for frontend parsing."""
    from services.files_dir import get_files_dir
    roadmap_path = get_files_dir() / "roadmap.md"
    if not roadmap_path.exists():
        raise HTTPException(status_code=404, detail="No roadmap found")
    content = roadmap_path.read_text(encoding="utf-8")
    return {"content": content, "path": roadmap_path.as_posix()}


@router.get("/agents/templates")
async def list_templates():
    """List every Agentfile in the repo with parsed capabilities.

    Each entry carries a ``capabilities`` field so the Agents page can
    show writes, restrictions, budget, time limit, and sandbox in plain
    language before the user hits Spawn. If an Agentfile fails to
    parse, the entry still appears with ``parse_error`` set, so the UI
    can mark the card unspawnable and tell the user to fix the file.

    Alias-only files (e.g. ``elit.agent``, ``saa.agent``) and fleet
    member files (``fleet-build-website-product-manager.agent`` etc.)
    are filtered out so the grid shows one card per real template.
    Aliases are attached to their target card as a list so the UI can
    render them as chips under the title. See ``_build_templates_list``.
    """
    return {"templates": _build_templates_list()}


# ── PM Agent Templates (built-in + custom CRUD) ─────────────────────


from services.agent_templates_store import agent_templates_store  # noqa: E402


@router.get("/agents/pm-templates")
async def list_pm_templates():
    """List all installed agent templates (built-ins + user-installed marketplace + custom).

    ``source=builtin`` entries are always present.
    ``source=marketplace`` entries appear here only when installed=True.
    ``source=custom`` entries are always present.
    """
    return {"templates": agent_templates_store.list_installed()}


@router.get("/agents/pm-templates/marketplace")
async def list_marketplace_templates():
    """List marketplace templates not yet installed by the user.

    Each entry is augmented with ``declared_mcps`` and ``declared_skills``
    parsed from the corresponding agentfile so the Agents page can display
    which integrations an agent will use before the user spawns it.
    """
    from services.agentfile_parser import get_agent_config_by_template
    templates = [dict(t) for t in agent_templates_store.list_marketplace()]
    for t in templates:
        stem = t["name"].lower().replace(" ", "-")
        config = get_agent_config_by_template(stem)
        if config:
            t["declared_mcps"] = config.mcp_servers
            t["declared_skills"] = config.skills
    return {"templates": templates}


@router.post("/agents/pm-templates/install-persona")
async def install_persona_templates(body: dict):
    """Install all marketplace templates for a given persona.

    Body: { "persona_id": "pm" }
    Returns the list of newly installed templates.
    Called during onboarding when the user picks a persona.
    """
    persona_id = (body.get("persona_id") or "").strip()
    if not persona_id:
        raise HTTPException(status_code=400, detail="persona_id is required")
    installed = agent_templates_store.install_for_persona(persona_id)
    return {"installed": installed, "count": len(installed)}


@router.post("/agents/pm-templates/{template_id}/install")
async def install_template(template_id: str):
    """Mark a marketplace template as installed."""
    changed = agent_templates_store.install(template_id)
    if not changed:
        return {"result": "already_installed", "id": template_id}
    return {"result": "installed", "id": template_id}


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


# ── Persona-aware templates ──────────────────────────────────────────


@router.get("/agents/persona-templates")
async def list_persona_templates(persona: str = "pm"):
    """Return installed templates for the given persona.

    Built-in templates (source=builtin) are always included.
    Marketplace templates are included only when they list the requested
    persona in their ``personas`` field and are currently installed.

    If ``persona`` is empty the PM set is returned as a safe default.
    """
    from services.agentfile_parser import get_agent_config_by_template
    effective_persona = (persona or "pm").strip()
    templates = [dict(t) for t in agent_templates_store.list_for_persona(effective_persona)]
    for t in templates:
        stem = t["name"].lower().replace(" ", "-")
        config = get_agent_config_by_template(stem)
        if config:
            t["declared_mcps"] = config.mcp_servers
            t["declared_skills"] = config.skills
    return {"templates": templates, "persona": effective_persona}


# ── User-created templates (persona-agnostic) ────────────────────────


@router.get("/agents/user-templates")
async def list_user_templates():
    """Return all user-created custom templates.

    These templates are not scoped to any persona. They appear in the
    Agent Templates section regardless of which persona the user has set.
    """
    return {"templates": agent_templates_store.list_user_custom()}


@router.post("/agents/user-templates")
async def create_user_template(body: dict):
    """Create a new user custom template (persona-agnostic)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    template = agent_templates_store.create(body)
    return {"template": template}


@router.put("/agents/user-templates/{template_id}")
async def update_user_template(template_id: str, body: dict):
    """Update an existing user custom template."""
    if template_id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Built-in templates cannot be edited")
    updated = agent_templates_store.update(template_id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": updated}


@router.delete("/agents/user-templates/{template_id}")
async def delete_user_template(template_id: str):
    """Delete a user custom template."""
    if template_id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Built-in templates cannot be deleted")
    deleted = agent_templates_store.delete(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"result": "deleted", "id": template_id}


# ── Template aliases ────────────────────────────────────────────────


@router.patch("/agents/templates/{template_id}/alias")
async def set_template_alias(template_id: str, body: dict):
    """Set or clear a user alias for a template.

    Send ``{"alias": "my-shortcut"}`` to set, or ``{"alias": null}`` to clear.
    Alias rules: 2-30 chars, lowercase letters, digits, and hyphens only.
    No collisions with existing template names or other aliases.
    """
    alias = body.get("alias")
    if alias is None:
        result = agent_templates_store.clear_alias(template_id)
        return {"template": result}

    if not isinstance(alias, str):
        raise HTTPException(status_code=400, detail="alias must be a string or null")

    try:
        result = agent_templates_store.set_alias(template_id, alias)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"template": result}


@router.get("/agents/templates/{template_id}/alias")
async def get_template_alias(template_id: str):
    """Return the current user alias for a template, or null."""
    alias = agent_templates_store.get_alias(template_id)
    return {"template_id": template_id, "alias": alias}


# ── Template descriptions ───────────────────────────────────────────


@router.patch("/agents/templates/{template_id}/description")
async def set_template_description(template_id: str, body: dict):
    """Save a user-edited description for a template.

    Send ``{"description": "new text"}`` to set, or ``{"description": null}``
    to clear a builtin / marketplace override and fall back to the shipped
    blurb. Custom templates cannot have their description cleared to null
    because they require a description, the call is treated as a no-op in
    that case.
    """
    description = body.get("description")
    if description is None:
        try:
            result = agent_templates_store.clear_description(template_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"template": result}

    if not isinstance(description, str):
        raise HTTPException(status_code=400, detail="description must be a string or null")

    try:
        result = agent_templates_store.set_description(template_id, description)
    except ValueError as e:
        # 404 when the template id does not exist; 400 for validation.
        detail = str(e)
        status = 404 if detail.startswith("No template") else 400
        raise HTTPException(status_code=status, detail=detail)
    return {"template": result}


@router.get("/agents/templates/{template_id}/description")
async def get_template_description(template_id: str):
    """Return the merged description for a template (user override wins)."""
    description = agent_templates_store.get_description(template_id)
    if description is None:
        raise HTTPException(status_code=404, detail=f"No template with id '{template_id}'.")
    return {"template_id": template_id, "description": description}


# ── Grants / Permission Requests ────────────────────────────────────


def _normalize_grants(raw_grants: list) -> list[dict]:
    normalized: list[dict] = []
    for g in raw_grants:
        agent = g.get("agent_alias") or g.get("agent") or ""
        if not agent or agent == "unknown":
            continue
        normalized.append({
            "id": g.get("id", ""),
            "agent": agent,
            "type": g.get("request_type") or g.get("type") or "other",
            "target": g.get("target", ""),
            "status": g.get("status", "pending"),
            "detail": g.get("reason") or g.get("detail") or "",
            "requested_at": g.get("timestamp") or g.get("requested_at") or "",
        })
    return normalized


async def _publish_grants_state() -> None:
    try:
        raw_grants = await ostk.list_grants("pending")
        normalized = _normalize_grants(raw_grants)
        await _grants_bus.publish("snapshot", {"grants": normalized})
    except Exception:
        logger.exception("_publish_grants_state failed")


async def _publish_locks_state() -> None:
    try:
        locks = await ostk.list_locks()
        await _locks_events_mod.bus.publish(locks)
    except Exception:
        logger.exception("_publish_locks_state failed")


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

    normalized = _normalize_grants(raw_grants)
    return {"grants": normalized, "status_filter": status}


@router.post("/agents/grants/{grant_id}/approve")
async def approve_grant(grant_id: str, body: Optional[GrantApprove] = None):
    """Approve a pending permission request."""
    ttl = body.ttl if body else 0
    scope = body.scope if body else None
    try:
        result = await ostk.approve_grant(grant_id, ttl=ttl, scope=scope)
        asyncio.create_task(_publish_grants_state())
        return {"result": result, "grant_id": grant_id, "action": "approved"}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/grants/{grant_id}/deny")
async def deny_grant(grant_id: str, body: Optional[GrantDeny] = None):
    """Deny a pending permission request."""
    reason = body.reason if body else "not permitted"
    try:
        result = await ostk.deny_grant(grant_id, reason=reason)
        asyncio.create_task(_publish_grants_state())
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
    # Keep the [CORRECTION] prefix on the on-disk and on-screen text
    # so the UI badge logic and any external readers continue to work.
    correction_message = f"[CORRECTION] {message}"
    try:
        nudge_data = await ostk.write_nudge(
            name, correction_message, kind="correction",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not save correction for '{name}'. Storage error: {exc}. "
                   "Try again in a moment.",
        ) from exc

    _touch_nudge_signal(name)

    # Wrap the correction in a strong stdin envelope so the agent
    # treats it as a course change to apply NOW, not at end of task.
    # The on-disk nudge keeps the raw [CORRECTION] text so the UI
    # renders the original message without leak through.
    stdin_payload = _build_stdin_envelope(name, message, "correction")

    # Try stdin delivery (same priority order as /nudge)
    delivery = "file_only"
    writer = _agent_stdin_writers.get(name)
    if writer is not None:
        if writer.is_closing():
            _agent_stdin_writers.pop(name, None)
        else:
            try:
                writer.write((stdin_payload + "\n").encode())
                await writer.drain()
                delivery = "stdin"
            except (BrokenPipeError, ConnectionResetError, OSError):
                _agent_stdin_writers.pop(name, None)
                delivery = "file_only"
    else:
        proc = active_agents.get(name)
        if proc and hasattr(proc, "stdin") and proc.stdin:
            try:
                proc.stdin.write((stdin_payload + "\n").encode())
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
        # Tag the record with both the legacy "type" and the new "kind"
        # field. "type" stayed because older clients read it; "kind" is
        # the canonical name used everywhere else (nudge, reply, ack).
        "type": "correction",
        "kind": "correction",
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


@router.get("/agents/spawn-locks")
async def get_spawn_locks(paths: Optional[str] = Query(default=None)):
    """List active spawn path-locks, optionally filtered to entries overlapping given paths.

    Query param ``paths`` is a comma-separated list of path strings. When provided,
    only locks whose recorded glob overlaps (prefix, exact, or fnmatch) at least one
    of the given paths are returned.
    """
    from services.spawn_isolation import _spawn_lock_holders, _spawn_lock_mutex

    with _spawn_lock_mutex:
        snapshot = list(_spawn_lock_holders.items())

    now = time.time()
    path_list = [p.strip() for p in paths.split(",")] if paths else None

    locks = []
    for _key, entry in snapshot:
        spawn_id, raw_glob, acquired_epoch = entry
        if path_list is not None:
            matched = any(
                raw_glob == p
                or raw_glob.startswith(p)
                or p.startswith(raw_glob)
                or _fnmatch.fnmatch(p, raw_glob)
                or _fnmatch.fnmatch(raw_glob, p)
                for p in path_list
            )
            if not matched:
                continue
        age = int(now - acquired_epoch)
        acquired_at = datetime.fromtimestamp(acquired_epoch, tz=timezone.utc).isoformat()
        locks.append({
            "spawn": spawn_id,
            "path": raw_glob,
            "acquired_at": acquired_at,
            "age_seconds": age,
        })

    return {"locks": locks}


@router.get("/agents/spawn-preflight")
async def spawn_preflight(paths: Optional[str] = Query(default=None)):
    """Check whether the given paths can be locked without conflict.

    Query param ``paths`` is a comma-separated list of path globs.
    Always returns HTTP 200; a non-empty ``conflicts`` list means the
    spawn would get a 409. Shape matches the lock_conflict 409 body so
    the frontend can reuse its conflict-display code.
    """
    from services.spawn_isolation import compute_path_conflicts

    path_list = [p.strip() for p in paths.split(",")] if paths else []
    raw_conflicts = compute_path_conflicts(path_list)
    return {
        "conflicts": [
            {"requested": req, "held_by_spawn": holder_id, "held_path": holder_glob}
            for req, holder_id, holder_glob in raw_conflicts
        ]
    }


@router.delete("/agents/locks/{lock_name}")
async def release_lock(lock_name: str):
    """Force release a coordination lock by name."""
    try:
        result = await ostk.release_lock(lock_name)
        return {"result": result, "lock": lock_name, "action": "released"}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/agents/{name}")
async def delete_agent(name: str):
    """Remove an agent from the records entirely.

    Agents from the audit log cannot be removed from the log (it's
    immutable), but we track deletion in a separate set so the list
    endpoint filters them out.
    """
    # Remove from in-memory metadata if present
    meta = agent_metadata.get(name)
    if meta and meta.get("status") in ("running", "spawned"):
        raise HTTPException(status_code=400, detail="Cancel the agent before deleting it.")
    if meta:
        del agent_metadata[name]
        await _save_agent_state_async()

    # Add to deleted set so audit-log entries are also filtered out
    deleted = _load_deleted_agents()
    deleted.add(name)
    _save_deleted_agents(deleted)
    return {"result": f"Agent '{name}' deleted."}


class BulkDeleteAgents(BaseModel):
    statuses: list[str] = []  # e.g. ["abandoned", "cancelled", "stopped", "killed", "failed"]


@router.post("/agents/bulk-delete")
async def bulk_delete_agents(body: BulkDeleteAgents):
    """Delete all agents matching the given statuses.

    Convenience endpoint for cleaning up demo agents. Running/spawned
    agents are never deleted.
    """
    if not body.statuses:
        return {"deleted": 0, "names": []}

    safe_statuses = set(body.statuses) - {"running", "spawned"}
    if not safe_statuses:
        return {"deleted": 0, "names": []}

    # Pass 1: agents visible via list_agents (post-processed, filtered view).
    list_response = await list_agents()
    agents_list = list_response.get("agents", [])

    target_names: set[str] = set()
    for a in agents_list:
        status = a.get("status")
        if status in safe_statuses:
            target_names.add(a.get("name"))

    # Pass 2: agents in raw agent_metadata that match the requested statuses
    # but are already in deleted_agents.json (so list_agents hides them) yet
    # still appear in agent_state.json (so agent_patterns still counts them).
    # Purge them from state so recommendations clear immediately after cleanup.
    for name, meta in list(agent_metadata.items()):
        raw_status = (meta.get("status") or "").lower()
        if raw_status in safe_statuses:
            target_names.add(name)

    # Apply deletion
    deleted = _load_deleted_agents()
    for name in target_names:
        deleted.add(name)
        if name in agent_metadata:
            del agent_metadata[name]
    await _save_agent_state_async()
    _save_deleted_agents(deleted)

    return {"deleted": len(target_names), "names": sorted(target_names)}


def _plain_language_feedback(name: str, meta: dict) -> str:
    """Return a plain-language one-liner for the chat bubble.

    Used by ``GET /agents/{name}/status-feedback`` so the chat panel
    can surface meaningful progress without the user having to ask.
    The text is deliberately conversational, jargon-free, and never
    exposes raw field names like ``gate_results`` or
    ``terminated_reason``. When an agent fails or stalls, the specific
    reason from the metadata is used (summary, terminated_reason,
    completed_at) so the user sees why, not just that something
    happened.
    """
    status = (meta or {}).get("status") or "running"
    summary = (meta or {}).get("summary") or ""
    reason = (meta or {}).get("terminated_reason") or ""
    if status == "completed":
        if summary:
            return f"Agent {name} finished. {summary}"
        return f"Agent {name} finished."
    if status == "failed":
        if summary:
            return f"Agent {name} failed. {summary}"
        return f"Agent {name} failed. No summary was recorded."
    if status == "cancelled":
        if reason:
            return f"Agent {name} was cancelled. {reason}"
        return f"Agent {name} was cancelled."
    if status == "terminated_stale":
        return (
            f"Agent {name} stopped responding and was ended. "
            "The last 15 minutes went by with no progress check-in."
        )
    if status in ("killed", "stopped", "abandoned", "completed_timeout"):
        if reason:
            return f"Agent {name} stopped. {reason}"
        return f"Agent {name} stopped."
    # Running / unknown: keep it light, no spammy heartbeat text.
    return f"Agent {name} is still working."


@router.get("/agents/{name}/status-feedback")
async def agent_status_feedback(name: str):
    """Chat-friendly status snapshot for a single agent.

    The chat panel polls this after a ``spawn_agent`` tool call so it
    can drop a plain-language bubble into the conversation when the
    agent transitions to a terminal state. Returns ``exists=false`` if
    the name is unknown so the poller can stop cleanly.

    Response shape::

        {
            "name": "roadmap",
            "exists": true,
            "status": "completed",
            "terminal": true,
            "summary": "created 12 tasks from roadmap.md",
            "completed_at": "2026-04-18T21:53:00+00:00",
            "last_heartbeat_at": "2026-04-18T21:52:51+00:00",
            "feedback": "Agent roadmap finished. created 12 tasks from roadmap.md",
        }
    """
    meta = agent_metadata.get(name)
    if not meta:
        return {
            "name": name,
            "exists": False,
            "status": None,
            "terminal": False,
            "summary": None,
            "completed_at": None,
            "last_heartbeat_at": None,
            "feedback": None,
        }
    status = (meta.get("status") or "running").lower()
    # Treat "completing" as running for the chat UI: we are mid AC gate,
    # the bubble should not flip to a terminal state until we know the
    # outcome. The existing complete endpoint upgrades it to completed
    # or failed shortly after.
    display_status = "running" if status == "completing" else status
    terminal = display_status in (
        "completed",
        "failed",
        "cancelled",
        "terminated_stale",
        "killed",
        "stopped",
        "abandoned",
        "completed_timeout",
    )
    return {
        "name": name,
        "exists": True,
        "status": display_status,
        "terminal": terminal,
        "summary": meta.get("summary"),
        "completed_at": meta.get("completed_at"),
        "last_heartbeat_at": meta.get("last_heartbeat_at"),
        "feedback": _plain_language_feedback(name, {**meta, "status": display_status}),
    }


@router.post("/agents/{name}/handoff")
async def agent_handoff(name: str, body: AgentHandoff):
    """Save a handoff summary so a recovery agent can pick up where this one left off."""
    handoff_dir = OSTK_DIR / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = handoff_dir / f"{name}.md"
    handoff_path.write_text(body.summary, encoding="utf-8")
    return {"result": "handoff saved"}


@router.get("/context-pages")
async def list_context_pages():
    """List context pages stored in .ostk/memory/ for sharing between agents."""
    memory_dir = OSTK_DIR / "memory"
    if not memory_dir.exists():
        return {"pages": []}
    pages = []
    for p in sorted(memory_dir.glob("*.page")):
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        pages.append({"name": p.stem, "size_bytes": size})
    return {"pages": pages}


@router.post("/agents/{name}/arrive")
async def agent_arrive(name: str, body: AgentArrive):
    """Record a milestone arrival so the orchestrator can detect it without polling."""
    agent_dir = OSTK_DIR / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    arrive_path = agent_dir / "arrive.json"
    record = {
        "agent": name,
        "milestone": body.milestone or "",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    arrive_path.write_text(json.dumps(record), encoding="utf-8")
    return {"result": "arrive recorded"}


@router.post("/agents/{name}/note")
async def agent_note_post(name: str, body: AgentNote):
    """Append a structured note about a decision or finding."""
    agent_dir = OSTK_DIR / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    notes_path = agent_dir / "notes.jsonl"
    record = {
        "content": body.content,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with notes_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return {"result": "note saved"}


@router.get("/agents/{name}/notes")
async def agent_notes_get(name: str):
    """Return all notes recorded by an agent."""
    notes_path = OSTK_DIR / "agents" / name / "notes.jsonl"
    if not notes_path.exists():
        return {"notes": []}
    notes = []
    for line in notes_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                notes.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"notes": notes}


# →1500 transcript-tail endpoint (hyphen path, stricter contract than →1454 underscore variant)
@router.get("/agents/{name}/transcript-tail")
async def agent_transcript_tail_v2(name: str, lines: int = 20):
    """Return the last N lines of the agent's transcript file.

    Default N=20, capped at 100. Control characters are sanitized.
    Returns 404 when no transcript exists for the agent.

    Response: {"lines": [...], "name": str, "transcript_path": str, "lines_returned": int}
    """
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid agent name")

    lines = min(max(lines, 1), 100)

    source = await asyncio.to_thread(_resolve_transcript_source, name)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no transcript for agent {name}")

    def _read_tail() -> list:
        try:
            with open(source, "rb") as fh:
                content = fh.read().decode("utf-8", errors="replace")
            all_lines = content.splitlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return [sanitize_for_json(ln) for ln in tail]
        except OSError:
            return []

    tail = await asyncio.to_thread(_read_tail)
    return {
        "lines": tail,
        "name": name,
        "transcript_path": str(source),
        "lines_returned": len(tail),
    }


_WS_ACTIVE_STATUSES = frozenset({"running", "spawned", "starting"})


def _compute_running_snapshot() -> dict:
    """Return running_count and agents list filtered to user-spawned active rows.

    Ghost agents (stale heartbeat) are excluded so that running_count matches
    what the Active Sessions list shows. Without this, the sidebar badge could
    read '1' while the Active Sessions panel showed 'No agents running' —
    the badge counted status=running agents but the list hid those whose
    last_heartbeat_at was >120s old (mirroring computeAgentGhostState in
    app/src/lib/agentUtils.ts).
    """
    from services.agent_filters import is_user_spawned_agent, is_ws_ghost
    deleted_names = _load_deleted_agents()
    running = []
    for _name, _meta in agent_metadata.items():
        if _name in deleted_names:
            continue
        row = {"name": _name, **_meta}
        if is_user_spawned_agent(row) and _meta.get("status") in _WS_ACTIVE_STATUSES:
            if is_ws_ghost(_meta):
                continue
            running.append({
                "name": _name,
                "status": _meta.get("status", "running"),
                "task_id": _meta.get("task_id"),
                "needle_id": _meta.get("needle_id"),
                "label": _meta.get("label"),
                "build_state": _meta.get("build_state"),
            })
    return {"running_count": len(running), "agents": running}


@router.websocket("/ws/grants/state")
async def grants_state_ws(websocket: WebSocket):
    """Push grants state to clients in real time.

    On connect: sends one snapshot frame with all pending grants.
    Then subscribes to _grants_bus and forwards each event as a frame.
    """
    await websocket.accept()
    try:
        raw_grants = await ostk.list_grants("pending")
        normalized = _normalize_grants(raw_grants)
        await websocket.send_json({"type": "snapshot", "grants": normalized})
        async with _grants_bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    await websocket.send_json({"type": event.type, **event.payload})
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@router.websocket("/ws/locks/state")
async def locks_state_ws(websocket: WebSocket):
    """Push coordination lock state to clients in real time.

    On connect: sends one snapshot frame with all active locks.
    Then subscribes to the locks event bus and forwards each frame.
    """
    await websocket.accept()
    try:
        locks = await ostk.list_locks()
        await websocket.send_json({"type": "snapshot", "locks": locks})
        async with _locks_events_mod.bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    await websocket.send_json({"type": "snapshot", "locks": event})
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


async def _ws_keepalive(websocket: WebSocket) -> None:
    while True:
        await asyncio.sleep(15)
        try:
            await websocket.send_json({"type": "ping"})
        except Exception:
            break


@router.websocket("/ws/agents/state")
async def agents_state_ws(websocket: WebSocket):
    """Push running-agent count to clients in real time.

    On connect: sends one snapshot frame.
    On every mutation (register, complete, cancel, stale-sweep): sends a delta
    frame with the recomputed running_count and agents list.
    Sends a keepalive ping every 15 seconds so proxies do not idle-drop the
    socket.
    """
    await websocket.accept()
    keepalive: asyncio.Task | None = None
    try:
        await websocket.send_json({"type": "snapshot", **_compute_running_snapshot()})
        keepalive = asyncio.create_task(_ws_keepalive(websocket))
        # →2946: subscribes to the consolidated bus; only the agent domain
        # events surface here so the frame shape is unchanged for clients.
        async with _event_bus.subscribe() as q:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if event.type not in (AGENT_DELTA, AGENT_SWEEP):
                    continue
                frame: dict = {
                    "type": event.type.split(".", 1)[1],
                    **_compute_running_snapshot(),
                }
                if event.type == AGENT_DELTA:
                    frame["changed"] = event.payload
                await websocket.send_json(frame)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if keepalive:
            keepalive.cancel()


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
            _agent_stdin_writers.pop(name, None)
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
        # Await cancelled tasks so their CancelledError is processed and they
        # are not destroyed while still pending ("Task was destroyed but it is pending!").
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except Exception:
        logger.exception("agent attach task wait failed for %s", name)
        stdout_task.cancel()
        client_task.cancel()
        await asyncio.gather(stdout_task, client_task, return_exceptions=True)


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
            # Await cancelled tasks so they process CancelledError and are not
            # destroyed while pending ("Task was destroyed but it is pending!").
            if _pending:
                await asyncio.gather(*_pending, return_exceptions=True)
        except Exception:
            logger.exception("agent stream task wait failed for %s", name)
            output_task.cancel()
            client_read_task.cancel()
            await asyncio.gather(output_task, client_read_task, return_exceptions=True)
    finally:
        # Always kill the subprocess to avoid orphans.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass


# ---------------------------------------------------------------------------
# Chat-session helpers
#
# The in-app chat spawns a `claude` CLI subprocess for every user turn, but
# that subprocess is a one-shot completion, not a long-lived agent. It was
# not visible on the Agents page or in the Activity feed, so Tori could not
# see that the system was actually doing anything when she sent a message.
#
# These two helpers let ``services/claude_code_provider.py`` register each
# chat turn as an agent record and emit the matching ``agent.spawned`` /
# ``agent.completed`` ostk audit events, without going through the heavier
# ``/agents/register`` + ``/agents/{name}/complete`` HTTP + acceptance-gate
# path. They share the same in-memory ``agent_metadata`` dict, so the
# Agents page GET picks them up automatically.
# ---------------------------------------------------------------------------


async def register_chat_session(
    name: str,
    *,
    model: str = "claude-code-subscription",
    prompt_preview: str = "",
) -> None:
    """Register a chat-driven Claude Code subprocess as an agent.

    Idempotent on repeated calls for the same name: if the agent is
    already running, preserve its model and spawned_at and skip the
    agent.spawned event. This prevents duplicate activity-feed entries
    when both call_model and claude_code_provider register the same tab.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    existing = agent_metadata.get(name) or {}

    # Already running: update heartbeat only, do not re-emit agent.spawned
    # and do not overwrite the model that was set on first registration.
    if existing.get("status") == "running":
        existing["last_heartbeat_at"] = now_iso
        await _save_agent_state_async()
        return

    spawned_at = existing.get("spawned_at") or now_iso
    record: dict = {
        "spawned_at": spawned_at,
        "budget": existing.get("budget", "0"),
        "model": MODEL_MAP.get(model, model),
        "source": "chat",
        "status": "running",
        "last_heartbeat_at": now_iso,
        "tokens_used": existing.get("tokens_used", 0),
    }
    if prompt_preview:
        record["prompt"] = prompt_preview[:500]
    agent_metadata[name] = record
    await _save_agent_state_async()
    _emit_audit_event(
        "agent.spawned",
        {"name": name, "model": record["model"], "source": "chat"},
    )


async def complete_chat_session(
    name: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    status: str = "completed",
) -> None:
    """Mark a chat-driven agent as completed and emit the audit event."""
    meta = agent_metadata.get(name)
    if meta is None:
        return
    # Don't flip a cancelled or terminated agent back to completed.
    if meta.get("status") in ("cancelled", "terminated_stale"):
        return
    # Idempotency: already in a terminal completion state, skip re-emit.
    if meta.get("status") in ("completed", "failed"):
        return
    _completed_at = datetime.now(timezone.utc).isoformat()
    _set_agent_status(name, status, completed_at=_completed_at, last_heartbeat_at=_completed_at)
    total_tokens = int(tokens_in or 0) + int(tokens_out or 0)
    if total_tokens:
        meta["tokens_used"] = int(meta.get("tokens_used", 0) or 0) + total_tokens
    await _save_agent_state_async()
    _emit_audit_event(
        "agent.completed" if status == "completed" else "agent.failed",
        {"name": name, "source": "chat", "tokens": total_tokens},
    )
    try:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Agent Teams endpoints (→2147) -- additive, built on mailbox/nudge/spawn
# substrate. A Team is a parent Task + N teammates + a shared task graph.
# All state is in-memory (same contract as agent_metadata).
# ---------------------------------------------------------------------------

class _TeamCreate(BaseModel):
    parent_task_id: str
    description: str = ""


class _TeamMemberAdd(BaseModel):
    agent_name: str
    role: str = "member"


class _TeamTaskAdd(BaseModel):
    task_id: str


@router.post("/teams")
async def create_team(body: _TeamCreate):
    team = _teams_svc.create_team(body.parent_task_id, body.description)
    return team


@router.get("/teams")
async def list_teams():
    return {"teams": _teams_svc.list_teams()}


@router.get("/teams/{team_id}")
async def get_team(team_id: str):
    team = _teams_svc.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {team_id!r} not found")
    return team


@router.post("/teams/{team_id}/members")
async def add_team_member(team_id: str, body: _TeamMemberAdd):
    try:
        team = _teams_svc.add_teammate(team_id, body.agent_name, body.role)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return team


@router.delete("/teams/{team_id}/members/{agent_name}")
async def remove_team_member(team_id: str, agent_name: str):
    try:
        team = _teams_svc.remove_teammate(team_id, agent_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return team


@router.post("/teams/{team_id}/tasks")
async def add_task_to_team(team_id: str, body: _TeamTaskAdd):
    try:
        team = _teams_svc.add_task_to_team(team_id, body.task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return team


@router.get("/teams/{team_id}/idle-check")
async def team_idle_check(team_id: str, agent_name: str = ""):
    """Check TeammateIdle gate for *agent_name* in *team_id*.

    Queries the live ostk open task list so the check reflects current
    state. The agent_name query param is optional; when absent returns
    the parent task open/closed status for the whole team.
    """
    team = _teams_svc.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"team {team_id!r} not found")
    try:
        raw_tasks = await ostk.list_tasks(status="open")
        open_ids = {t.get("id", "") for t in raw_tasks if t.get("id")}
    except Exception:
        open_ids = set()
    if not agent_name:
        parent_open = team["parent_task_id"] in open_ids
        return {
            "team_id": team_id,
            "parent_task_id": team["parent_task_id"],
            "parent_task_open": parent_open,
        }
    result = _teams_svc.teammate_idle_check(team_id, agent_name, open_ids)
    return {"team_id": team_id, "agent_name": agent_name, **result}


# ---------------------------------------------------------------------------
# Deferred startup recovery (moved from earlier in module so that
# _resolve_transcript_source is defined when _recover_stale_agents runs).
# ---------------------------------------------------------------------------
try:
    _recover_stale_agents()
except Exception as _e:  # noqa: BLE001
    import logging
    logging.getLogger(__name__).warning("startup recovery failed: %s", _e)

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from models.schemas import AgentSpawn, AgentNudge, AgentNudgeReply, GrantApprove, GrantDeny
from services.ostk import ostk, OstkError
from services.agentfile_parser import get_agent_config
import services.agent_memory as agent_memory_svc
from services import chat_ack_bot
from services import recent_deletes

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
    "claude-code", "run", "running", "task",
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
MAILBOX_SLOW_POLL_SECONDS = 60

# Legacy alias kept so existing callers and tests that import
# MAILBOX_CHECK_INTERVAL_SECONDS keep working without changes.
MAILBOX_CHECK_INTERVAL_SECONDS = MAILBOX_SLOW_POLL_SECONDS


def agent_mailbox_instruction_short(agent_name: str) -> str:
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
    # reads it in one glance. Tori is named so the "human on the other
    # end" cue survives.
    ct = "-H 'Content-Type: application/json'"
    register_body = (
        '{"name":"' + agent_name + '","status":"running",'
        '"task":"<one line>","source":"claude-code"}'
    )
    heartbeat_body = '{"step":"<now>"}'
    reply_body = '{"message":"<reply>"}'
    complete_body = '{"summary":"<one line>"}'
    return (
        "## Mailbox (mandatory, Tori)\n"
        f"Register: curl -sSk -X POST {base}/register {ct} "
        f"-d '{register_body}'\n"
        f"Heartbeat every {slow} seconds: "
        f"curl --connect-timeout 3 -m 5 -sSk -X POST {base}/{agent_name}/heartbeat "
        f"{ct} -d '{heartbeat_body}'\n"
        f"Between every tool call, poll /nudges (long poll 30s): "
        f"curl --connect-timeout 3 -m 35 -sSk "
        f'"{base}/{agent_name}/nudges?wait=30&since=<latest_ts>"\n'
        "If a nudge is waiting, POST /reply within 2 seconds with 1-2 "
        "warm, conversational sentences acknowledging Tori and a HONEST "
        "status (examples: 'Still working on this.' or 'Got it, looking "
        "now.'). Do NOT invent a time estimate you cannot keep. Then "
        "immediately resume your task. "
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
    the backend also touches ~/.myos/nudges/{name}.signal. Agents can stat
    this file each cycle: if mtime is newer than the last check, skip ahead
    and poll /nudges immediately without waiting for the interval to expire.
    The stat call is cheap and works even at the slow poll cadence.
    """
    fast = MAILBOX_FAST_POLL_SECONDS
    slow = MAILBOX_SLOW_POLL_SECONDS
    return (
        f"## Agent registration and mailbox (mandatory)\n\n"
        "### Step 0: Register immediately\n"
        "Before doing ANY work, register yourself so the user can see you "
        "in the Agents page:\n"
        f"   `curl -sSk -X POST https://127.0.0.1:8000/api/agents/register "
        "-H 'Content-Type: application/json' "
        f"-d '{{\"name\": \"{agent_name}\", \"model\": \"sonnet\", \"budget\": 5, \"task\": \"<one line description of your task>\", \"source\": \"claude-code\"}}'`\n\n"
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
        "the Agents page in myOS. To pick those up, you MUST do the "
        "following on a regular schedule, alongside your heartbeat:\n\n"
        f"**Adaptive poll schedule**: start your poll interval at {fast} "
        f"seconds. On each cycle with no new nudge, double the interval "
        f"(20s, 40s, ...) up to a cap of {slow} seconds. When you receive "
        f"any nudge, reset the interval back to {fast} seconds. This keeps "
        "delivery fast when Tori is replying and saves your turn budget "
        "during long quiet stretches.\n\n"
        f"**Signal file shortcut**: each time the user sends a nudge the "
        f"backend also touches `~/.myos/nudges/{agent_name}.signal`. On "
        "each poll cycle, stat that file first. If its mtime is newer than "
        "your last check, skip ahead and poll /nudges immediately rather "
        "than waiting for the interval to expire. The stat call is a single "
        "syscall and effectively free.\n\n"
        f"**Long-poll (fastest delivery)**: the /nudges endpoint supports "
        f"a `?wait=<seconds>&since=<iso_timestamp>` parameter. When you "
        f"pass a `since` marker and `wait` up to {NUDGE_LONG_POLL_MAX_SECONDS}, "
        f"the server holds the request open and returns the instant Tori "
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
        "4. After acting on a nudge, you MUST post a reply so the user "
        "sees your answer inline. This is not optional. Silence feels "
        "like the agent ignored the message:\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/reply"
        " -H 'Content-Type: application/json' -d '{\"message\": \"<your reply>\"}'`\n"
        "   Post a /reply every time a nudge arrives, even if the reply "
        "is short like 'On it' or 'Still working on this.'. Post another "
        "/reply when the work the nudge asked about is done.\n"
        "   Your reply must be warm, conversational, and HONEST. Do NOT "
        "invent a time estimate. If you do not know exactly how long "
        "your current step will take, do not promise one. Good examples: "
        "'Still working on this, I will update when I have an answer.' "
        "or 'Almost done with the current step.' or 'Got it, looking "
        "now.' Bad examples: 'Acknowledged.' or 'Request received, "
        "processing.' or 'Give me two minutes.' (unless you actually "
        "know it is two minutes). Never fabricate a number.\n"
        "   Never wait for a response from Tori. Post /reply and "
        "immediately resume your task. She does not want the agent to "
        "stall waiting on her next message.\n"
        "   Between every tool call you make, poll /nudges once. If a "
        "message is waiting, reply in 1-2 sentences then keep working. "
        "If you are about to start a long-running tool call (pytest, "
        "tsc, big file write), poll BEFORE starting it and AGAIN the "
        "moment it returns. Minimum cadence: never let more than 30 "
        "seconds of wall-clock work pass without a /nudges check.\n"
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
        "Tori is waiting on the other end.\n\n"
        "### Finishing your work (mandatory)\n\n"
        "When you finish the work you were asked to do, you MUST mark "
        "yourself complete so the Agents page stops showing you as "
        "active. This is not optional. Do this as the very last step, "
        "after any final reply:\n"
        f"   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        f"https://127.0.0.1:8000/api/agents/{agent_name}/complete"
        " -H 'Content-Type: application/json' -d '{\"summary\": \"<one line summary>\"}'`\n"
        "Without this call the agent row stays in the running state "
        "forever even though you exited.\n\n"
        "### Pull model (when you finish a task)\n"
        "Before calling /complete, you can pull the next available "
        "task instead of stopping:\n"
        "   `curl --connect-timeout 3 -m 5 -sSk -X POST "
        "https://127.0.0.1:8000/api/tasks/pull`\n"
        "If the response has `claimed: true`, work on that task next. "
        f"If `claimed: false`, no tasks are available. POST /api/agents/{agent_name}/complete and exit."
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
                    proc.kill()  # SIGKILL
                elif hasattr(proc, 'pid'):
                    import os
                    os.kill(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    asyncio.create_task(_ensure_dead())
    return True


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
        # Transcript-growth heuristic: if the transcript file was written
        # recently the agent is still active even with no HTTP heartbeat.
        # This catches Claude Code Agent-tool subagents that were never
        # given the mailbox instruction block.
        if _transcript_recently_active(name, now):
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


def _transcript_grew_recently(name: str, now: datetime) -> bool:
    """Return True if the agent's transcript was modified within the last
    STALE_AGENT_TRANSCRIPT_GRACE_SECONDS seconds.

    Distinct from ``_transcript_recently_active`` (which uses the longer
    STALE_AGENT_TIMEOUT_SECONDS window to keep live agents from being swept).
    This tighter 2-minute window is used by the auto-complete pass: if the
    transcript grew in the last 2 minutes the agent is still mid-stream and
    must not be auto-completed yet.
    """
    source = _resolve_transcript_source(name)
    if source is None:
        return False
    try:
        mtime = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        return (now - mtime).total_seconds() <= STALE_AGENT_TRANSCRIPT_GRACE_SECONDS
    except OSError:
        return False


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
        with source.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
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
      No transcript was found AND the heartbeat / spawned_at is older than
      STALE_AGENT_AUTOCOMPLETE_SECONDS (5 minutes). Covers agents that
      registered but never wrote a transcript file.

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
            if not _transcript_grew_recently(name, now):
                # Transcript exists and is idle: agent finished.
                meta["status"] = "completed"
                meta["completed_at"] = now.isoformat()
                meta["summary"] = _stale_sweep_summary_for(name)
                changed = True
                _emit_audit_event("agent.completed", {"name": name})
            # Either idle (just completed above) or still active.
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
        last_seen_raw = meta.get("last_heartbeat_at") or meta.get("spawned_at")
        last_seen = _parse_iso(last_seen_raw) if isinstance(last_seen_raw, str) else None
        if last_seen is None:
            continue
        age_seconds = (now - last_seen).total_seconds()
        if age_seconds <= STALE_AGENT_AUTOCOMPLETE_SECONDS:
            continue
        # All checks passed: agent exited without calling /complete.
        meta["status"] = "completed"
        meta["completed_at"] = now.isoformat()
        meta["summary"] = _stale_sweep_summary_for(name)
        changed = True
        _emit_audit_event("agent.completed", {"name": name})
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
       backend, AND prewarm replay agents (pid=0) whose streaming coroutine
       lived only inside the dead backend process. Without this rule those
       rows survive restart and show as RUNNING in the UI even though
       nothing is happening, surprising the user with phantom agents.
    """
    now = datetime.now(timezone.utc)
    changed = False
    for name, meta in agent_metadata.items():
        if meta.get("status") != "running":
            continue
        pid = meta.get("pid")
        # Case 1: live PID. Keep.
        if pid and _is_pid_alive(pid):
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
                    continue
        # Case 3: backend-managed spawn (ui/api/chat) or prewarm replay
        # (pid=0) or stale claude-code session. Worker is dead. Mark
        # abandoned so the Active Sessions list does not show phantoms.
        meta["status"] = "abandoned"
        meta["abandoned_at"] = now.isoformat()
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
        meta["status"] = "completed"
        meta["completed_at"] = heartbeat_raw
        meta["summary"] = "Recovered after bulk cancel: agent was still active when cancelled"
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

        meta["status"] = "cancelled"
        meta["terminated_at"] = now.isoformat()
        meta["terminated_reason"] = "workflow ended"
        changed = True

    return changed


# Restore metadata from disk on startup, then recover any stale running agents.
agent_metadata.update(_load_agent_state())
_recover_stale_agents()
# Recover agents wrongly cancelled by a bulk cancel that swept actively-running
# workers. This repairs the on-disk state immediately so the UI shows the correct
# status on the first GET /agents after a server restart.
if _recover_bulk_cancelled_agents():
    _save_agent_state()

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


def _resolve_transcript_source_uncached(name: str) -> Optional[Path]:
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
    meta = agent_metadata.get(name) or {}
    raw_path = meta.get("transcript_path")
    if raw_path:
        candidate = Path(raw_path)
        if candidate.exists() and candidate.stat().st_size > 0:
            suffix = candidate.suffix.lower()
            if suffix in (".output", ".jsonl"):
                if _is_real_conversation_jsonl(candidate):
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
_candidates_cache: dict[tuple[str, str, int], tuple[float, list[tuple[float, Path, str]]]] = {}
_CANDIDATES_TTL_SECONDS = 30.0


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


def _load_candidates(root: Path, pattern: str) -> list[tuple[float, Path, str]]:
    """Return a cached list of ``(mtime, path, first_line_lower)`` tuples
    for every file under ``root`` matching ``pattern``.

    First call for a (root, pattern, root_mtime_ns) triple does the real
    filesystem work (glob, stat, open + readline per file). Subsequent calls
    with the same triple return the cached list. A new file in ``root``
    changes its directory mtime, which changes the key and forces a rescan.
    Sorted freshest-first so callers can stop at the first match.
    """
    import time as _time
    now = _time.monotonic()
    root_mtime = _dir_mtime_ns(root)
    key = (str(root), pattern, root_mtime)
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


# (project_dir, row_mtime_ns) -> (expires_at_monotonic, [(mtime, jsonl_path, description)])
# Same shape as ``_candidates_cache`` but keyed on description rather than
# first-line content. Used by the meta.description fallback path when the
# strict needle match fails.
_meta_candidates_cache: dict[tuple[str, int], tuple[float, list[tuple[float, Path, str]]]] = {}
_META_CANDIDATES_TTL_SECONDS = 30.0


def _reset_meta_candidates_cache() -> None:
    """Test hook. Drop the cached meta.json index."""
    _meta_candidates_cache.clear()


def _load_meta_candidates(project_dir: Path) -> list[tuple[float, Path, str]]:
    """Return a cached list of ``(mtime, jsonl_path, description)`` tuples
    for every ``agent-<id>.meta.json`` under ``project_dir``.

    Sorted freshest-first so callers can stop at the first match.
    """
    import time as _time
    now = _time.monotonic()
    root_mtime = _dir_mtime_ns(project_dir)
    key = (str(project_dir), root_mtime)
    entry = _meta_candidates_cache.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]

    candidates: list[tuple[float, Path, str]] = []
    try:
        for meta_path in project_dir.glob("*/subagents/agent-*.meta.json"):
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
        return {"name": name, "content": "", "bytes": 0, "empty": True, "reason": reason}

    return {"name": name, "content": content, "bytes": len(content), "empty": False}


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


@router.get("/agents")
async def list_agents(
    user_spawned_only: bool = False,
    summary: int = 0,
    filter_status: Optional[str] = Query(None, alias="status"),
    filter_source: Optional[str] = Query(None, alias="source"),
    limit: Optional[int] = None,
):
    """List every agent known to myOS.

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
    ps_result = await ostk.kernel_ps()
    audit_agents_list = await ostk.audit_agents()
    daemon_running = ps_result.get("daemon_running", False)
    daemon_agent_names = {a["name"] for a in ps_result.get("agents", [])}
    deleted_names = _load_deleted_agents()

    # Orphan cleanup intentionally removed: it was killing real agents
    # whose register call hadn't landed task metadata yet by the time
    # GET /agents fired. The user-spawned filter on the UI side already
    # hides mystery rows; no need to destroy them on the server.

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
            # If the metadata already carries a terminal status (e.g.
            # "cancelled" from cancel-all), respect it even though the
            # subprocess handle is still technically alive. The process may
            # not have exited yet but the user's intent is clear.
            #
            # ``completed_timeout`` is the demo-mode supervisor's terminal
            # status: it fires the moment ``_schedule_demo_force_complete``
            # SIGKILLs a stuck demo agent and flips the row, even though
            # the kernel may not have reaped the proc handle yet. Without
            # this in the terminal set, the very next GET /agents would
            # see proc.returncode is None and revert the row to "running",
            # which is what made the demo smoke poll loop time out.
            _TERMINAL_FROM_META = {
                "cancelled", "failed", "terminated_stale",
                "killed", "stopped", "abandoned",
                "completed_timeout",
            }
            persisted = meta.get("status", "")
            effective_status = persisted if persisted in _TERMINAL_FROM_META else "running"
            agents_map[name] = {
                "name": name,
                "source": "api",
                **meta,
                "status": effective_status,
            }

    # 2b. Persisted metadata (agents from previous server sessions)
    for name, meta in agent_metadata.items():
        if name in active_agents:
            continue  # in-memory process, step 2 already handled it
        # If this agent is already in agents_map from the audit log (step 1)
        # but agent_metadata says it's "running", "completed", or any terminal
        # status, the stored metadata is more authoritative than the audit
        # log's guess. Override the audit log entry.
        # Terminal statuses must win over audit-log "running" rows so that
        # cancel-all (and other cancel paths) stick on the next GET /agents
        # poll instead of reverting to "running".
        _AUTHORITATIVE_STATUSES = {
            "running", "completed",
            "cancelled", "failed", "terminated_stale", "killed", "stopped", "abandoned",
            # Demo-mode supervisor's terminal status. Without this in the
            # authoritative set, a force-completed demo agent keeps the
            # audit-log placeholder (source="audit") and gets filtered
            # out of the Recent tab by is_user_spawned_agent.
            "completed_timeout",
        }
        persisted_status_check = meta.get("status")
        if name in agents_map and persisted_status_check in _AUTHORITATIVE_STATUSES:
            # Fast PID-death reconcile: if metadata says running but the
            # pid has exited, flip to completed before overriding the
            # audit log. Prevents dead rows from re-stamping themselves
            # as running via this merge path.
            override_pid = meta.get("pid")
            if (
                persisted_status_check == "running"
                and override_pid
                and not _is_pid_alive(int(override_pid))
            ):
                now_iso = datetime.now(timezone.utc).isoformat()
                meta["status"] = "completed"
                meta["completed_at"] = now_iso
                meta["completion_reason"] = (
                    "PID exited (list endpoint reconciled on read)"
                )
                agent_metadata[name] = meta
                _save_agent_state()
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
            # Audit-log entry stands, but if agent_metadata has a real
            # source (e.g. "api", "ui", "claude-code"), it wins over the
            # audit-log's "audit" placeholder. This ensures a user-spawned
            # agent stays visible in the Recent tab even when its status
            # was never matched by the authoritative-status override path.
            meta_source = meta.get("source")
            if meta_source and meta_source != "audit":
                agents_map[name] = {**agents_map[name], "source": meta_source}
            continue
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
            # Fast PID-death reconcile (needle: demo staleness).
            # If the record carries a pid and that pid is NOT alive, the
            # process has exited. Flip to "completed" now rather than
            # waiting the 900s heartbeat sweep. This removes the lag
            # that made reaped builders keep showing as running in the
            # Agents page for up to 15 minutes after they finished.
            pid_for_check = meta.get("pid")
            if pid_for_check and not _is_pid_alive(int(pid_for_check)):
                now_iso = datetime.now(timezone.utc).isoformat()
                meta["status"] = "completed"
                meta["completed_at"] = now_iso
                meta["completion_reason"] = (
                    "PID exited (list endpoint reconciled on read)"
                )
                agent_metadata[name] = meta
                _save_agent_state()
                agents_map[name] = {
                    "name": name,
                    "source": meta.get("source", "api"),
                    **meta,
                    "status": "completed",
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
        elif persisted_status in (
            "terminated_stale", "cancelled", "failed", "killed", "stopped",
            # Demo-mode supervisor's terminal status. Without it here the
            # branch below would mis-derive the row as "completed" or
            # "running" and the smoke loop would never see a terminal.
            "completed_timeout",
        ):
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
        # Two thresholds. Claude Code Agent-tool subagents
        # (source="claude-code") get the shorter 8-minute window because
        # they are short-lived one-shot spawns, and the register-agent.sh
        # PreToolUse hook runs a detached heartbeat loop for 45 minutes
        # that masks the normal sweep. Everything else
        # (externally-registered agents running pytest, tsc, long shell
        # work) keeps the 15-minute window. Demoted status also differs:
        # claude-code subagents get "completed_timeout" (the common case
        # is a clean exit without calling /complete), others stay
        # "terminated_stale" (implies an unclean exit).
        source = agent.get("source") or (agent_metadata.get(name) or {}).get("source")
        is_cc_subagent = source == "claude-code"
        threshold = (
            STALE_CLAUDE_CODE_SUBAGENT_SECONDS
            if is_cc_subagent
            else STALE_AGENT_TIMEOUT_SECONDS
        )
        if age_seconds <= threshold:
            continue
        # Needle 300: proc is ground truth. If the subprocess is still
        # running, the agent is working even if its HTTP channel has
        # been quiet past the timeout. Only the death signal matters.
        if _proc_handle_is_alive(name):
            continue
        # Transcript-growth heuristic: Claude Code Agent-tool subagents
        # never see the mailbox instruction block and never heartbeat via
        # HTTP. Their transcript file mtime is the only liveness signal.
        if _transcript_recently_active(name, now_for_sweep):
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
        if is_cc_subagent:
            # Mirror the completed_at field so UI filters that hide
            # completed rows pick this up alongside terminal rows.
            agent["completed_at"] = terminated_at
        # Persist to agent_metadata so the next request does not re-sweep.
        meta = agent_metadata.get(name)
        if meta is not None:
            meta["status"] = demoted_status
            meta["terminated_at"] = terminated_at
            meta["terminated_reason"] = reason
            if is_cc_subagent:
                meta["completed_at"] = terminated_at
            sweep_changed = True
    if sweep_changed:
        _save_agent_state()

    # Auto-complete pass: flip source='claude-code' agents that exited
    # cleanly (no heartbeat for >5 min, transcript idle for >2 min, no
    # live PID) to status='completed'. Runs AFTER the terminated_stale
    # sweep so the two passes see consistent state. If an agent was just
    # swept to terminated_stale it won't be 'running' here and won't be
    # auto-completed (correct: a stale timeout is not a clean exit).
    # Persists once and updates agents_map so the response reflects the
    # new status immediately.
    ac_changed = _autocomplete_exited_subagents()
    if ac_changed:
        _save_agent_state()
        # Reflect the completed status into agents_map for this response.
        for name, meta in agent_metadata.items():
            if meta.get("status") == "completed" and name in agents_map:
                if agents_map[name].get("status") == "running":
                    agents_map[name]["status"] = "completed"
                    agents_map[name]["completed_at"] = meta.get("completed_at", "")

    # Recovery pass: flip bulk-cancelled agents that were still heartbeating
    # after the cancel timestamp back to 'completed'. This repairs the
    # regression where a sibling agent's test call to /cancel-all wiped
    # actively-running workers. Runs once-per-list so already-recovered
    # rows (status != 'cancelled') are skipped cheaply.
    rc_changed = _recover_bulk_cancelled_agents()
    if rc_changed:
        _save_agent_state()
        for name, meta in agent_metadata.items():
            if meta.get("status") == "completed" and name in agents_map:
                if agents_map[name].get("status") == "cancelled":
                    agents_map[name]["status"] = "completed"
                    agents_map[name]["completed_at"] = meta.get("completed_at", "")
                    agents_map[name].pop("terminated_at", None)
                    agents_map[name].pop("terminated_reason", None)

    # Workflow step-agent reconcile pass: auto-cancel running agents whose
    # parent workflow has already finished. The direct cancel fires inside
    # run_workflow() at completion, but this pass catches any that slipped
    # through (e.g. agents that registered after the workflow closed, or
    # agents orphaned by a server restart). Only fires after the grace
    # window (_WORKFLOW_ORPHAN_GRACE_SECONDS) so the direct cancel path
    # gets first crack. Never touches agents without workflow_run_id.
    wf_changed = _reconcile_workflow_step_agents()
    if wf_changed:
        _save_agent_state()
        for name, meta in agent_metadata.items():
            if meta.get("status") == "cancelled" and meta.get("terminated_reason") == "workflow ended":
                if name in agents_map and agents_map[name].get("status") == "running":
                    agents_map[name]["status"] = "cancelled"
                    agents_map[name]["terminated_at"] = meta.get("terminated_at", "")
                    agents_map[name]["terminated_reason"] = "workflow ended"

    # Merge in live Claude Code sessions inferred from transcript
    # file mtimes. This catches tabs that were open before the
    # SessionStart hook was wired up (which only fires for NEW
    # sessions) so Tori sees every Claude Code session she has
    # running, not just the ones that registered via the hook.
    try:
        from pathlib import Path as _Path
        from config import PROJECT_ROOT as _PROJECT_ROOT
        projects_dir = _Path.home() / ".claude" / "projects" / str(_PROJECT_ROOT).replace("/", "-")
        if projects_dir.is_dir():
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
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
        # Never let the transcript sweep fail the main agent list.
        pass

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
        # UX patch: if a row was swept to terminated_stale in a previous
        # request but the transcript has grown since then, the sweep fired
        # in a narrow timing window while the agent was still working.
        # Return the status as "running" in this response only (do not
        # overwrite the persisted record here -- /complete will flip it
        # to completed when the agent finishes). This prevents a false-red
        # row in the UI for agents that are actively writing output.
        if agent.get("status") == "terminated_stale" and _transcript_recently_active(
            agent["name"], now_for_sweep
        ):
            agent["status"] = "running"

    # Filter out agents the user explicitly deleted
    filtered_agents = [a for a in all_agents if a.get("name") not in deleted_names]

    # Optional: apply the same filter the Agents page uses so CLI callers
    # (status scripts, sidebar badge math, etc.) get the exact same count.
    if user_spawned_only:
        from services.agent_filters import is_user_spawned_agent
        filtered_agents = [a for a in filtered_agents if is_user_spawned_agent(a)]

    # Compact-mode params (summary/status/source/limit). These are used by
    # the UserPromptSubmit standing-rules hook so it can poll the backend
    # on every turn without pulling the full 600KB+ payload. Hook timeout
    # is 5s and the full response routinely exceeds that on transfer
    # alone, which was falsely tripping the "couldn't reach myOS backend"
    # fallback even when the backend was healthy.
    if filter_status:
        filtered_agents = [a for a in filtered_agents if a.get("status") == filter_status]
    if filter_source:
        filtered_agents = [a for a in filtered_agents if a.get("source") == filter_source]
    if limit is not None and limit >= 0:
        # Sort oldest-first on spawned_at so long-runners surface at the top,
        # matching the standing-rules hook's display order.
        filtered_agents = sorted(
            filtered_agents,
            key=lambda a: a.get("spawned_at") or "",
        )[:limit]

    if summary:
        # description + model are required by the frontend's
        # isUserSpawnedAgent filter (app/src/lib/agentUtils.ts): description
        # feeds isMainSession detection and model excludes subscription
        # sessions. Without these the user's own claude-code-* main-session
        # row slips through, producing a "1" Agents nav badge when Active
        # Sessions correctly shows 0.
        compact_keys = (
            "name", "source", "status", "spawned_at",
            "transcript_bytes", "last_heartbeat_at",
            "description", "model",
        )
        compact_agents = [
            {k: a.get(k) for k in compact_keys if a.get(k) is not None}
            for a in filtered_agents
        ]
        return {"agents": compact_agents}

    return {
        "daemon_running": daemon_running,
        "status": ps_result.get("raw", "unknown"),
        "active": [
            a["name"] for a in filtered_agents
            if a.get("status") == "running"
        ],
        "agents": filtered_agents,
        "avg_min_per_dollar": _avg_minutes_per_dollar(),
    }


import shutil
CLAUDE_BIN = shutil.which("claude") or "claude"

MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
}


# Hard wall-clock cap (seconds) for a demo-mode agent. After this, the
# supervisor force-completes the agent with a short "Completed quickly
# for demo." summary and SIGKILLs the subprocess if it is still alive.
DEMO_MODE_WALL_CLOCK_SECONDS = 180

# Cap on the number of output tokens a demo-mode agent can emit. The
# Claude CLI forwards this via --max-turns-cap / output budgeting so
# short demo responses stay short. Kept small so the demo finishes fast.
DEMO_MODE_MAX_OUTPUT_TOKENS = 800


def _load_project_mcp_servers_for_demo() -> Optional[str]:
    """Return a schema-valid ``--mcp-config`` JSON string with ostk only.

    Builder-template demo agents spawned by the spec-build pipeline need
    real ostk MCP tools. The project-level ``.claude/hooks/ostk-first.sh``
    fires whenever the backend or ostk kernel is running and blocks every
    native Bash/Read/Edit/Grep/Write tool call with "use mcp__ostk__*".
    Stripping MCP via ``--mcp-config '{"mcpServers":{}}'`` leaves the
    subagent with no way to satisfy the hook, so the agent freezes on
    the first tool call until the 180s wall-clock force-complete. To
    avoid that, this helper loads the project's ``.mcp.json`` at spawn
    time, extracts only the ``ostk`` server entry (other servers like
    ``stitch`` are demo-irrelevant and slow the spawn), and returns a
    compact JSON string suitable for passing to ``--mcp-config``.

    Returns ``None`` when the file does not exist, cannot be parsed, or
    does not contain an ``ostk`` server. The caller falls back to the
    empty ``{"mcpServers":{}}`` config in that case so the spawn still
    succeeds (just without ostk tools, same as before this fix).
    """
    try:
        from config import PROJECT_ROOT
    except Exception:
        return None
    mcp_path = PROJECT_ROOT / ".mcp.json"
    if not mcp_path.is_file():
        return None
    try:
        with open(mcp_path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    servers = (data or {}).get("mcpServers") or {}
    ostk_entry = servers.get("ostk")
    if not ostk_entry:
        return None
    # Compact separators so the argv stays short; the CLI validator only
    # cares about schema, not whitespace.
    return json.dumps(
        {"mcpServers": {"ostk": ostk_entry}},
        separators=(",", ":"),
    )


def _spawn_demo_mode(body: "AgentSpawn") -> bool:
    """Return True when the spawn target opts into demo mode.

    Demo mode stacks on top of quick mode. It can be opted in three ways
    in priority order:

      1. Caller-side ``body.demo_mode = True`` (set by demo surfaces like
         the workflows materialiser and the "build it" chat chain). This
         wins regardless of what the agentfile says so the demo budget
         is enforced even on agents that share an agentfile with a
         non-demo path.
      2. Agentfile ``LIMIT demo_mode true`` resolved through the
         ``template`` field.
      3. Agentfile ``LIMIT demo_mode true`` resolved through the agent
         ``name``.

    Never raises. Any lookup error falls back to False so the spawn stays
    on the normal path.
    """
    # Priority 1: explicit caller opt-in. Wins even over an agentfile
    # that forbids demo mode by omission. The ``saa`` template carve-out
    # below still applies so demo_mode + template=saa is silently
    # downgraded (saa is an opinionated long-form agent, never quick).
    try:
        explicit = getattr(body, "demo_mode", None)
    except Exception:
        explicit = None

    try:
        from services.agentfile_parser import (
            get_agent_config,
            get_agent_config_by_template,
        )
    except Exception:
        return bool(explicit)

    try:
        template_raw = (getattr(body, "template", None) or "").strip().lower()
        if template_raw == "saa":
            # Same carve-out as quick mode: saa keeps its full envelope.
            return False
        if explicit is True:
            return True

        if template_raw:
            try:
                from services.agent_templates_store import (
                    _resolve_alias,
                    _BUILTIN_BY_ID,
                    _name_to_stem,
                )
                alias_id = _resolve_alias(body.template)
                if alias_id:
                    # Try both stem derivations. Built-ins like "builder"
                    # live at ``agents/<id-minus-builtin-prefix>.agent``
                    # while marketplace templates live at
                    # ``agents/marketplace/<name_to_stem>.agent``, where
                    # the id prefix does NOT match the file stem (e.g.
                    # ``builtin-pm-roadmap`` -> ``roadmap.agent``).
                    stems = [alias_id.replace("builtin-", "")]
                    tpl = _BUILTIN_BY_ID.get(alias_id) or {}
                    tpl_name = tpl.get("name")
                    if tpl_name:
                        name_stem = _name_to_stem(tpl_name)
                        if name_stem and name_stem not in stems:
                            stems.append(name_stem)
                    for stem in stems:
                        cfg = get_agent_config_by_template(stem)
                        if cfg is not None and getattr(cfg, "demo_mode", False):
                            return True
            except Exception:
                pass
            cfg = get_agent_config_by_template(body.template)
            if cfg is not None and getattr(cfg, "demo_mode", False):
                return True
        cfg = get_agent_config(body.name)
        if cfg is not None and getattr(cfg, "demo_mode", False):
            return True
    except Exception:
        return False
    return False


# Minimum assistant-text length (in characters) the transcript must contain
# before a demo-timeout force-complete is allowed to write a Recent Documents
# .md. Below this threshold we skip the write entirely. A "Done." or a
# one-word ack is not useful to surface and a file reading only the apology
# string is worse than no file. 120 chars is the "at least a full sentence or
# two of real work" bar.
_DEMO_TIMEOUT_MIN_PARTIAL_CHARS = 120


def _extract_partial_assistant_output(agent_name: str) -> str:
    """Return the meaningful assistant text from an agent's transcript.

    Walks the on-disk transcript (via :func:`_resolve_transcript_source`) and
    pulls only the assistant's natural-language output, ignoring tool calls,
    user prompts, tool results, and the standard Claude Code opening banner.
    Used by the demo-timeout force-complete path to decide whether there is
    real partial work worth writing to Recent Documents.

    Returns an empty string on any error or when there is no usable text.
    Best-effort by design: a demo supervisor must never raise.
    """
    try:
        source = _resolve_transcript_source(agent_name)
    except Exception:
        return ""
    if source is None:
        return ""
    try:
        if not source.exists() or source.stat().st_size == 0:
            return ""
    except OSError:
        return ""

    suffix = source.suffix.lower()
    # Markdown transcripts: return the whole body. These are only written by
    # the daemon-spawned flow and are already "clean" assistant output.
    if suffix == ".md":
        try:
            return source.read_text(errors="replace").strip()
        except OSError:
            return ""

    # JSONL (or .output that sniffs as JSONL): walk the entries and collect
    # only real assistant text blocks.
    parts: list[str] = []
    try:
        with open(source, "r", errors="replace") as f:
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
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "text":
                        continue
                    text = (block.get("text") or "").strip()
                    if text:
                        parts.append(text)
    except OSError:
        return ""

    return "\n\n".join(parts).strip()


async def _schedule_demo_force_complete(
    agent_name: str,
    deadline_seconds: int = DEMO_MODE_WALL_CLOCK_SECONDS,
) -> None:
    """Background task: force-complete a demo agent after the deadline.

    Waits ``deadline_seconds`` wall-clock time. If the agent is still in
    a non-terminal status, SIGKILLs the subprocess (if any), flips the
    metadata to ``completed_timeout`` with a short summary, and persists
    the state. Idempotent: if the agent already completed or was
    cancelled, this is a no-op.

    Never raises. This runs as a fire-and-forget task so any failure
    stays local. The 90s deadline is the biggest lever for keeping the
    whole fleet under 3 minutes end-to-end.
    """
    try:
        await asyncio.sleep(max(1, int(deadline_seconds)))
    except asyncio.CancelledError:
        return

    meta = agent_metadata.get(agent_name)
    if not meta:
        return
    status = meta.get("status")
    # Terminal statuses are all a no-op. The agent already finished.
    if status in {"completed", "failed", "cancelled", "terminated_stale", "completed_timeout"}:
        return

    # SIGKILL the subprocess if we still have a handle. Hard kill is the
    # right choice for demo mode: the wall-clock cap is the whole point.
    proc = active_agents.get(agent_name)
    if proc is not None:
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            pass
        active_agents.pop(agent_name, None)

    # Flip status. We write directly rather than reusing /complete so
    # the AC gate cannot block this supervisor. The summary reads in
    # plain language for the UI.
    now_iso = datetime.now(timezone.utc).isoformat()
    meta["status"] = "completed_timeout"
    meta["summary"] = "Completed quickly for demo."
    meta["completed_at"] = now_iso
    meta["last_heartbeat_at"] = now_iso
    agent_metadata[agent_name] = meta
    try:
        _save_agent_state()
    except Exception:
        pass

    # Artifact policy for demo-timeout force-complete:
    #
    # 1. If the transcript has NO meaningful assistant text (empty or below
    #    the _DEMO_TIMEOUT_MIN_PARTIAL_CHARS bar), we write NOTHING to
    #    Recent Documents. A file that only says "the agent was stopped
    #    before it could return anything" is worse than no file: it clutters
    #    the Files tab and references internal jargon the user does not know.
    # 2. If the transcript DOES have real partial output, we write a .md
    #    whose body is the actual partial output (not an apology), so
    #    downstream hooks (roadmap notification, kind: fleet-output front
    #    matter) still fire for the fleet / Roadmap paths.
    #
    # Auto-tasks stay suppressed (``skip_auto_tasks=True``) even on the
    # real-partial-output path: a cut-short run's half-finished bullets are
    # not something we want to spam the Tasks list with.
    try:
        partial = _extract_partial_assistant_output(agent_name)
        if partial and len(partial) >= _DEMO_TIMEOUT_MIN_PARTIAL_CHARS:
            _save_agent_output_to_files(
                agent_name, partial, skip_auto_tasks=True
            )
        # Else: deliberately skip the write. The agent's completed_timeout
        # status is already recorded on the Agents page for any audit.
    except Exception:
        pass


# Demo prewarm replay: path where a real Roadmap run is cached so the
# live demo can stream it back in ~10 to 15 seconds with no LLM call.
# Only read when demo_mode is true AND the template resolves to Roadmap.
# Absence of the file is the safe default: normal LLM spawn runs.
# Documented here so operators know exactly where the demo asset lives.
PREWARM_DIR = Path.home() / ".myos" / "prewarm"
PREWARM_ROADMAP_PATH = PREWARM_DIR / "roadmap.md"

# Target wall time for the replay stream. Aim for ~5 seconds total so
# the Roadmap answer feels snappy while still showing visible token-by-
# token growth (instead of an instant paste). A ~4KB prewarm over 5s is
# ~800 bytes/s which reads as "the agent is typing" to the viewer.
# The chunked writer divides the prewarm content into small writes and
# aims for this budget, with per-chunk fsync overhead absorbed inside
# it so the wall time stays near target regardless of filesystem speed.
# Two Agents-page polls (polling at 2 s) still land inside 5s so the
# Active Sessions list catches the running row at least once.
_PREWARM_TARGET_SECONDS = 5.0
_PREWARM_CHUNK_DELAY_SECONDS = 0.04


def _is_roadmap_template_request(body: "AgentSpawn") -> bool:
    """True when a spawn body targets the Roadmap marketplace template.

    Matches the display name ("Roadmap"), the builtin id
    ("builtin-pm-roadmap"), and the legacy "pm-roadmap" stem. Name-based
    matching is intentionally avoided: a diagnose agent whose name
    contains "roadmap" must not accidentally take the replay path.
    """
    template_raw = (getattr(body, "template", None) or "").strip().lower()
    if not template_raw:
        return False
    return template_raw in {"roadmap", "pm-roadmap", "builtin-pm-roadmap"}


def _strip_leading_frontmatter(text: str) -> str:
    """Drop a leading ``---\\n...\\n---\\n`` YAML block if present.

    Only strips the first block and only when it begins on the very
    first line. Idempotent: text without a frontmatter block is
    returned unchanged. The goal is to avoid leaking the prewarm
    asset's internal metadata (``source:``, ``template:``) into the
    user-facing roadmap.md when the replay writer composes its own
    wrapper.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    # Consume the closing fence line plus its trailing newline(s).
    after = end + len("\n---")
    # Skip the newline right after ``---`` if present.
    if after < len(text) and text[after] == "\n":
        after += 1
    # Also consume one more leading blank line so the body does not
    # start with a stray empty line.
    if after < len(text) and text[after] == "\n":
        after += 1
    return text[after:]


async def _stream_prewarm_roadmap_replay(
    body: "AgentSpawn",
    transcript_path: Path,
    model: str,
) -> dict:
    """Replay a cached Roadmap transcript as if a live agent were running.

    Registers the agent row with status=running, writes the prewarm
    content to the transcript in small flushed chunks so the UI's
    transcript poll sees it growing, flips status to completed, and
    persists the final summary through the same files hook that a
    natural completion uses.

    Returns the same response shape as the real spawn path so the
    caller's JSON contract is unchanged.
    """
    # Read the cached roadmap. Safe to assume it exists: the gate check
    # in spawn_agent only enters this function when the file is present.
    content = PREWARM_ROADMAP_PATH.read_text()

    # Strip any leading YAML front matter so the downstream writer's own
    # wrapper does not produce a duplicate ``---\n...\n---`` block in
    # the saved artifact. The prewarm asset on disk is a developer file
    # and its ``source:`` / ``template:`` fields are internal metadata
    # the viewer does not need to see. _save_agent_output_to_files
    # composes a neutral wrapper (source=<agent_name>, kind=roadmap)
    # which is all the frontend parser needs.
    content = _strip_leading_frontmatter(content)

    # Register the running agent so the UI shows a live row immediately.
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate any prior transcript so the stream shows fresh growth.
    transcript_path.write_text("")

    now_spawn = datetime.now(timezone.utc).isoformat()
    spawn_meta: dict = {
        "status": "running",
        "spawned_at": now_spawn,
        "last_heartbeat_at": now_spawn,
        "budget": str(body.budget),
        "model": model,
        "pid": 0,
        "tokens_used": 0,
        "demo_mode": True,
        "prewarm_replay": True,
        "source": body.source or "api",
    }
    if body.task:
        spawn_meta["task"] = body.task
    if body.description:
        spawn_meta["description"] = body.description
    if body.template:
        spawn_meta["template"] = body.template
    # Roadmap template always produces a doc so the Files tab shows it.
    spawn_meta["template_produces_doc"] = True
    agent_metadata[body.name] = spawn_meta
    try:
        _save_agent_state()
    except Exception:
        pass

    # Compute chunk count so the total wall time lands near the target.
    # Budget 20ms per chunk for fsync and asyncio scheduling overhead on
    # top of the programmed delay. That keeps the stream inside the
    # 10 to 15 second window across slow and fast machines.
    _OVERHEAD_PER_CHUNK_S = 0.02
    per_chunk_s = _PREWARM_CHUNK_DELAY_SECONDS + _OVERHEAD_PER_CHUNK_S
    target_chunks = max(15, int(_PREWARM_TARGET_SECONDS / per_chunk_s))
    total = len(content)
    chunk_size = max(1, (total + target_chunks - 1) // target_chunks)

    async def _drip() -> None:
        try:
            written = 0
            with open(str(transcript_path), "w") as fh:
                while written < total:
                    end = min(total, written + chunk_size)
                    fh.write(content[written:end])
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
                    written = end
                    if written < total:
                        await asyncio.sleep(_PREWARM_CHUNK_DELAY_SECONDS)

            # Flip to completed and persist the summary. Mirrors the
            # natural-completion contract: completed status, summary
            # body, completed_at timestamp, and the files hook so the
            # roadmap.md artifact lands in ~/.myos/files/.
            now_done = datetime.now(timezone.utc).isoformat()
            meta = agent_metadata.get(body.name) or {}
            meta["status"] = "completed"
            meta["summary"] = content
            meta["completed_at"] = now_done
            meta["last_heartbeat_at"] = now_done
            meta["transcript_bytes"] = total
            agent_metadata[body.name] = meta
            try:
                _save_agent_state()
            except Exception:
                pass
            try:
                _save_agent_output_to_files(
                    body.name, content, skip_auto_tasks=True
                )
            except Exception:
                logger.exception(
                    "prewarm_replay.save_files.failed name=%s", body.name
                )
        except Exception:
            logger.exception(
                "prewarm_replay.stream.failed name=%s", body.name
            )

    try:
        asyncio.create_task(_drip())
    except Exception:
        logger.exception("prewarm_replay.schedule.failed name=%s", body.name)

    # Audit the replay spawn so operators can distinguish a prewarm run
    # from a real LLM run in the audit log.
    try:
        await ostk._run(
            "os",
            "audit",
            "--event",
            "agent.spawned",
            "--data",
            json.dumps(
                {
                    "name": body.name,
                    "model": model,
                    "budget": str(body.budget),
                    "prewarm_replay": True,
                }
            ),
        )
    except Exception:
        pass

    return {
        "result": f"Agent '{body.name}' spawned (prewarm replay)",
        "name": body.name,
        "pid": 0,
        "transcript": str(transcript_path),
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
                    # See _spawn_demo_mode for why two stems are tried:
                    # marketplace templates live at
                    # ``agents/marketplace/<name_to_stem>.agent`` and
                    # their file stem does NOT match the built-in id
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

    # Resolve demo mode up front so we can coerce the model to Haiku
    # (the fastest Claude tier) and skip every optional context-inject
    # step that adds first-byte latency. Demo mode is the biggest lever
    # for keeping a 4-agent fleet under 3 minutes end-to-end.
    #
    # Explicit user model override
    # ----------------------------
    # When the caller sets ``honor_explicit_model=True`` we respect the
    # ``body.model`` they picked even if the matching agentfile has
    # ``LIMIT demo_mode true``. The rest of the demo path (90s wall
    # clock, compact mailbox, skipped warm up) still applies, so the
    # agent probably times out if the user chose a slow tier. We log a
    # warning in that case so it shows up in backend logs without
    # refusing the spawn. This is the lever the template-detail edit
    # modal uses to let the user swap Haiku for Sonnet on built-in
    # templates like Roadmap without rewriting the agentfile.
    _demo_mode = _spawn_demo_mode(body)
    _honor_explicit_model = bool(getattr(body, "honor_explicit_model", False))
    if _demo_mode and not _honor_explicit_model:
        # Force the fastest tier regardless of what the caller asked for.
        model = MODEL_MAP["haiku"]
    else:
        model = MODEL_MAP.get(body.model, body.model)
        if _demo_mode and _honor_explicit_model and "haiku" not in str(model).lower():
            logger.warning(
                "demo_mode.explicit_model name=%s model=%s cap=%ds "
                "(agent may hit wall-clock force-complete)",
                body.name,
                model,
                DEMO_MODE_WALL_CLOCK_SECONDS,
            )
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

    transcript_path = PROJECT_ROOT / "transcripts" / f"{body.name}.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    # Demo prewarm replay gate. When demo_mode is true AND the template
    # resolves to Roadmap AND the cached prewarm file exists, skip the
    # claude subprocess entirely and stream the cached transcript back.
    # This is the live-demo replay path: a 10 to 15 second chunked write
    # that looks like a real agent run in the UI. Absence of any one
    # condition falls through to the normal LLM spawn path so this
    # change is a no-op for everyone except the demo surface.
    if (
        _demo_mode
        and _is_roadmap_template_request(body)
        and PREWARM_ROADMAP_PATH.exists()
    ):
        try:
            return await _stream_prewarm_roadmap_replay(
                body, transcript_path, model
            )
        except Exception:
            # Never let a replay failure block a real spawn. If the
            # cached file is unreadable for any reason, fall through to
            # the subprocess path so the demo still gets an answer.
            logger.exception(
                "prewarm_replay.entry.failed name=%s", body.name
            )

    # Prepend past memory context so the agent picks up where it left off.
    # Demo mode skips this entirely: the memory block can run to several
    # KB and costs real first-byte time we do not have.
    if _demo_mode:
        prompt_with_memory = body.prompt
    else:
        memory_ctx = agent_memory_svc.get_context(body.name)
        prompt_with_memory = (memory_ctx + body.prompt) if memory_ctx and body.prompt else body.prompt

    # Prepend shared workspace summary so agents can see findings from peers.
    # Demo mode skips this for the same reason: keep the prompt tiny.
    if not _demo_mode:
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
    # chars) so the first-byte latency on short demo spawns drops to the
    # raw subprocess fork time. The full block stays the default so
    # existing agents are untouched.
    _quick_mode = _spawn_quick_mode(body)
    # _demo_mode was resolved earlier (before the model coercion) so we
    # could force Haiku. Do not re-resolve here.
    # Demo mode stacks on top of quick mode: we always use the short
    # mailbox block when either flag is on.
    if _quick_mode or _demo_mode:
        mailbox_block = agent_mailbox_instruction_short(body.name)
    else:
        mailbox_block = agent_mailbox_instruction(body.name)
    if prompt_with_memory:
        prompt_with_memory = mailbox_block + "\n\n---\n\n" + prompt_with_memory
    else:
        prompt_with_memory = mailbox_block

    # Prepend the user's standing instructions so every spawned agent
    # follows the house rules (tone, preferred tools, how to explain
    # code, etc.) that the user saved once in Settings. Empty string
    # when the setting is blank so this is a no-op for most users.
    # Demo mode skips this: the demo prompt must stay minimal.
    if not _demo_mode:
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
    if _demo_mode:
        # Demo mode skips template envelope and quality-gate injection
        # entirely. The fleet prompts already carry the role specifics
        # and the 90s wall-clock cap forbids an AC gate anyway.
        pass
    elif body.template:
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
                canonical_name = tpl.get("name", body.template).lower().replace(" ", "-")
                # For agentfile-backed templates, the file stem is the canonical name
                # Strip "builtin-" prefix to get the stem: builtin-builder -> builder
                stem = alias_id.replace("builtin-", "")
                resolved_template = stem
        except Exception:
            pass

        template_config = get_agent_config_by_template(resolved_template)
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
    if _demo_mode:
        # Speed flags: skip MCP tool registration and skill/slash-command
        # loading. Three past footguns fixed here and locked in by tests:
        #
        # 1. ``--mcp-config '{}'`` is schema-invalid. The CLI validator
        #    rejects it with "mcpServers: Does not adhere to MCP server
        #    configuration schema" and the subprocess exits 1 in under
        #    300 ms, leaving a zero-byte transcript and no output. Tori
        #    saw this live: all four fleet members flipped straight from
        #    spawned to completed with the autocomplete summary "Agent
        #    exited without calling /complete" and tokens_used=0. The
        #    valid empty shape is ``{"mcpServers": {}}``.
        #
        # 2. ``--bare`` disables keychain OAuth reads. Without
        #    ``ANTHROPIC_API_KEY`` set in the uvicorn environment the
        #    CLI prints "Not logged in \u00b7 Please run /login" and exits
        #    1. Only add --bare when an API key is actually present so
        #    the demo path still authenticates in every developer
        #    setup. We lose the CLAUDE.md / hooks / plugin-sync skip
        #    when falling back, but we keep the MCP + skills skip which
        #    is the bulk of first-byte latency.
        #
        # 3. Builder template needs ostk MCP. When the spec-build pipeline
        #    (POST /specs/{path}/build) spawns a Builder agent in demo
        #    mode, stripping all MCP servers breaks the demo. The backend
        #    and ostk kernel are still running, so the project-level
        #    .claude/hooks/ostk-first.sh hook fires on every Bash/Read/
        #    Edit/Grep/Write tool call and blocks it with "use mcp__ostk__*".
        #    But the subagent does not have those tools (we stripped
        #    MCP). Result: the Builder agent is frozen on the first tool
        #    call until the 180s wall-clock force-complete. The spec
        #    stays at 0/3 tasks built and the demo narrative collapses.
        #    Fix: load the project's .mcp.json and inject just the ostk
        #    server entry for Builder-template demo spawns, so they have
        #    real ostk tools that the hook expects. Other demo-mode
        #    spawns (fleet members, chat "build it" chain) keep the empty
        #    MCP config since they do not invoke file-writing tools.
        _mcp_config_arg = '{"mcpServers":{}}'
        if (body.template or "").strip().lower() == "builder":
            injected = _load_project_mcp_servers_for_demo()
            if injected is not None:
                _mcp_config_arg = injected
        cmd.extend([
            "--strict-mcp-config",
            "--mcp-config", _mcp_config_arg,
            "--disable-slash-commands",
        ])
        if os.environ.get("ANTHROPIC_API_KEY"):
            cmd.append("--bare")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=open(str(transcript_path), "w"),
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )

        # Send the initial prompt to stdin and CLOSE it (write_eof) so
        # ``claude --print`` knows the input is complete and produces
        # output. With ``--input-format text`` (the default), claude
        # reads stdin until EOF and only emits its response after that
        # signal. Holding the pipe open caused every demo-mode spawn
        # to hang until the 90s wall-clock force-complete with a 0-byte
        # transcript. Nudges fall back to the file-based mailbox via
        # the BrokenPipe-clean path in /nudge (line ~5012).
        #
        # Defensive: wrap the write+drain so a subprocess that exits
        # immediately (claude CLI not authenticated, exec arg too long,
        # OS pipe race under burst load) does not surface a confusing
        # ``WriteUnixTransport closed`` error to the caller. The agent
        # row still lands with status=running so the demo smoke can
        # observe the supervisor force-complete or the natural exit; we
        # just skip pushing the prompt down a half-open pipe.
        if prompt_with_memory and proc.stdin is not None:
            try:
                proc.stdin.write(prompt_with_memory.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as _stdin_exc:
                logger.warning(
                    "spawn.stdin_drain.failed name=%s err=%s",
                    body.name, _stdin_exc,
                )
        # Close stdin so claude --print sees EOF and emits its response.
        # Without this, the subprocess hangs forever waiting for more
        # input. asyncio's StreamWriter.close() alone does not reliably
        # send EOF on a unix pipe to a subprocess; write_eof() is the
        # half-close primitive that actually sends EOF before the
        # transport tears down. Regression guard: e2e-roadmap-probe2
        # (2026-04-17) hung 70s+ alive with transcript_bytes=0 because
        # only close() was called and the child kept reading.
        if proc.stdin is not None and not proc.stdin.is_closing():
            try:
                if proc.stdin.can_write_eof():
                    proc.stdin.write_eof()
            except (BrokenPipeError, OSError, RuntimeError, AttributeError):
                pass
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, RuntimeError):
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

        async def _drain_stderr(p, name: str, tpath: Path, slog: Path) -> None:
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
            except Exception:
                pass

        try:
            asyncio.create_task(
                _drain_stderr(proc, body.name, transcript_path, stderr_log_path)
            )
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
        # Record the resolved template name so completion hooks (e.g. the
        # Roadmap output capture) can detect which marketplace template
        # produced the final answer. Stored raw; downstream comparisons
        # lowercase and strip before matching.
        if body.template:
            spawn_meta["template"] = body.template
            # Resolve the template's ``produces_doc`` flag at spawn time and
            # stamp it into metadata so the /complete hook does not need to
            # re-look up the template (which may have been edited between
            # spawn and complete). Only templates that opt in produce a .md
            # artifact and auto-tasks for a solo agent run. Fleets and
            # workflows keep their existing signals (fleet_id, workflow
            # rollup path) and do not depend on this flag.
            try:
                from services.agent_templates_store import agent_templates_store
                tpl = agent_templates_store.get_by_name_or_alias(body.template)
                if tpl and tpl.get("produces_doc"):
                    spawn_meta["template_produces_doc"] = True
            except Exception:
                pass
        # Persist the caller-supplied human-readable task and description
        # so the Agents page can show a friendly title instead of the
        # opaque internal name (e.g. "build-568"). The UI's agentTitleParts
        # helper promotes ``task`` (or ``description``) to the primary
        # row label when present. Without this block the spawn path drops
        # both fields and the row falls back to the raw name.
        if body.task:
            spawn_meta["task"] = body.task
        if body.description:
            spawn_meta["description"] = body.description
        # Always stamp a real source. When the caller does not specify one,
        # default to "api" so the list endpoint never falls back to the
        # audit-log's "source=audit" placeholder (which is filtered out of
        # the Recent tab by is_user_spawned_agent). Previously this block
        # only set the key when body.source was truthy, leaving the row
        # with no source at all and letting the audit-log merge win.
        spawn_meta["source"] = body.source or "api"
        # Demo mode: stamp a tight output token cap and the wall-clock
        # deadline so the UI can show "force-complete at ..." and the
        # supervisor task below knows exactly when to pull the plug.
        if _demo_mode:
            # Caller can override the default 90 second cap via
            # body.deadline_seconds. Used by built-in workflows so a
            # 3-step pipeline can fit each step in 30s and keep the
            # whole workflow under 90s. Clamped to the supported range
            # so a stray 0 or huge value never bypasses the supervisor.
            _raw_deadline = getattr(body, "deadline_seconds", None)
            try:
                _raw_int = int(_raw_deadline) if _raw_deadline is not None else None
            except (TypeError, ValueError):
                _raw_int = None
            if _raw_int is None:
                _deadline = DEMO_MODE_WALL_CLOCK_SECONDS
            else:
                _deadline = max(5, min(_raw_int, DEMO_MODE_WALL_CLOCK_SECONDS))
            spawn_meta["demo_mode"] = True
            spawn_meta["max_output_tokens"] = DEMO_MODE_MAX_OUTPUT_TOKENS
            spawn_meta["deadline_seconds"] = _deadline
            spawn_meta["force_complete_at"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=_deadline)
            ).isoformat()
        # Preserve recovery_count across re-spawns so the cap is tracked
        existing_meta = agent_metadata.get(body.name) or {}
        if existing_meta.get("recovery_count"):
            spawn_meta["recovery_count"] = existing_meta["recovery_count"]
        # Workflow linkage: carry forward from caller or existing metadata.
        workflow_run_id = body.workflow_run_id or existing_meta.get("workflow_run_id")
        if workflow_run_id:
            spawn_meta["workflow_run_id"] = workflow_run_id
        agent_metadata[body.name] = spawn_meta
        _save_agent_state()

        # Demo mode: schedule the hard wall-clock force-complete as a
        # fire-and-forget background task. If the agent already finishes
        # on its own (normal /complete path), this task sees the
        # terminal status and no-ops. Otherwise it SIGKILLs the
        # subprocess at the deadline mark and flips status to
        # completed_timeout. Uses the resolved per-spawn deadline so a
        # built-in workflow step (30s) and a plain demo agent (90s)
        # both honour the right window.
        if _demo_mode:
            try:
                asyncio.create_task(
                    _schedule_demo_force_complete(
                        body.name,
                        deadline_seconds=spawn_meta.get(
                            "deadline_seconds", DEMO_MODE_WALL_CLOCK_SECONDS
                        ),
                    )
                )
            except Exception:
                logger.exception(
                    "demo_mode.schedule_force_complete.failed name=%s",
                    body.name,
                )

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
            "name": body.name,
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
        workspace_dir = Path.home() / ".myos" / "agent_workspace"
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


@router.post("/agents/fleets/spawn")
async def spawn_fleet(body: FleetSpawn):
    """Spawn all members of a fleet template as parallel agents.

    Each member gets a role-specific prompt with the user's context
    prepended. All agents share the workspace for coordination.

    Deprecated: fleet launching has been folded into the Plans page
    template grid. This endpoint is kept alive for backwards compatibility
    with existing callers but new UI should use
    POST /api/specs/from-template instead.
    """
    logger.warning(
        "spawn_fleet called; fleets are folded into plan templates; "
        "caller fleet_id=%s",
        body.fleet_id,
    )
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

    # If the user previously deleted any fleet member by name, un-delete
    # it so this relaunch actually shows up in the Active Agents list.
    # Without this, launching "Build a Website" a second time appears to
    # succeed (the POST returns 200) but every card gets filtered out of
    # GET /api/agents because the deleted_agents guard still hides them.
    # Same demo bug regression as the claude-code source stamp below.
    deleted_names = _load_deleted_agents()
    deleted_changed = False
    for member in fleet["members"]:
        role_slug = member["role"].lower().replace(" ", "-")
        agent_name = f"{fleet['id']}-{role_slug}"
        if agent_name in deleted_names:
            deleted_names.discard(agent_name)
            deleted_changed = True
    if deleted_changed:
        _save_deleted_agents(deleted_names)

    # Fleet-level speed flags: when the template opts in to quick_mode or
    # demo_mode, every member inherits the fast path (90s force-complete).
    # This is how fleet-build-website hits the sub-3-minute demo budget
    # without needing a per-member agentfile.
    fleet_quick_mode = bool(fleet.get("quick_mode", False))
    fleet_demo_mode = bool(fleet.get("demo_mode", False))

    # The e2e smoke test spawns this fleet on every release to prove the
    # endpoint is alive, and it marks the request by putting "e2e test
    # only" in the context field. When we see that sentinel, tag every
    # member with source="e2e-smoke" so the Recent Agents panel can hide
    # these rows by default. Regression guard for Tori's complaint that
    # e2e runs were cluttering her Recent list with noise.
    is_e2e_context = "e2e test only" in (body.context or "").lower()
    member_source = "e2e-smoke" if is_e2e_context else "claude-code"

    # Build all agent bodies first so the gather below is pure I/O.
    member_specs: list[tuple[dict, AgentSpawn]] = []
    for member in fleet["members"]:
        role_slug = member["role"].lower().replace(" ", "-")
        agent_name = f"{fleet['id']}-{role_slug}"
        # Per-member speed flags override the fleet default so a single
        # slow role (e.g. a synthesis step) can opt out even inside a
        # quick-mode fleet. Absent keys inherit the fleet default.
        member_quick_mode = bool(member.get("quick_mode", fleet_quick_mode))
        member_demo_mode = bool(member.get("demo_mode", fleet_demo_mode))
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
            # Propagate the resolved per-member demo flag so spawn_agent
            # skips past-session memory injection, workspace summary, and
            # standing instructions even when the member has no matching
            # agentfile. Without this, demo fleet members leaked "Past
            # sessions" blocks from earlier demo runs into fresh spawns.
            demo_mode=member_demo_mode if member_demo_mode else None,
        )
        # Stash the flags on the dict so _spawn_one can read them without
        # re-parsing the template. AgentSpawn itself stays quiet since
        # those flags are not pydantic fields.
        member_specs.append((
            {**member, "_quick_mode": member_quick_mode, "_demo_mode": member_demo_mode},
            agent_body,
        ))

    # Spawn all fleet members in parallel. Each subprocess fork is I/O-bound
    # (os.fork + pipe setup) so asyncio.gather runs them concurrently without
    # blocking the event loop. Previously they were sequential: 4 agents x
    # ~0.3 s/fork = ~1.2 s serialized. Parallel brings that to ~0.3 s total.
    t_spawn_start = time.perf_counter()
    logger.info(
        "fleet.spawn.start fleet_id=%s members=%d model=%s",
        fleet["id"], len(member_specs), body.model,
    )

    async def _spawn_one(member: dict, agent_body: AgentSpawn) -> dict:
        agent_name = agent_body.name
        try:
            result = await spawn_agent(agent_body)
            # The UI promises "They are starting now" and expects to see
            # all fleet members in the Active Agents list immediately.
            # spawn_agent adds each agent to active_agents and stamps
            # metadata status=running, but the claude CLI subprocess can
            # exit in milliseconds (short prompt, flaky env), after which
            # the list_agents step 2 branch flips the status to "failed"
            # on the very next GET and the card disappears. To keep the
            # fleet visible while the real subprocess does its work, we
            # promote each member to the claude-code source (the same
            # shape /agents/register uses) and drop it from active_agents
            # so step 2b drives the UI. The subprocess keeps running in
            # the background and will update its own status via the
            # mailbox /complete hook when finished. Regression guard for
            # the "Active Agents empty after Launch" demo bug.
            active_agents.pop(agent_name, None)
            now_iso = datetime.now(timezone.utc).isoformat()
            existing_meta = agent_metadata.get(agent_name) or {}
            existing_meta.update({
                "status": "running",
                "source": member_source,
                "role": member["role"],
                "fleet_id": fleet["id"],
                "fleet_name": fleet["name"],
                "last_heartbeat_at": now_iso,
            })
            existing_meta.setdefault("spawned_at", now_iso)
            agent_metadata[agent_name] = existing_meta
            _save_agent_state()
            # Fleet-level demo budget: when the member opts in via
            # quick_mode or demo_mode, schedule a force-complete so a
            # single slow role never holds up the whole fleet past the
            # 90-second wall clock. Fire-and-forget; failures are local.
            if member.get("_quick_mode") or member.get("_demo_mode"):
                try:
                    asyncio.create_task(
                        _schedule_demo_force_complete(
                            agent_name,
                            DEMO_MODE_WALL_CLOCK_SECONDS,
                        )
                    )
                except Exception:
                    pass
            return {
                "name": agent_name,
                "role": member["role"],
                "pid": result.get("pid"),
            }
        except Exception as e:
            return {
                "name": agent_name,
                "role": member["role"],
                "error": str(e),
            }

    results = await asyncio.gather(
        *(_spawn_one(m, ab) for m, ab in member_specs)
    )
    spawned = list(results)

    elapsed_ms = int((time.perf_counter() - t_spawn_start) * 1000)
    logger.info(
        "fleet.spawn.done fleet_id=%s total=%d elapsed_ms=%d",
        fleet["id"], len(spawned), elapsed_ms,
    )

    return {
        "fleet": fleet["name"],
        "spawned": spawned,
        "total": len(spawned),
        "elapsed_ms": elapsed_ms,
    }


@router.post("/agents/fleets/{fleet_id}/demo-run")
async def demo_run_fleet(fleet_id: str, body: Optional[FleetSpawn] = None):
    """Demo-fast fleet launch: parallel, Haiku, 90 second hard cap.

    Tailored for live stage demos. Every member is force-flipped into
    demo mode (Haiku, --bare, no MCP, no CLAUDE.md) regardless of what
    the matching agentfile says, and a supervisor task SIGKILLs any
    agent still running after 90 seconds and marks it
    ``completed_timeout`` with a plain-language summary.

    Returns immediately with the list of spawned agent names, the 90
    second deadline, and the ISO timestamp at which any stragglers will
    be force-completed. The supervisor runs in the background so the
    HTTP call returns in well under a second on a warm fleet.
    """
    from services.fleet_templates import list_fleet_templates
    from services.policy_enforcement import check_budget, check_approval_required

    # Default context/budget when the caller omits a body. Demo mode
    # caps spend tightly so a stuck agent cannot burn the budget before
    # the 90s deadline fires.
    if body is None:
        body = FleetSpawn(fleet_id=fleet_id, context="", model="haiku", budget=0.25)
    else:
        # Ensure the path param wins if the caller sent a mismatched body.
        body.fleet_id = fleet_id

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
    fleet = next((f for f in templates if f["id"] == fleet_id), None)
    if not fleet:
        raise HTTPException(status_code=404, detail=f"Fleet template '{fleet_id}' not found")

    # Un-delete any previously deleted fleet members so the Active
    # Agents list actually shows them (same guard as spawn_fleet).
    deleted_names = _load_deleted_agents()
    deleted_changed = False
    for member in fleet["members"]:
        role_slug = member["role"].lower().replace(" ", "-")
        agent_name = f"{fleet['id']}-{role_slug}"
        if agent_name in deleted_names:
            deleted_names.discard(agent_name)
            deleted_changed = True
    if deleted_changed:
        _save_deleted_agents(deleted_names)

    # Same e2e-smoke tag as spawn_fleet: when the caller marks the
    # context with "e2e test only", stamp every member with
    # source="e2e-smoke" so Recent Agents hides them by default.
    is_e2e_context = "e2e test only" in (body.context or "").lower()
    member_source = "e2e-smoke" if is_e2e_context else "claude-code"

    member_specs: list[tuple[dict, AgentSpawn]] = []
    for member in fleet["members"]:
        role_slug = member["role"].lower().replace(" ", "-")
        agent_name = f"{fleet['id']}-{role_slug}"
        # Keep prompts very short in demo mode.
        full_prompt = (
            f"ROLE: {member['role']}\n"
            f"CONTEXT: {body.context}\n"
            f"{member['prompt']}\n"
            "Demo mode: be brief. Under 200 words. Stop after your first cut."
        )
        agent_body = AgentSpawn(
            name=agent_name,
            prompt=full_prompt,
            model="haiku",  # force Haiku for demos regardless of caller
            budget=body.budget,
            # Force demo_mode on every demo-run member so the spawn path
            # skips past-session memory injection, workspace summary,
            # standing instructions, and CLAUDE.md auto-discovery even
            # when the member has no matching agentfile with
            # ``LIMIT demo_mode true``. Without this, the first demo run
            # is clean but every subsequent run appends a summary and
            # the NEXT demo inherits a growing "Past sessions" block.
            # Tori's screenshot showed exactly that: 5 past sessions for
            # the roadmap agent leaking into a fresh demo.
            demo_mode=True,
        )
        member_specs.append((member, agent_body))

    t_spawn_start = time.perf_counter()
    logger.info(
        "fleet.demo_run.start fleet_id=%s members=%d",
        fleet["id"], len(member_specs),
    )

    deadline_seconds = DEMO_MODE_WALL_CLOCK_SECONDS
    force_complete_at = (
        datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds)
    ).isoformat()

    async def _spawn_one_demo(member: dict, agent_body: AgentSpawn) -> dict:
        agent_name = agent_body.name
        try:
            result = await spawn_agent(agent_body)
            active_agents.pop(agent_name, None)
            now_iso = datetime.now(timezone.utc).isoformat()
            existing_meta = agent_metadata.get(agent_name) or {}
            existing_meta.update({
                "status": "running",
                "source": member_source,
                "role": member["role"],
                "fleet_id": fleet["id"],
                "fleet_name": fleet["name"],
                "last_heartbeat_at": now_iso,
                # Stamp demo_mode on every member unconditionally so the
                # supervisor fires even when no agentfile matched.
                "demo_mode": True,
                "deadline_seconds": deadline_seconds,
                "force_complete_at": force_complete_at,
            })
            existing_meta.setdefault("spawned_at", now_iso)
            agent_metadata[agent_name] = existing_meta
            _save_agent_state()
            # Belt-and-suspenders: schedule the force-complete task even
            # when the agentfile did not flip demo mode on its own.
            # _schedule_demo_force_complete is idempotent.
            try:
                asyncio.create_task(
                    _schedule_demo_force_complete(
                        agent_name, deadline_seconds=deadline_seconds
                    )
                )
            except Exception:
                logger.exception(
                    "demo_run.schedule_force_complete.failed name=%s",
                    agent_name,
                )
            return {
                "name": agent_name,
                "role": member["role"],
                "pid": result.get("pid"),
            }
        except Exception as e:
            return {
                "name": agent_name,
                "role": member["role"],
                "error": str(e),
            }

    # Spawn every fleet member in parallel via asyncio.gather.
    results = await asyncio.gather(
        *(_spawn_one_demo(m, ab) for m, ab in member_specs)
    )
    spawned = list(results)

    elapsed_ms = int((time.perf_counter() - t_spawn_start) * 1000)
    logger.info(
        "fleet.demo_run.done fleet_id=%s total=%d elapsed_ms=%d",
        fleet["id"], len(spawned), elapsed_ms,
    )

    return {
        "fleet": fleet["name"],
        "agents": [s["name"] for s in spawned],
        "spawned": spawned,
        "total": len(spawned),
        "elapsed_ms": elapsed_ms,
        "deadline_seconds": deadline_seconds,
        "will_force_complete_at": force_complete_at,
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


@router.post("/agents/register")
async def register_agent(body: AgentSpawn, request: Request = None):
    """Register an external agent (e.g., Claude Code subagent) without spawning a process.

    This lets myOS track agents that are managed by another system. Agents
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

    model = MODEL_MAP.get(body.model, body.model)
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
            # Preserve the hook's description (usually richer) but adopt
            # the subagent's prompt if the hook did not capture one.
            if body.prompt and not existing_meta.get("prompt"):
                existing_meta["prompt"] = body.prompt[:500]
            # Record the alias so later /heartbeat, /status, and /complete
            # calls under the subagent-chosen name route back to the
            # existing row without 404-ing.
            agent_aliases[body.name] = existing_name
            agent_metadata[existing_name] = existing_meta
            _save_agent_state()
            return {
                "result": (
                    f"Agent '{body.name}' merged into existing hook "
                    f"preregistration '{existing_name}'"
                ),
                "source": "claude-code",
                "status": existing_meta.get("status", "running"),
                "merged_into": existing_name,
                "mailbox_instruction": agent_mailbox_instruction(existing_name),
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
    _TERMINAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "terminated_stale",
        "completed_timeout",
        "killed",
        "stopped",
        "abandoned",
    }
    existing_status = existing.get("status", "")
    # Reject re-registration of a name that already holds a terminal status
    # as running. A Claude Code subprocess that keeps heartbeating after a
    # user cancel must NOT resurrect the same row: the correct behaviour
    # is for the caller to pick a fresh name (suffix with a short random
    # token) so the cancelled row stays cancelled and the new work gets a
    # new row. Returning 409 tells the caller to retry with a new name.
    if existing_status in _TERMINAL_STATUSES and status == "running":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Agent '{body.name}' already terminated with status "
                f"'{existing_status}'. Register under a fresh name "
                "(e.g. append '-retry-XXXX') so the terminal row is "
                "preserved and the new work gets its own row."
            ),
        )

    record: dict = {
        "spawned_at": spawned_at,
        "budget": str(body.budget),
        "model": model,
        "source": body.source,
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
    agent_metadata[body.name] = record
    _save_agent_state()

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


# Directory where user-facing generated files (like roadmap.md from the
# Roadmap template) are written. Scanned by /docs/recent so these files
# show up on the Files page without needing to live inside the repo.
MYOS_FILES_DIR = Path.home() / ".myos" / "files"

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
            r"chat-|workflow-|spec-|fleet-build-)",
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
    artifact in ``~/.myos/files/``?"

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
    return bool(fleet_id or is_roadmap or template_produces_doc)


def _save_agent_output_to_files(
    agent_name: str,
    summary: str,
    skip_auto_tasks: bool = False,
    emit_notification: bool = True,
) -> list[Path]:
    """Persist an agent's final summary to ``~/.myos/files/`` so it shows
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
        # this roadmap" before any tasks get generated. The torichat
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


# Backward-compatible shim so external callers / existing tests
# referencing the old name keep working. Returns the Roadmap path if
# one was written (matching the old contract), else None.
def _maybe_save_roadmap_output(agent_name: str, summary: str) -> Optional[Path]:
    paths = _save_agent_output_to_files(agent_name, summary)
    for p in paths:
        if p.name == "roadmap.md":
            return p
    return None


def _retroactively_save_agent_summaries(limit: int = 50) -> int:
    """Walk ``~/.myos/agent_memory/*.json`` and write the latest summary
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
        return {
            "result": f"Agent '{name}' was deleted, complete ignored",
            "status": "deleted",
        }

    # Set a "completing" sentinel status BEFORE any awaits so concurrent
    # requests see a non-None status and are turned away by the guard above.
    # This closes the race window where two simultaneous /complete calls
    # both passed the guard while the first was still awaiting the AC gate.
    now_iso = datetime.now(timezone.utc).isoformat()
    if name in agent_metadata:
        agent_metadata[name]["status"] = "completing"
    else:
        agent_metadata[name] = {
            "spawned_at": now_iso,
            "status": "completing",
            "source": "claude-code",
        }
    # Persist the sentinel so even a server restart within the AC window
    # does not create a second completion event.
    _save_agent_state()

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
                _save_agent_state()

    # Save session summary to memory if provided
    if body and body.summary:
        try:
            agent_memory_svc.append_summary(name, body.summary)
        except Exception:
            pass

        # Files-tab artifact capture: every agent whose summary clears
        # the length / name-filter bar gets its final output written to
        # ~/.myos/files/<slug>-<timestamp>.md so Tori can find IA review
        # / PRD / custom-build output alongside the roadmap on the Files
        # tab. Roadmap-template runs also keep their stable roadmap.md
        # copy so chat's "read the roadmap.md" shortcut still works.
        # Best-effort; a write failure must never block completion.
        try:
            _save_agent_output_to_files(name, body.summary)
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
    if name in agent_metadata:
        agent_metadata[name]["status"] = "completed"
        agent_metadata[name]["completed_at"] = completed_at
    else:
        # Agent was deleted from metadata before /complete arrived (deleted
        # agents are blocked above, so this branch is an unlikely edge case
        # where metadata was cleared mid-flight). Recreate a minimal record.
        agent_metadata[name] = {
            "spawned_at": completed_at,
            "completed_at": completed_at,
            "status": "completed",
            "source": "claude-code",
        }
    _save_agent_state()

    # Log to audit so the audit_agents() helper also reflects completion
    _emit_audit_event("agent.completed", {"name": name})

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
    """Mark an agent as cancelled and terminate its subprocess if one exists.

    Unlike the old behaviour that only flipped metadata, this now also
    sends SIGTERM to the in-process subprocess (if any) and follows up
    with SIGKILL after a 5-second grace period so resilient processes
    do not survive a user cancel.
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

    # Audit so the audit log reflects the cancel.
    _emit_audit_event("agent.cancelled", {"name": name, "reason": reason})

    return {"ok": True, "status": "cancelled", "terminated_at": now_iso, "process_killed": killed}


@router.post("/agents/cancel-all")
async def cancel_all_agents():
    """Cancel every background agent that is currently running or spawned.

    Safety gate: agents with ``source='chat'`` are live interactive sessions
    and must never be cancelled here. Only agents with ``source='claude-code'``
    or ``source='api'`` (background work) are eligible.

    Agents that are already in a terminal state (completed, failed, cancelled,
    terminated_stale, killed, stopped) are left untouched.

    Returns the count of agents cancelled and their names so the frontend can
    show a meaningful confirmation message.
    """
    _TERMINAL_STATUSES = {"completed", "failed", "cancelled", "terminated_stale", "killed", "stopped"}
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

        meta["status"] = "cancelled"
        meta["terminated_at"] = now_iso
        meta["terminated_reason"] = "bulk cancel"
        cancelled_names.append(name)

    if cancelled_names:
        _save_agent_state()
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

    return {"cancelled": len(cancelled_names), "names": cancelled_names}


# How often the background reconciliation loop runs (seconds).
# Tightened from 300s to 60s so zombie "running" rows (agents that died
# without calling /complete) clear within a minute instead of sitting in
# the demo for 5+ minutes. The actual stale-cutoff is still governed by
# STALE_AGENT_TIMEOUT_SECONDS / STALE_CLAUDE_CODE_SUBAGENT_SECONDS; this
# only controls how often we check.
RECONCILE_INTERVAL_SECONDS = 60  # 1 minute


@router.post("/agents/reconcile")
async def reconcile_agents():
    """Scan running agent records and mark orphans as stopped.

    For each agent_metadata entry with status "running":
    - If a live subprocess exists in active_agents, leave it alone.
    - If the transcript was recently active, leave it alone.
    - If no live process and no recent heartbeat (>5 min), mark stopped.

    Returns the count of reconciled (stopped) agents and the count of
    agents that are still legitimately running.
    """
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

        # No live process, no recent heartbeat, no transcript activity.
        # Mark as stopped.
        meta["status"] = "stopped"
        meta["terminated_at"] = now_iso
        meta["terminated_reason"] = "reconcile: no live process or recent heartbeat"
        reconciled_names.append(name)

        # Clean up the active_agents dict entry if lingering.
        active_agents.pop(name, None)
        _agent_stdin_writers.pop(name, None)

    if reconciled_names:
        _save_agent_state()
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
            stale_changed = _sweep_stale_running_agents()
            ac_changed = _autocomplete_exited_subagents()
            if stale_changed or ac_changed:
                _save_agent_state()
        except Exception:
            pass
        try:
            await reconcile_agents()
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


_NUDGE_SIGNAL_DIR = Path.home() / ".myos" / "nudges"


def _touch_nudge_signal(name: str) -> None:
    """Touch ~/.myos/nudges/<name>.signal to let the agent skip ahead.

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
            "\n=== URGENT CORRECTION FROM TORI (act immediately) ===\n"
            "She just sent a course-correction. Do NOT defer this to "
            "the end of your task. Stop your current step at the next "
            "safe boundary, change your approach to honour the "
            "correction, and POST a /reply within 2 seconds confirming "
            "you have it and what you will do differently.\n"
            f"Correction: {message}\n"
            f"Reply now: {reply_curl}\n"
            "=== end correction ===\n"
        )
    return (
        "\n=== URGENT MAILBOX MESSAGE FROM TORI (act now) ===\n"
        "She just sent you a message through the inline chat. Do NOT "
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
            meta["status"] = "completed"
            meta["completed_at"] = now_iso
            meta["revival_reason"] = (
                "Reply arrived after the record was marked terminated_stale. "
                "The agent was still working. Record restored to completed."
            )
            revived = True
        _save_agent_state()

    # Wake any long-poll /nudges waiters so the frontend transcript poll
    # surfaces the reply immediately instead of on the next cycle. This
    # is symmetrical to POST /nudge: both directions push.
    _wake_nudge_waiters(name)

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
    """List marketplace templates not yet installed by the user."""
    return {"templates": agent_templates_store.list_marketplace()}


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
    effective_persona = (persona or "pm").strip()
    templates = agent_templates_store.list_for_persona(effective_persona)
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
        _save_agent_state()

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
    _save_agent_state()
    _save_deleted_agents(deleted)

    return {"deleted": len(target_names), "names": sorted(target_names)}


def _plain_language_feedback(name: str, meta: dict) -> str:
    """Return a plain-language one-liner for the chat bubble.

    Used by ``GET /agents/{name}/status-feedback`` so the torichat panel
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

    The torichat panel polls this after a ``spawn_agent`` tool call so it
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

    Idempotent on repeated calls for the same name: re-registering keeps
    the original ``spawned_at`` so a long conversation does not look
    like it restarts on every turn.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    existing = agent_metadata.get(name) or {}
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
    _save_agent_state()
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
    meta["status"] = status
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["last_heartbeat_at"] = meta["completed_at"]
    total_tokens = int(tokens_in or 0) + int(tokens_out or 0)
    if total_tokens:
        meta["tokens_used"] = int(meta.get("tokens_used", 0) or 0) + total_tokens
    _save_agent_state()
    _emit_audit_event(
        "agent.completed" if status == "completed" else "agent.failed",
        {"name": name, "source": "chat", "tokens": total_tokens},
    )
    try:
        pass
    except Exception:
        pass

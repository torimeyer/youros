"""Acknowledgment bot that keeps inline agent chat responsive.

Background
----------
Claude Code subagents run via ``claude --print`` in one-shot mode.
Even with strict mailbox instructions they cannot reliably poll
``/nudges`` mid-turn: they are locked inside a long tool call, a
pytest run, or a large file write. That means when Tori types into
an agent chat from the Agents page, her message can sit for 30+
seconds before the subagent happens to check in.

The ack bot is a pragmatic UX fix. It is a separate coroutine spawned
alongside the real subagent. Every two seconds it polls ``/nudges``
for the agent it watches. The moment a new nudge lands, it posts a
short, warm acknowledgment back through ``/reply`` so Tori sees
"message received" within two seconds while the real subagent
continues its current step. When the real subagent finally posts its
own answer, the ack bot stays out of the way: it tracks the timestamp
of every nudge it has already acked and only responds to *new* ones.

The bot exits as soon as the agent's status flips to ``completed``,
``cancelled``, ``failed``, or any other terminal state, so it never
outlives the agent it serves.

This module is process-local and owns no state on disk. All state
lives in the agents router in-memory dicts. The ack bot is a thin
coroutine that calls the same helpers the HTTP layer calls, so tests
can drive it without spinning up a full ASGI transport.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Honest ack copy. Every string here is safe to ship when we have NO
# real measurement of reply latency for the agent, because nothing here
# promises a specific number of seconds or minutes. The old list had
# "On it, give me a sec." and "I will fold that in as soon as I surface."
# which either implied a one-second reply or invented a vague but still
# concrete time window. If the real agent was mid-pytest the user saw a
# promise the backend could not keep. Every candidate string here can
# stand on its own with no timing data.
_ACK_RESPONSES_NO_ESTIMATE: tuple[str, ...] = (
    "Got your message. Working on this now.",
    "Heard you. Still working on your last request.",
    "Got it. I will reply as soon as I finish this step.",
    "Thanks, I see your message. Picking it up next.",
    "Noted. I will fold this in on the next turn.",
)

# Kept for backwards compatibility with existing tests and callers that
# imported the old symbol. The tuple now holds honest copy only. Any
# time-bearing text is produced by ``build_ack_message`` with a measured
# estimate; it never comes from this list.
ACK_RESPONSES: tuple[str, ...] = _ACK_RESPONSES_NO_ESTIMATE

# How often the bot polls /nudges. Two seconds is the contract we
# quote to the user in the UI banner. Tighter would hammer the API
# and the filesystem for no real gain. Looser would break the promise.
ACK_POLL_INTERVAL_SECONDS: float = 2.0

# Terminal statuses that should stop the bot. Matches the set the
# agents router uses when deciding whether an agent row is still
# active. Keep this list loose: any non-running status is a reason
# to exit.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed",
    "completed_timeout",
    "cancelled",
    "failed",
    "terminated_stale",
    "error",
})

# Active ack-bot tasks per agent name. spawn_agent populates this so
# the cancel/complete paths can tear the bot down even if the agent
# exits out of band. Tests drive it directly to assert teardown.
_agent_ack_tasks: dict[str, asyncio.Task] = {}

# Per-agent record of the latest nudge timestamp the bot has already
# acked. We use this to:
#
# 1. Avoid re-acking the same nudge on the next poll cycle.
# 2. Distinguish "ack" replies from "real" subagent replies so the
#    UI can render them in a lighter tone. The real agent's /reply
#    posts through the HTTP router and never touches this dict.
_last_ack_ts: dict[str, str] = {}

# Per-agent set of nudge identities the bot has already acked. The
# identity is the nudge's timestamp string (or ``timestamp:message``
# when the timestamp is missing). We track this in addition to
# ``_last_ack_ts`` because the latter is fragile: any path that resets
# it (process restart with on-disk nudges still present, an empty
# string sneaking in as ``newest_ts``, a clock skew that lets a "newer"
# nudge sort below the previously-acked timestamp) would let the bot
# re-ack a nudge it already acknowledged. The screenshot bug Tori hit
# showed three identical "On it, give me a sec." replies for one user
# message, which is exactly what happens when the gate above is the
# only line of defense and it fails open. The set is the regression
# guard: a nudge id is added the moment we ack it, and every later
# cycle treats any id in the set as "already handled" no matter what
# the timestamp comparison says.
_acked_nudge_ids: dict[str, set[str]] = {}


def _nudge_id(entry: dict) -> str:
    """Return a stable identity for a nudge dict.

    Prefer the timestamp because it is the canonical key used by the
    rest of the system. Fall back to ``timestamp:message`` when the
    timestamp is missing or empty so we still have a usable key. This
    function is the single source of truth for how the ack bot
    identifies a nudge so the gate and the recording path can never
    drift apart.
    """
    ts = str(entry.get("timestamp", ""))
    if ts:
        return ts
    msg = str(entry.get("message", ""))
    return f":{msg}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Kept as a helper so tests can monkey-patch it to freeze time
    without touching the standard library clock everywhere.
    """
    return datetime.now(timezone.utc).isoformat()


def _pick_ack(index: int) -> str:
    """Return a canned ack response, rotating through the list.

    ``index`` is a monotonically increasing counter kept per agent.
    We mod it against the list length so the rotation wraps cleanly.
    Kept as a free function so tests can assert rotation without
    monkey-patching random state.
    """
    return ACK_RESPONSES[index % len(ACK_RESPONSES)]


# Below this floor (in seconds) we treat the agent as "fast enough"
# that quoting a number would feel silly and could still be wrong by
# a factor of two. A plain acknowledgement is honest and enough.
_FAST_ENOUGH_FLOOR_SECONDS: float = 10.0

# Above this cap we stop quoting a specific minute count because we do
# not have enough samples to be confident it is representative. We say
# "this may take a while" instead. Better to under-promise than invent
# a number we did not actually measure.
_LONG_TAIL_CAP_SECONDS: float = 300.0


def _round_up_seconds(seconds: float) -> int:
    """Round a positive latency up to a user-friendly whole number.

    We always round up so the number we quote is a ceiling, never a
    lower bound the user will feel cheated by. Steps match what a
    person would say out loud: nearest 5s up to 30s, nearest 10s up to
    60s, nearest 30s after that. The result is an integer number of
    seconds; the caller decides whether to phrase it in seconds or
    minutes.
    """
    if seconds <= 0:
        return 0
    if seconds <= 30:
        step = 5
    elif seconds <= 60:
        step = 10
    else:
        step = 30
    return int(((int(seconds) + step - 1) // step) * step)


def build_ack_message(
    measured_seconds: Optional[float],
    *,
    rotation_index: int = 0,
) -> str:
    """Return an ack string that is honest about expected reply time.

    The ack bot calls this on every new nudge. The contract:

    * If ``measured_seconds`` is ``None``, we have no data. Return a
      canned acknowledgement that makes no time promise.
    * If the measurement is under the fast-enough floor, return a
      canned acknowledgement that makes no time promise. The real
      reply is already coming in seconds; quoting a number is noise.
    * If the measurement is in the "usually under N seconds" band,
      return a line that quotes N, where N is rounded UP from the
      measured value. The rounding is generous on purpose: a promise
      that is slightly too long keeps.
    * If the measurement is above the long-tail cap, return a line
      that says the wait may be long, without inventing a number.

    No path here fabricates a time. Every number the user reads maps
    back one-to-one to a value the caller passed in after looking at
    real reply-latency telemetry.
    """
    base = _ACK_RESPONSES_NO_ESTIMATE[rotation_index % len(_ACK_RESPONSES_NO_ESTIMATE)]
    if measured_seconds is None:
        return base
    if measured_seconds <= 0:
        return base
    if measured_seconds < _FAST_ENOUGH_FLOOR_SECONDS:
        return base
    if measured_seconds >= _LONG_TAIL_CAP_SECONDS:
        return (
            "Got your message. This one may take a while. "
            "I will reply as soon as I can."
        )
    rounded = _round_up_seconds(measured_seconds)
    if rounded >= 90:
        minutes = (rounded + 59) // 60
        unit = "minute" if minutes == 1 else "minutes"
        return (
            f"Got your message. Replies usually come within "
            f"about {minutes} {unit}."
        )
    return (
        f"Got your message. Replies usually come within "
        f"about {rounded} seconds."
    )


# Rolling store of (nudge_timestamp, reply_timestamp) pairs per agent,
# measured in wall-clock seconds. Populated by ``record_reply_latency``
# which is called from the agent /reply router whenever a ``kind=real``
# reply lands with a matching ``in_reply_to``. The list is bounded to
# the most recent ``_MAX_LATENCY_SAMPLES`` entries so a long-lived
# agent does not grow this unbounded. The bot reads the aggregate
# through ``measured_p95_seconds`` to decide whether to quote a number
# in the ack text. No other code path should read this directly.
_reply_latencies_seconds: dict[str, list[float]] = {}
_MAX_LATENCY_SAMPLES: int = 50

# Minimum number of samples before we trust the aggregate enough to
# quote a specific number. Under this floor we fall through to the
# no-estimate acknowledgement. Two samples is enough to catch a wild
# outlier; one sample is not. Tunable.
_MIN_SAMPLES_FOR_ESTIMATE: int = 2


def record_reply_latency(name: str, seconds: float) -> None:
    """Append a measured nudge-to-reply latency for ``name``.

    Silently ignores non-positive values (clock skew, same-second
    replies) and anything larger than the long-tail cap (the agent
    was probably off doing something unrelated to the last nudge).
    """
    if seconds <= 0:
        return
    if seconds > _LONG_TAIL_CAP_SECONDS * 4:
        # Treat an hour-plus gap as unrelated to the triggering nudge.
        return
    bucket = _reply_latencies_seconds.setdefault(name, [])
    bucket.append(float(seconds))
    if len(bucket) > _MAX_LATENCY_SAMPLES:
        del bucket[: len(bucket) - _MAX_LATENCY_SAMPLES]


def measured_p95_seconds(name: str) -> Optional[float]:
    """Return the agent's measured p95 reply latency, or ``None``.

    Returns ``None`` when we have fewer than ``_MIN_SAMPLES_FOR_ESTIMATE``
    samples. ``build_ack_message`` treats ``None`` as "no data, do not
    quote a number" so the ack stays honest until we have proof.
    """
    samples = _reply_latencies_seconds.get(name) or []
    if len(samples) < _MIN_SAMPLES_FOR_ESTIMATE:
        return None
    ordered = sorted(samples)
    # 95th percentile via nearest-rank. For small sample counts this
    # is conservative (biased toward the slowest seen), which is what
    # we want for a time we are about to quote to the user.
    rank = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[rank]


def _infer_latency_from_reply(
    replies: list[dict],
    new_reply: dict,
) -> Optional[float]:
    """Return the seconds between ``new_reply.in_reply_to`` and its
    own timestamp, or ``None`` if either field is missing or unparseable.

    Used by the /reply handler to push a sample into the rolling store
    the instant a real subagent reply lands. Kept as a helper so the
    router stays thin and the parsing rules live next to the store.
    """
    if not isinstance(new_reply, dict):
        return None
    if new_reply.get("kind") != "real":
        return None
    ts_raw = str(new_reply.get("timestamp") or "")
    nudge_ts_raw = str(new_reply.get("in_reply_to") or "")
    if not ts_raw or not nudge_ts_raw:
        return None
    try:
        reply_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        nudge_dt = datetime.fromisoformat(nudge_ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (reply_dt - nudge_dt).total_seconds()
    if delta <= 0:
        return None
    return delta


def reset_latency_store() -> None:
    """Clear the rolling latency store. Used by tests to get a fresh view."""
    _reply_latencies_seconds.clear()


def _is_terminal(meta: Optional[dict]) -> bool:
    """Return True if the agent's metadata says it has stopped.

    A missing record is treated as terminal because it means the
    agent was never registered or has been reaped. Either way, the
    bot has nothing to serve and should exit.
    """
    if not meta:
        return True
    status = str(meta.get("status", "")).lower()
    return status in TERMINAL_STATUSES


async def _ack_once(
    name: str,
    counter: "itertools.count[int]",
) -> bool:
    """Check for new nudges and post one ack if any are found.

    Returns True when the agent is still running and the bot should
    keep looping. Returns False when the agent has reached a terminal
    state and the bot should exit.

    The router imports are done lazily because ``chat_ack_bot`` is
    imported at module load time by the router and a top-level import
    back into the router would create a cycle.
    """
    from routers import agents as agents_router

    meta = agents_router.agent_metadata.get(name)
    if _is_terminal(meta):
        return False

    # Snapshot both session and on-disk nudges. We look at both so the
    # bot works in tests (which use the in-memory nudge_history) and
    # in production (where POST /nudge writes to disk before appending
    # to nudge_history).
    session_nudges = list(agents_router.nudge_history.get(name, []))
    try:
        file_nudges = await agents_router.ostk.list_nudges(name)
    except Exception as exc:
        # Filesystem read errors are not fatal. Log and keep going
        # with just the session view so a flaky disk does not kill
        # the ack loop.
        logger.warning("ack bot could not read nudges for %s: %s", name, exc)
        file_nudges = []

    latest_ts = _last_ack_ts.get(name, "")
    acked_ids = _acked_nudge_ids.setdefault(name, set())
    # Find the newest nudge that is strictly newer than the last one
    # we acked AND has not already been acked by id. ISO-8601 UTC
    # timestamps compare correctly with string comparison, so no
    # parsing is required. The id check is the belt to the timestamp
    # check's suspenders: even if ``latest_ts`` was reset (process
    # restart, clock skew, an empty newest_ts in a prior cycle) the
    # set stops a nudge being acked twice within the lifetime of the
    # bot.
    candidates: list[dict] = []
    for entry in itertools.chain(session_nudges, file_nudges):
        if not isinstance(entry, dict):
            continue
        ts = str(entry.get("timestamp", ""))
        nid = _nudge_id(entry)
        if nid in acked_ids:
            continue
        if ts and ts > latest_ts:
            candidates.append(entry)

    if not candidates:
        return True

    # Only ack the newest nudge. If several nudges arrived in the
    # same window, one warm reply covers them. Acking each one would
    # spam the chat. Mark every candidate as acked so a later cycle
    # never re-acks a nudge that arrived in this same batch but lost
    # the max() race.
    newest = max(candidates, key=lambda e: str(e.get("timestamp", "")))
    newest_ts = str(newest.get("timestamp", ""))

    # Build the ack text. When we have real measurements for this
    # agent we may quote an honest "usually under Ns" line. When we
    # do not, the message falls back to one of the canned strings
    # that makes no time promise. Either path is grounded in telemetry
    # (or the lack of it); neither invents a number.
    rotation_index = next(counter)
    p95 = measured_p95_seconds(name)
    message = build_ack_message(p95, rotation_index=rotation_index)

    # Go through the same HTTP-layer helper the real router uses so
    # the reply lands in nudge_replies, the on-disk store, and wakes
    # every long-poll /nudges waiter. The ack tag keeps the UI able
    # to render acks in a lighter tone.
    try:
        reply_data = await agents_router.ostk.append_nudge_reply(
            name,
            message,
            in_reply_to=newest_ts or None,
            kind="ack",
        )
    except Exception as exc:
        logger.warning("ack bot could not persist reply for %s: %s", name, exc)
        # Fall back to a session-only reply. Better to ack in memory
        # than drop the user's feedback promise on the floor.
        reply_data = {
            "message": message,
            "timestamp": _now_iso(),
            "in_reply_to": newest_ts or None,
        }

    # Tag as an ack so the UI can render it in a lighter tone. The
    # real subagent's /reply path never sets this flag, so anything
    # without ``kind == "ack"`` is a real answer.
    reply_data["kind"] = "ack"
    reply_data["source"] = "ack_bot"

    agents_router.nudge_replies.setdefault(name, []).append(reply_data)
    agents_router._wake_nudge_waiters(name)

    # Record so we do not re-ack on the next cycle and so the real
    # agent's eventual answer (which has a newer timestamp than this
    # nudge) is never shadow-acked. Track every candidate id, not
    # just the newest, so a nudge that arrived in this same batch but
    # lost the max() race cannot be re-acked on the next poll.
    _last_ack_ts[name] = newest_ts or _now_iso()
    for entry in candidates:
        acked_ids.add(_nudge_id(entry))
    return True


async def _hydrate_acked_ids_from_disk(name: str) -> None:
    """Seed ``_acked_nudge_ids`` from the on-disk replies directory.

    The in-memory acked-id set is process-local. Every backend restart
    wipes it. When the ack bot then runs ``start_for_running_agents``
    and finds an agent whose on-disk nudges include a message that was
    already acked before the restart, the bot has no memory of the
    earlier ack and writes another. The screenshot Tori hit showed four
    "On it, give me a sec." replies for one user nudge, all hours apart,
    each one posted on a different backend boot. The cure is to look at
    the on-disk replies the moment the bot starts up for an agent: any
    reply tagged ``kind=ack`` whose ``in_reply_to`` matches a nudge we
    would otherwise ack is proof the previous boot already handled it.
    We add that nudge id to the set so the first poll cycle treats it
    as done and never writes a second copy.

    Failures are swallowed: a broken replies dir should never stop the
    bot from coming up. Worst case we fall back to the old behavior.
    """
    from routers import agents as agents_router

    try:
        prior_replies = await agents_router.ostk.list_nudge_replies(name)
    except Exception as exc:
        logger.debug("ack bot hydrate failed for %s: %s", name, exc)
        return

    acked = _acked_nudge_ids.setdefault(name, set())
    latest_ts = _last_ack_ts.get(name, "")
    for reply in prior_replies:
        if not isinstance(reply, dict):
            continue
        # Only ack-bot replies prove we already covered the referenced
        # nudge. A real subagent reply is a different shape of message
        # and should not gate the ack bot.
        if reply.get("kind") != "ack":
            continue
        in_reply_to = str(reply.get("in_reply_to") or "")
        if in_reply_to:
            acked.add(in_reply_to)
            if in_reply_to > latest_ts:
                latest_ts = in_reply_to
    if latest_ts:
        _last_ack_ts[name] = latest_ts


async def run_for(name: str) -> None:
    """Run the ack loop for a single agent until it reaches terminal.

    The loop polls every ``ACK_POLL_INTERVAL_SECONDS`` seconds. It
    exits cleanly on ``asyncio.CancelledError`` (shutdown from the
    router) and on any terminal status seen in ``agent_metadata``.
    Exceptions from a single cycle are logged and swallowed so a
    transient error never takes down the bot.
    """
    counter = itertools.count()
    # Hydrate the per-agent acked-id set from the on-disk replies the
    # moment the bot starts. This closes the cross-restart replay
    # window: the in-memory dedupe alone is erased by every restart,
    # and a stale on-disk nudge would otherwise get re-acked.
    await _hydrate_acked_ids_from_disk(name)
    try:
        while True:
            try:
                still_running = await _ack_once(name, counter)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ack bot cycle failed for %s: %s", name, exc)
                still_running = True

            if not still_running:
                break
            await asyncio.sleep(ACK_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # Normal teardown path. Router cancels the task when the
        # agent completes or is cancelled.
        return
    finally:
        _last_ack_ts.pop(name, None)
        _acked_nudge_ids.pop(name, None)


def start(name: str) -> asyncio.Task:
    """Spawn an ack-bot task for ``name`` and track it for teardown.

    If an ack task is already running for this agent the existing
    task is returned unchanged. Re-spawn is a caller error we handle
    silently rather than racing two bots against each other. This
    idempotency is load-bearing for the startup backfill path, which
    blindly calls ``start`` on every running agent without tracking
    whether spawn_agent already launched a bot earlier in the session.
    """
    existing = _agent_ack_tasks.get(name)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(run_for(name), name=f"ack_bot:{name}")
    _agent_ack_tasks[name] = task
    # Clean up the slot when the task ends so a later re-spawn
    # works. add_done_callback runs on the event loop thread, so the
    # pop is safe without a lock.
    task.add_done_callback(lambda _t, _n=name: _agent_ack_tasks.pop(_n, None))
    return task


def start_for_running_agents() -> dict[str, list[str]]:
    """Backfill ack bots for every agent currently marked as running.

    Two paths call this function:

    * The FastAPI startup hook, so an uvicorn restart picks up every
      subagent that is still alive from a previous session and begins
      acking its inline chat within two seconds.
    * The admin endpoint ``POST /api/agents/ack-bot/backfill``, which
      lets the running backend turn the feature on for already-running
      agents without a restart.

    The function is idempotent: calling ``start`` for an agent that
    already has a bot is a cheap no-op, and the returned dict reports
    which agents actually needed a fresh bot started versus which were
    already covered. We only backfill agents whose source marks them
    as a registered Claude Code subagent because the ack bot has no
    job for long-lived process agents that manage their own mailbox.
    """
    # Lazy import avoids a cycle: agents_router imports chat_ack_bot at
    # module load, so the top-level import would not resolve.
    from routers import agents as agents_router

    started: list[str] = []
    already_active: list[str] = []
    for name, meta in list(agents_router.agent_metadata.items()):
        if not isinstance(meta, dict):
            continue
        if str(meta.get("status", "")).lower() != "running":
            continue
        source = str(meta.get("source", "")).lower()
        # ``claude-code`` is the source stamped by the /register path
        # Tori's UI uses for inline chat. ``subagent`` is the source the
        # fix-ack-bot-activation runner uses for its own registration.
        # Both are user-spawned registered agents that need an ack bot.
        if source not in {"claude-code", "subagent"}:
            continue
        if is_active(name):
            already_active.append(name)
            continue
        try:
            start(name)
        except RuntimeError:
            # create_task requires a running loop. The startup hook is
            # async so this always has one, but admin callers on a
            # stopped loop would land here. Skip rather than crash.
            logger.warning("ack bot backfill could not start for %s: no loop", name)
            continue
        started.append(name)
    return {"started": started, "already_active": already_active}


def stop(name: str) -> None:
    """Cancel the ack-bot task for ``name`` if one is running.

    Safe to call repeatedly. Used by the agent complete/cancel paths
    to make sure the bot does not outlive its agent.
    """
    task = _agent_ack_tasks.pop(name, None)
    if task is not None and not task.done():
        task.cancel()


def is_active(name: str) -> bool:
    """Return True if an ack bot is currently running for ``name``.

    The banner message reads this to decide whether to promise the
    two-second ack window.
    """
    task = _agent_ack_tasks.get(name)
    return task is not None and not task.done()

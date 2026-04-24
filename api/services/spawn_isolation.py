"""Heuristic for defaulting claude-code subagent spawns to a git worktree.

Parallel "spawn burst" bursts race on ``git add``/``git commit`` in the
main worktree and contaminate each other's diffs (see memory entry
``feedback_spawn_burst_commit_contamination.md``). The Task tool supports
``isolation: "worktree"`` which puts each subagent in its own git
worktree, so those commits stay isolated.

Callers rarely remember to set this. This module picks a sensible
default: if the spawn's description or prompt looks like a code-edit
task (verbs like ``edit``, ``fix``, ``implement``, ``build``, ``add``,
``refactor``, etc.) we default to ``"worktree"``. Research-only verbs
(``explore``, ``read``, ``review``, ``audit``, ``report``, etc.) stay in
the main worktree so their transcript and commits surface where the user
expects them.

The caller can always opt out by setting ``isolation`` to any non-empty
string. ``"none"`` is an explicit sentinel that forces the main
worktree, useful for tests or for agents that intentionally share the
tree with the parent.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Verbs that strongly suggest the subagent will edit, create, or commit
# code. Order is not significant; match is substring-word.
CODE_EDIT_VERBS = frozenset(
    {
        "edit",
        "edits",
        "editing",
        "write",
        "writes",
        "writing",
        "commit",
        "commits",
        "committing",
        "fix",
        "fixes",
        "fixing",
        "refactor",
        "refactors",
        "refactoring",
        "implement",
        "implements",
        "implementing",
        "build",
        "builds",
        "building",
        "add",
        "adds",
        "adding",
        "create",
        "creates",
        "creating",
        "saa",  # torios verb for "spawn agent to do it"
        "diagnose",  # torios verb: find root cause, fix, regression tests
        "patch",
        "patches",
        "patching",
        "update",
        "updates",
        "updating",
        "migrate",
        "migrates",
        "migrating",
        "rename",
        "renames",
        "renaming",
        "delete",
        "deletes",
        "deleting",
        "remove",
        "removes",
        "removing",
    }
)

# Verbs that suggest read-only work. When ONLY these verbs appear we keep
# the subagent in the main worktree. If a code-edit verb also appears,
# the edit verb wins.
RESEARCH_VERBS = frozenset(
    {
        "explore",
        "explores",
        "exploring",
        "read",
        "reads",
        "reading",
        "search",
        "searches",
        "searching",
        "review",
        "reviews",
        "reviewing",
        "audit",
        "audits",
        "auditing",
        "report",
        "reports",
        "reporting",
        "investigate",
        "investigates",
        "investigating",
        "analyze",
        "analyzes",
        "analyzing",
        "inspect",
        "inspects",
        "inspecting",
        "summarize",
        "summarizes",
        "summarizing",
        "describe",
        "describes",
        "describing",
    }
)

# Sentinel for explicit opt-out. Callers who really want the main
# worktree (tests, shared-tree agents) pass this instead of None.
ISOLATION_NONE = "none"
ISOLATION_WORKTREE = "worktree"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_-]*")


def _tokenize(text: str) -> set[str]:
    """Return a lowercased set of word tokens for matching."""
    if not text:
        return set()
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def decide_isolation(
    *,
    description: Optional[str] = None,
    prompt: Optional[str] = None,
    explicit: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Pick the isolation level for a claude-code subagent spawn.

    Rules:
      1. If ``explicit`` is any non-empty string (including ``"none"``),
         return it unchanged. Explicit caller choice always wins.
      2. If the description or prompt contains any code-edit verb, return
         ``"worktree"``.
      3. Otherwise return ``"none"`` (main worktree).

    The decision is logged at INFO so operators can audit false
    positives and negatives after the fact.
    """
    if explicit:
        explicit_norm = explicit.strip().lower()
        logger.info(
            "spawn.isolation.explicit agent=%s isolation=%s",
            agent_name or "?",
            explicit_norm,
        )
        return explicit_norm

    tokens = _tokenize(description or "") | _tokenize(prompt or "")
    edit_hits = tokens & CODE_EDIT_VERBS
    research_hits = tokens & RESEARCH_VERBS

    if edit_hits:
        decision = ISOLATION_WORKTREE
        reason = f"edit_verbs={sorted(edit_hits)[:3]}"
    elif research_hits:
        decision = ISOLATION_NONE
        reason = f"research_verbs={sorted(research_hits)[:3]}"
    else:
        # No signal either way. Default to the main worktree so we do
        # not pay the fork-a-worktree cost for pure chat / status pings.
        decision = ISOLATION_NONE
        reason = "no_verb_match"

    logger.info(
        "spawn.isolation.decided agent=%s isolation=%s reason=%s",
        agent_name or "?",
        decision,
        reason,
    )
    return decision


# ---------------------------------------------------------------------------
# Spawn lock registry
# ---------------------------------------------------------------------------
#
# Parallel REST spawns can pick up edit work on the same file paths and
# clobber each other's commits even when worktree isolation succeeds
# (two spawns both set to "worktree" still race when they later merge
# back or when a caller declares two agents that will each touch
# app/src/pages/Files.tsx). The locks registry is a process-wide guard:
# before a spawn subprocess runs, the caller reserves each declared
# glob; a contending spawn is rejected with 409 until the holder
# releases. Release happens in the spawn's completion path (drain_stderr
# callback) and on every error branch via ``release_spawn_locks``.
#
# The in-process map is authoritative. ``ostk lock create`` is issued as
# a best-effort side effect so operators can inspect orphan locks via
# ``ostk lock list`` and so out-of-process tooling sees the reservation.
# If the subprocess call fails, the in-process reservation still holds.

# Sentinel glob that means "I promise not to edit anything". Research
# verbs (explore, read, review, audit, report, ...) may pass this to
# satisfy the required-locks check without actually reserving any path.
LOCKS_WILDCARD = "*"

# Process-wide registry mapping sanitized glob -> (spawn_id, raw_glob).
# ``spawn_id`` is the agent name passed to /api/agents/spawn. The raw
# glob is retained so the 409 error body can surface the exact string
# the contending caller passed (useful for debugging overlapping globs
# that look different on the wire but normalize to the same key).
_spawn_lock_holders: dict[str, Tuple[str, str]] = {}

# Protects ``_spawn_lock_holders`` across threads. The FastAPI test
# client runs the handler on the default loop but the Uvicorn worker
# runs it on a threadpool, so an asyncio.Lock is not enough.
_spawn_lock_mutex = threading.Lock()


_GLOB_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._*/-]+")


def _sanitize_glob(glob: str) -> str:
    """Normalize a glob for use as a lock key and ostk lock name.

    Strips leading/trailing whitespace and slashes, collapses
    non-alphanumeric runs to ``-``. Empty string after normalization
    is returned as-is so the caller can reject it upstream.
    """
    s = (glob or "").strip().strip("/")
    return _GLOB_SANITIZE_RE.sub("-", s)


def validate_locks_for_spawn(
    *,
    isolation: str,
    locks: Optional[List[str]],
) -> Tuple[bool, str]:
    """Return ``(ok, error_message)`` for a spawn's ``locks`` payload.

    Rules:
      * Edit-capable spawns (``isolation == "worktree"``) MUST pass a
        non-empty list. Globs are accepted as-is.
      * Read-only spawns (``isolation == "none"``) may pass ``None``,
        ``[]``, or ``["*"]`` -- all mean "I won't edit anything".

    The error message is plain-language and names the ``locks`` field
    so the caller knows exactly what to add. See the
    ``test_spawn_locks`` suite for the exact assertions.
    """
    is_edit = (isolation or "").lower() == ISOLATION_WORKTREE
    if not is_edit:
        return True, ""
    if not locks:
        return False, (
            "This spawn will edit code so it must declare which paths it "
            "will touch. Pass a non-empty `locks` array in the request body, "
            "e.g. `\"locks\": [\"app/src/pages/Files.tsx\"]`. Read-only "
            "spawns may pass `\"locks\": [\"*\"]` instead."
        )
    # Reject the wildcard for edit spawns: "*" is the read-only opt-out,
    # not a license to edit everything.
    if any((str(g).strip() == LOCKS_WILDCARD) for g in locks):
        return False, (
            "An edit-capable spawn cannot use `locks: [\"*\"]`. The "
            "wildcard is the read-only opt-out. Declare the actual files "
            "or globs this spawn will touch."
        )
    return True, ""


def acquire_spawn_locks(
    *,
    spawn_id: str,
    locks: Optional[Iterable[str]],
) -> Tuple[bool, List[str], List[Tuple[str, str, str]]]:
    """Reserve every glob in ``locks`` for ``spawn_id``.

    Returns ``(ok, acquired_keys, contenders)`` where ``contenders`` is
    a list of ``(raw_glob, holder_spawn_id, holder_raw_glob)`` tuples
    describing every clash. On clash, NO keys are reserved; any keys
    acquired before the first clash are rolled back atomically.

    The wildcard glob ``"*"`` is a no-op. Callers pass it to signal a
    read-only spawn and we record nothing for it so two research
    spawns never conflict.
    """
    acquired: List[str] = []
    contenders: List[Tuple[str, str, str]] = []
    if not locks:
        return True, acquired, contenders
    with _spawn_lock_mutex:
        for raw in locks:
            g = str(raw or "").strip()
            if not g or g == LOCKS_WILDCARD:
                continue
            key = _sanitize_glob(g)
            if not key:
                continue
            holder = _spawn_lock_holders.get(key)
            if holder is not None and holder[0] != spawn_id:
                contenders.append((g, holder[0], holder[1]))
                continue
            _spawn_lock_holders[key] = (spawn_id, g)
            acquired.append(key)
        if contenders:
            # Roll back: release anything we just acquired so a
            # partial-clash does not leak reservations.
            for key in acquired:
                holder = _spawn_lock_holders.get(key)
                if holder is not None and holder[0] == spawn_id:
                    _spawn_lock_holders.pop(key, None)
            return False, [], contenders
    # Best-effort: also create ostk locks so operators can see them via
    # `ostk lock list`. Failures are ignored; the in-process registry
    # is the source of truth.
    try:
        asyncio.get_event_loop()
        _schedule_ostk_lock_create(spawn_id, acquired)
    except RuntimeError:
        # Called outside an event loop (e.g. unit test). Skip the
        # side effect; in-process registry is sufficient.
        pass
    return True, acquired, []


def release_spawn_locks(
    *,
    spawn_id: str,
    keys: Optional[Iterable[str]] = None,
) -> List[str]:
    """Release every lock held by ``spawn_id``.

    When ``keys`` is provided, only those keys are released (and only
    when the holder matches ``spawn_id``). When ``keys`` is None, every
    key whose holder is ``spawn_id`` is released. Returns the list of
    keys that were actually released so callers can log.
    """
    released: List[str] = []
    with _spawn_lock_mutex:
        if keys is None:
            victims = [
                k for k, v in _spawn_lock_holders.items() if v[0] == spawn_id
            ]
        else:
            victims = list(keys)
        for key in victims:
            holder = _spawn_lock_holders.get(key)
            if holder is not None and holder[0] == spawn_id:
                _spawn_lock_holders.pop(key, None)
                released.append(key)
    if released:
        try:
            asyncio.get_event_loop()
            _schedule_ostk_lock_release(spawn_id, released)
        except RuntimeError:
            pass
    return released


def _ostk_lock_name(spawn_id: str, key: str) -> str:
    """Build the ``ostk lock`` name for a (spawn_id, glob-key) pair."""
    return f"spawn-{spawn_id}-path-{key}"


def _schedule_ostk_lock_create(spawn_id: str, keys: List[str]) -> None:
    """Best-effort: issue ``ostk lock create`` for each key in a bg task."""

    async def _do() -> None:
        try:
            from services import ostk as _ostk
        except Exception:
            return
        for key in keys:
            name = _ostk_lock_name(spawn_id, key)
            try:
                await _ostk._run("lock", "create", "--name", name, timeout=3)
            except Exception as exc:
                logger.debug(
                    "spawn.lock.ostk_create_failed name=%s err=%s",
                    name, exc,
                )

    try:
        asyncio.ensure_future(_do())
    except Exception:
        pass


def _schedule_ostk_lock_release(spawn_id: str, keys: List[str]) -> None:
    """Best-effort: issue ``ostk lock release`` for each key in a bg task."""

    async def _do() -> None:
        try:
            from services import ostk as _ostk
        except Exception:
            return
        for key in keys:
            name = _ostk_lock_name(spawn_id, key)
            try:
                await _ostk._run("lock", "release", "--name", name, timeout=3)
            except Exception as exc:
                logger.debug(
                    "spawn.lock.ostk_release_failed name=%s err=%s",
                    name, exc,
                )

    try:
        asyncio.ensure_future(_do())
    except Exception:
        pass


def _reset_spawn_lock_registry_for_tests() -> None:
    """Test-only helper: clear the in-process registry between tests."""
    with _spawn_lock_mutex:
        _spawn_lock_holders.clear()

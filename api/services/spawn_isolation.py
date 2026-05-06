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
import time
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

# Process-wide registry mapping sanitized glob -> (spawn_id, raw_glob, acquired_epoch).
# ``spawn_id`` is the agent name passed to /api/agents/spawn. The raw
# glob is retained so the 409 error body can surface the exact string
# the contending caller passed (useful for debugging overlapping globs
# that look different on the wire but normalize to the same key).
# ``acquired_epoch`` is time.time() at acquisition, used by the TTL sweep.
_spawn_lock_holders: dict[str, Tuple[str, str, float]] = {}

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
            _spawn_lock_holders[key] = (spawn_id, g, time.time())
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



async def _has_unmerged_commits(cwd: str, branch: str) -> bool:
    """Return True if branch has commits ahead of main (safe default: True on error).

    Returns False when the branch does not exist (no commits can be ahead of
    main for a non-existent ref).  This allows create_worktree() to proceed
    normally on first spawn without tripping the conservative error path that
    fires when rev-list returns rc=128 (bad revision) for an unknown branch.
    """
    try:
        # If the branch doesn't exist, there are zero unmerged commits.
        rc_exists, _, _ = await _run_git(
            "rev-parse", "--verify", branch,
            cwd=cwd, timeout=5.0,
        )
        if rc_exists != 0:
            return False

        rc, out, _ = await _run_git(
            "rev-list", "--count", f"main..{branch}",
            cwd=cwd, timeout=5.0,
        )
        if rc != 0:
            return True  # conservative: assume unmerged if check fails
        stripped = out.strip()
        if not stripped:
            # rc=0 + empty stdout never occurs with real git; treat as 0.
            return False
        return int(stripped) > 0
    except Exception:
        return True  # conservative: assume unmerged on any error


async def _unmerged_onelines(cwd: str, branch: str, max_commits: int = 3) -> str:
    """Return a short log of commits ahead of main, for human-readable log messages."""
    try:
        rc, out, _ = await _run_git(
            "log", "--oneline", f"main..{branch}", f"-{max_commits}",
            cwd=cwd, timeout=5.0,
        )
        if rc != 0 or not out.strip():
            return "(could not fetch commit list)"
        return out.decode(errors="replace").strip()
    except Exception:
        return "(error fetching commits)"


async def _run_git(
    *args: str,
    cwd: str,
    timeout: float = 15.0,
) -> tuple[int, bytes, bytes]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, b"", b"timeout"
    return proc.returncode or 0, out, err


async def create_worktree(
    *,
    project_root,
    agent_name: str,
    branch: str,
    wt_path,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    """Create a real git worktree for an agent at wt_path on branch.

    Handles the re-spawn case: if the branch or worktree already exists from a
    prior run, removes them before creating a fresh one. Returns (ok, error).

    This is the authoritative worktree-creation path. Callers must not call
    git worktree add directly so the pre-clean logic is always applied.
    """
    from pathlib import Path as _P

    cwd = str(project_root)
    wt = _P(wt_path)

    # Safety: refuse if branch has unmerged commits, regardless of whether
    # the worktree directory exists. The directory may have been removed already
    # (by a prior partial cleanup, manual rm, or the bash reaper racing with a
    # commit) while the branch still holds commits not on main. The unconditional
    # `git branch -D` at step 2 below would orphan those commits without this
    # gate. This is the fix for the "second deletion path" (see CLAUDE.md
    # "Worktree hygiene": unique worktrees are always parked, never deleted).
    if await _has_unmerged_commits(cwd, branch):
        _onelines = await _unmerged_onelines(cwd, branch)
        logger.warning(
            "spawn.worktree.unmerged_safety name=%s branch=%s -- refusing pre-clean; "
            "cherry-pick before re-spawning. unmerged commits:\n%s",
            agent_name, branch, _onelines,
        )
        return False, f"safety: branch {branch} has unmerged commits; cherry-pick before re-spawning"

    # 1. Remove any existing registered worktree at this path.
    #    Worktrees are created with --lock, so a plain `git worktree remove
    #    --force` will be refused ("locked") unless we unlock first.
    if wt.exists():
        # Unlock (no-op if not locked; never fatal).
        await _run_git("worktree", "unlock", str(wt), cwd=cwd, timeout=5.0)
        # Now remove the worktree registration and its directory.
        rc, _, err = await _run_git(
            "worktree", "remove", "--force", str(wt),
            cwd=cwd, timeout=timeout,
        )
        if rc != 0:
            logger.debug(
                "spawn.worktree.pre_remove_nonzero path=%s rc=%s err=%s",
                wt, rc, err.decode(errors="replace")[:120],
            )
        # If the directory survived (unregistered orphan), remove it from disk.
        if wt.exists():
            import shutil as _shutil
            try:
                _shutil.rmtree(str(wt))
            except Exception as exc:
                logger.warning("spawn.worktree.orphan_rmtree_failed path=%s err=%s", wt, exc)
                return False, f"orphan dir {wt} could not be removed: {exc}"

    # 2. Delete the branch if it still exists.
    rc, _, _ = await _run_git(
        "branch", "-D", branch,
        cwd=cwd, timeout=5.0,
    )
    # rc != 0 just means the branch didn't exist; that's fine.

    # 3. Create the worktree on a fresh branch pinned to main.
    wt.parent.mkdir(parents=True, exist_ok=True)
    rc, out, err = await _run_git(
        "worktree", "add", "--lock",
        str(wt), "-b", branch, "main",
        cwd=cwd, timeout=timeout,
    )
    if rc != 0:
        # Clean up any partial directory left by git.
        if wt.exists():
            import shutil as _shutil
            try:
                _shutil.rmtree(str(wt))
            except Exception:
                pass
        err_msg = err.decode(errors="replace")[:200]
        logger.warning(
            "spawn.worktree.add_failed name=%s rc=%s err=%s",
            agent_name, rc, err_msg,
        )
        return False, f"git worktree add exited {rc}: {err_msg}"

    # Ensure the new worktree is never sparse. git worktree add can inherit
    # sparse-checkout state from system or global git config even when the
    # local repo has no sparse config. Disabling it is a no-op when sparse
    # mode is inactive and guarantees a full source tree when it is.
    await _run_git("sparse-checkout", "disable", cwd=str(wt), timeout=5.0)

    logger.info(
        "spawn.worktree.created name=%s path=%s branch=%s",
        agent_name, wt, branch,
    )
    return True, ""


async def remove_worktree(
    *,
    project_root,
    wt_path,
    branch: str,
    timeout: float = 10.0,
) -> bool:
    """Remove a git worktree and delete its branch.

    Called from the agent completion path so the next re-spawn of the same
    agent name gets a clean slate. Never raises; returns True on full success.
    """
    from pathlib import Path as _P

    cwd = str(project_root)
    wt = _P(wt_path)
    ok = True

    # Safety: refuse to delete a worktree that has unmerged commits.
    if await _has_unmerged_commits(cwd, branch):
        _onelines = await _unmerged_onelines(cwd, branch)
        logger.warning(
            "spawn.worktree.remove_unmerged_safety branch=%s -- refusing removal; "
            "cherry-pick before removing. unmerged commits:\n%s",
            branch, _onelines,
        )
        return False

    # Safety: refuse to delete a worktree that has uncommitted changes (staged
    # or unstaged). Agents that write files but delegate committing to the parent
    # (e.g. a "ready for parent to commit" pattern) would lose all their work
    # silently without this guard. The worktree-reaper.sh shell script performs
    # the same wt_dirty check; mirror it here so the Python cleanup path is
    # equally safe. Only meaningful when the directory exists.
    if wt.exists():
        rc_unstaged, _, _ = await _run_git(
            "diff", "--quiet",
            cwd=str(wt), timeout=5.0,
        )
        rc_staged, _, _ = await _run_git(
            "diff", "--cached", "--quiet",
            cwd=str(wt), timeout=5.0,
        )
        if rc_unstaged != 0 or rc_staged != 0:
            logger.warning(
                "spawn.worktree.dirty_safety branch=%s -- refusing removal (uncommitted changes)",
                branch,
            )
            return False

    if wt.exists() or True:  # attempt even if dir is gone (unregister stale entry)
        # Unlock first; worktrees are created with --lock so a plain remove
        # --force will be refused without the prior unlock.
        await _run_git("worktree", "unlock", str(wt), cwd=cwd, timeout=5.0)
        rc, _, err = await _run_git(
            "worktree", "remove", "--force", str(wt),
            cwd=cwd, timeout=timeout,
        )
        if rc != 0:
            logger.warning(
                "spawn.worktree.remove_failed path=%s rc=%s err=%s",
                wt, rc, err.decode(errors="replace")[:120],
            )
            ok = False
        # Ensure directory is gone even if git didn't clean it.
        if wt.exists():
            import shutil as _shutil
            try:
                _shutil.rmtree(str(wt))
            except Exception as exc:
                logger.warning("spawn.worktree.rmtree_failed path=%s err=%s", wt, exc)
                ok = False

    # Delete the branch.
    rc, _, _ = await _run_git("branch", "-D", branch, cwd=cwd, timeout=5.0)
    if rc != 0:
        logger.debug("spawn.worktree.branch_delete_nonzero branch=%s rc=%s", branch, rc)
        # Not fatal; branch may have already been deleted by the agent itself.

    return ok


async def sync_claude_dir_to_worktree(src_claude_dir, dst_claude_dir, timeout=10.0):
    """Sync .claude/ (hooks + lib) into a new worktree via rsync + symlink.

    L2.3 (→902): git worktree add does not copy untracked dirs, so without
    this every "isolated" worktree runs main's hooks by reference. Hook
    file mutations race across sessions. rsync the dir at fork time.

    →971: hooks/ is now a live symlink to the parent repo's hooks/ instead
    of a rsynced copy. This means hook edits on main land immediately in
    every worktree without a re-sync, eliminating the stale-hook failure
    mode where a worktree spawned after a hook fix still runs the old file.

    Excludes nested worktrees/ (infinite recursion) and session-history/
    (bloat). Failure is logged WARN but never raises; the worktree remains
    functional with parent hooks.

    Returns True on success, False on any failure. Never raises.
    """
    import os
    import shutil as _shutil
    from pathlib import Path as _P

    try:
        src = _P(src_claude_dir)
        dst = _P(dst_claude_dir)
        if not src.exists():
            logger.warning(
                "spawn.hook_sync.src_missing src=%s", src,
            )
            return False
        dst.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "rsync", "-a",
            "--exclude=worktrees/",
            "--exclude=session-history/",
            "--exclude=hooks/",
            f"{src}/", f"{dst}/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            logger.warning(
                "spawn.hook_sync.timeout src=%s dst=%s timeout=%s",
                src, dst, timeout,
            )
            return False
        if proc.returncode != 0:
            logger.warning(
                "spawn.hook_sync.failed rc=%s src=%s dst=%s err=%s",
                proc.returncode, src, dst,
                (err or b"").decode(errors="replace")[:200],
            )
            return False

        # →971: symlink hooks/ so live edits on main land immediately in
        # every worktree. A copied hooks/ drifts the moment main is patched;
        # a symlink has zero lag and requires no re-sync.
        src_hooks = src / "hooks"
        dst_hooks = dst / "hooks"
        if src_hooks.is_dir():
            # Remove any stale copy or symlink before creating the live link.
            if dst_hooks.is_symlink():
                dst_hooks.unlink()
            elif dst_hooks.exists():
                _shutil.rmtree(str(dst_hooks))
            try:
                dst_hooks.symlink_to(src_hooks.resolve())
                logger.info(
                    "spawn.hook_sync.hooks_symlinked src=%s dst=%s",
                    src_hooks, dst_hooks,
                )
            except Exception as sym_exc:
                logger.warning(
                    "spawn.hook_sync.hooks_symlink_failed src=%s dst=%s err=%s "
                    "-- falling back to rsync copy",
                    src_hooks, dst_hooks, sym_exc,
                )
                proc2 = await asyncio.create_subprocess_exec(
                    "rsync", "-a", f"{src_hooks}/", f"{dst_hooks}/",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(proc2.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    try:
                        proc2.kill()
                    except Exception:
                        pass

        logger.info("spawn.hook_sync.ok src=%s dst=%s", src, dst)
        return True
    except Exception as exc:
        logger.warning(
            "spawn.hook_sync.exception src=%s dst=%s err=%s",
            src_claude_dir, dst_claude_dir, exc,
        )
        return False

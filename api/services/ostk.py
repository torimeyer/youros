from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from config import PROJECT_ROOT, OSTK_DIR as _OSTK_DIR
from services.atomic_io import atomic_write_text
from services.youros_paths import youros_home

_logger = logging.getLogger(__name__)

PROJECT_DIR = os.environ.get("OSTK_PROJECT_ROOT", str(PROJECT_ROOT))
OSTK_DIR = _OSTK_DIR
NUDGES_DIR = OSTK_DIR / "nudges"
USER_SPECS_DIR = Path(
    os.environ.get("MYOS_USER_SPECS_DIR", str(youros_home() / "specs"))
)
USER_DRAFTS_DIR = Path(
    os.environ.get("MYOS_USER_DRAFTS_DIR", str(youros_home() / "drafts"))
)


class OstkError(Exception):
    pass


def get_effective_root() -> Path:
    """Return the effective project root for the current execution context.

    Worktree agents have OSTK_PROJECT_ROOT injected into their environment by
    the spawn endpoint before launching the claude-code subprocess.  Using
    this function (rather than PROJECT_ROOT directly) ensures path resolution
    in OstkService operates against the worktree, not the main repo.
    """
    root = os.environ.get("OSTK_PROJECT_ROOT") or os.environ.get("OSTK_ROOT")
    if root:
        p = Path(root)
        if p.exists():
            return p
    import config as _config
    return Path(_config.PROJECT_ROOT)


# Shared parse cache for .ostk/audit.jsonl. The file is ~400 KB and was
# re-read by routers/dashboard.py and services/briefing.py on the async
# event loop on every request. Now every caller goes through
# :func:`read_audit_entries` which checks
# the file's size + mtime_ns and returns the cached parse if the file
# is unchanged. Callers get a shared list[dict] they must NOT mutate.
_audit_cache: dict[str, tuple[int, int, list[dict]]] = {}

# Incremental tail cache: stores (last_offset, entries_so_far) per path.
# When the file grows (active agents appending), we seek to last_offset
# and parse only the new bytes instead of re-parsing the full file.
_audit_tail: dict[str, tuple[int, list[dict]]] = {}


# Per-event-loop lock that serializes ``close_task`` rewrites of
# ``issues.jsonl``. Without this, three parallel spec-builder agents
# completing at the same second each kick off their own
# ``ostk work close <id>`` subprocess AND a Python-side rewrite of the
# same file. The overlapping read-modify-writes clobber each other and
# at least one task ends up stuck in the "open" state even though its
# builder agent ran to completion. That is exactly how the multi-model
# side-by-side spec landed on the Specs page showing "Done. Every task
# closed and the feature is live." while task 832 was still open.
#
# Keyed by ``id(loop)`` so pytest-asyncio test runs (each of which spins
# up a fresh event loop) get a fresh lock instead of trying to reuse a
# lock bound to a now-closed loop.
_close_task_locks: dict[int, asyncio.Lock] = {}


def _get_close_task_lock() -> asyncio.Lock:
    """Return the ``close_task`` lock for the current event loop.

    asyncio.Lock binds to the loop it is constructed in, so a single
    module-level Lock blows up under pytest-asyncio where each test
    runs on a fresh loop. Looking up the lock by ``id(loop)`` keeps
    production behavior (one lock, all close_task calls serialize) while
    letting tests each get their own.
    """
    loop = asyncio.get_event_loop()
    key = id(loop)
    lock = _close_task_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _close_task_locks[key] = lock
    return lock


def read_audit_entries(audit_path: Optional[Path] = None) -> list[dict]:
    """Return the parsed list of audit.jsonl entries.

    Uses an incremental tail cache: on the first read (or if the file
    shrinks/truncates) we parse from the start. On subsequent reads where
    the file only grew, we seek to the previous end-of-file offset and
    parse only the new bytes. This makes each append O(new bytes) instead
    of O(total file size), which is critical when agents are actively
    writing to a 2.6 MB / 20K-line audit.jsonl.

    Thread-safe: all mutations are single dict-slot reference assignments.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    if not audit_path.exists():
        return []
    try:
        stat = audit_path.stat()
    except OSError:
        return []

    key = str(audit_path)
    current_size = stat.st_size

    # Fast path: size unchanged (mtime may drift but content didn't grow)
    cached = _audit_cache.get(key)
    if cached is not None and cached[0] == current_size and cached[1] == stat.st_mtime_ns:
        return cached[2]

    # Incremental path: file strictly grew since last read.
    # If size is unchanged but mtime changed (content replaced at same size),
    # the fast path above would have returned; reaching here means mtime changed
    # too — fall through to full reparse rather than returning stale entries.
    tail = _audit_tail.get(key)
    if tail is not None and tail[0] > 0 and tail[0] < current_size:
        last_offset, existing_entries = tail
        try:
            with audit_path.open("rb") as fh:
                fh.seek(last_offset)
                new_bytes = fh.read()
        except OSError:
            return existing_entries
        new_entries = list(existing_entries)
        for raw_line in new_bytes.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                new_entries.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
        _audit_tail[key] = (current_size, new_entries)
        _audit_cache[key] = (current_size, stat.st_mtime_ns, new_entries)
        return new_entries

    # Full parse: first read or file was truncated/replaced
    try:
        text = audit_path.read_text()
    except OSError:
        return []
    entries: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    _audit_cache[key] = (current_size, stat.st_mtime_ns, entries)
    _audit_tail[key] = (current_size, entries)
    return entries


def _normalize_id(tid) -> str:
    """Canonicalize a task ID so all storage formats compare equal.

    ostk 7.6.0 changed issues.jsonl to store IDs as bare integers (e.g.
    ``1``) while ``ostk work list --json`` still emits the arrow-prefixed
    zero-padded string form (e.g. ``"→001"``).  Two normalizations are
    required to make them match:

    1. Strip the ``→`` / ``->`` arrow prefix.
    2. Strip leading zeros by converting through int (so ``"001"`` → ``"1"``
       and integer ``1`` → ``"1"``).

    Non-numeric IDs (e.g. slug-style) fall through to string comparison.
    """
    if tid is None or tid == "" or tid == 0:
        return ""
    s = str(tid).strip()
    if s.startswith("→"):
        s = s[1:]
    elif s.startswith("->"):
        s = s[2:]
    try:
        return str(int(s))
    except (ValueError, OverflowError):
        return s


def _read_active_store_ids(root: Path) -> Optional[set]:
    """Return the set of task IDs present in issues.jsonl (the active store).

    Returns None when the file does not exist (caller treats None as
    "no filter" to preserve backward compatibility in test environments
    where the file is absent).  Used by list_tasks to exclude rotated-
    archive entries served by the ostk daemon (→1694).

    IDs are normalized (arrow prefix stripped) so that issues.jsonl entries
    written by ostk 7.6.0 (bare numeric IDs) match daemon output that still
    emits the arrow-prefixed form.
    """
    issues_path = root / ".ostk" / "needles" / "issues.jsonl"
    if not issues_path.exists():
        return None
    ids: set = set()
    try:
        for line in issues_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                nid = entry.get("id")
                if nid:
                    ids.add(_normalize_id(str(nid)))
            except json.JSONDecodeError:
                pass
    except OSError:
        return None
    return ids


# Statuses that mark a needle as terminal/archived. Everything else
# (open, in_progress, ready, ...) is live work the daemon is authoritative
# about and must never be filtered out by the active-store reconcile.
_TERMINAL_STATUSES = {"closed", "shelved"}


def _reconcile_active(seen: dict, active_ids: Optional[set]) -> list:
    """Reconcile daemon-returned needles against the active on-disk store.

    →1694: the ostk daemon reads both issues.jsonl (active) AND
    issues.jsonl.1 (rotated historical archive), so its output includes
    1400+ historical CLOSED entries. We suppress those archive-only closed
    entries so the API never serves rotated-archive noise.

    →2200: a mid-day rotation can push older open needles into the archive
    while a few fresh tasks created after the rotation appear in the active
    store. The former ``active_healthy`` gate incorrectly treated those fresh
    tasks as proof that the active store was authoritative, causing the older
    open archive needles to be dropped. The gate is removed.

    Rule: an entry absent from the active store is kept if and only if its
    status is non-terminal (open/in_progress/ready/...). Terminal
    (closed/shelved) entries absent from the active store are always dropped
    (→1694 closed-archive-noise suppression preserved).
    """
    if active_ids is None:
        return list(seen.values())
    # Normalize BOTH sides: ostk 7.6.0 stores bare IDs in issues.jsonl while the
    # daemon (and legacy callers/tests) still emit arrow-prefixed IDs. Normalizing
    # only the key dropped active entries whose active_ids still carried the arrow
    # prefix (regression caught at the →2216 merge gate).
    _active_norm = {_normalize_id(a) for a in active_ids}
    out: list = []
    for k, v in seen.items():
        if _normalize_id(k) in _active_norm:
            out.append(v)
            continue
        status = (v.get("status") or "").lower()
        if status not in _TERMINAL_STATUSES:
            out.append(v)
    return out


def invalidate_audit_cache(audit_path: Optional[Path] = None) -> None:
    """Drop the cached parse for an audit.jsonl file. Call this right
    after appending an entry so the next reader sees the new line.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    _audit_cache.pop(str(audit_path), None)


def write_audit_entry(entry: dict, audit_path: Optional[Path] = None) -> None:
    """Append a single JSON entry to audit.jsonl and invalidate the cache.

    Best-effort: any IO failure is swallowed so audit logging can never
    break the caller. Used by chat handlers and agent registration to
    record usage events without shelling out to ``ostk``.
    """
    if audit_path is None:
        audit_path = OSTK_DIR / "audit.jsonl"
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        invalidate_audit_cache(audit_path)
    except OSError:
        pass


# In-flight coalescing for expensive read-only ostk calls. When two
# handlers ask for the same thing at the same time (for example
# /api/tasks and /api/dashboard/summary both call ``list_tasks()``
# during the same page load), they share ONE subprocess spawn instead
# of each spawning their own. Only applied to read-only calls keyed on
# (method, args). Never dedupe writes like ``add_task`` or ``close_task``
# because they would get silently merged.
_inflight_calls: dict[tuple, "asyncio.Future"] = {}


async def _coalesce_call(key: tuple, coro_factory):
    """Run ``coro_factory()`` once per ``key`` at a time. Other callers
    that enter before the first one finishes ``await`` its Future and
    receive the same result. Not a TTL cache: as soon as the first
    call resolves the key is freed, so serial callers always get fresh
    results.
    """
    existing = _inflight_calls.get(key)
    if existing is not None:
        # Another task is already running this call. Wait for its
        # result instead of spawning a second subprocess. shield()
        # prevents a canceled waiter from canceling the producer.
        return await asyncio.shield(existing)
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _inflight_calls[key] = fut
    try:
        result = await coro_factory()
        if not fut.done():
            fut.set_result(result)
        return result
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _inflight_calls.pop(key, None)


# Short-TTL result cache for hot read-only ostk calls (→2018).
#
# `_coalesce_call` only dedupes requests that *overlap in time*. The
# dashboard polls /api/tasks/counts, /api/specs/counts, /api/agents and
# friends on a few-second interval, and several of those endpoints call
# the SAME underlying ostk primitive (e.g. ``list_tasks``) back to back
# rather than at the exact same instant. With coalescing alone every one
# of those serial pollers re-spawns the heavy work (subprocess + JSON
# parse of ~1400 needles), and when many land together the GIL-heavy
# parsing starves the event loop until requests time out (HTTP 000).
#
# The TTL cache closes that gap: the first caller in a short window
# computes the result; everyone else within ``ttl`` seconds gets the
# already-computed value with zero ostk work. ``_coalesce_call`` still
# wraps the producer so that even the *first* concurrent burst shares one
# computation. Cache is keyed on (method, cwd, args) exactly like the
# coalesce key, so filtered variants never collide. Returns the shared
# object; callers that mutate must copy (same contract as before).
_ttl_cache: dict[tuple, tuple[float, object]] = {}


def _mtime_ns_or_zero(path: "Optional[Path]") -> int:
    """Return ``path``'s mtime in ns, or 0 if it does not exist / no path.
    Used to make the TTL cache self-invalidate when the backing file is
    written, so a task mutation (which rewrites issues.jsonl) is reflected
    immediately rather than after the TTL expires."""
    if path is None:
        return 0
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


async def _ttl_cached_call(
    key: tuple,
    coro_factory,
    ttl: float = 2.0,
    mtime_of: "Optional[Path]" = None,
):
    """Return a cached result for ``key`` if it is younger than ``ttl``
    seconds AND the optional ``mtime_of`` file is unchanged; otherwise
    compute via ``coro_factory`` (coalesced so a concurrent burst shares
    one computation) and cache it.

    ``mtime_of`` makes the cache exact for writes: any mutation that
    rewrites that file (e.g. issues.jsonl) busts the cache on the next
    read regardless of TTL, so the dashboard never shows a stale count
    after add/close/shelve. ``ttl`` still bounds how long a burst of pure
    reads may share one computed result.

    A ``ttl`` of 0 disables caching (always recompute).
    """
    cur_mtime = _mtime_ns_or_zero(mtime_of)
    if ttl > 0:
        cached = _ttl_cache.get(key)
        if cached is not None:
            ts, cached_mtime, value = cached
            if cached_mtime == cur_mtime and (time.monotonic() - ts) < ttl:
                return value

    async def _produce():
        result = await coro_factory()
        if ttl > 0:
            # Re-read mtime after producing so a write that landed during
            # the (awaited) computation invalidates the entry next time.
            _ttl_cache[key] = (time.monotonic(), _mtime_ns_or_zero(mtime_of), result)
        return result

    # Coalesce so a simultaneous burst (the exact case that wedges the
    # loop) still collapses to a single producer even on a cold cache.
    return await _coalesce_call(key, _produce)


# ostk subcommands that change the task store. After any of these succeed
# we drop the cached ``list_tasks`` result so the next read recomputes
# rather than serving a stale count for up to the TTL window.
_TASK_WRITE_VERBS = {"add", "close", "delete", "update", "claim", "reopen", "shelve", "unshelve", "priority", "status"}


def _maybe_invalidate_after_write(args: tuple) -> None:
    """If ``args`` is a task-mutating ostk command, drop cached task reads."""
    if len(args) >= 2 and args[0] == "work" and args[1] in _TASK_WRITE_VERBS:
        invalidate_ttl_cache("list_tasks")


def invalidate_ttl_cache(prefix: Optional[str] = None) -> None:
    """Drop cached entries. With no prefix, clears everything; with a
    prefix (e.g. ``"list_tasks"``) clears only matching method keys so a
    write (add/close task) can force the next read to recompute."""
    if prefix is None:
        _ttl_cache.clear()
        return
    for k in [k for k in _ttl_cache if k and k[0] == prefix]:
        _ttl_cache.pop(k, None)


def _spec_audit_enrich_sync(
    docs: list,
    repo_root: "Path",
    task_status_map: dict,
) -> None:
    """Synchronous spec_audit enrichment pass.  MUST run off the event loop.

    Calls compute_shipped() and compute_husk_status() per doc, both of which
    do filesystem I/O (Path.read_text + Path.exists).  Moving this work to a
    worker thread via asyncio.to_thread() releases the event loop to keep
    serving other requests (WS feeds, health, /api/agents/spawn) during the
    scan (fix for →1739 / →1738).
    """
    try:
        from services.spec_audit import (  # type: ignore[import]
            compute_shipped,
            compute_husk_status,
            compute_stage,
            ShippedResult,
            HuskResult,
        )
        for doc in docs:
            raw_path = doc.get("path", "")
            if not raw_path or doc.get("status") == "plan":
                doc.setdefault("stage", "draft")
                doc.setdefault("husk", False)
                doc.setdefault("missing_files", [])
                doc.setdefault("open_linked_needles", [])
                continue
            if raw_path.startswith("/") or raw_path.startswith("~"):
                abs_path = Path(raw_path).expanduser()
            else:
                abs_path = repo_root / raw_path
            needle_statuses = dict(task_status_map)
            try:
                shipped = compute_shipped(
                    abs_path, repo_root=repo_root, needle_statuses=needle_statuses
                )
            except Exception:
                shipped = ShippedResult(is_shipped=False, missing_files=[], open_needles=[])
            try:
                husk = compute_husk_status(abs_path)
            except Exception:
                husk = HuskResult(is_husk=False, reason="")
            stage = compute_stage(doc, husk=husk, shipped=shipped)
            doc["stage"] = stage
            doc["husk"] = husk.is_husk
            doc["husk_reason"] = husk.reason
            doc["missing_files"] = shipped.missing_files
            doc["open_linked_needles"] = shipped.open_needles
    except Exception:
        for doc in docs:
            doc.setdefault("stage", "draft")
            doc.setdefault("husk", False)
            doc.setdefault("husk_reason", "")
            doc.setdefault("missing_files", [])
            doc.setdefault("open_linked_needles", [])


class OstkService:
    def __init__(self, cwd: str = None):
        self.cwd = cwd if cwd is not None else str(get_effective_root())
        # Tri-state: None = unknown, True = working, False = unavailable
        self._socket_available: Optional[bool] = None

    def _resolve_socket_tool(self, args: tuple) -> Optional[tuple]:
        """Map CLI argument tuples to MCP tool names and arguments.

        Returns (tool_name, arguments_dict) or None if the command
        should go through subprocess instead.
        """
        if not args:
            return None

        head = args[0]

        if head == "os" and len(args) >= 2:
            sub = args[1]
            if sub == "status":
                return ("status", {})
            if sub == "diff":
                return ("diff", {})
            if sub == "clock":
                return ("clock", {})
            if sub == "history":
                params: dict = {}
                rest = args[2:]
                i = 0
                while i < len(rest):
                    if rest[i] == "--last" and i + 1 < len(rest):
                        params["last"] = rest[i + 1]
                        i += 2
                    else:
                        params["target"] = rest[i]
                        i += 1
                return ("session_history", params)
            return None

        if head == "kernel" and len(args) >= 2:
            sub = args[1]
            if sub == "ps":
                return ("ps", {})
            if sub == "reap":
                return ("reap", {})
            return None

        if head == "work" and len(args) >= 2:
            sub = args[1]
            # Only map read-only commands; mutations use subprocess
            if sub == "list":
                params = {}
                rest = args[2:]
                i = 0
                while i < len(rest):
                    if rest[i] == "--json":
                        params["format"] = "json"
                        i += 1
                    elif rest[i] == "--status" and i + 1 < len(rest):
                        params["status"] = rest[i + 1]
                        i += 2
                    elif rest[i] == "--priority" and i + 1 < len(rest):
                        params["priority"] = rest[i + 1]
                        i += 2
                    else:
                        i += 1
                return ("needle", params)
            if sub == "near" and len(args) >= 3:
                return ("near", {"query": args[2]})
            if sub == "hay":
                if len(args) >= 3:
                    return ("hay", {"thought": args[2]})
                return ("hay", {})
            if sub == "activate" and len(args) >= 3:
                return ("activate", {"needle": args[2]})
            if sub == "radiate":
                if len(args) >= 3:
                    return ("related", {"needle": args[2]})
                return ("related", {})
            return None

        if head == "compounds":
            return ("related", {"type": "compounds"})
        if head == "trace" and len(args) >= 2:
            return ("trace", {"needle": args[1]})
        if head == "boot":
            return ("boot", {})

        # Deep search verbs (MCP-only, no CLI equivalent)
        if head == "pitchfork" and len(args) >= 2:
            return ("pitchfork", {"query": " ".join(args[1:])})
        if head == "recall" and len(args) >= 2:
            return ("recall", {"query": " ".join(args[1:])})

        return None

    async def _run_socket(self, *args: str, timeout: int = 5) -> str:
        """Try to run an ostk command via the MCP socket transport."""
        from services.ostk_socket import call_tool, OstkSocketError

        mapping = self._resolve_socket_tool(args)
        if mapping is None:
            raise OstkSocketError(f"No socket mapping for: {args}")

        tool_name, tool_args = mapping
        return await call_tool(tool_name, tool_args, timeout=float(timeout))

    async def _run(self, *args: str, timeout: int = 5) -> str:
        # Try socket transport first (fast path)
        if self._socket_available is not False:
            try:
                result = await self._run_socket(*args, timeout=timeout)
                self._socket_available = True
                _maybe_invalidate_after_write(args)
                return result
            except Exception:
                self._socket_available = False

        # Fallback to subprocess
        import subprocess
        cmd = ["ostk", *args]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise OstkError(f"ostk command timed out: {' '.join(cmd)}")
        output = result.stdout.strip()
        if result.returncode != 0:
            # →2193: robust success detection. ostk may exit non-zero when a
            # post-action hook (like a git commit or AC check) fails even
            # though the core operation successfully updated the substrate.
            # If the output confirms the action, we treat it as success so
            # the UI doesn't report a failure for a task that was actually updated.
            confirmations = ("added ", "closed ", "shelved ", "unshelved ", "reopened ")
            if any(output.startswith(c) or f"\n{c}" in output for c in confirmations):
                _maybe_invalidate_after_write(args)
                return output
            err = result.stderr.strip() or output
            raise OstkError(err)
        _maybe_invalidate_after_write(args)
        return output

    async def _run_json(self, *args: str) -> Union[list, dict]:
        output = await self._run(*args)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise OstkError(
                f"invalid JSON from 'ostk {' '.join(args)}': {exc}"
            ) from exc

    # --- Tasks / Needles ---

    async def list_tasks(self, status: Optional[str] = None, priority: Optional[str] = None) -> list[dict]:
        # →2477: do NOT forward --status to the daemon. The socket transport's
        # needle tool with a status filter reads only issues.jsonl (active file),
        # silently dropping archive tasks whose status is "open". Fetch all tasks
        # from the daemon without a status filter and apply it in Python after
        # _reconcile_active so archive-open tasks are never silently omitted.
        args = ["work", "list", "--json"]
        if priority:
            args += ["--priority", priority]

        # Coalesce concurrent identical reads so a single page load that
        # hits /api/tasks + /api/dashboard/summary + /api/briefing all
        # at the same instant shares ONE ostk subprocess spawn instead
        # of racing three of them. All status variants share one cache key
        # now that status filtering happens in Python. Returns a defensive
        # copy so the shared list is not accidentally mutated by one caller
        # in a way that leaks to the others.
        key = ("list_tasks", self.cwd, tuple(args))

        async def _do_call() -> list[dict]:
            raw: list[dict] = await self._run_json(*args)
            seen: dict[str, dict] = {}
            for entry in raw:
                task_id = entry.get("id")
                if task_id:
                    seen[task_id] = entry
            # →1694: reconcile against the active on-disk store.
            # The ostk daemon reads both issues.jsonl (active) AND
            # issues.jsonl.1 (rotated historical archive), so ``raw``
            # contains all historical closed entries too — 1400+ on a
            # long-lived project. Filter to only IDs present in the
            # active file so the API never serves rotated-archive noise.
            active_ids = _read_active_store_ids(Path(self.cwd))
            return _reconcile_active(seen, active_ids)

        # →2018: TTL-cache this hot read so the dashboard's concurrent
        # pollers (task_counts + specs/counts both call list_tasks) share
        # one computed result within a short window instead of each
        # re-spawning the ~1400-needle parse and starving the loop.
        # Keyed on issues.jsonl's mtime so any task write busts the cache
        # immediately (even direct-file writers like shelve/update_status).
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        shared = await _ttl_cached_call(key, _do_call, ttl=2.0, mtime_of=issues_path)
        tasks = list(shared)
        if status is not None:
            tasks = [t for t in tasks if (t.get("status") or "").lower() == status.lower()]
        return tasks

    async def add_task(
        self,
        title: str,
        priority: str = "P1",
        description: str = "",
        ac: str = "",
    ) -> str:
        # ostk requires --description and --ac. Fall back to sensible defaults
        # derived from the title so quick-add from the UI still works.
        desc = description.strip() or title
        acceptance = ac.strip() or "Task is complete and verified."
        return await self._run(
            "work", "add", title,
            "--priority", priority,
            "--description", desc,
            "--ac", acceptance,
        )

    async def close_task(self, task_id: str, closed_reason: Optional[str] = None) -> str:
        """Close a task and optionally tag it with a structured reason.

        ``closed_reason`` is a controlled vocabulary used by the Tasks audit
        feature to mark why a task was closed: ``"completed"`` when the work
        is already done, ``"duplicate"`` when another task covers it, or
        ``"archived"`` when it is no longer relevant. When omitted the task
        is closed as usual and no extra field is written. Values outside
        the allowed set are rejected so callers cannot invent ad-hoc tags.

        Serialized behind ``_close_task_lock`` so concurrent calls (for
        example the three builder agents of a 3-AC spec finishing at the
        same instant) do not race on the underlying ``issues.jsonl``
        read-modify-write and lose one of the closes. Before the lock,
        three simultaneous closes could clobber each other and leave one
        task stuck ``open`` even after its builder ran to completion.
        """
        allowed = {"completed", "duplicate", "archived"}
        if closed_reason is not None and closed_reason not in allowed:
            raise OstkError(
                f"invalid closed_reason '{closed_reason}', must be one of {sorted(allowed)}"
            )
        async with _get_close_task_lock():
            result = await self._run("work", "close", task_id)
            # ``ostk work close`` appends a new closed entry to issues.jsonl
            # but leaves the original open entry intact — producing duplicate
            # rows for the same ID.  The CLI reads first-occurrence (open);
            # the API reads last-occurrence (closed).  Deduplicate now, keeping
            # the last row per ID, then stamp closed_reason on the survivor so
            # both readers see a single, consistent closed entry.
            issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
            if issues_path.exists():
                norm_target = self._normalize_task_id(task_id)
                lines = issues_path.read_text().strip().splitlines()
                seen_order: list[str] = []
                entries: dict[str, dict] = {}
                for line in lines:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    eid = entry.get("id", "")
                    if eid and eid not in entries:
                        seen_order.append(eid)
                    if eid:
                        entries[eid] = entry  # last occurrence wins
                # →2477: anchor archive-only closes in the active file.
                # If the task lives only in issues.jsonl.1 (archive), the
                # dedup above produces no entry to stamp or write. A daemon
                # rotation that later rewrites .1 would undo the archive flip
                # below, silently re-opening the task. Writing a closed record
                # to issues.jsonl makes the close durable regardless of what
                # the daemon does to .1, because last-occurrence-wins always
                # reads issues.jsonl after .1.
                _found_in_active = any(
                    self._normalize_task_id(str(eid)) == norm_target
                    for eid in seen_order
                )
                if not _found_in_active:
                    _rot = issues_path.with_suffix(".jsonl.1")
                    _anchor: dict = {"id": norm_target, "status": "closed"}
                    if _rot.exists():
                        for _line in _rot.read_text().splitlines():
                            if not _line.strip():
                                continue
                            try:
                                _e = json.loads(_line)
                            except json.JSONDecodeError:
                                continue
                            if self._normalize_task_id(str(_e.get("id", ""))) == norm_target:
                                _anchor = {**_e, "status": "closed"}
                    if closed_reason is not None:
                        _anchor["closed_reason"] = closed_reason
                    _anchor_key = _anchor.get("id", norm_target)
                    seen_order.append(_anchor_key)
                    entries[_anchor_key] = _anchor

                if closed_reason is not None:
                    for eid in seen_order:
                        if self._normalize_task_id(eid) == norm_target:
                            entries[eid]["closed_reason"] = closed_reason
                issues_path.write_text(
                    "\n".join(
                        json.dumps(entries[eid], ensure_ascii=False)
                        for eid in seen_order
                    ) + "\n"
                )

            # Two-file consistency: a close must leave NO open record in the
            # rotated archive either. The merged daemon read hides this (the
            # active "closed" line overrides the archive "open" on a
            # last-occurrence-wins scan), but the archive entry still reads
            # "open" — so a later store rotation can re-surface the stale open.
            # That is the mechanism behind the board never clearing. Flip the
            # matching archive entry to closed (mirrors delete_task's →1694
            # archive handling, but preserves the record as closed for history).
            rotated_path = issues_path.with_suffix(".jsonl.1")
            if rotated_path.exists():
                rot_lines = rotated_path.read_text().strip().splitlines()
                rot_changed = False
                rot_updated: list[str] = []
                for line in rot_lines:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        rot_updated.append(line)
                        continue
                    if (
                        self._normalize_task_id(str(entry.get("id", ""))) == norm_target
                        and entry.get("status") not in ("closed", "shelved")
                    ):
                        entry["status"] = "closed"
                        if closed_reason is not None:
                            entry["closed_reason"] = closed_reason
                        rot_changed = True
                    rot_updated.append(json.dumps(entry, ensure_ascii=False))
                if rot_changed:
                    rotated_path.write_text("\n".join(rot_updated) + "\n")
            return result

    async def reopen_task(self, task_id: str) -> str:
        """Reopen a closed task by editing the JSONL file directly."""
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        norm_target = self._normalize_task_id(task_id)
        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if self._normalize_task_id(entry.get("id", "")) == norm_target:
                entry["status"] = "open"
                entry.pop("close_reason", None)
                entry.pop("closed_at", None)
                entry.pop("closed_reason", None)
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"reopened {task_id}"

    async def set_task_in_progress(self, needle_id: str) -> bool:
        """Persist in_progress status for a needle in issues.jsonl.

        Only transitions open → in_progress. Already in_progress or
        terminal needles are left unchanged. Returns True if a write
        happened, False if no change was needed or needle not found.

        Called at agent spawn/register time so the needle shows
        in_progress persistently (not just as a live-agent overlay),
        surviving across agent completion until the branch merges to
        main and close_task is called.
        """
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            return False

        norm_id = self._normalize_task_id(needle_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        def _sync_update() -> bool:
            # All file I/O is in this sync helper so the async caller
            # can delegate via asyncio.to_thread and never block the loop.
            lines = issues_path.read_text().strip().splitlines()
            found = False
            changed = False
            updated: list[str] = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    updated.append(line)
                    continue
                raw_id = str(entry.get("id", ""))
                if self._normalize_task_id(raw_id) == norm_id:
                    found = True
                    if entry.get("status") == "open":
                        entry["status"] = "in_progress"
                        entry["in_progress_at"] = now_iso
                        changed = True
                updated.append(json.dumps(entry, ensure_ascii=False))

            if not found or not changed:
                return False

            issues_path.write_text("\n".join(updated) + "\n")
            return True

        return await asyncio.to_thread(_sync_update)

    def release_needle_sync(self, needle_id: str) -> bool:
        """Synchronously reset a needle from in_progress back to open.

        Only acts on needles whose current stored status is ``in_progress``.
        Closed, shelved, or already-open needles are left unchanged. Returns
        True if a write happened, False if no change was needed.

        Called synchronously from _fire_release_needle_if_orphaned in agents.py
        so no asyncio task is created and no extra event loop cycles are added.
        """
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            return False

        norm_id = self._normalize_task_id(needle_id)
        raw_content = issues_path.read_text()
        # Fast path: skip JSON parsing when needle_id clearly absent.
        bare = needle_id.lstrip("→").strip()
        if needle_id not in raw_content and bare not in raw_content:
            return False
        lines = raw_content.strip().splitlines()
        found = False
        changed = False
        updated: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                updated.append(line)
                continue
            raw_id = str(entry.get("id", ""))
            if self._normalize_task_id(raw_id) == norm_id:
                found = True
                if entry.get("status") == "in_progress":
                    entry["status"] = "open"
                    entry.pop("in_progress_at", None)
                    changed = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found or not changed:
            return False

        issues_path.write_text("\n".join(updated) + "\n")
        return True

    async def release_needle(self, needle_id: str) -> bool:
        """Async wrapper around release_needle_sync — used by tests and async callers."""
        return self.release_needle_sync(needle_id)

    async def delete_task(self, task_id: str) -> str:
        """Permanently remove a task from issues.jsonl.

        Always calls ``ostk work close`` first so the daemon's in-memory state
        is updated. Without this step list_tasks (which reads via the daemon
        socket) returns stale data and the row reappears on the next poll even
        after a full page refresh (→1502).

        Two cases:
        - Task in issues.jsonl: close notifies daemon, file edit removes entry.
          Close errors are swallowed so the file removal always runs.
        - Task not in issues.jsonl (older needle): close IS the delete. If close
          fails the task truly doesn't exist and OstkError is raised.
        """
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"

        async with _get_close_task_lock():
            found_in_file = False
            if issues_path.exists():
                for line in issues_path.read_text().strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        if json.loads(line).get("id") == task_id:
                            found_in_file = True
                            break
                    except json.JSONDecodeError:
                        pass

            # Notify the daemon. For tasks not in the file this is the primary
            # delete path. Catch broadly so FileNotFoundError (ostk not in PATH
            # in some test environments) is also handled gracefully.
            try:
                await self._run("work", "close", task_id)
                daemon_closed = True
            except Exception as exc:
                daemon_closed = False
                if not found_in_file:
                    raise OstkError(f"task '{task_id}' not found") from exc
                raise OstkError(f"daemon close failed for '{task_id}': {exc}") from exc

            if not issues_path.exists():
                return f"deleted {task_id}"

            # Re-read after close (close may append a new closed line) then
            # strip all occurrences of task_id so the entry is permanently gone.
            lines = issues_path.read_text().strip().splitlines()
            updated = []
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    updated.append(line)
                    continue
                if entry.get("id") != task_id:
                    updated.append(json.dumps(entry, ensure_ascii=False))

            issues_path.write_text("\n".join(updated) + ("\n" if updated else ""))

            # →1694 resurrection fix: also remove from issues.jsonl.1 (rotated
            # archive).  Without this, the entry survives in the rotated file
            # and the daemon returns it again on the next ``ostk work list``,
            # making the task reappear as if it was never deleted.
            rotated_path = issues_path.with_suffix(".jsonl.1")
            if rotated_path.exists():
                rot_lines = rotated_path.read_text().strip().splitlines()
                rot_updated = []
                for line in rot_lines:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        rot_updated.append(line)
                        continue
                    if entry.get("id") != task_id:
                        rot_updated.append(json.dumps(entry, ensure_ascii=False))
                rotated_path.write_text(
                    "\n".join(rot_updated) + ("\n" if rot_updated else "")
                )

        return f"deleted {task_id}"

    async def update_task_priority(
        self,
        task_id: str,
        priority: str,
        reason: Optional[str] = None,
    ) -> str:
        """Update a task's priority.

        When *reason* is provided, the change is logged via ``ostk work promote``
        so the audit trail captures why the priority was changed. Without a
        reason the JSONL file is edited directly (preserving the existing
        fast-path behaviour).
        """
        valid = {"P0", "P1", "P2", "P3"}
        if priority not in valid:
            raise OstkError(f"invalid priority '{priority}', must be one of {valid}")

        if reason:
            return await self._run(
                "work", "promote", task_id, priority, "--reason", reason
            )

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        norm_target = self._normalize_task_id(task_id)
        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if self._normalize_task_id(entry.get("id", "")) == norm_target:
                entry["priority"] = priority
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"updated {task_id} priority to {priority}"

    async def update_task_fields(
        self,
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> str:
        """Rename a task's title and/or description, or set notes.

        ``ostk needle edit`` only supports ``--description`` today, so this
        edits ``issues.jsonl`` directly for title changes. Description and
        notes changes use the same fast path for consistency. All fields are
        optional and any unset field is left untouched.
        """
        if title is None and description is None and notes is None:
            raise OstkError("update_task_fields requires title, description, or notes")

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                if title is not None:
                    entry["title"] = title
                if description is not None:
                    entry["description"] = description
                if notes is not None:
                    entry["notes"] = notes
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        fields = []
        if title is not None:
            fields.append("title")
        if description is not None:
            fields.append("description")
        if notes is not None:
            fields.append("notes")
        return f"updated {task_id} {' and '.join(fields)}"

    async def update_task_status(self, task_id: str, status: str) -> str:
        """Move a task back to the open queue.

        Only ``open`` is accepted here. ``in_progress`` is derived at read
        time from live agent state and must never be written directly, since
        it would create stuck rows if the agent dies. ``closed`` and
        ``shelved`` are managed through their own dedicated endpoints.
        """
        valid = {"open"}
        if status not in valid:
            raise OstkError(
                f"invalid status '{status}', must be one of {valid}"
            )

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == task_id:
                current = entry.get("status")
                # Closed and shelved tasks must not silently jump back to
                # open through this path. The caller should reopen or unshelve
                # first, which is a distinct user intent.
                if current in ("closed", "shelved"):
                    raise OstkError(
                        f"task '{task_id}' is {current}; reopen or unshelve it first"
                    )
                entry["status"] = status
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"updated {task_id} status to {status}"

    async def shelve_task(self, task_id: str) -> str:
        """Pause a task by writing 'shelved' status directly to issues.jsonl.

        ``ostk work shelve`` does not exist in the installed CLI; we manage
        the status field the same way update_task_status does.
        """
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        norm_task_id = _normalize_id(task_id)
        for line in lines:
            entry = json.loads(line)
            if _normalize_id(entry.get("id", "")) == norm_task_id:
                if entry.get("status") == "closed":
                    raise OstkError(f"task '{task_id}' is closed; reopen it first")
                entry["status"] = "shelved"
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        # →2018: this writer bypasses _run (writes issues.jsonl directly),
        # so invalidate the cached task list here too.
        invalidate_ttl_cache("list_tasks")
        return f"shelved {task_id}"

    async def unshelve_task(self, task_id: str) -> str:
        """Resume a shelved task by writing 'open' status directly to issues.jsonl.

        ``ostk work unshelve`` does not exist in the installed CLI.
        """
        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        norm_task_id = _normalize_id(task_id)
        for line in lines:
            entry = json.loads(line)
            if _normalize_id(entry.get("id", "")) == norm_task_id:
                if entry.get("status") != "shelved":
                    raise OstkError(
                        f"task '{task_id}' is not shelved (status: {entry.get('status')})"
                    )
                entry["status"] = "open"
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"task '{task_id}' not found")

        issues_path.write_text("\n".join(updated) + "\n")
        # →2018: bypasses _run; invalidate cached task list.
        invalidate_ttl_cache("list_tasks")
        return f"unshelved {task_id}"

    async def link_tasks(self, source: str, relation: str, target: str) -> str:
        """Link two tasks with a relationship (blocks or depends-on)."""
        valid_relations = {"blocks", "depends-on"}
        if relation not in valid_relations:
            raise OstkError(
                f"invalid relation '{relation}', must be one of {valid_relations}"
            )
        return await self._run("work", "link", source, relation, target)

    async def get_dependencies(self, task_id: str) -> dict:
        """Get the dependency info for a task from the task list data.

        Rather than parsing ``ostk work depends`` text output, we read the
        blocks/depends_on arrays that ``ostk work list --json`` already
        provides. This is faster and more reliable.
        """
        tasks = await self.list_tasks()
        task = None
        for t in tasks:
            if t.get("id") == task_id:
                task = t
                break
        if task is None:
            raise OstkError(f"task \'{task_id}\' not found")

        blocks = task.get("blocks", [])
        depends_on = task.get("depends_on", [])

        # Build title lookup for referenced tasks
        titles: dict[str, str] = {}
        for t in tasks:
            tid = t.get("id", "")
            if tid in blocks or tid in depends_on:
                titles[tid] = t.get("title", "")

        return {
            "task_id": task_id,
            "blocks": [{"id": b, "title": titles.get(b, "")} for b in blocks],
            "depends_on": [
                {"id": d, "title": titles.get(d, "")} for d in depends_on
            ],
        }

    async def unlink_tasks(self, source: str, relation: str, target: str) -> str:
        """Remove a link between two tasks by editing issues.jsonl directly.

        ostk CLI does not have an unlink command, so we manipulate the
        data file directly, similar to reopen_task and update_task_priority.
        """
        valid_relations = {"blocks", "depends-on"}
        if relation not in valid_relations:
            raise OstkError(
                f"invalid relation \'{relation}\', must be one of {valid_relations}"
            )

        issues_path = Path(self.cwd) / ".ostk" / "needles" / "issues.jsonl"
        if not issues_path.exists():
            raise OstkError("issues.jsonl not found")

        field = "blocks" if relation == "blocks" else "depends_on"
        lines = issues_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("id") == source and target in entry.get(field, []):
                entry[field] = [t for t in entry[field] if t != target]
                if not entry[field]:
                    del entry[field]
                found = True
            updated.append(json.dumps(entry, ensure_ascii=False))

        if not found:
            raise OstkError(f"link not found: {source} {relation} {target}")

        issues_path.write_text("\n".join(updated) + "\n")
        return f"unlinked {source} {relation} {target}"

    async def next_task(self) -> str:
        return await self._run("work", "next")

    async def claim_next_task(self) -> str:
        """Pull and claim the next task from the queue.

        Uses ostk's pull model: the agent declares readiness, ostk selects
        the highest-priority task (sphere-radius-aware), and atomically
        marks it as in_progress. Returns the task info or empty if no
        tasks available.
        """
        try:
            return await self._run("work", "next", "--claim")
        except OstkError:
            return ""

    async def activate_task(self, task_id: str) -> dict:
        """Run ``ostk work activate <id>`` and parse the briefing output.

        Returns a dict with structured briefing data: the task info,
        sphere details, neighbors, blockers, unblocks, and any hay
        (nearby ideas) mentioned.
        """
        raw = await self._run("work", "activate", task_id)
        return self._parse_activate(raw)

    def _parse_activate(self, output: str) -> dict:
        """Parse the text output of ``ostk work activate`` into a dict."""
        result: dict = {
            "task_id": "",
            "priority": "",
            "status": "",
            "title": "",
            "sphere": None,
            "neighbors": [],
            "blocked_by": [],
            "unblocks": [],
            "all_blockers_resolved": False,
            "raw": output,
        }

        lines = output.strip().splitlines()
        section: Optional[str] = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Header: ACTIVATE ->088 [P1|open]
            if stripped.startswith("\u2550") and "ACTIVATE" in stripped:
                m = re.search(r"ACTIVATE\s+(\S+)\s+\[(\w+)\|(\w+)\]", stripped)
                if m:
                    result["task_id"] = m.group(1)
                    result["priority"] = m.group(2)
                    result["status"] = m.group(3)
                continue

            # Footer line
            if stripped.startswith("\u2550") and "ready" in stripped.lower():
                continue

            # Section headers
            if stripped.startswith("SPHERE:"):
                result["sphere"] = stripped[len("SPHERE:"):].strip()
                section = "sphere"
                continue

            if stripped.startswith("NEIGHBORS"):
                section = "neighbors"
                continue

            if stripped.startswith("BLOCKED BY:"):
                section = "blocked_by"
                continue

            if stripped.startswith("UNBLOCKS:"):
                section = "unblocks"
                continue

            if stripped.startswith("HAY"):
                section = "hay"
                continue

            # Title line (first content before any section starts)
            if section is None and not result["title"]:
                result["title"] = stripped
                continue

            # Parse items in sections
            if section == "neighbors" and stripped.startswith("\u2192"):
                # Remove leading "→ " prefix (one arrow + space)
                text = re.sub(r"^\u2192\s*", "", stripped).strip()
                result["neighbors"].append(text)

            elif section == "blocked_by":
                if "all blockers resolved" in stripped.lower():
                    result["all_blockers_resolved"] = True
                    continue
                # ostk emits a trailing footer like "-> unresolved blockers, may not be ready"
                # when at least one blocker is still open. It is a status note, not an item.
                if stripped.startswith("\u2192") and "\u26a0" in stripped:
                    continue
                # Blocker items start with ✓ (done), ✗ (still open), or → (legacy/unknown).
                if (
                    stripped.startswith("\u2713")
                    or stripped.startswith("\u2717")
                    or stripped.startswith("\u2192")
                ):
                    resolved = stripped.startswith("\u2713")
                    # Remove one leading ✓, ✗, or → plus whitespace
                    text = re.sub(r"^[\u2713\u2717\u2192]\s*", "", stripped).strip()
                    result["blocked_by"].append({
                        "text": text,
                        "resolved": resolved,
                    })

            elif section == "unblocks" and stripped.startswith("\u2192"):
                # Remove leading "→ " prefix (one arrow + space)
                text = re.sub(r"^\u2192\s*", "", stripped).strip()
                result["unblocks"].append(text)

        return result

    # --- Hay / Ideas ---

    async def list_hay(self, exclude_converted: bool = True) -> dict:
        output = await self._run("work", "hay")
        result = self._parse_hay(output)
        if exclude_converted:
            converted_straws = {c["straw"] for c in await self.list_converted_hay()}
            if converted_straws:
                for cluster in result.get("clusters", []):
                    cluster["items"] = [
                        item for item in cluster["items"]
                        if item not in converted_straws
                    ]
                    cluster["count"] = len(cluster["items"])
                result["clusters"] = [c for c in result["clusters"] if c["items"]]
                result["unclustered"] = [
                    item for item in result.get("unclustered", [])
                    if item not in converted_straws
                ]
        return result

    async def add_hay(self, thought: str) -> str:
        return await self._run("work", "hay", thought)

    async def add_hay_from_chat(self, thought: str) -> str:
        """Save a hay entry that originated from a chat conversation.

        Calls the ostk CLI to file the hay (so it appears in the active list),
        then patches the last hay.filed event in audit.jsonl to mark
        source as 'chat'.
        """
        result = await self._run("work", "hay", thought)
        # Patch the source field on the hay.filed entry we just appended.
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if audit_path.exists():
            lines = audit_path.read_text().strip().splitlines()
            for i in range(len(lines) - 1, -1, -1):
                try:
                    entry = json.loads(lines[i])
                except json.JSONDecodeError:
                    continue
                if entry.get("event") == "hay.filed" and entry.get("straw") == thought:
                    entry["source"] = "chat"
                    lines[i] = json.dumps(entry, ensure_ascii=False)
                    audit_path.write_text("\n".join(lines) + "\n")
                    break
        return result

    async def compile_hay(self, dry_run: bool = False) -> str:
        args = ["work", "compile"]
        if dry_run:
            args.append("--dry-run")
        return await self._run(*args)

    async def delete_hay(self, straw: str, include_converted: bool = False) -> str:
        """Remove a hay entry from audit.jsonl by its straw text.

        When *include_converted* is True, also remove any matching
        hay.converted event so the idea disappears from both lists.
        """
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if not audit_path.exists():
            raise OstkError("audit.jsonl not found")

        lines = audit_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("event") == "hay.filed" and entry.get("straw") == straw:
                found = True
                continue
            if include_converted and entry.get("event") == "hay.converted" and entry.get("straw") == straw:
                continue
            updated.append(line)

        if not found:
            raise OstkError(f"hay item not found: {straw}")

        audit_path.write_text("\n".join(updated) + "\n")
        invalidate_audit_cache(audit_path)
        return f"deleted hay: {straw}"

    async def delete_converted_hay(self, straw: str) -> str:
        """Remove a hay.converted entry from audit.jsonl by its straw text."""
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        if not audit_path.exists():
            raise OstkError("audit.jsonl not found")

        lines = audit_path.read_text().strip().splitlines()
        found = False
        updated = []
        for line in lines:
            entry = json.loads(line)
            if entry.get("event") == "hay.converted" and entry.get("straw") == straw:
                found = True
                continue
            updated.append(line)

        if not found:
            raise OstkError(f"converted hay item not found: {straw}")

        audit_path.write_text("\n".join(updated) + "\n")
        invalidate_audit_cache(audit_path)
        return f"deleted converted hay: {straw}"

    async def convert_hay_to_task(self, straw: str, priority: str = "P1", delete_hay: bool = False) -> str:
        """Turn a hay entry into a task and mark it as converted.

        The hay item stays in the audit log but a ``hay.converted`` event
        is appended so the UI can filter it out of the active list and
        show it in a "Converted" tab instead.

        The legacy ``delete_hay`` flag is still accepted for backwards
        compatibility but defaults to False.
        """
        result = await self.add_task(straw, priority)
        # Mark the idea as converted in the audit log
        await self._mark_hay_converted(straw, result)
        if delete_hay:
            try:
                await self.delete_hay(straw)
            except OstkError:
                pass  # hay may already be gone; task was still created
        return result

    async def _mark_hay_converted(self, straw: str, task_result: str) -> None:
        """Append a hay.converted event to audit.jsonl."""
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        # Extract task ID from the result string if possible (e.g. "added →042")
        task_id = ""
        for word in task_result.split():
            if word.startswith("→") or word.startswith("->"):
                task_id = word
                break
        entry = {
            "event": "hay.converted",
            "straw": straw,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # Write on a worker thread so the append does not block the
        # async event loop, then invalidate the shared parse cache so
        # the next reader sees this entry.
        def _append() -> None:
            with open(audit_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        await asyncio.to_thread(_append)
        invalidate_audit_cache(audit_path)

    async def list_converted_hay(self) -> list[dict]:
        """Return hay items that have been converted into tasks.

        Reads audit.jsonl for hay.converted events and returns a list of
        dicts with the straw text and the task ID they became. Uses the
        shared :func:`read_audit_entries` cache so concurrent handlers
        do not re-parse the file on every request.
        """
        audit_path = Path(self.cwd) / ".ostk" / "audit.jsonl"
        converted: list[dict] = []
        for entry in read_audit_entries(audit_path):
            if entry.get("event") == "hay.converted":
                converted.append({
                    "straw": entry.get("straw", ""),
                    "task_id": entry.get("task_id", ""),
                    "converted_at": entry.get("timestamp", ""),
                })
        return converted

    def _parse_hay(self, output: str) -> dict:
        clusters: list[dict] = []
        unclustered: list[str] = []
        current_section = None
        current_cluster: Optional[dict] = None

        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "clusters (discovered)" in line.lower() or "clusters" in line.lower() and "──" in line:
                current_section = "clusters"
                continue
            if "unclustered" in line.lower() and "──" in line:
                current_section = "unclustered"
                if current_cluster:
                    clusters.append(current_cluster)
                    current_cluster = None
                continue

            if current_section == "clusters":
                cluster_match = re.match(r"(\w+)\s+\((\d+)\s+hay\)", line)
                if cluster_match:
                    if current_cluster:
                        clusters.append(current_cluster)
                    current_cluster = {
                        "name": cluster_match.group(1),
                        "count": int(cluster_match.group(2)),
                        "items": [],
                    }
                    continue
                if line.startswith("~"):
                    text = line.lstrip("~ ").strip()
                    if current_cluster:
                        current_cluster["items"].append(text)
                    continue

            if current_section == "unclustered" and line.startswith("~"):
                text = line.lstrip("~ ").strip()
                unclustered.append(text)

        if current_cluster:
            clusters.append(current_cluster)

        return {"clusters": clusters, "unclustered": unclustered}

    # --- Trace / Attribution ---

    async def trace(self, task_id: str) -> dict:
        """Run ``ostk trace <task_id>`` and parse the attribution chain.

        Returns a dict with the task headline plus lists for specs,
        drafts, agentfiles, depends_on, blocks, and commits.
        """
        raw = await self._run("trace", task_id)
        return self._parse_trace(raw)

    @staticmethod
    def _parse_trace(raw: str) -> dict:
        """Parse the text output of ``ostk trace`` into structured data.

        Example input::

            →093: Add attribution tracing [P2, open]
              specs: (none)
              drafts: (none)
              agentfiles: (none)
              depends_on: (none)
              blocks: →002
              commits: abc1234 Fix the thing
        """
        lines = raw.strip().splitlines()
        result: dict = {
            "headline": "",
            "specs": [],
            "drafts": [],
            "agentfiles": [],
            "depends_on": [],
            "blocks": [],
            "commits": [],
        }

        if not lines:
            return result

        result["headline"] = lines[0].strip()

        current_key: Optional[str] = None
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue

            # Check if line starts a new field ("key: value")
            if ":" in stripped:
                candidate_key, _, rest = stripped.partition(":")
                candidate_key = candidate_key.strip().lower().replace(" ", "_")
                if candidate_key in result and candidate_key != "headline":
                    current_key = candidate_key
                    rest = rest.strip()
                    if rest and rest != "(none)":
                        result[current_key].append(rest)
                    continue

            # Continuation line for the current field
            if current_key and stripped and stripped != "(none)":
                result[current_key].append(stripped)

        return result

    # --- Commits ---

    async def commit(
        self,
        message: str,
        needle: Optional[str] = None,
        spec: Optional[str] = None,
        section: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> str:
        """Run ostk commit with optional needle attribution.

        Links a git commit to a task so the work is tracked.
        """
        args = ["commit", "-m", message]
        if needle:
            args += ["--needle", needle]
        if spec:
            args += ["--spec", spec]
        if section:
            args += ["--section", section]
        if agent:
            args += ["--agent", agent]
        return await self._run(*args)

    # --- Search ---

    async def search_near(self, query: str) -> dict:
        """Search tasks by concept using ostk work near.

        Returns a dict with 'tasks' (list of matching needles).
        The CLI output is parsed into structured results.
        """
        tasks: list[dict] = []

        # Search tasks (needles) via ostk work near
        try:
            raw = await self._run("work", "near", query)
            tasks = self._parse_near_output(raw)
        except OstkError:
            # "no open needles matching ..." is not a real error
            pass

        return {"tasks": tasks, "query": query}

    def _parse_near_output(self, output: str) -> list[dict]:
        """Parse the output of ``ostk work near`` into a list of task dicts."""
        results: list[dict] = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Match lines like: →086 [P1] Add concept search across tasks and ideas
            match = re.match(r"(→\d+)\s+\[(\w+)\]\s+(.+)", line)
            if match:
                results.append({
                    "id": match.group(1),
                    "priority": match.group(2),
                    "title": match.group(3).strip(),
                })
        return results

    # --- Deep Search (pitchfork / recall) ---

    async def pitchfork(self, query: str) -> dict:
        """Search all kernel state via ostk pitchfork.

        Returns categorized results: needles, audit event summaries,
        and transcript snippets. Uses the MCP socket (no CLI equivalent).
        Timeout is longer because pitchfork scans transcripts.
        """
        try:
            raw = await self._run("pitchfork", query, timeout=15)
        except OstkError:
            return {"needles": [], "audit": [], "transcripts": [], "query": query}
        return self._parse_pitchfork(raw, query)

    async def recall(self, query: str) -> dict:
        """Search kernel state and past transcripts via ostk recall.

        Same output format as pitchfork. Kept as a separate method in
        case the two diverge in the future, but today the MCP tools
        return the same structure.
        """
        try:
            raw = await self._run("recall", query, timeout=15)
        except OstkError:
            return {"needles": [], "audit": [], "transcripts": [], "query": query}
        return self._parse_pitchfork(raw, query)

    @staticmethod
    def _parse_pitchfork(output: str, query: str) -> dict:
        """Parse the text output of pitchfork/recall into structured data.

        Output has three sections separated by blank lines:
          needles (N matches):
            - ->NNN [status] title
          audit (N matches, last 1000 events):
            - event_type (xN)
          transcripts (N matches in ~/.claude/projects/):
            - [date] session_id
              role: snippet...
        """
        needles: list[dict] = []
        audit: list[dict] = []
        transcripts: list[dict] = []

        section: Optional[str] = None
        current_transcript: Optional[dict] = None

        for line in output.splitlines():
            stripped = line.strip()

            # Detect section headers
            if stripped.startswith("needles ("):
                section = "needles"
                continue
            if stripped.startswith("audit ("):
                section = "audit"
                continue
            if stripped.startswith("transcripts ("):
                section = "transcripts"
                continue

            if not stripped:
                continue

            if section == "needles" and stripped.startswith("- "):
                item = stripped[2:]
                # Parse: ->NNN [status] title
                m = re.match(r"(→\d+)\s+\[(\w+)\]\s+(.+)", item)
                if m:
                    needles.append({
                        "id": m.group(1),
                        "status": m.group(2),
                        "title": m.group(3).strip(),
                    })

            elif section == "audit" and stripped.startswith("- "):
                item = stripped[2:]
                # Parse: event_type (xN)
                m = re.match(r"(.+?)\s+\(×(\d+)\)", item)
                if m:
                    audit.append({
                        "event": m.group(1).strip(),
                        "count": int(m.group(2)),
                    })

            elif section == "transcripts":
                if stripped.startswith("- ["):
                    # New transcript entry: - [date] session_id
                    if current_transcript is not None:
                        transcripts.append(current_transcript)
                    m = re.match(r"-\s+\[([^\]]+)\]\s+(\S+)", stripped)
                    if m:
                        current_transcript = {
                            "date": m.group(1),
                            "session_id": m.group(2),
                            "snippets": [],
                        }
                    else:
                        current_transcript = None
                elif current_transcript is not None:
                    # Snippet lines: "ai: ..." or "user: ..."
                    role_match = re.match(r"(ai|user):\s+(.*)", stripped)
                    if role_match:
                        current_transcript["snippets"].append({
                            "role": role_match.group(1),
                            "text": role_match.group(2).strip(),
                        })

        # Flush last transcript
        if current_transcript is not None:
            transcripts.append(current_transcript)

        return {
            "needles": needles,
            "audit": audit,
            "transcripts": transcripts,
            "query": query,
        }

    # --- Delegation / Radiate ---

    async def work_radiate(self, needle_id: Optional[str] = None) -> dict:
        """Run ``ostk work radiate`` and parse the output.

        Returns a dict with ``point`` (the center needle), ``rings`` (list of
        ring dicts with radius and needles), and ``delegation_targets`` (list
        of dicts ready for agent spawning).
        """
        args = ["work", "radiate"]
        if needle_id:
            args.append(needle_id)
        output = await self._run(*args)
        return self._parse_radiate(output)

    @staticmethod
    def _parse_radiate(output: str) -> dict:
        """Parse the text output of ``ostk work radiate``."""
        result: dict = {
            "point": None,
            "point_title": "",
            "rings": [],
            "delegation_targets": [],
        }

        section = "header"
        current_ring: Optional[dict] = None

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Header: extract point info
            header_match = re.match(
                r".*radiate from\s+(→\d+)\s+\[([^\]]+)\].*",
                stripped,
            )
            if header_match:
                result["point"] = header_match.group(1)
                continue

            # Point title (line after header, before "max radius")
            if section == "header" and result["point"] and not result["point_title"]:
                if not stripped.startswith("max radius"):
                    result["point_title"] = stripped
                    continue

            if stripped.startswith("max radius"):
                section = "rings"
                continue

            # Point line
            point_match = re.match(r".*POINT\s+\(radius\s+0\):\s+(→\d+)", stripped)
            if point_match:
                result["point"] = point_match.group(1)
                section = "rings"
                continue

            # Ring header
            ring_match = re.match(
                r".*RING\s+(\d+)\s+\((\d+)\s+needles?,\s*(\d+)\s+open\):",
                stripped,
            )
            if ring_match:
                if current_ring:
                    result["rings"].append(current_ring)
                current_ring = {
                    "radius": int(ring_match.group(1)),
                    "total": int(ring_match.group(2)),
                    "open": int(ring_match.group(3)),
                    "needles": [],
                }
                continue

            # Needle inside a ring
            if current_ring is not None and not stripped.startswith(("─", ":")):
                needle_match = re.match(
                    r"(→\d+)\s+\[([^\]]+)\]\s+(.*)",
                    stripped,
                )
                if needle_match:
                    current_ring["needles"].append({
                        "id": needle_match.group(1),
                        "priority": needle_match.group(2),
                        "title": needle_match.group(3).strip(),
                    })
                    continue

            # Delegation frontier header
            if "delegation frontier" in stripped.lower():
                if current_ring:
                    result["rings"].append(current_ring)
                    current_ring = None
                section = "delegation"
                continue

            # Delegation target lines
            if section == "delegation":
                spawn_match = re.match(
                    r":spawn\s+(→\d+)\s+→\s+(.*)",
                    stripped,
                )
                if spawn_match:
                    result["delegation_targets"].append({
                        "id": spawn_match.group(1),
                        "title": spawn_match.group(2).strip(),
                    })

        # Flush last ring
        if current_ring:
            result["rings"].append(current_ring)

        return result

    # --- Compounds ---

    async def get_compounds(self) -> list[dict]:
        """Run ``ostk compounds`` and parse the output.

        Returns a list of dicts, each with ``id``, ``title``, and
        ``blocks_count`` (how many other tasks it unblocks). The list is
        sorted so the highest-leverage task comes first.

        When there are no blocking dependencies the CLI prints
        "(no blocking dependencies found)" and we return an empty list.
        """
        output = await self._run("compounds")
        return self._parse_compounds(output)

    @staticmethod
    def _parse_compounds(output: str) -> list[dict]:
        """Parse the text output of ``ostk compounds``.

        Expected line formats (examples):
            →042  Build smart focus  (blocks 5)
            →017  Fix auth redirect  (blocks 2)

        If the output contains "no blocking dependencies" we return [].
        """
        if "no blocking dependencies" in output.lower():
            return []

        results: list[dict] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith(":") or line.startswith("─"):
                continue
            # Match lines like: →042  Some title  (blocks 5)
            match = re.match(
                r"(→\d+)\s+(.+?)\s+\(blocks?\s+(\d+)\)",
                line,
            )
            if match:
                results.append({
                    "id": match.group(1),
                    "title": match.group(2).strip(),
                    "blocks_count": int(match.group(3)),
                })
        # Sort descending by blocks_count (CLI may already do this, but be safe)
        results.sort(key=lambda r: r["blocks_count"], reverse=True)
        return results

    # --- Session Diff ---

    async def get_session_diff(self) -> dict:
        """Run ``ostk os diff`` and parse the output into structured data.

        Returns a dict with:
        - files_changed: list of file paths modified since boot
        - needles_filed: list of dicts with id, priority, title
        - audit_events: list of dicts with count and event type
        - audit_total: int total audit events
        """
        raw = await self._run("os", "diff")
        return self._parse_session_diff(raw)

    def _parse_session_diff(self, output: str) -> dict:
        files_changed: list[str] = []
        needles_filed: list[dict] = []
        audit_events: list[dict] = []
        audit_total = 0
        section: Optional[str] = None

        for line in output.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # Detect section headers
            if stripped.startswith("## Files changed"):
                section = "files"
                continue
            elif stripped.startswith("## Needles filed"):
                section = "needles"
                continue
            elif stripped.startswith("## Audit events"):
                section = "audit"
                continue
            elif stripped.startswith("## Active staging"):
                section = "staging"
                continue

            # Skip informational notes in parentheses
            if stripped.startswith("("):
                # Extract total from audit summary like "(22 total events)"
                if section == "audit":
                    total_match = re.match(r"\((\d+)\s+total", stripped)
                    if total_match:
                        audit_total = int(total_match.group(1))
                continue

            if section == "files":
                if stripped and not stripped.startswith("("):
                    files_changed.append(stripped)

            elif section == "needles":
                # Format: [P1] ->089  Title text
                needle_match = re.match(
                    r"\[([^\]]+)\]\s+([\u2192\->]+\d+)\s+(.*)", stripped
                )
                if needle_match:
                    needles_filed.append({
                        "priority": needle_match.group(1),
                        "id": needle_match.group(2),
                        "title": needle_match.group(3).strip(),
                    })

            elif section == "audit":
                # Format:  16  task.added
                event_match = re.match(r"(\d+)\s+(.+)", stripped)
                if event_match:
                    audit_events.append({
                        "count": int(event_match.group(1)),
                        "event": event_match.group(2).strip(),
                    })

        return {
            "files_changed": files_changed,
            "needles_filed": needles_filed,
            "audit_events": audit_events,
            "audit_total": audit_total,
        }

    # --- History ---

    async def get_history(self, last: Optional[int] = None, target: Optional[str] = None) -> list[dict]:
        """Fetch chronological activity events from ostk os history.

        Returns a list of dicts with keys: timestamp, event, detail.
        The CLI returns plain text lines like:
          [2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management
        """
        args = ["os", "history"]
        if last is not None:
            args += ["--last", str(last)]
        if target is not None:
            args.append(target)
        try:
            raw = await self._run(*args)
        except OstkError:
            return []

        events: list[dict] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = self._parse_history_line(line)
            if parsed:
                events.append(parsed)
        return events

    @staticmethod
    def _parse_history_line(line: str) -> Optional[dict]:
        """Parse a single ostk os history output line.

        Handles both legacy format (Z suffix, no microseconds):
          [2026-04-06T17:42:46Z] task.added        →091 Use ostk secret management
        And the current format (microseconds + UTC offset):
          [2026-04-14T22:30:45.665455+00:00] agent.spawned     name="fix-activity-insights"
        """
        m = re.match(
            r"\[(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}))\]\s+(\S+)\s*(.*)",
            line,
        )
        if not m:
            return None
        return {
            "timestamp": m.group(1),
            "event": m.group(2),
            "detail": m.group(3).strip(),
        }

    # --- Refine / Health Check ---

    async def refine_tasks(self, task_ids: Optional[list[str]] = None) -> dict:
        """Run ostk work refine on the given task IDs (or all open tasks).

        Parses the CLI output into structured data with per-task info
        about spheres (clusters), joints (connections), and overall
        health signals like isolated tasks and duplicate titles.

        Returns a dict with:
          - tasks: list of parsed task results
          - issues: list of detected problems (duplicates, isolated, no description)
          - summary: counts of total, issues, connected, isolated
        """
        if not task_ids:
            all_open = await self.list_tasks(status="open")
            task_ids = [t["id"] for t in all_open]

        if not task_ids:
            return {"tasks": [], "issues": [], "summary": {"total": 0, "issues": 0, "connected": 0, "isolated": 0}}

        args = ["work", "refine"] + task_ids
        raw = await self._run(*args)

        refined = self._parse_refine(raw)

        # Fetch full task list for additional checks
        all_tasks = await self.list_tasks()
        open_tasks = [t for t in all_tasks if t.get("status") == "open"]

        issues = self._detect_issues(refined, open_tasks)

        connected = sum(1 for t in refined if t.get("degree", 0) > 0)
        isolated = sum(1 for t in refined if t.get("degree", 0) == 0)

        return {
            "tasks": refined,
            "issues": issues,
            "summary": {
                "total": len(refined),
                "issues": len(issues),
                "connected": connected,
                "isolated": isolated,
            },
        }

    def _parse_refine(self, output: str) -> list[dict]:
        """Parse the output of ostk work refine into structured data."""
        tasks: list[dict] = []
        current: Optional[dict] = None

        for line in output.split("\n"):
            line = line.rstrip()

            # Task header: ->NNN [P1|open] Title text
            task_match = re.match(r"^(→\d+)\s+\[(\w+)\|(\w+)\]\s+(.+)$", line)
            if task_match:
                if current:
                    tasks.append(current)
                current = {
                    "id": task_match.group(1),
                    "priority": task_match.group(2),
                    "status": task_match.group(3),
                    "title": task_match.group(4),
                    "sphere": None,
                    "degree": 0,
                    "joints": [],
                }
                continue

            if not current:
                continue

            stripped = line.strip()

            # Sphere: sphere: N (M needles, point=->NNN)
            sphere_match = re.match(r"sphere:\s+(\d+)\s+\((\d+)\s+needles?,\s+point=(→\d+)\)", stripped)
            if sphere_match:
                current["sphere"] = {
                    "id": int(sphere_match.group(1)),
                    "size": int(sphere_match.group(2)),
                    "point": sphere_match.group(3),
                }
                continue

            # Degree: degree: N joints
            degree_match = re.match(r"degree:\s+(\d+)\s+joints?", stripped)
            if degree_match:
                current["degree"] = int(degree_match.group(1))
                continue

            # Joint: <-> ->NNN Title text
            joint_match = re.match(r"↔\s+(→\d+)\s+(.+)", stripped)
            if joint_match:
                current["joints"].append({
                    "id": joint_match.group(1),
                    "title": joint_match.group(2),
                })
                continue

        if current:
            tasks.append(current)

        return tasks

    def _detect_issues(self, refined: list[dict], open_tasks: list[dict]) -> list[dict]:
        """Detect quality issues in the task list."""
        issues: list[dict] = []

        # 1. Duplicate titles
        titles_by_id: dict[str, str] = {}
        for t in open_tasks:
            titles_by_id[t["id"]] = t.get("title", "").strip().lower()

        seen_titles: dict[str, str] = {}
        for tid, title in titles_by_id.items():
            if title in seen_titles:
                issues.append({
                    "type": "duplicate",
                    "severity": "warning",
                    "message": f"Tasks {seen_titles[title]} and {tid} have the same title",
                    "task_ids": [seen_titles[title], tid],
                })
            else:
                seen_titles[title] = tid

        # 2. Missing descriptions
        for t in open_tasks:
            desc = (t.get("description") or "").strip()
            if not desc:
                issues.append({
                    "type": "no_description",
                    "severity": "info",
                    "message": f"Task {t['id']} has no description",
                    "task_ids": [t["id"]],
                })

        # 3. Isolated tasks (no connections)
        for t in refined:
            if t.get("degree", 0) == 0:
                issues.append({
                    "type": "isolated",
                    "severity": "info",
                    "message": f"Task {t['id']} is not linked to any other tasks",
                    "task_ids": [t["id"]],
                })

        return issues

    # --- OS Status ---

    async def os_clock(self) -> dict:
        """Run ``ostk os clock`` and parse the key-value output into a dict.

        Returns a dict with keys like wall, session, kernel, swap,
        last_gen, audit, and focus.
        """
        raw = await self._run("os", "clock")
        result: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.startswith("ostk"):
                continue
            # Lines look like:  "  wall      2026-04-20T17:46:04Z"
            # Keys may be multi-word like "last gen"
            m = re.match(r'^([\w][\w ]*?)\s{2,}(.+)$', line)
            if m:
                key = m.group(1).strip().replace(" ", "_")
                value = m.group(2).strip()
                result[key] = value
        return result

    async def os_status(self) -> str:
        try:
            return await self._run("os", "status")
        except OstkError:
            return "no daemon running"

    async def os_metrics(self) -> str:
        try:
            return await self._run("os", "metrics")
        except OstkError:
            return "no metrics available"

    # --- Kernel / Agents ---

    async def kernel_ps(self) -> dict:
        """Query ostk kernel ps for active agents.

        Returns a dict with 'raw' (the CLI output string), 'daemon_running'
        (bool), and 'agents' (list of parsed agent dicts when available).
        Concurrent identical calls are coalesced into one subprocess spawn.
        """
        key = ("kernel_ps", self.cwd)

        async def _do_call() -> dict:
            try:
                raw = await self._run("kernel", "ps")
            except OstkError:
                raw = "no daemon running"

            daemon_running = "no daemon" not in raw.lower()
            agents: list[dict] = []

            if daemon_running and raw.strip():
                for line in raw.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith("─") or line.lower().startswith("name"):
                        continue
                    parts = line.split()
                    if parts:
                        agent: dict = {"name": parts[0], "source": "daemon"}
                        if len(parts) > 1:
                            agent["status"] = parts[1]
                        if len(parts) > 2:
                            agent["model"] = parts[2]
                        agents.append(agent)

            return {"raw": raw, "daemon_running": daemon_running, "agents": agents}

        shared = await _coalesce_call(key, _do_call)
        # Shallow copy: the dict has an "agents" list which we also copy
        # so a downstream mutation does not bleed across coalesced callers.
        return {**shared, "agents": list(shared.get("agents", []))}

    def _sync_audit_agents(self) -> list[dict]:
        """Sync body of audit_agents — safe to run in a thread pool."""
        audit_path = OSTK_DIR / "audit.jsonl"
        agents_by_name: dict[str, dict] = {}
        for entry in read_audit_entries(audit_path):
            event = entry.get("event", "")
            name = entry.get("name", "")

            if event == "agent.spawned" and name:
                agents_by_name[name] = {
                    "name": name,
                    "status": "spawned",
                    "model": entry.get("model", ""),
                    "budget": entry.get("budget", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "source": "audit",
                }
            elif event == "agent.completed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "completed"
                    agents_by_name[name]["completed_at"] = entry.get("timestamp", "")
            elif event == "agent.failed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "failed"
                    agents_by_name[name]["failed_at"] = entry.get("timestamp", "")
            elif event == "agent.killed" and name:
                if name in agents_by_name:
                    agents_by_name[name]["status"] = "killed"
                    agents_by_name[name]["killed_at"] = entry.get("timestamp", "")
            elif event == "session.shutdown":
                # A session ended. Any agent still marked "spawned" (i.e. no
                # completed/failed event arrived before this shutdown) should
                # be considered stopped. The process that ran them is gone.
                for agent in agents_by_name.values():
                    if agent["status"] == "spawned":
                        agent["status"] = "stopped"

        return list(agents_by_name.values())

    async def audit_agents(self) -> list[dict]:
        """Read .ostk/audit.jsonl and return agent lifecycle events.

        Delegates to _sync_audit_agents via asyncio.to_thread so the
        file I/O and JSON parsing happen off the event loop. The
        incremental tail cache in read_audit_entries means each call
        only parses new bytes appended since the last read.
        """
        return await asyncio.to_thread(self._sync_audit_agents)

    async def kernel_spawn(self, name: str, prompt: str = "", model: str = "sonnet", budget: float = 2.0) -> asyncio.subprocess.Process:
        # Pass prompt via stdin, never as a CLI argument, so it does not
        # appear in the process list (ps aux / pgrep output). Needle 342.
        args = ["ostk", "kernel", "spawn", name, "--model", model, "--budget", str(budget)]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        if prompt:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
        proc.stdin.close()
        return proc

    async def kernel_kill(self, name: str) -> dict:
        """Kill a running agent by name.

        Strategy:
        1. Use pgrep to find processes whose command line contains
           'ostk kernel spawn <name>'.
        2. Send SIGTERM to each matching process.
        3. Record an agent.killed event in the audit log.

        Returns a dict with 'killed' (bool) and 'pids' (list of killed PIDs).
        """
        # Find processes matching the spawn command for this agent name.
        # The spawn command looks like: ostk kernel spawn <name> ...
        pattern = f"ostk kernel spawn {name}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-f", pattern,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            # pgrep not available on this system
            return {"killed": False, "pids": [], "error": "pgrep not available"}

        pids_text = stdout.decode().strip()
        if not pids_text:
            return {"killed": False, "pids": [], "error": "no matching process found"}

        killed_pids = []
        for pid_str in pids_text.splitlines():
            pid_str = pid_str.strip()
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
                os.kill(pid, signal.SIGTERM)
                killed_pids.append(pid)
            except (ValueError, ProcessLookupError, PermissionError):
                continue

        if killed_pids:
            await self._record_agent_killed(name)

        return {"killed": len(killed_pids) > 0, "pids": killed_pids}

    async def _record_agent_killed(self, name: str) -> None:
        """Append an agent.killed event to audit.jsonl."""
        audit_path = OSTK_DIR / "audit.jsonl"
        entry = {
            "event": "agent.killed",
            "name": name,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "ui",
        }
        try:
            with open(audit_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    async def kernel_reap(self) -> str:
        try:
            return await self._run("kernel", "reap")
        except OstkError as e:
            return str(e)


    # --- Docs ---

    async def doc_draft(self, title: str) -> str:
        """Create a new draft document. Returns the file path.

        Refuses titles that look like hooks reviews (containing "hook"
        case-insensitive). Hooks reviews live in ~/.youros/hooks/, never
        under docs/draft/ — per feedback_hooks_at_user_scope.md.
        Fixes →1455.

        After the binary creates the file, appends the canonical body scaffold
        when the file is frontmatter-only so CLI callers (agents calling ostk
        directly, not through the API) never receive empty stubs (→2038).
        """
        if "hook" in title.lower():
            raise OstkError(
                f"Refusing to draft {title!r}: titles containing 'hook' belong "
                "in ~/.youros/hooks/, not docs/draft/. Save the file directly to "
                "~/.youros/hooks/ instead of using `ostk doc draft`."
            )
        path_str = await self._run("doc", "draft", title)
        draft_path = Path(self.cwd) / path_str.strip()
        if draft_path.exists():
            existing = draft_path.read_text()
            if "## " not in existing:
                from services.spec_templates import canonical_spec_template_body
                draft_path.write_text(existing + "\n" + canonical_spec_template_body())
        return path_str

    async def doc_promote(self, path: str) -> str:
        """Promote a draft to a spec. Returns the new file path.

        Pure-Python implementation to avoid SIGKILL issues with the
        ostk binary in some environments, and to ensure correct routing
        to USER_SPECS_DIR (~/.youros/specs/).
        """
        source = (Path(self.cwd) / path).resolve()
        if not source.exists():
            raise OstkError(f"Draft not found: {path}")

        text = source.read_text()
        file_lines = text.split("\n")

        # Validation: require at least one unchecked checkbox in the body.
        # Mirrors the CLI validation.
        body_text = text
        if file_lines and file_lines[0].strip() == "---":
            for i, line in enumerate(file_lines[1:], 1):
                if line.strip() == "---":
                    body_text = "\n".join(file_lines[i + 1 :])
                    break

        has_checkbox = any(
            line.strip().startswith("- [ ]") for line in body_text.split("\n")
        )
        if not has_checkbox:
            raise OstkError(
                "This draft needs at least one checkbox acceptance criterion "
                "(a line starting with '- [ ]') before it can be promoted."
            )

        # Assign a sequential spec_id unless the draft already has one.
        _existing_spec_id = ""
        for _line in file_lines:
            _s = _line.strip()
            if _s.startswith("spec_id:"):
                _existing_spec_id = _s[len("spec_id:"):].strip()
                break
        if not _existing_spec_id:
            _scan_dirs = [
                Path(self.cwd) / "docs" / "spec",
                USER_SPECS_DIR,
            ]
            _existing_spec_id = self._next_spec_id(_scan_dirs)

        # Flip status and add promoted_at + spec_id in front matter.
        promoted_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_lines: list[str] = []
        status_written = False
        promoted_at_written = False
        spec_id_written = False
        in_front_matter = bool(file_lines and file_lines[0].strip() == "---")

        for idx, line in enumerate(file_lines):
            stripped = line.strip()
            if in_front_matter and idx == 0:
                new_lines.append(line)
                continue
            if in_front_matter and stripped == "---" and idx > 0:
                # End of front matter: inject promoted_at / spec_id if not already present.
                if not promoted_at_written:
                    new_lines.append(f"promoted_at: {promoted_at}")
                    promoted_at_written = True
                if not spec_id_written:
                    new_lines.append(f"spec_id: {_existing_spec_id}")
                    spec_id_written = True
                in_front_matter = False
                new_lines.append(line)
                continue
            if in_front_matter and stripped.startswith("status:"):
                new_lines.append("status: spec")
                status_written = True
                continue
            if in_front_matter and stripped.startswith("promoted_at:"):
                new_lines.append(f"promoted_at: {promoted_at}")
                promoted_at_written = True
                continue
            if in_front_matter and stripped.startswith("spec_id:"):
                new_lines.append(f"spec_id: {_existing_spec_id}")
                spec_id_written = True
                continue
            new_lines.append(line)

        # No front matter (or missing status): prepend one.
        if not status_written:
            new_lines = [
                "---",
                "status: spec",
                f"promoted_at: {promoted_at}",
                f"spec_id: {_existing_spec_id}",
                "---",
            ] + new_lines

        USER_SPECS_DIR.mkdir(parents=True, exist_ok=True)
        target = USER_SPECS_DIR / source.name

        atomic_write_text(
            target, "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
        )
        source.unlink()

        try:
            await self.doc_decompose(str(target), auto=True)
        except Exception as exc:
            _logger.warning("doc_promote: decompose failed for %s: %s", target, exc)

        return str(target)

    async def doc_decompose(self, path: str, auto: bool = False) -> dict:
        """Break a spec into tasks. Returns result text and extracted task IDs.

        After ostk decomposes the spec, this method:
        1. Parses the output for created needle IDs (lines matching ->NNN)
        2. Writes those IDs back to the spec's front matter ``tasks:`` field
        3. Returns ``{"result": "...", "task_ids": ["NNN", ...]}``

        Pass ``auto=True`` to append ``--auto`` (no confirmation prompts).
        """
        args = ["doc", "decompose", path]
        if auto:
            args.append("--auto")
        result = await self._run(*args, timeout=45)

        # Extract needle IDs from output lines like "->407" or "→407 Some task title"
        # ostk may emit ASCII "->NNN" or Unicode "→NNN"; handle both.
        task_ids: list[str] = []
        for line in result.split("\n"):
            stripped = line.strip()
            match = re.match(r"(?:->|→)(\d+)", stripped)
            if match:
                task_ids.append(match.group(1))

        # Write task IDs back to spec front matter if any were created
        if task_ids:
            self._write_tasks_to_frontmatter(path, task_ids)

        return {"result": result, "task_ids": task_ids}

    @staticmethod
    def _is_orphan_plan_transcript(text: str) -> bool:
        """Return True when a plan transcript contains no real plan content.

        Orphan transcripts accumulate when planning agents are cancelled,
        complete without producing real work, or explicitly say no plan is
        needed. These should be hidden from the Specs panel.
        """
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("# TERMINATED WITHOUT WORK"):
            return True
        non_blank = [ln for ln in stripped.splitlines() if ln.strip()]
        if len(non_blank) == 1 and "(registered externally)." in non_blank[0]:
            return True
        first = ""
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("[heartbeat"):
                first = s.lower()
                break
        return "no plan needed" in first or "work is already done" in first

    async def list_docs(self) -> list[dict]:
        """Scan docs/draft and docs/spec directories for documents.

        Returns a list of dicts with path, title, status, timestamps,
        task_ids, task_summary, acceptance_criteria, and computed status
        parsed from the YAML front matter and body.

        Also scans transcripts/ for plan files matching ^plan-(\\d+)\\.md$
        and emits them with status="plan" so the Recent Documents widget
        can surface them alongside specs.
        """
        import re as _re
        from datetime import datetime, timezone as _tz

        docs_dir = Path(self.cwd) / "docs"

        # →2018 (live freeze): the frontmatter scan below globs docs/draft,
        # docs/spec and ~/.youros/specs and calls read_text()+parse on every
        # file, plus a transcripts scan. That is pure synchronous filesystem
        # I/O. Running it on the event loop blocks every other request and
        # WebSocket publish tick for the full scan duration — under the
        # dashboard's repeated /api/specs polling the loop never catches up
        # and the server wedges. Offload the whole scan to a worker thread so
        # the loop stays responsive (GIL is released during the os.stat / read
        # syscalls, so to_thread is effective). Mirrors the stage-enrichment
        # offload already added below at _spec_audit_enrich_sync.
        def _scan_docs_sync() -> list[dict]:
            scanned: list[dict] = []
            # Track by filename so a leftover draft does not double-count a
            # promoted spec that already covers the same slug.
            _seen_names: set[str] = set()

            # SECURITY (UAT item 8): specs and drafts are read ONLY from the
            # per-user ~/.youros/ store, never from the repo-local docs/draft or
            # docs/spec directories. Those repo dirs travel with any copied or
            # shared checkout, so reading them surfaced one user's specs in a
            # different user/install's app (two pclaude drafts appeared on a
            # separate work machine). Writes were already locked to ~/.youros/
            # (→1512/→2104); this locks reads to match so no spec ever leaves
            # the machine that created it.

            # 1. User-local specs (private/promoted, from ~/.youros/specs/)
            if USER_SPECS_DIR.is_dir():
                for md in sorted(USER_SPECS_DIR.glob("*.md")):
                    _seen_names.add(md.name)
                    doc = self._parse_doc_frontmatter(md, "spec")
                    doc["is_user_local"] = True
                    scanned.append(doc)

            # 2. User-local drafts (from ~/.youros/drafts/, →2104)
            if USER_DRAFTS_DIR.is_dir():
                for md in sorted(USER_DRAFTS_DIR.glob("*.md")):
                    if md.name in _seen_names:
                        continue  # a promoted spec already covers this slug
                    _seen_names.add(md.name)
                    doc = self._parse_doc_frontmatter(md, "draft")
                    doc["is_user_local"] = True
                    scanned.append(doc)

            # 3. Transcripts/plans
            # These are written by the Plan skill and should surface in Recent
            # Documents alongside specs. They use status="plan" to distinguish
            # them visually in the widget.
            _plan_re = _re.compile(r"^plan-(\d+)\.md$")
            transcripts_dir = Path(self.cwd) / "transcripts"
            if transcripts_dir.is_dir():
                for md in sorted(transcripts_dir.glob("plan-*.md")):
                    m = _plan_re.match(md.name)
                    if not m:
                        continue
                    needle_id = m.group(1)
                    try:
                        text = md.read_text(errors="replace")
                        mtime = md.stat().st_mtime
                        mtime_ms = int(mtime * 1000)
                        created_at = datetime.fromtimestamp(mtime, tz=_tz.utc).isoformat()
                    except OSError:
                        continue
                    if self._is_orphan_plan_transcript(text):
                        continue
                    # Derive title from the first non-blank, non-heartbeat line.
                    title = f"Plan for →{needle_id}"
                    for line in text.split("\n"):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("[heartbeat"):
                            candidate = stripped.lstrip("#").lstrip("*").rstrip("*").strip()
                            if candidate and candidate != "---":
                                title = candidate[:80]
                                break
                    scanned.append({
                        "path": f"transcripts/{md.name}",
                        "filename": md.name,
                        "title": title,
                        "status": "plan",
                        "created_at": created_at,
                        "promoted_at": "",
                        "updated_at_ms": mtime_ms,
                        "body": text[:2000],
                        "task_ids": [needle_id],
                        "acceptance_criteria": [],
                    })
            return scanned

        results: list[dict] = await asyncio.to_thread(_scan_docs_sync)

        # Collect all task IDs referenced by any spec. Spec front matter
        # stores bare numeric IDs ("407") but ostk returns IDs prefixed with
        # an arrow ("→407"). Normalize both sides so the lookup matches.
        all_task_ids: set[str] = set()
        for doc in results:
            all_task_ids.update(
                self._normalize_task_id(t) for t in doc.get("task_ids", [])
            )

        # Fetch task statuses in one batch if any specs reference tasks
        task_status_map: dict[str, str] = {}
        if all_task_ids:
            try:
                all_tasks = await self.list_tasks()
                for t in all_tasks:
                    tid = self._normalize_task_id(t.get("id"))
                    if tid and tid in all_task_ids:
                        task_status_map[tid] = t.get("status", "open")
            except OstkError:
                pass  # graceful degradation if ostk is unavailable

        # Enrich each doc with task_summary and computed status
        for doc in results:
            raw_ids = doc.get("task_ids", [])
            norm_ids = [self._normalize_task_id(t) for t in raw_ids]
            total = len(norm_ids)
            closed = sum(
                1 for tid in norm_ids
                if task_status_map.get(tid, "open") == "closed"
            )
            doc["task_summary"] = {
                "total": total,
                "open": total - closed,
                "closed": closed,
            }
            # Include acceptance-criteria progress in the computation so a
            # spec with all tasks closed flips to "complete" once Verify has
            # checked every box. Without an explicit Verify run, ACs start
            # unchecked and the spec stays "in-progress" so the Verify step
            # is still required in the happy path.
            ac = doc.get("acceptance_criteria", [])
            ac_all_met = bool(ac) and all(c.get("checked") for c in ac)
            prior_status = doc["status"]
            doc["status"] = self.compute_spec_status(
                prior_status, norm_ids, task_status_map, ac_all_met=ac_all_met
            )
            # Persist the transition to "complete" so the spec auto-closes
            # on disk. Without this writeback the frontmatter still says
            # "building"/"spec" and the spec never durably closes. (→1698)
            if doc["status"] == "complete" and prior_status not in ("complete", "done"):
                doc_path_str = doc.get("path", "")
                if doc_path_str:
                    try:
                        if doc_path_str.startswith("/") or doc_path_str.startswith("~"):
                            _spec_path = Path(doc_path_str).expanduser()
                        else:
                            _spec_path = Path(self.cwd) / doc_path_str
                        self._write_status_to_frontmatter(_spec_path, "complete")
                    except Exception:
                        pass  # best effort; stale state better than a 500

        # Stage enrichment (→1512 / →1739): compute shipped/husk/stage per doc.
        # compute_shipped() and compute_husk_status() do synchronous filesystem I/O
        # (Path.read_text + Path.exists per file ref).  Running them on the event
        # loop would block all other requests for the full scan duration.  Offload
        # to a worker thread so the loop stays responsive.  GIL is released during
        # the actual os.stat / read syscalls so to_thread is effective here.
        repo_root = Path(self.cwd)
        await asyncio.to_thread(
            _spec_audit_enrich_sync, results, repo_root, task_status_map
        )

        # (Removed UAT item 8) The former docs/draft husk-dedup pass is gone:
        # reads are now locked to ~/.youros/specs and ~/.youros/drafts only, so no
        # result ever carries a docs/draft/ path, and the per-slug collision
        # between a promoted spec and a same-named draft is already handled by
        # the _seen_names skip in _scan_docs_sync above.
        return results

    @staticmethod
    def _normalize_task_id(tid: Optional[str]) -> str:
        """Strip the ``→`` / ``->`` prefix so IDs compare cleanly.

        Spec front matter stores bare numeric IDs ("407") while ostk's
        ``work list --json`` emits the arrow-prefixed form ("→407"). Both
        map to the same task; without this normalization the spec status
        never flipped to Complete because the lookup always missed.
        """
        if not tid:
            return ""
        s = str(tid).strip()
        if s.startswith("→"):
            return s[1:]
        if s.startswith("->"):
            return s[2:]
        return s

    def _parse_doc_frontmatter(self, path: Path, fallback_status: str) -> dict:
        """Read YAML front matter from a markdown file.

        Returns a dict with the standard fields plus Phase 2 additions:
        - ``task_ids``: list of needle IDs from the ``tasks:`` front matter
        - ``acceptance_criteria``: list of ``{text, checked}`` dicts parsed
          from ``- [ ]`` / ``- [x]`` checkboxes in the body
        """
        text = path.read_text()
        # File mtime doubles as a "last-updated" stamp. Used by the
        # ReleaseNotesWatcher grace window so a spec that flipped to
        # complete moments before a page reload still fires the modal
        # even when the watcher mounts after the transition.
        try:
            mtime_ms = int(path.stat().st_mtime * 1000)
        except OSError:
            mtime_ms = 0

        # Handle path relative to cwd if inside project, otherwise use absolute path
        try:
            display_path = str(path.relative_to(self.cwd))
        except ValueError:
            display_path = str(path)

        doc: dict = {
            "path": display_path,
            "filename": path.name,
            "title": path.stem.replace("-", " "),
            "status": fallback_status,
            "created_at": "",
            "promoted_at": "",
            "spec_id": "",
            "updated_at_ms": mtime_ms,
            "body": "",
            "task_ids": [],
            "acceptance_criteria": [],
        }

        lines = text.split("\n")
        if lines and lines[0].strip() == "---":
            end = None
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "---":
                    end = i
                    break
            if end:
                # Track whether we are inside the tasks: YAML list
                in_tasks_list = False
                for line in lines[1:end]:
                    stripped = line.strip()
                    # Detect YAML list items under tasks:
                    if in_tasks_list:
                        if stripped.startswith("- "):
                            val = stripped[2:].strip().strip('"').strip("'")
                            if val:
                                doc["task_ids"].append(val)
                            continue
                        else:
                            in_tasks_list = False
                    if ":" in line:
                        key, _, val = line.partition(":")
                        key = key.strip()
                        val = val.strip()
                        if key == "title":
                            doc["title"] = val
                        elif key == "status":
                            doc["status"] = val
                        elif key == "no_ac_needed":
                            doc["no_ac_needed"] = (val.lower() == "true")
                        elif key == "created_at":
                            doc["created_at"] = val
                        elif key == "promoted_at":
                            doc["promoted_at"] = val
                        elif key == "spec_id":
                            doc["spec_id"] = val
                        elif key == "tasks":
                            # Inline format: tasks: ["407", "408"]
                            if val.startswith("["):
                                import ast
                                try:
                                    parsed = ast.literal_eval(val)
                                    doc["task_ids"] = [str(t) for t in parsed]
                                except (ValueError, SyntaxError):
                                    pass
                            else:
                                # Block format: subsequent lines are list items
                                in_tasks_list = True
                # Body is everything after the front matter
                doc["body"] = "\n".join(lines[end + 1:]).strip()
        else:
            doc["body"] = text.strip()

        # Parse acceptance criteria from markdown checkboxes in the body
        doc["acceptance_criteria"] = self._parse_acceptance_criteria(doc["body"])

        return doc

    @staticmethod
    def _parse_acceptance_criteria(body: str) -> list[dict]:
        """Extract ``- [ ]`` and ``- [x]`` checkboxes from markdown body."""
        criteria: list[dict] = []
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
                text = stripped[5:].strip()
                criteria.append({"text": text, "checked": True})
            elif stripped.startswith("- [ ]"):
                text = stripped[5:].strip()
                criteria.append({"text": text, "checked": False})
        return criteria

    @staticmethod
    def _next_spec_id(scan_dirs: "list[Path]") -> str:
        """Return the next sequential spec ID (S001, S002, ...).

        Scans all *.md files in scan_dirs for ``spec_id: SXXX`` frontmatter
        lines and returns the next unused ID.
        """
        import re as _re
        _id_re = _re.compile(r"^spec_id:\s*S(\d+)", _re.MULTILINE)
        max_num = 0
        for d in scan_dirs:
            if not d.is_dir():
                continue
            for md in d.glob("*.md"):
                try:
                    text = md.read_text(errors="replace")
                    for m in _id_re.finditer(text):
                        max_num = max(max_num, int(m.group(1)))
                except OSError:
                    pass
        return f"S{max_num + 1:03d}"

    @staticmethod
    def resolve_spec_by_id(spec_id: str, scan_dirs: "list[Path]") -> "Path | None":
        """Return the Path of the spec file bearing spec_id, or None."""
        import re as _re
        pattern = _re.compile(
            r"^spec_id:\s*" + _re.escape(spec_id) + r"\s*$", _re.MULTILINE
        )
        for d in scan_dirs:
            if not d.is_dir():
                continue
            for md in d.glob("*.md"):
                try:
                    if pattern.search(md.read_text(errors="replace")):
                        return md
                except OSError:
                    pass
        return None

    def _write_status_to_frontmatter(self, path: Path, status: str) -> None:
        """Persist a new status value into a spec's YAML frontmatter.

        Rewrites the ``status:`` line in place.  If no ``status:`` field
        exists, appends one.  No-ops when the file is missing or has no
        frontmatter block.
        """
        if not path.exists():
            return
        text = path.read_text()
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            return
        end = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i
                break
        if end is None:
            return

        # Rewrite or append status: inside the frontmatter block
        fm_lines = lines[1:end]
        replaced = False
        for idx, line in enumerate(fm_lines):
            if line.strip().startswith("status:"):
                fm_lines[idx] = f"status: {status}"
                replaced = True
                break
        if not replaced:
            fm_lines.append(f"status: {status}")

        new_lines = ["---"] + fm_lines + ["---"] + lines[end + 1:]
        path.write_text("\n".join(new_lines))

    def _write_tasks_to_frontmatter(self, spec_path: str, task_ids: list[str]) -> None:
        """Write task IDs into a spec's YAML front matter ``tasks:`` field.

        Merges with any existing task IDs to avoid duplicates.
        """
        full_path = Path(self.cwd) / spec_path
        if not full_path.exists():
            return

        text = full_path.read_text()
        lines = text.split("\n")

        if not (lines and lines[0].strip() == "---"):
            return

        end = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i
                break
        if end is None:
            return

        # Parse existing task IDs from front matter
        existing_ids: list[str] = []
        fm_lines = lines[1:end]
        tasks_start = None
        tasks_end = None
        in_tasks = False
        for idx, line in enumerate(fm_lines):
            stripped = line.strip()
            if in_tasks:
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip('"').strip("'")
                    if val:
                        existing_ids.append(val)
                    tasks_end = idx + 1
                    continue
                else:
                    in_tasks = False
            if stripped.startswith("tasks:"):
                _, _, val = stripped.partition(":")
                val = val.strip()
                tasks_start = idx
                tasks_end = idx + 1
                if val.startswith("["):
                    import ast
                    try:
                        parsed = ast.literal_eval(val)
                        existing_ids = [str(t) for t in parsed]
                    except (ValueError, SyntaxError):
                        pass
                elif not val:
                    in_tasks = True

        # Merge: add new IDs that are not already present
        merged = list(existing_ids)
        for tid in task_ids:
            if tid not in merged:
                merged.append(tid)

        # Build the tasks: YAML block
        tasks_yaml_lines = ["tasks:"]
        for tid in merged:
            tasks_yaml_lines.append(f'  - "{tid}"')

        # Rebuild front matter
        if tasks_start is not None:
            new_fm = fm_lines[:tasks_start] + tasks_yaml_lines + fm_lines[tasks_end:]
        else:
            new_fm = fm_lines + tasks_yaml_lines

        new_lines = ["---"] + new_fm + ["---"] + lines[end + 1:]
        full_path.write_text("\n".join(new_lines))

    def _resolve_spec_path(self, spec_path: str) -> Path:
        """Resolve a spec path to a full filesystem path, with validation."""
        if spec_path.startswith("/") or spec_path.startswith("~"):
            full = Path(os.path.expanduser(spec_path))
        else:
            full = Path(self.cwd) / spec_path

        if not full.exists():
            raise OstkError(f"Spec not found: {spec_path}")

        docs_dir = Path(self.cwd) / "docs"
        resolved = full.resolve()

        # Allow project-local docs or user-local specs
        is_in_docs = str(resolved).startswith(str(docs_dir.resolve()))
        is_in_user_specs = str(resolved).startswith(str(USER_SPECS_DIR.resolve()))

        if not (is_in_docs or is_in_user_specs):
            raise OstkError("Spec path must be under docs/ or ~/.youros/specs/")
        return full

    async def spec_tasks(self, spec_path: str) -> list[dict]:
        """Read a spec's linked tasks and return their current status.

        Returns a list of dicts: ``[{id, title, status, priority}, ...]``
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        task_ids = doc.get("task_ids", [])
        if not task_ids:
            return []

        # Fetch all tasks and filter to the ones linked to this spec.
        # IDs on both sides may or may not carry the arrow prefix, so
        # normalize before comparing.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        matched: list[dict] = []
        for t in all_tasks:
            if self._normalize_task_id(t.get("id")) in id_set:
                matched.append({
                    "id": t["id"],
                    "title": t.get("title", ""),
                    "status": t.get("status", "open"),
                    "priority": t.get("priority", "P1"),
                })
        return matched

    async def spec_verify(self, spec_path: str) -> dict:
        """Verify a spec's acceptance criteria against linked task status.

        Returns::

            {
                "criteria": [{"text": "...", "met": bool}, ...],
                "all_met": bool,
                "task_summary": {"total": N, "open": N, "closed": N}
            }
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        ac = doc.get("acceptance_criteria", [])
        task_ids = doc.get("task_ids", [])

        # Get task statuses. Normalize IDs so arrow-prefixed tasks returned
        # by ostk match the bare numeric IDs stored in the spec front matter.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        linked_tasks = [
            t for t in all_tasks
            if self._normalize_task_id(t.get("id")) in id_set
        ]

        total = len(linked_tasks)
        closed = sum(1 for t in linked_tasks if t.get("status") == "closed")
        open_count = total - closed

        # Acceptance criteria are treated as met when either the checkbox is
        # already checked in the spec body, OR every linked task is closed
        # and there are tasks to begin with. This mirrors a real Verify run:
        # once the agents finish every task, the checklist is considered
        # satisfied. We also persist the checkmarks back to the spec body
        # so subsequent list_docs calls see the spec as complete.
        all_tasks_closed = total > 0 and closed == total
        criteria = [
            {
                "text": c["text"],
                "met": c["checked"] or all_tasks_closed,
            }
            for c in ac
        ]
        all_met = bool(criteria) and all(c["met"] for c in criteria)

        # If Verify confirms every box, write the checkmarks back to disk so
        # the spec's computed status flips to complete on the next list.
        if all_met and not all(c["checked"] for c in ac):
            try:
                self._check_all_acceptance_criteria(spec_path)
            except Exception:
                pass  # best effort; stale state is better than a 500

        return {
            "criteria": criteria,
            "all_met": all_met,
            "task_summary": {"total": total, "open": open_count, "closed": closed},
        }

    def _check_all_acceptance_criteria(self, spec_path: str) -> None:
        """Flip every ``- [ ]`` line in a spec body to ``- [x]``.

        Called when Verify decides every criterion is met. Keeps the spec
        file, the Verify response, and the derived Complete status aligned.
        """
        full = Path(self.cwd) / spec_path
        if not full.exists():
            return
        text = full.read_text()
        new_lines: list[str] = []
        for line in text.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("- [ ]"):
                indent = line[: len(line) - len(stripped)]
                new_lines.append(indent + "- [x]" + stripped[5:])
            else:
                new_lines.append(line)
        full.write_text("\n".join(new_lines))

    async def spec_build(self, spec_path: str) -> dict:
        """Build agent configs for a spec's open tasks.

        Returns agent configurations without actually spawning them.
        The frontend (Phase 3) handles the actual spawning.

        Returns::

            {"agents": [{"name": "...", "task_id": "...", "prompt": "..."}, ...]}
        """
        full = self._resolve_spec_path(spec_path)
        doc = self._parse_doc_frontmatter(full, "spec")
        spec_name = Path(spec_path).stem
        spec_text = doc.get("body", "")
        task_ids = doc.get("task_ids", [])

        if not task_ids:
            return {"agents": []}

        # Get open tasks only. Normalize so arrow-prefixed IDs match.
        all_tasks = await self.list_tasks()
        id_set = {self._normalize_task_id(tid) for tid in task_ids}
        open_tasks = [
            t for t in all_tasks
            if self._normalize_task_id(t.get("id")) in id_set
            and t.get("status") != "closed"
        ]

        agents: list[dict] = []
        for task in open_tasks:
            task_id = task["id"]
            task_title = task.get("title", "")
            agent_name = f"spec-{spec_name}-{task_id}"
            prompt = (
                f"You are building task {task_id}: {task_title}\n\n"
                f"## Spec\n\n{spec_text}\n\n"
                f"## Instructions\n\n"
                f"- Complete the task described above.\n"
                f"- Edit files directly. Do NOT run `ostk commit` or "
                f"`ostk work close` — those calls can lag under load and "
                f"will blow the demo timeout. The spec router closes the "
                f"task for you via HTTP when you finish.\n"
            )
            agents.append({
                "name": agent_name,
                "task_id": task_id,
                "task_title": task_title,
                "prompt": prompt,
            })

        return {"agents": agents}

    @staticmethod
    def compute_spec_status(
        base_status: str,
        task_ids: list[str],
        task_statuses: dict[str, str],
        ac_all_met: bool = False,
        claims: Optional[list[dict]] = None,
    ) -> str:
        """Derive a spec's lifecycle status from its tasks and active claims.

        Status rules (in priority order):

        - ``draft``, ``plan``: frontmatter-driven, never overridden.
        - ``ready``: promoted (status=spec), no tasks AND no active claims.
        - ``in-progress``: any open task OR any active claim.
        - ``complete``: tasks exist, all closed, and no active claims remain.

        A claim is considered **active** when at least one of its
        ``task_ids`` is still open.  A claim with an empty ``task_ids``
        list is treated as active only when the spec itself has no closed
        tasks yet (it was registered before decomposition); once all spec-
        level tasks are closed the empty-list claim auto-releases.

        Claim auto-release is computed here on every call rather than
        requiring a separate cleanup step.  There is no heartbeat timer.

        ``ac_all_met`` short-circuits to ``complete`` when every
        acceptance criterion is checked and no build agent is actively
        working, so a shipped spec whose tasks were never formally
        closed still reports ``complete`` (→2148).
        """
        if base_status in ("draft", "plan"):
            return base_status

        # Determine whether any spec-level tasks are still open.
        statuses = [task_statuses.get(tid, "open") for tid in task_ids]
        all_tasks_closed = bool(task_ids) and all(s == "closed" for s in statuses)
        any_task_open = any(s != "closed" for s in statuses)

        # Determine whether any claims are still active.
        # A claim is active when at least one of its listed task_ids is
        # not yet closed.  An empty task_ids list is active only when
        # the spec still has open tasks (or no tasks at all) — once
        # every spec-level task is closed, empty-list claims auto-release.
        active_claims: list[dict] = []
        for claim in (claims or []):
            claim_tids = claim.get("task_ids") or []
            if not claim_tids:
                # Empty task_ids: active only while the spec has work left.
                if not all_tasks_closed:
                    active_claims.append(claim)
            else:
                # Has task_ids: active while any of them is still open.
                if any(
                    task_statuses.get(tid, "open") != "closed"
                    for tid in claim_tids
                ):
                    active_claims.append(claim)

        has_active_claim = bool(active_claims)

        # All acceptance criteria confirmed by the verifier → the spec is complete
        # regardless of stale open tasks, as long as no build agent is actively
        # working right now. This handles the case where a spec's feature shipped
        # but its tasks were never formally closed (→2148).
        if ac_all_met and not has_active_claim:
            return "complete"

        # "building" means implementation has started (set by build_spec or
        # by spawn_agent when spec_id is provided). Treat it as in-progress
        # even when no tasks are linked yet, to avoid flickering back to
        # the "ready" badge during the narrow window between the status
        # write and the task-frontmatter write. (→1420)
        if base_status == "building":
            if all_tasks_closed and not has_active_claim:
                return "complete"
            return "in-progress"
        if not task_ids:
            # No tasks linked yet.
            if base_status in ("complete", "done"):
                return "complete"
            if has_active_claim:
                return "in-progress"
            return "ready"

        # CRITICAL: if the frontmatter says ``complete`` or ``done`` but
        # at least one linked task is still open, trust the tasks. This
        # catches the failure mode where the auto-advancer wrote
        # ``status: complete`` at an instant when a concurrent-close
        # race had made every task briefly appear closed, and a later
        # write to issues.jsonl clobbered one of the closes back to
        # open. Before this check the Specs page banner kept claiming
        # "Done. Every task closed and the feature is live." with a
        # still-open task sitting right above it.
        if base_status in ("complete", "done"):
            # Absent task IDs mean the task was purged/archived — treat as closed.
            # Only tasks that are explicitly present in the store with a non-closed
            # status keep this spec in-progress.
            any_present_open = any(
                task_statuses.get(tid) is not None and task_statuses[tid] != "closed"
                for tid in task_ids
            )
            if not any_present_open and not has_active_claim:
                return "complete"
            return "in-progress"

        if all_tasks_closed and not has_active_claim:
            return "complete"

        # A spec enters "in-progress" only when at least one task has been
        # actively started (status is neither "open"/unstarted nor "closed"/done).
        # Tasks that exist but haven't been touched keep the spec in "ready".
        any_task_started = any(s not in ("open", "closed") for s in statuses)
        if any_task_started or has_active_claim:
            return "in-progress"
        return "ready"

    # --- Threads ---

    async def create_thread(self, name: str, needle_ids: Optional[list[str]] = None) -> str:
        """Create a thread grouping needles via the ostk CLI."""
        args = ["thread", "create", name]
        if needle_ids:
            args += ["--needles"] + needle_ids
            args += ["--force"]  # allow missing/closed needles
        return await self._run(*args)

    async def list_threads(self) -> str:
        """List all threads via the ostk CLI."""
        return await self._run("thread", "list")

    # --- MCP Servers ---

    async def mcp_list(self) -> list[dict]:
        """List MCP servers configured in ostk (via HUMANFILE).

        Parses the plain-text output of ``ostk mcp list`` into a list of
        dicts with ``name`` and ``command`` keys.  Returns an empty list
        when no servers are configured.
        """
        try:
            raw = await self._run("mcp", "list")
        except OstkError:
            return []

        if "no mcp servers configured" in raw.lower():
            return []

        return self._parse_mcp_list(raw)

    @staticmethod
    def _parse_mcp_list(raw: str) -> list[dict]:
        """Parse the plain-text output of ``ostk mcp list``.

        Expected format (one server per line)::

            name    command
            ────    ───────
            linear  npx -y @anthropic/linear-mcp-server
            github  gh mcp-server

        Also handles a simpler ``name: command`` format.
        """
        servers: list[dict] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.lower().startswith("name"):
                continue
            # Try tab/multi-space separated: "linear  npx -y ..."
            parts = re.split(r"\s{2,}|\t", line, maxsplit=1)
            if len(parts) == 2:
                servers.append({"name": parts[0].strip(), "command": parts[1].strip()})
                continue
            # Try "name: command" format
            if ":" in line:
                name, _, command = line.partition(":")
                name = name.strip()
                command = command.strip()
                if name and command:
                    servers.append({"name": name, "command": command})
        return servers

    # --- Nudges ---

    async def purge_agent_chat_state(self, agent_name: str) -> dict:
        """Delete every on-disk chat artifact for ``agent_name``.

        Called at the top of a fresh spawn so a second Roadmap (or any
        template) run never sees the previous run's nudges, replies, or
        transcript. Without this purge the UI's inline chat panel merges
        stale file-based entries from ``.ostk/nudges/{name}/`` and
        ``.ostk/nudges/{name}/replies/`` with the new agent's state and
        the operator sees messages that do not belong to the new run.

        Removes:
          * ``.ostk/nudges/{agent_name}/`` (user messages)
          * ``.ostk/nudges/{agent_name}/replies/`` (agent replies)
          * ``transcripts/{agent_name}.md`` (legacy markdown transcript)

        Safe to call when the directories do not exist (no-op). Errors
        on individual files are swallowed so one bad file does not
        block the spawn. Returns a small dict with the counts of
        artifacts removed so callers and tests can assert the purge
        actually fired.
        """
        import shutil

        counts = {"nudges": 0, "replies": 0, "transcripts": 0}

        # 1. Nudges + replies directory. A single rmtree on the parent
        #    takes both in one shot because replies is nested under it.
        nudges_dir = NUDGES_DIR / agent_name
        if nudges_dir.exists():
            try:
                nudge_files = list(nudges_dir.glob("*.json"))
                counts["nudges"] = len(nudge_files)
                replies_dir = nudges_dir / "replies"
                if replies_dir.exists():
                    counts["replies"] = len(list(replies_dir.glob("*.json")))
                shutil.rmtree(nudges_dir)
            except OSError:
                # Swallow: a partially broken dir should not block a
                # fresh spawn. The UI will still get a clean chat as
                # long as at least the new nudges land on top.
                pass

        # 2. Legacy markdown transcript. The spawn path reopens this
        #    with ``open(..., "w")`` which truncates, but we unlink
        #    first so a stale stub from a crashed prior run cannot
        #    briefly surface before the new output lands.
        transcript_path = PROJECT_ROOT / "transcripts" / f"{agent_name}.md"
        if transcript_path.exists():
            try:
                transcript_path.unlink()
                counts["transcripts"] = 1
            except OSError:
                pass

        return counts

    async def write_nudge(
        self,
        agent_name: str,
        message: str,
        kind: Optional[str] = None,
    ) -> dict:
        """Write a nudge file to .ostk/nudges/{agent_name}/.

        Nudges are the file-based messaging system for communicating with
        running agents. Each nudge is a JSON file with a timestamp-based
        name so multiple nudges can queue up.

        ``kind`` tags the nudge so the agent's mailbox poller and the
        inline chat UI can distinguish a plain follow-up message
        (``user_message``) from a course-correcting instruction
        (``correction``). When omitted, the field is left absent on
        disk for backward compatibility with older readers.
        """
        nudges_dir = NUDGES_DIR / agent_name
        nudges_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc)
        filename = f"{ts.strftime('%Y%m%dT%H%M%S')}_{int(ts.timestamp() * 1000) % 1000:03d}.json"
        nudge_path = nudges_dir / filename

        nudge_data: dict = {
            "agent": agent_name,
            "message": message,
            "timestamp": ts.isoformat(),
            "source": "ui",
        }
        if kind:
            nudge_data["kind"] = kind

        atomic_write_text(nudge_path, json.dumps(nudge_data, indent=2) + "\n")
        return nudge_data

    async def list_nudges(self, agent_name: str) -> list[dict]:
        """Read all nudge files for an agent, sorted oldest first."""
        nudges_dir = NUDGES_DIR / agent_name
        if not nudges_dir.exists():
            return []

        nudges = []
        for f in sorted(nudges_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                data["file"] = f.name
                nudges.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return nudges

    async def append_nudge_reply(
        self,
        agent_name: str,
        message: str,
        in_reply_to: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> dict:
        """Write an agent's reply to a prior nudge under .ostk/nudges/{agent}/replies/.

        Replies are stored as their own JSON files so any observer that
        polls the nudge directory also picks up new replies without
        schema changes. ``in_reply_to`` is an optional timestamp that
        correlates the reply to a specific user message, so the UI can
        thread them. When omitted, the reply is treated as a free form
        status update from the agent.

        ``kind`` lets the writer mark the reply so the inline chat UI
        can distinguish a warm ack bot reply (``ack``) from a real
        substantive subagent reply (``real``). Real subagent replies
        coming through POST /reply default to ``real`` so the UI can
        always tell the two apart, even when the field is absent on
        older records.

        Write-time dedupe
        -----------------
        If an identical ``(in_reply_to, message, kind)`` record was
        written within the last 30 seconds, the new write is skipped and
        the existing record is returned unchanged. This is the last line
        of defense for the inline chat dupe bug Tori hit: even when the
        process-local acked-id set in ``chat_ack_bot`` gets wiped (a
        restart, a backend reload during development, a reload watchdog
        replay), the on-disk store itself refuses to accept a second
        copy of the same reply. Callers never see an error: they get
        the first record, so the UI shows one bubble per ack.
        """
        replies_dir = NUDGES_DIR / agent_name / "replies"
        replies_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc)

        # Write-time dedupe. Scan the last few reply files. If one of
        # them has the same ``(in_reply_to, message, kind)`` fingerprint
        # AND was written within the last 30 seconds, treat this call
        # as a duplicate and return the existing record. The 30 second
        # window covers the practical failure modes (ack bot restart,
        # rapid double-ack race) without ever folding two genuinely
        # distinct replies that happen to repeat the same text hours
        # apart. We only walk the tail of the sorted list so this scan
        # stays O(1) amortised even when the replies dir has thousands
        # of entries.
        try:
            recent = sorted(replies_dir.glob("*.json"))[-16:]
            cutoff = ts.timestamp() - 30.0
            for existing_path in recent:
                try:
                    existing = json.loads(existing_path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                existing_kind = existing.get("kind") if kind else None
                if (
                    existing.get("message") == message
                    and existing.get("in_reply_to") == in_reply_to
                    and existing_kind == kind
                ):
                    existing_ts_raw = str(existing.get("timestamp", ""))
                    try:
                        existing_dt = datetime.fromisoformat(existing_ts_raw)
                    except ValueError:
                        continue
                    if existing_dt.timestamp() >= cutoff:
                        existing["file"] = existing_path.name
                        return existing
        except OSError:
            # A broken replies dir should not block a fresh write. Fall
            # through to the normal path so the caller's message still
            # lands on disk.
            pass

        # Use microsecond precision in the filename so two replies written
        # back to back with different identities never collide and silently
        # overwrite each other. Millisecond precision was not enough: the
        # ack bot's burst path (one ack per nudge within a few microseconds)
        # and the dedupe test case both exposed a collision where the
        # second write landed on the same filename as the first.
        filename = (
            f"{ts.strftime('%Y%m%dT%H%M%S')}_"
            f"{ts.microsecond:06d}.json"
        )
        reply_path = replies_dir / filename

        reply_data: dict = {
            "agent": agent_name,
            "message": message,
            "timestamp": ts.isoformat(),
            "source": "agent",
            "in_reply_to": in_reply_to,
        }
        if kind:
            reply_data["kind"] = kind

        atomic_write_text(reply_path, json.dumps(reply_data, indent=2) + "\n")
        return reply_data

    async def list_nudge_replies(self, agent_name: str) -> list[dict]:
        """Read all reply files for an agent, sorted oldest first."""
        replies_dir = NUDGES_DIR / agent_name / "replies"
        if not replies_dir.exists():
            return []

        replies = []
        for f in sorted(replies_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                data["file"] = f.name
                replies.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return replies

    # --- Grants / Permission Requests ---

    async def list_grants(self, status: str = "pending") -> list[dict]:
        """List agent permission requests filtered by status.

        Calls ``ostk grant list --status <status> --json`` and returns
        the parsed JSON list.  When ostk reports 'no <status> requests'
        (i.e. empty results), an empty list is returned.
        """
        try:
            return await self._run_json("grant", "list", "--status", status, "--json")
        except OstkError as e:
            # ostk prints 'no pending requests' (non-JSON) when the list
            # is empty, which causes _run to raise OstkError.
            if "no" in str(e).lower() and "requests" in str(e).lower():
                return []
            raise
        except json.JSONDecodeError:
            return []

    async def approve_grant(self, grant_id: str, ttl: int = 0, scope: Optional[str] = None) -> str:
        """Approve a permission request by ID.

        ``ttl`` is lifetime in seconds (0 = no expiry).
        ``scope`` overrides the default target scope if provided.
        """
        args = ["grant", "approve", grant_id, "--ttl", str(ttl)]
        if scope:
            args += ["--scope", scope]
        return await self._run(*args)

    async def deny_grant(self, grant_id: str, reason: str = "not permitted") -> str:
        """Deny a permission request by ID with an optional reason."""
        return await self._run("grant", "deny", grant_id, "--reason", reason)

    # --- Secrets ---

    # --- Decisions ---

    async def log_decision(self, key: str, value: str, reason: str = "", visibility: str = None) -> str:
        """Log a decision via ``ostk decide <key> <value> --reason <reason>``.

        Appends an entry to .ostk/decisions.jsonl with the key, value,
        reason, and a timestamp.
        """
        args = ["decide", key, value]
        if reason:
            args += ["--reason", reason]
        result = await self._run(*args)
        if visibility is not None:
            import json as _json
            from pathlib import Path as _Path
            decisions_path = _Path(self.cwd) / ".ostk" / "decisions.jsonl"
            if decisions_path.exists():
                lines = decisions_path.read_text().splitlines()
                if lines:
                    try:
                        last = _json.loads(lines[-1])
                        last["visibility"] = visibility
                        lines[-1] = _json.dumps(last)
                        decisions_path.write_text("\n".join(lines) + "\n")
                    except (_json.JSONDecodeError, OSError):
                        pass
        return result

    def list_decisions(self) -> list[dict]:
        """Read .ostk/decisions.jsonl and return entries as a list of dicts.

        Each entry has key, value, reason, and timestamp fields.
        Returns newest first.
        """
        decisions_path = Path(self.cwd) / ".ostk" / "decisions.jsonl"
        if not decisions_path.exists():
            return []
        entries: list[dict] = []
        try:
            text = decisions_path.read_text()
        except OSError:
            return []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        entries.reverse()  # newest first
        return entries

    async def get_activity_summary(self) -> str:
        """Build a plain-text summary of the last 24 hours using ostk data.

        Combines ostk os history (recent events) and ostk os diff (session
        delta) into a short text block. This runs locally without any LLM
        call, so it is fast and free.

        Returns the summary text, or empty string if nothing is available.
        """
        parts: list[str] = []

        # Recent history (last 50 events)
        try:
            events = await self.get_history(last=50)
            if events:
                # Count events by type
                counts: dict[str, int] = {}
                for ev in events:
                    event_type = ev.get("event", "unknown")
                    counts[event_type] = counts.get(event_type, 0) + 1

                summary_lines = []
                for etype, count in sorted(counts.items(), key=lambda x: -x[1]):
                    summary_lines.append(f"  {count}x {etype}")
                parts.append("Recent activity:\n" + "\n".join(summary_lines))
        except OstkError:
            pass

        # Session diff
        try:
            diff = await self.get_session_diff()
            diff_lines = []
            if diff.get("files_changed"):
                diff_lines.append(f"  {len(diff['files_changed'])} files changed this session")
            if diff.get("needles_filed"):
                diff_lines.append(f"  {len(diff['needles_filed'])} tasks filed this session")
            if diff.get("audit_total"):
                diff_lines.append(f"  {diff['audit_total']} total audit events")
            if diff_lines:
                parts.append("Session summary:\n" + "\n".join(diff_lines))
        except OstkError:
            pass

        return "\n\n".join(parts)

    async def secret_set(self, key: str, value: str) -> str:
        """Store a secret in the system keychain via ostk."""
        return await self._run("secret", "set", key, "--value", value)

    async def secret_get(self, key: str) -> str:
        """Retrieve a secret from the system keychain.

        Returns the secret value, or an empty string if the key is not found.
        """
        try:
            return await self._run("secret", "get", key)
        except OstkError:
            return ""

    async def secret_list(self) -> list[dict]:
        """List available secret names with their status.

        Returns a list of dicts with 'name', 'tag', and 'available' fields.
        """
        try:
            raw = await self._run("secret", "list")
        except OstkError:
            return []

        secrets: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "secret list" in line.lower() or "/" in line and "keys" in line:
                continue
            # Parse lines like:
            #   + ANTHROPIC_API_KEY              [anthropic] available
            #   - GEMINI_API_KEY                 [google] missing
            available = line.startswith("+")
            # Strip the leading + or - indicator
            cleaned = line.lstrip("+-").strip()
            parts = cleaned.split()
            if not parts:
                continue
            name = parts[0]
            tag = ""
            for part in parts:
                if part.startswith("[") and part.endswith("]"):
                    tag = part.strip("[]")
                    break
            secrets.append({
                "name": name,
                "available": available,
                "tag": tag,
            })
        return secrets

    # --- Agent Correction (needle 333) ---

    async def correct_agent(self, agent_name: str, message: str) -> str:
        """Send a structured correction to an agent via ostk work correct.

        This calls the ostk :correct verb which records the correction in
        the audit trail and delivers it to the agent. The caller should
        also send a regular nudge so both systems stay in sync.
        """
        return await self._run("work", "correct", agent_name, message, timeout=10)

    # --- Agent Lifecycle / Context Pressure (needle 337) ---

    async def agent_lifecycle(self, agent_name: str) -> dict | None:
        """Query the lifecycle service for an agent.

        The :lifecycle service may be inactive. If it fails, returns None
        so callers can degrade gracefully.
        """
        try:
            raw = await self._run("work", "lifecycle", agent_name, timeout=5)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw, "agent": agent_name}
        except OstkError:
            return None

    async def check_context_pressure(self, agent_name: str) -> dict | None:
        """Check context pressure for an agent via the :dying service.

        The :dying service may be inactive. If it fails, returns None so
        callers can degrade gracefully without showing anything in the UI.
        """
        try:
            raw = await self._run("work", "dying", agent_name, timeout=5)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pct_match = re.search(r"(\d+)%", raw)
                if pct_match:
                    return {
                        "agent": agent_name,
                        "pressure_pct": int(pct_match.group(1)),
                        "raw": raw,
                    }
                return {"agent": agent_name, "raw": raw}
        except OstkError:
            return None

    # --- Coordination Locks (needle 338) ---

    async def list_locks(self) -> list[dict]:
        """List active coordination locks via ostk lock list.

        Returns a list of dicts with lock name, holder, and creation time.
        If no locks exist or the command fails, returns an empty list.
        """
        try:
            raw = await self._run("lock", "list", timeout=5)
        except OstkError:
            return []

        if not raw.strip() or "no active locks" in raw.lower():
            return []

        locks: list[dict] = []
        # Try JSON parse first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "locks" in parsed:
                return parsed["locks"]
        except json.JSONDecodeError:
            pass

        # Parse text output line by line
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.startswith("="):
                continue
            # Skip header lines
            if "LOCK" in line.upper() and "NAME" in line.upper():
                continue
            parts = line.split()
            if len(parts) >= 1:
                lock: dict = {"name": parts[0]}
                if len(parts) >= 2:
                    lock["holder"] = parts[1]
                if len(parts) >= 3:
                    lock["created_at"] = " ".join(parts[2:])
                locks.append(lock)
        return locks

    async def release_lock(self, name: str) -> str:
        """Release a coordination lock by name."""
        return await self._run("lock", "release", name, timeout=5)

    # --- ostk profile wrappers (Tier 2.1) ---

    async def profile_tokens(self, last: int = 50) -> list:
        """Return per-call token usage from ``ostk profile tokens --json --last N``.

        Returns an empty list when there are no api.call rows yet.
        """
        try:
            raw = await self._run("profile", "tokens", "--json", "--last", str(last), timeout=15)
        except OstkError:
            return []
        if not raw.strip() or "no api.call rows" in raw:
            return []
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else [result]
        except json.JSONDecodeError:
            return []

    async def profile_cache(self, per_driver: bool = False) -> dict:
        """Return cache efficiency metrics from ``ostk profile cache --json``.

        Returns an empty dict when the command fails or there are no rows.
        """
        args = ["profile", "cache", "--json"]
        if per_driver:
            args.append("--per-driver")
        try:
            raw = await self._run(*args, timeout=15)
        except OstkError:
            return {}
        if not raw.strip() or "no api.call rows" in raw:
            return {}
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            return {}

    async def profile_sessions(self) -> list:
        """Return session-level usage data from ``ostk profile sessions --json``.

        Returns an empty list when there are no sessions or the command fails.
        """
        try:
            raw = await self._run("profile", "sessions", "--json", timeout=15)
        except OstkError:
            return []
        if not raw.strip() or "no api.call rows" in raw:
            return []
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            return []
        except json.JSONDecodeError:
            return []

    # --- ostk run <Agentfile> wrapper (Tier 2.2) ---

    async def run_agentfile(
        self,
        agentfile_path: str,
        env_passthrough: list[str] | None = None,
        runtime: str = "host",
        dry_run: bool = False,
    ) -> dict:
        """Wrap ``ostk run <agentfile> [--env-passthrough K] [--runtime R] [--dry-run]``.

        Returns a dict with stdout/stderr/exit_code/pid (pid is None; subprocess.run
        does not expose pid after completion). Dry-run mode emits what would happen
        without spawning anything.
        """
        import subprocess

        cmd: list[str] = ["ostk", "run", agentfile_path]
        if env_passthrough:
            for key in env_passthrough:
                cmd += ["--env-passthrough", key]
        if runtime != "host":
            cmd += ["--runtime", runtime]
        if dry_run:
            cmd.append("--dry-run")

        timeout = 30 if dry_run else 300
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise OstkError(f"ostk run timed out after {timeout}s: {agentfile_path}")

        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
            "pid": None,
            "cmd": cmd,
        }


ostk = OstkService()

# --- Clock cache ---
# A background task refreshes this on a slow cadence so the /status/clock
# endpoint never has to spawn an ostk subprocess on every poll.

_clock_cache: dict = {}
_clock_cache_task: asyncio.Task | None = None


def get_cached_clock() -> dict:
    """Return a copy of the in-memory clock cache (empty dict if not yet primed)."""
    return dict(_clock_cache)


async def start_clock_refresher(interval_seconds: int = 30):
    """Start a single background task that keeps _clock_cache fresh.

    Idempotent: a second call while the task is alive is a no-op.
    On any exception the previous cached value is kept and the loop retries.
    Returns the task (new or existing) so callers can register it for cleanup.
    """
    global _clock_cache_task
    if _clock_cache_task is not None and not _clock_cache_task.done():
        return None

    async def _refresh_loop() -> None:
        while True:
            try:
                result = await ostk.os_clock()
                _clock_cache.clear()
                _clock_cache.update(result)
            except Exception:
                pass
            await asyncio.sleep(interval_seconds)

    _clock_cache_task = asyncio.create_task(_refresh_loop())
    return _clock_cache_task

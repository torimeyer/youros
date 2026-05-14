# →1333 Root cause: orphan dirs in `.claude/worktrees/`

**Date:** 2026-05-14

## Summary

145 dirs (49% of 299 total) existed in `.claude/worktrees/` with no corresponding
`git worktree list` entry. The reaper was completely blind to them. Once a dir
loses its git registration, nothing ever cleans it up.

## Root causes (confirmed by code reading + test)

### 1. `worktree-reaper.sh` iterates `git worktree list --porcelain`, never the filesystem

The reaper's main loop is:
```bash
while IFS= read -r line; do
  case "$line" in
    worktree\ *) wt_path=...
    branch\ refs/heads/worktree-agent-*) # process
```

It only sees dirs that git already knows about. Any dir in `.claude/worktrees/agent-*`
that is not registered in git is invisible to the reaper. There is **no disk-first
sweep** anywhere in the codebase.

### 2. Multiple registry-cleanup paths delete rows without touching disk

All three of these delete rows from `agent_metadata` / `agent_state.json` but never
call `remove_worktree()`:

- `ghost_reaper.py` `_do_sweep()` → `del agent_metadata[name]`
- `agent_state_prune.py` `run_startup_prune()` → drops rows older than 30 days
- Stale sweep in `GET /api/agents` → marks rows `terminated_stale`

After the row is gone there is no record of what `worktree_path` or `worktree_branch`
was, so no later code can call `remove_worktree()` for that agent.

### 3. `_drain_stderr` is the only cleanup callsite for `remove_worktree()`

`remove_worktree()` (in `api/services/spawn_isolation.py`) is called from exactly
one place: `_drain_stderr()` in `api/routers/agents.py` (line 4978). If the backend
restarts (uvicorn reload, SIGTERM) while a subprocess is running, the asyncio event
loop dies, `_drain_stderr` never completes, and the worktree is never cleaned up.
This is the primary driver of accumulation (confirmed by →1332 diagnosis showing
SIGKILL of backend mid-agent).

### 4. How orphan dirs lose their git registration

When `remove_worktree()` is called but returns `False` (safety gate: unmerged commits),
the worktree dir and git registration both survive. Over time, one of these removes
the git registration while leaving the dir:

- `git worktree prune` (run by `git gc --auto` or explicitly) removes registrations
  where the gitdir file inside the worktree points to a stale/moved location. If the
  main repo path ever changed, all old gitdir files become stale and prune clears the
  registrations, leaving dirs behind.
- Direct deletion of `.git/worktrees/<id>/` entries by any cleanup script/tool.
- Re-spawn pre-clean in `create_worktree()`: step 1 calls `git worktree remove --force`
  (removes registration) then `shutil.rmtree` as fallback. If rmtree fails, the dir
  stays without a registration.

## What does NOT happen

- `worktree-reaper.sh` does NOT create orphans. `git worktree remove --force` (after
  unlock) succeeds atomically on git 2.50.1 (Apple Git-155): verified by test in
  `/tmp/test-orphan-wt`.
- The `isolation_bridge` hook does NOT create orphans. It correctly routes through
  `/api/agents/spawn` which calls `create_worktree()`.

## Fix

Add a disk-first orphan sweep to `worktree-reaper.sh` (runs as part of `--apply`):
1. `find .claude/worktrees/agent-*` for all dirs on disk
2. Cross-reference against `git worktree list --porcelain`
3. For dirs not in git's list (orphans): check for safety (git diff, active agent PID),
   then `git worktree prune` + `rm -rf`
4. Also add `git worktree prune` at the end of every `--apply` run to clean up the
   reverse case (registration without dir).

The sweep runs automatically on every parent session start via `session-start.sh`
line 110: `"$REAPER" --apply`.

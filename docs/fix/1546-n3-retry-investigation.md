# →1546 N3 worktree retry path — investigation

## Root cause

`create_worktree()` in `api/services/spawn_isolation.py` always creates a
**fresh** worktree. When a retry uses the same agent name and the abandoned
worktree has scaffold commits ahead of `main`, the unmerged-commits safety gate
returns `(False, "unmerged commits")`. The spawn handler treats `_wt_ok=False`
as "fall back to main repo" rather than "reuse the existing checkout."

Two bad outcomes result:
1. Retry runs in the parent repo (race hazard with other agents).
2. All in-progress work in the abandoned worktree is invisible to the retry.

When the retry gets a *different* name (kernel suffix variation), the hash in
`short_worktree_id` changes, producing a brand-new path — so TWO worktrees
exist for the same conceptual task.

## Fix location

`api/services/spawn_isolation.py` — `create_worktree()` function.

## Fix plan

Add `_check_worktree_reuse_safe(wt_path, branch)` helper:
- HEAD is on `branch` (not detached)
- No unresolved merge conflicts (`git ls-files --unmerged` is empty)

Modify the unmerged-commits block in `create_worktree()`:
- If worktree exists AND branch has unmerged commits → run safety checks
- If safe → return `(True, "")` (reuse without touching checkout)
- If not safe → return `(False, reason)` (existing behavior, caller falls back)

Add a test in `api/tests/test_agents.py` verifying the retry path reuses
the abandoned worktree.

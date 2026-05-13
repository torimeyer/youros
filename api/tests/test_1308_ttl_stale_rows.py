"""→1308: /api/agents must drop zombie rows whose worktree dir is gone and PID is dead.

Three cases:
1. Worktree gone + PID dead  → excluded from running list (terminated_stale)
2. Worktree exists           → included as running
3. Worktree gone + PID alive → included as running (live agent, worktree may not matter)
"""
# placeholder — implementation follows in the same commit

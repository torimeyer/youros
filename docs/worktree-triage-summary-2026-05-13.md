# Worktree triage summary — 2026-05-13

Full report: `~/.claude/handoffs/worktree-triage-2026-05-13.md`

| Bucket | Count | Action |
|--------|-------|--------|
| absorbed (live agents, skip now) | 2 | wait for agents to finish, then run reaper --apply |
| duplicate (needle on main, safe to delete) | 122 | delete with python script in report |
| stale unique | 0 | n/a — everything < 7 days old |
| active unique (keep) | 30 | do not touch |
| orphan branches (no worktree dir) | 5 | git branch -D (see report) |

**Total safe-to-delete: 127** (122 worktrees + 5 branches)

Classification method: `scripts/worktree-reaper.sh` dry-run for absorbed/unique split,
then needle ID cross-check against `git log main --oneline` to split unique into
duplicate vs genuinely-new-content.

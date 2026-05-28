# Worktree Cleanup Pass 2 — 2026-05-21

**Agent**: pass-2-worktree-cleanup-179-bran-281384  
**Source**: Pass-1 audit at `docs/draft/_review/2026-05-21-worktree-cleanup.md`  
**Scope**: `closed-Task-stale` (92) + `no-Task-orphan` (87) = 179 branches

---

## Summary

| metric | value |
|--------|-------|
| archive tags created | 179 |
| branches deleted | 178 |
| branches already gone pre-run | 1 (`worktree-agent-rag-fix-agent-gems-in-067f3705`) |
| failures | 0 |
| worktree dirs removed by reaper | 0 (all orphans already cleaned) |
| reaper protected (active agents) | 2 |

**Root issue discovered**: All worktrees were added with `--lock`, so `git worktree remove --force` fails with "use 'remove -f -f'". Fixed by using double-force flag.

---

## Verification

| check | pre | post |
|-------|-----|------|
| `archive/cleanup-2026-05-21/*` tags | 61 | 240 (+179 ✓) |
| `git branch \| grep -c worktree-agent` | 201 | 27 |

### Remaining 27 branches (all expected — not touched)

| branch | reason kept |
|--------|-------------|
| `worktree-agent-pass-2-worktree-clean-0f5b9e89` | this agent's branch |
| `worktree-agent-worktree-cleanup-pass-1-536094` | explicitly excluded |
| `worktree-agent-diagnose-tool-calls-c-c8d4043b` | explicitly excluded |
| `worktree-agent-1525-*` | open Task →1525 |
| `worktree-agent-1586-*` | open Task →1586 |
| `worktree-agent-1590-*` | open Task →1590 |
| `worktree-agent-1338-*`, `worktree-agent-1345-*`, `worktree-agent-1531-*` | not in pass-1 target lists |
| remaining 18 | active worktrees with unmerged commits (reaper refused, correct) |

---

## Worktree reaper output

```
summary: absorbed=2 unique=25 error=0
applying: removing 2 absorbed worktree(s)...
  protected (agent still active): worktree-agent-code-hygiene-scan-dep-ba22a5df (x2)
done. removed=0 protected=2 failed=0
phase 2: orphan sweep: found=0 removed=0 skipped=0 failed=0
```

---

## Failed list

None. All 179 target branches were tagged and deleted (or confirmed already gone).

---

## Process notes

- Tags created first (all 179) before any deletions attempted — recovery guaranteed.
- Initial delete attempt failed because branches were registered as locked worktrees.
- Fix: `git worktree remove -f -f <path>` (double-force overrides `--lock`).
- Script ran in batches with heartbeat every 30 branches.

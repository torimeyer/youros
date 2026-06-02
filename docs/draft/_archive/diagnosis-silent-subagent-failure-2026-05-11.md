# Diagnosis: Silent Subagent Failure — 2026-05-11

Authored by agent `diagnose-silent-agent-diagnose-b-135d81`.

## Summary

On 2026-05-11, 5 of 7 REST-spawned subagents produced transcripts but made zero
file edits and zero commits. This document records the evidence, root causes,
and fixes implemented or filed.

---

## Failure Inventory

| Agent name | Transcript | Runtime | Outcome |
|---|---|---|---|
| `fix-loadavg-Task-count-46639c` | 816 B | ~4 min | Analysis done, no commit, no Task |
| `fix-loadavg-Task-count-46639c-r2` | 0 B | ~0 min | Never started; gone from list |
| `diagnose-stale-specs-in-list-doc-8e1bfb` | 475 B | ~4 min | Reading code, no commit, no Task |
| `diagnose-backend-http-500-on-api-cfa194` | 3 788 B | ~22 min | Did edit files, never committed |
| `diagnose-1144-backend-wedge-f7f902` | 4 282 B | ~23 min | Committed `a3cc902`, but to **main** not its worktree branch |

Two that worked:
- `add-current-model-to-write-kerne-28562d` — committed `fa8f121` correctly
- http-500 — salvaged inline by parent session

---

## Root Cause Analysis

### RC1 — MCP tools absent in worktree sessions (primary)

**Evidence**: The 1144 agent transcript says explicitly at line 3:
> "The ostk bash/fs_ops tools aren't in the deferred list — the MCP server may
> not have those tool names registered. I'll fall through to native tools."

The ostk MCP server (`server.rs` Test 3, line 239) only registers:
```
["shell", "spawn", "interact", "session", "lock", "help"]
```
`bash`, `fs_ops`, `read` are NOT in `tools/list`. `ToolSearch` on `mcp__ostk__`
returns only 13 context/recall/search/nudge tools. Every agent that needs to
write files must either detect this and fall back to native tools (which the
1144 agent did) or silently lose write capability.

**Impact**: Agents that follow CLAUDE.md's "use mcp__ostk__bash" instruction
have no callable write tools. They can read (native Read works) and analyse,
but the first `mcp__ostk__fs_ops` or `mcp__ostk__bash` call returns
`unknown tool`, which terminates that branch of the model's plan. Loadavg and
stale-specs agents appear to have stopped at exactly this point.

**Fix location**: `haystack-main/src/serve/server.rs` — out of scope for this
agent (scope pin). Filed as P0 Task →NNNN (see below).

---

### RC2 — Shared daemon cwd routes commits to main (secondary)

**Evidence**: `a3cc902` (the 1144 agent's commit) landed on `main`, not on
`worktree-agent-diagnose-1144-backend-wedge-f7f902`. That worktree branch does
not exist. The 1144 transcript shows native Bash calls including `git commit`.

The daemon's `dispatch.rs` line 473:
```rust
let project_root = self.state.ostk_dir.parent()
    .unwrap_or(&self.state.ostk_dir)
```
All bash commands from any connected client (including worktree clients) run
with the main repo as cwd. `mcp__ostk__bash(cmd="git commit")` from inside a
worktree commits to `main`'s current branch. The 1144 agent used native Bash
(which runs in the worktree's process cwd), so its commits landed on main's
HEAD — the same branch the parent session is on.

**Impact**: Even when an agent successfully commits, the commit may land on the
wrong branch, defeating isolation.

**Fix location**: `haystack-main/src/serve/dispatch.rs` — out of scope. Filed
with RC1 Task.

---

### RC3 — Brief does not require commit-or-Task before exit (fixable)

**Evidence**: Loadavg transcript ends mid-analysis after discovering the
`count_active_Tasks` vs `count_open_Tasks` discrepancy. The model wrote
a substantive analysis in 816 B of transcript, then exited cleanly. No tool
call failed — the process exited normally. The brief said "find root cause and
fix" but did not say "you must commit or file a Task before exiting."
Stale-specs shows the same pattern (475 B, reading `list_docs`, then stops).

The `scaffold-commit-watcher.sh` PostToolUse hook never fires for bridge-spawned
agents because `task-isolation-bridge.sh` exits 2 (blocks the native Agent
call), so PostToolUse:Agent does not trigger.

**Fix**: Inject a completion requirement into the REST-spawned prompt inside
`task-isolation-bridge.sh`. Implemented in this commit (see Changes below).

---

### RC4 — Test suite blocks commit step for 22+ minutes (brief quality)

**Evidence**: The http-500 transcript shows the agent ran `pytest` without
`-x` or file filtering. The 22-minute runtime is consistent with running all
1 244 tests. The agent planned to commit after tests passed but was cancelled
before that.

**Fix**: The RC3 prompt injection now also instructs agents to commit first with
focused tests, not the full suite. The broader convention is documented in
feedback_subagent_brief_must_name_test_command.md but was not enforced by the
bridge itself.

---

### RC5 — Transcript API returns 0 bytes when name prefix mismatches

**Evidence**: REST spawn stores name without "agent-" prefix
(`fix-loadavg-Task-count-46639c`). Transcript at
`transcripts/fix-loadavg-Task-count-46639c.md`. If `_resolve_transcript_source_uncached`
is called with `"agent-fix-loadavg-Task-count-46639c"` (with prefix), step 1
looks for `transcripts/agent-fix-loadavg-Task-count-46639c.md` which does not
exist. The metadata step 2 should catch it (transcript_path is recorded at
spawn time), but any caller that skips the metadata path returns 0 bytes.

**Impact**: Low severity for this batch (transcripts ARE accessible with the
correct name). The mismatch causes confusing Agents-page display. Filed as P1.

---

## Changes Implemented

### `task-isolation-bridge.sh` — inject completion requirement (RC3 + RC4)

Added a completion clause to every REST-spawned prompt:

```
COMPLETION REQUIREMENT: Before your process exits you MUST do one of:
  (a) git commit -m "..." (commit all file changes), OR
  (b) ostk work add "..." --priority P0 (file a Task with evidence).
Exiting after analysis only, with no commit and no Task, is a failed run.
If running tests, run targeted tests first (e.g. pytest path/to/test.py -x -q),
commit, then optionally run the full suite. Do not block a commit on full-suite pass.
```

---

## Evidence Checklist

- [x] Transcript files read directly (not via API) to avoid name-prefix confusion
- [x] `server.rs` test confirms only 6 tools registered (shell/spawn/interact/session/lock/help)
- [x] `dispatch.rs` line 473 confirms daemon cwd = main repo for all clients
- [x] `task-isolation-bridge.sh` line 319 confirmed PostToolUse:Agent never fires after exit 2
- [x] `scaffold-commit-watcher.sh` confirmed: fires on PostToolUse:Agent only
- [x] `a3cc902` confirmed on main branch, worktree branch `worktree-agent-diagnose-1144-backend-wedge-f7f902` does not exist

---

## Tasks Filed

- **P0 →1152**: MCP tools missing from worktree ostk sessions (RC1 + RC2)
  — fix requires changes to `haystack-main/src/serve/server.rs` and `dispatch.rs`

---

*Authored 2026-05-11 by `diagnose-silent-agent-diagnose-b-135d81`*

# Spawn pre-merge gate

## Problem

torios runs claude-code subagents under `isolation: "worktree"`. Each
subagent gets its own git worktree and branch. When a burst of 5+
agents finish at once (ADHD spawn pattern), nothing stops a broken
worktree from contaminating `main` at rollup time. See
`feedback_spawn_burst_commit_contamination.md`.

## Choice: option (b), a merge-helper script

Two shapes were considered:

- **(a)** PostToolUse hook on the Task tool, runs tests automatically
  when a worktree agent finishes.
- **(b)** A standalone script (`scripts/worktree-gate.sh`) invoked by
  torios/user before rollup, plus a status-reader
  (`scripts/worktree-status.sh`).

Option (b) is the minimum viable pick:

1. Hooks on Task fire when the tool call **returns**, not when the
   subagent completes. See
   `feedback_background_task_hook_returns_immediately.md`. Parsing
   transcripts or waiting on completion inside a hook is fragile and
   has burned us before.
2. A script is directly testable. Hooks aren't.
3. The gate is advisory, not blocking. A script-shaped gate fits that
   model: the user (or a later, well-behaved hook) calls it and reads
   the status file.
4. We can add a PostToolUse hook later that simply shells out to the
   same script, once the reliability is known.

## Contract

`scripts/worktree-gate.sh <worktree-path>`

1. Detects the branch from the worktree.
2. Diffs the worktree tip against `main` to list changed files.
3. Infers which suites to run (frontend only, backend only, both, or
   neither) from the changed file tree. Logic lives in
   `scripts/lib/worktree_suite.py` so it is unit-tested.
4. Runs the relevant suites **inside the worktree**, not in main.
5. Writes `.ostk/worktree_status/<branch>.json`:
   ```
   {
     "branch": "worktree-agent-abc",
     "path":   "/abs/path/to/worktree",
     "status": "ready" | "parked",
     "failing_tests": ["api/tests/test_foo.py::test_bar", ...],
     "ran_at":  "2026-04-23T18:04:00Z",
     "suite":   "backend" | "frontend" | "both" | "none"
   }
   ```
6. Exits 0 if ready, 1 if parked. The gate never merges, never resets,
   never force-pushes. It only writes the status file.

`scripts/worktree-status.sh` prints a one-line-per-worktree table
showing branch, status, suite, and age of last run. Reads, does not
write.

## Non-goals (v1)

- No auto-merge. User still runs the merge themselves.
- No hook wiring. Script is callable; hook can be added later.
- No transcript parsing. Worktree path is passed explicitly.
- No register/heartbeat logic. Existing hooks handle that.

## Tests

- `api/tests/test_worktree_suite.py` covers the file-to-suite
  heuristic and the status-file writer.
- `scripts/test_worktree_status.sh` asserts the helper reads and
  prints all status files.

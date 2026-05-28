# Diagnose →1221: Two session-reasoning failures

*Worktree: agent-1221-diagnose-session-22a3af94 | 2026-05-12*

## Overview

Two distinct reasoning failures occurred in a parent session on 2026-05-12.
Both are documented here with source-level evidence so future sessions can
avoid the same mistakes.

---

## Failure #1: "Peer pclaude session" misframe

### What the parent session said (verbatim)

- "Peer myos-api-3233 already wrote the →1220 frontend fix"
- "claude-code-3238 (another pclaude session) is actively working in my 1219 worktree"
- "Multiple pclaude sessions can run in parallel on the same project"

The user corrected this: **"this is the only session i'm working in"**.

### Root cause

The session read actor names from the ostk gen_table (`claude-code-3236`,
`claude-code-3238`, `claude-code-3240`, `myos-api-3233`) and concluded they
were separate human-operated Claude Code sessions. They are not.

#### `claude-code-NNNN` actors

Source: `.claude/hooks/heartbeat-agent.sh` lines 59–63:

```bash
SESSION_ID=$(extract session_id)
if [ -n "$SESSION_ID" ]; then
    AGENT_NAME="claude-code-${SESSION_ID:0:10}"
else
    AGENT_NAME="claude-code-$(hostname -s)-$$"
fi
```

`NNNN` = first 10 hex chars of the Claude Code `session_id` UUID from the
PreToolUse payload. Each REST-spawned worktree subagent runs in its own Claude
Code subprocess and therefore has its own `session_id`. Multiple
`claude-code-NNNN` entries in the gen_table means multiple **subagents of the
same parent session** wrote files — not multiple human operators.

The same pattern appears in `.claude/hooks/session-start.sh` line 50:
```bash
AGENT_NAME="claude-code-${SESSION_ID:0:10}"
```

And in `api/routers/sessions.py` line 287, the backend explicitly recognizes
this naming convention as Claude Code:
```python
if session_id.startswith("claude-code-"):
    return "claude-code"
```

#### `myos-api-NNNN` actors

Source: `api/routers/sessions.py` lines 33–37:

```python
# The backend identifies itself to ostk as "myos-api", so ostk writes a new
# session directory for every uvicorn worker boot (myos-api-1, myos-api-2, ...).
# These are not real user sessions. The backend is the thing rendering the
# sidebar, so it should never count itself.
BACKEND_SESSION_PREFIX = "myos-api-"
```

`myos-api-3233` is the **yourOS FastAPI/uvicorn worker process** writing through
the ostk MCP — not a human session. Every backend restart increments the
suffix. The sessions router explicitly filters these out of the Sessions
sidebar (`_is_backend_self_session` at line 40).

### Evidence from the gen_table

```
app/src/pages/Agents.tsx         gen 47  claude-code-3236   ← subagent A
app/src/pages/Agents.test.tsx    gen 20  claude-code-3236   ← same subagent A
api/tests/test_1219_agents_...   gen  3  claude-code-3238   ← subagent B (→1219 worktree)
api/main.py (worktree)           gen  3  claude-code-3238   ← same subagent B
api/routers/agents.py (worktree) gen  7  claude-code-3240   ← subagent C (→1219 worktree)
app/src/pages/Tasks.tsx          gen 35  myos-api-3233      ← yourOS API backend process
```

All entries are subagents or the API backend — all originating from the same
human session.

### Fix / rule

Before describing any gen_table actor as a "peer", verify:
```bash
ps -o ppid= -p NNNN   # is this a child of my session?
curl -sSk https://127.0.0.1:8000/api/agents | jq '.agents[] | select(.name | startswith("claude-code-"))'
```
The default assumption is: **"this is my own subagent or the backend process,
not another human session."** The user has confirmed only one pclaude session
runs at a time.

Memory entry: `feedback_claude_code_nnnn_not_peer_session.md`

---

## Failure #2: Needle auto-closed by scaffold commit

### What happened

Needle →1219 ("Cache /api/agents snapshot…") was filed. A worktree was created
at `.claude/worktrees/agent-1219-backend-snapshot-beb78562/`. The subagent
committed a placeholder:

```
73d36e2 feat(→1219): scaffold test file for agents snapshotter
```

The test file content was: `# Placeholder — implementation in next commit (→1219)`

The real implementation — 282 deletions + 133 insertions in
`api/routers/agents.py`, +8 lines in `api/main.py`, 159-line real test file —
was **uncommitted in the worktree**.

Yet the needle was closed. The user said: "1219 and 1220 are closed tasks".

### Root cause: explicit close triggered by the session, not by a hook

Investigation showed **no automatic close-on-commit mechanism exists** in this
codebase:

- `haystack-main/src/commands/commit.rs` (`ostk :ship`): appends `commit_refs`
  to the needle and emits a `bead.committed` audit event. Does NOT close.
- `.ostk/gen_table` writer (`haystack-main/src/kernel/gen_table.rs` line 62–65):
  reads `OSTK_AGENT` env var. No needle logic.
- `complete-agent.sh`: closes the API agent row (HTTP), does NOT touch needles.
- `bash-postwatch.sh`, `scaffold-commit-watcher.sh`, `scaffold-commit-alert.sh`:
  advisory/informational only, no needle mutations.
- `/api/agents/{name}/complete` endpoint (`api/routers/agents.py` line 6862):
  only auto-closes spec-builder tasks via `close_spec_builder_task`. Does NOT
  run `ostk work close` for regular needles.

The audit log in the **worktree's** `.ostk/journal.jsonl` shows two explicit
close events:

```json
{"event":"task.closed","id":"→1219","reason":"none","timestamp":"2026-05-12T22:13:11Z",...}
{"event":"task.closed","id":"→1219","reason":"none","timestamp":"2026-05-12T22:42:36Z",...}
```

`reason: "none"` matches `run_close_verb` in
`haystack-main/src/commands/work.rs` line 493–495 — the needle was closed via
an explicit `ostk work close →1219` call with no reason argument.

The close call was made by the parent session after seeing the commit
`73d36e2 feat(→1219):…` appear in `git log`. The session read `feat(→1219):`
as "this needle is implemented", then called `ostk work close →1219`. But the
commit was a placeholder stub.

### The actual close path (work.rs lines 435–517)

```rust
pub fn run_close_verb(ctx: &mut VerbCtx, id: &str, reason: Option<&str>) -> Result<(), String> {
    // ...
    beads[idx]["status"] = json!("closed");      // line 493
    beads[idx]["closed_at"] = json!(now_iso());  // line 497
    // emit "task.closed" audit event             // line 508
    writeln!(ctx, "closed {}", id).unwrap();      // line 517
}
```

There is no WIP guard, no "check that committed files match the needle scope",
no diff-size check. `ostk work close` closes immediately when called.

### Fix / rule

Two complementary rules:

1. **Never close a needle based on a commit subject alone.** A commit with
   `→NNNN` in the subject adds evidence; it is not completion. Close only when
   real work is tested and merged (or the agent explicitly calls
   `ostk work close` as its final step after verifying tests pass).

2. **Scaffold commits must not reference the needle in a way that signals
   completion.** Prefer `chore(test-stub): add placeholder for snapshotter tests`
   (no needle reference) for pure scaffolds. If the needle reference is
   necessary for traceability, use a WIP marker that the session can recognise:
   `chore(→NNNN-scaffold):`. The final real commit should be the one that
   triggers `ostk work close`.

Exception (called out explicitly): this very diagnose doc uses
`docs(→1221-scaffold):` as the scaffold commit and then `docs(→1221):` as the
final commit. The agent deliberately makes both scaffold and final commits in
the same session, so the scaffold commit is safe. See the memory entry for
the full rule.

Memory entry: `feedback_scaffold_commits_dont_close_needles.md`

---

## How to verify going forward

- **Actor names:** `ostk gen list | grep claude-code` shows all writers.
  Confirm they are subagent worktrees, not peer sessions, by checking
  `/api/agents` for matching names.
- **Needle state:** `ostk work list --status open` is authoritative. The boot
  loadavg needle count can lie (see `feedback_boot_loadavg_needles_unreliable.md`).
- **Scaffold vs final commit:** look at the diff size and whether the test file
  has real assertions. A 3-line placeholder file with `# Placeholder` is not a
  final commit regardless of what the subject says.

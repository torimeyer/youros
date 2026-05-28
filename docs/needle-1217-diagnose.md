# Task-1217-diagnose: silent-fail root cause for first attempt

**Agent:** `1217-mcp-transport-repro-02a0e3`
**Spawned:** 2026-05-12T22:05:37 UTC
**Last heartbeat:** 2026-05-12T22:08:32 UTC
**Worktree branch commits:** 0 (branch `worktree-agent-1217-mcp-transport-re-1a5708d7` is an ancestor of main)

---

## Verdict: not a true silent fail — work landed, but via wrong target

`docs/Task-1217-repro.md` exists at commit `9ea72e2` on main
(`2026-05-12 17:18:12 -0500` = 22:18 UTC), 10 minutes after the agent's
last heartbeat. The worktree branch has zero commits because the commit
went directly to main instead of the worktree branch.

---

## Causes (in order of impact)

### 1. MCP transport self-kill (primary cause of session death)

The agent was investigating the MCP -32000 drop bug. Its transcript shows a
deliberate escalation of repro attempts:

```
[heartbeat 22:06:27] 215 files, 326ms — too small
[heartbeat 22:06:52] 67K files — large enough to hit 30s timeout
[heartbeat 22:07:17] 12s for 62K TS files — not enough to hit 30s
[heartbeat 22:07:42] 12s, 7.2MB to file. Now the key test — piping that
                     7MB of output directly through MCP (no redirect)
[heartbeat 22:08:07] (blank)
[heartbeat 22:08:32] (blank)
```

Evidence: `/Users/torimeyer/claude/torios/transcripts/1217-mcp-transport-repro-02a0e3.md`

The blank heartbeats confirm the agent was still alive (HTTP worked) but
could no longer issue tool calls. The MCP transport dropped after the 7MB
pipe. The agent reproduced the bug it was characterising by triggering it
on itself.

### 2. Commit targeted main, not worktree branch

The commit `9ea72e2` landed on main via git author `torimeyer@Mac.attlocal.net`.
The worktree branch has zero commits. This matches the pattern in
`feedback_subagent_cwd_must_be_worktree.md`: if `mcp__ostk__bash` defaults
cwd to the kernel root (`/Users/torimeyer/claude/torios`) rather than the
worktree path, all `git commit` operations land on whatever branch the main
repo HEAD points to (main).

The brief did not pass `cwd=<worktree_path>` for git operations. The agent
likely ran `git add` and `git commit` without specifying the worktree cwd,
so the commit landed on main.

### 3. No /complete call

The agent died mid-execution after the MCP transport drop. It never called
`POST /api/agents/1217-mcp-transport-repro-02a0e3/complete`, so the row
shows a generic summary: *"Agent finished its work. It didn't formally close
the task — check git log / transcript for details."*

`tokens_used: 0` in the agents row confirms the token tracking was not
flushed before exit (consistent with an abrupt session end).

### 4. Hook denies (contributing, not causal)

`~/.claude/logs/hook-denies.log` shows four entries in the 22:04–22:07 window:

- `22:04:50` — `bash-guards.sh` blocked a command containing `status=`
  (zsh read-only variable). This was **from the parent/orchestrator session**
  spawning the sibling agents, not from the 1217 agent itself (the 1217 agent
  wasn't spawned until 22:05:37).

- `22:05:41`, `22:06:50`, `22:07:13` — `adhd-mode-monitor-enforcer.sh`
  blocked Agent spawns in the parent session because no Monitor was armed.
  These did not affect the 1217 agent's own execution.

None of the four denies blocked the 1217 agent's tool calls.

---

## Is the cause fixable in torios code?

**Yes.** Two fixes needed:

### Fix A — Brief template: write doc before dangerous repro

Subagent briefs for repro/diagnostic work should mandate writing the
findings file **before** any command that might kill the agent's own MCP
connection. Pattern:

```
1. Read relevant source files
2. Write <findings_doc> with preliminary findings
3. git add + git commit  ← commit preliminary findings first
4. THEN attempt the dangerous repro (with file redirect, not raw MCP pipe)
5. Update the doc with repro result + commit again
```

The 1217 brief said "write docs/Task-1217-repro.md" but did not require a
commit before the large-output repro test.

### Fix B — Brief template: always pass cwd to bash for git ops

All subagent brief templates should include:

```
All git operations (add, commit, status) MUST use:
  mcp__ostk__bash(cmd="git add ...", cwd="<worktree_absolute_path>")
Do NOT omit cwd — the kernel default is the parent repo root and
git commits will land on main instead of your worktree branch.
```

This is already documented in `feedback_subagent_cwd_must_be_worktree.md`
but the 1217 brief did not enforce it.

---

## Can the original 1217 work be safely respawned?

**No respawn needed.** The work landed:

- `docs/Task-1217-repro.md` committed at `9ea72e2` on main
- Full findings: architecture analysis, repro attempts, OOM hypothesis,
  workaround (spawn+interact+tee), proposed fix (wire `_timeout_secs` in
  `sh_run.rs:19`)

Recommended next step: **close →1217** (the Task is done; the findings
doc is on main). Then file a separate P1 or sub-Task for the two
`sh_run.rs` fixes if those haven't been tracked yet.

---

## Evidence index

| Item | Location |
|------|----------|
| Transcript (heartbeats) | `/Users/torimeyer/claude/torios/transcripts/1217-mcp-transport-repro-02a0e3.md` |
| Claude Code session jsonl | `/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/815d55b1-7dab-4eed-a318-57b1217b8c3e.jsonl` |
| Repro doc (landed) | `docs/Task-1217-repro.md` @ `9ea72e2` |
| Hook denies | `~/.claude/logs/hook-denies.log` lines `22:04:50`, `22:05:41`, `22:06:50`, `22:07:13` |
| agents.jsonl API row | `GET /api/agents` → `1217-mcp-transport-repro-02a0e3` |
| cwd bug reference | `feedback_subagent_cwd_must_be_worktree.md` |

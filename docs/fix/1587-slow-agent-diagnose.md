# →1587 — Agents look stuck but aren't (root cause + tier-1 fix)

## Symptom

Subagents spawned via `Agent` tool with `isolation: worktree` appear stuck:
- `current_step: ?` for 5-10 minutes
- Transcript bytes grow slowly (under 200 bytes/minute)
- No commits in the worktree
- Eventually status flips to `abandoned`

Pclaude (and tori) conclude the agent is wedged and cancel/respawn.

## Real cause

The agent is **not stuck**. It is doing real work but the visibility
channels lie:

1. **Transcript blindspot.** The transcript file at
   `transcripts/<agent>.md` captures only the agent's plain-text model
   output between tool calls. Tool calls and tool results are NOT
   written to the transcript. An agent making 30 well-targeted
   `search()` / `read()` calls produces almost no transcript text — it
   looks identical to an agent paralyzed in extended thinking.

2. **`current_step` only updates when the agent explicitly heartbeats.**
   The brief asked the agent to "set current_step" but didn't tell it
   when. Most subagents never call `/api/agents/<name>/heartbeat` until
   a major milestone, so `current_step` stays `?` for the whole early
   investigation phase.

3. **mcp__ostk__bash 30s socket timeout kills vitest/pytest runs.** The
   agent reaches the verify step, runs `bash scripts/run-vitest.sh`,
   bash times out, transcript stops growing, runtime watchdog marks
   the agent abandoned.

4. **Pre-existing vitest failures (→1568, 164 tests) confuse the agent.**
   Agent sees a wall of failures, tries to interpret which are theirs,
   burns time on the wrong question.

## Evidence

`/transcripts/saa-user-bubble-right-align-1584-1fb70c.md` (1768 bytes total):

- 03:10 — register, find bubble component code
- 03:11 — find line 2736 (the inner div with `max-w-[75%] w-fit`)
- 03:14 — check git history
- 03:19 — **identified root cause**: "commit 5ae3d11 (→1578 code block
  fix) changed the assistant bubble from inline-block to
  block w-fit max-w-full but left the user bubble as inline-block"
- 03:20 — applied uncommitted fix, updated tests
- 03:21-23 — ran vitest, hit pre-existing failures, got tangled

10 minutes of productive work, ~150 bytes/minute of visible heartbeats.
No commit, no current_step updates.

## Tier-1 fix (this commit)

Memory rule update at
`~/.claude/projects/-Users-torimeyer-claude-torios/memory/feedback_subagent_prompt_template.md`
adds three lines pclaude must include in every saa brief:

1. Heartbeat current_step before every major tool call.
2. mcp__ostk__spawn + interact for pytest/vitest, never mcp__ostk__bash.
3. Known pre-existing failures (→1568) → file follow-up, do not try
   to fix.

## Out-of-scope (deferred)

- Transcript capture of tool calls (Tier-2; bigger backend change).
- /api/agents extended-thinking indicator (Tier-2).
- Auto-stagger respawn when no growth in N min (Tier-3).

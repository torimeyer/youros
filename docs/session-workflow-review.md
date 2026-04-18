# Session workflow review

Session: `d6b1b534-a3a2-4e52-a8f3-179399e34e1d` (Apr 17-18, 2026). 5,172 main-session lines, 2,110 turns, **805M total tokens**, 28 subagents.

## Session summary

Demo prep for Roadmap → Spec → Build. Shipped Waves 1-4 (rename Specs, auto-promote, templates, polish), roadmap prewarm, file-clobber fix, orphan-heartbeat idle detection, nudge 500 fix. Did not ship: multi-model side-by-side chat (dry-run timed out at 0-byte), speed-first-loads (silent false-complete). Session ended on the toriagent insight: today's bugs are workarounds for spawning `claude --print` instead of ostk-native agents.

## Top 5 inefficiencies

### 1. Re-reading giant files whole
- **Symptom:** `api/routers/agents.py` (6,707 lines) read **25 times**. `test_agents.py` (11,056 lines) read 16 times. `Agents.tsx` (4,158 lines) read 14 times.
- **Cost:** 25 × 6,707 = ~167k lines re-materialized for one file. At ~25 tokens/line, **~4.2M tokens on a single file's re-reads**.
- **Root cause:** no offset/limit discipline. Every new subtask re-read whole files instead of Grep → targeted offset.
- **Fix (CLAUDE.md):** *"Never Read a file >1000 lines whole. Grep first, Read with offset+limit."* Plus a hook warning on duplicate Reads in the same session.

### 2. Native Bash/Read/Grep when ostk was up
- **Symptom:** 435 Bash calls, 118 Read calls, despite the standing rule. User: *"youre using ostk right? you keep forgetting that and it's very annoying."*
- **Example:** 60 native `curl` calls to the API; ostk would have logged each to `.ostk/audit.jsonl` so the session could check before repeating.
- **Root cause:** ostk tools are deferred, not primary-bound. First instinct stays Bash.
- **Fix (settings.json):** `PreToolUse` matcher on `Bash`/`Read`/`Grep` that blocks when `mcp__ostk__shell` is loaded, redirecting to ostk. And auto-invoke `ToolSearch query="ostk" max_results=30` at session boot.

### 3. Diagnosed the wrong layer five times before toriagent
- **Symptom:** 0-byte transcript bug diagnosed as (a) AC gate, (b) stale transcript sweep, (c) rate limit, (d) spawn stderr pipe regression, (e) Sonnet quota cap. Then user said *"something that could showcase an ostk feature we arent using"* and the real substrate problem clicked.
- **Example:** 3 parallel diagnose agents (`diag-zerobyte-timeout`, `diag-ostk-latency`, `diag-chat-to-agent-latency`) **all hit the exact bug they were sent to find**. ~12 min wasted + 3 stuck agents to cancel.
- **Root cause:** memory notes on this class of bug (`feedback_quota_silent_fail.md`, `project_spawn_stderr_pipe_fix.md`) were treated as history, not active hypotheses.
- **Fix (CLAUDE.md):** *"Before spawning a diagnose subagent for a subagent-failure bug, run `claude auth status` and `ostk ps` inline. If 3+ diagnoses target the same subsystem today, write up the shared substrate before the 4th."*

### 4. `in_progress` meant "parked"
- **Symptom:** user called out *"you say in progress but there is no agent running in torios"* and *"you arent checking in every 60 seconds like you are supposed to."* `speed-first-loads` sat in_progress for 20+ min after silent failure.
- **Root cause:** rule exists (`feedback_task_states.md`) but nothing enforced it.
- **Fix (hook):** `PostToolUse` on `TaskUpdate` nags if status=in_progress and no tool_use in last 10 min of the transcript; auto-flip to pending.

### 5. Silent dry-run protected a no-op
- **Symptom:** snapshot → build → revert for multi-model chat spec. All 3 builders hit `completed_timeout` at 167s, zero tokens, zero transcript. Revert succeeded but protected nothing.
- **Cost:** ~5 min wall time on a dry-run whose failure mode the design didn't anticipate.
- **Root cause:** the pattern assumed "builders ran → revert needed." Real failure was "builders never emitted."
- **Fix (script):** `scripts/spawn-health-check.sh` — a 30s trivial builder that writes `hello` to /tmp. Run before any dry-run or demo rehearsal. If it fails, bail before snapshotting.

## Wins

- **Subagent prompts were self-contained.** Waves 1-4 embedded exact file paths, line numbers, ostk-tool rules, and acceptance criteria. Biggest transcript (782KB) rarely had to re-discover context.
- **Parallel diagnose + builder spawns** when done right (Wave 2 & 3 ran concurrent with the perf diagnose). Saved ~30 min.
- **Honest course-correction** on "inline is faster" — agent updated memory with specific violations rather than apologizing.
- **Roadmap prewarm** was the correct answer to "simulate the agent but prewarmed." Deterministic demo path.
- **The toriagent insight itself** — stepping back to "the whole `claude --print` subprocess is the wrong substrate" after hours of workarounds was the right conclusion.

## Suggested changes

1. **`CLAUDE.md`** — add under Development rules: *"Never Read a file >1000 lines whole. Grep first, then Read with offset+limit."*
2. **`CLAUDE.md`** — add under Agent rules: *"Before spawning a diagnose subagent for a subagent-failure bug, run `claude auth status` and `ostk ps` inline."*
3. **`.claude/settings.json`** — `PreToolUse` hook blocks native Bash/Read/Grep when ostk MCP is loaded. Use the `update-config` skill.
4. **Session-boot hook** — auto-invoke `ToolSearch query="ostk" max_results=30` so ostk is primary, not deferred.
5. **`scripts/spawn-health-check.sh`** — 30s trivial-builder probe. Required before dry-runs and demos.
6. **`TaskUpdate` post-hook** — flag in_progress with no tool_use in 10 min, auto-flip to pending.

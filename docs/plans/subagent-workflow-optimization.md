# Subagent Workflow Optimization Plan

**Date:** 2026-05-01  
**Session:** Five fixes shipped today — 4c4f5c4, ac0b5ab, ec104aa, 359e760, 90d3312  
**Goal:** Keep the "everything tracked and tested" guarantee while removing the ~30s boot lag, cascade failures, and brittle bits.

---

## Tori's Questions

> 1. Why didn't "grandchildren" come up before today? Why is the cascade pattern only surfacing now?
> 2. Are grandchildren necessary? When would you ever want one?
> 3. Can we optimize the subagent boot? It currently takes ~30s before the agent is doing real work.
> 4. Should we build rules for when a task should be a subagent vs when it's fine inline?
> 5. What ideas do you have to optimize the workflow overall?

---

## Context

Today was the first session where we ran a large fleet of fix/diagnose briefs back-to-back in tight succession. That surface pressure exposed four separate bugs that had been dormant:

| Commit | Fix |
|--------|-----|
| 359e760 | Worktrees got sparse checkouts — agent had no source files |
| 4c4f5c4 | `saa-must-spawn.sh` fired inside subagent sessions, triggering grandchild cascades |
| ac0b5ab | `ostk-first.sh` blocked all native tools when socket file was stale — zero working tools |
| ec104aa | Subagent commits on worktree branches weren't merged back to main, becoming dangling |
| 90d3312 | 12 separate hook scripts were forking 12 python3 processes per Bash call (~1s of overhead) |

None of these were new bugs. They were all latent. The fleet just hit them all at once.

---

## Q1: Why did grandchildren only surface today?

`saa-must-spawn.sh` gates on the *last user message* matching `"saa ..."`, `"diagnose ..."`, or `"fix ..."` (exact prefix, lowercase). Before 4c4f5c4, it had no worktree skip — subagents inherited `$HOME` and therefore read the same `enable_tori_rules: true` config as the parent.

The cascade happens in three steps:

1. Parent sends a brief that starts with "fix X" or "diagnose Y"
2. Subagent launches, picks up the parent's last user message (still `"fix X"`) from the shared JSONL log
3. Subagent's first non-Agent tool call hits the hook, which reads `"fix"` and blocks with exit 2, forcing the subagent to spawn *another* agent before doing any work

**Why it never bit us before:** Most prior briefs used "build", "add", "implement", "create" — verbs outside the `saa|diagnose|fix` trigger list. Today was the first time we sent a fleet where nearly every brief description started with "fix" or "diagnose" as the literal first word. The hook is a word-prefix match on the last user message, not a semantic check, so it only fires when the words align exactly. That alignment happened today across enough parallel agents to make the pattern unmistakable.

Confirmed by the 4c4f5c4 fix: adding `case "${CLAUDE_PROJECT_DIR:-}" in */.claude/worktrees/*) exit 0 ;; esac` before the verb check is the entire fix. One guard, five lines. The bug was always there — we just hadn't run the right workload to expose it.

---

## Q2: Are grandchildren necessary?

**No. Not intentionally. They are always a bug in practice.**

Conceptually, a child agent *could* split its own work into parallel sub-tasks, each in a sub-worktree. That would be a legitimate use of grandchildren. But we have never designed for this pattern and have no infrastructure for it: the worktree naming scheme is flat (`worktree-agent-<name>`), there's no parent-child tracking in `/api/agents`, and the auto-merge in `complete-agent.sh` assumes exactly one level of nesting (child onto main).

Every actual grandchild we've seen has been a cascade bug, not intentional parallelism.

**Recommendation:** Keep the worktree skip in `saa-must-spawn.sh` (already done). If a child agent legitimately needs to parallelize, the correct approach is to hand that parallelism back to the parent — the parent orchestrates additional waves. The child should never spawn.

If we ever want a child to parallelize its own sub-tasks, we'd need to build a different primitive first (parent-scoped fleet API, not ad-hoc grandchild spawning). We're not there yet.

---

## Q3: Subagent boot optimization

The ~30s pre-work time breaks down into roughly:

| Step | Estimated cost | Notes |
|------|---------------|-------|
| Process spawn + Claude Code harness init | ~3-5s | Fixed cost of a new CC subprocess |
| MCP server connect (cold socket) | ~10-15s | Each MCP server handshakes on first call |
| ToolSearch to load deferred ostk schemas | ~5s | One round-trip per schema load |
| Hook chain on first tool call | ~1-3s | After 90d3312 consolidation; was ~5-8s before |
| register-agent.sh first attempt + retry | ~1-4s | Fast when backend is up; worst case 31s if slow |

The biggest wins, ordered by impact:

**1. Pre-load ToolSearch in the spawn prompt (no code change, just discipline)**

Every subagent brief should open with a ToolSearch call as its literal first action. Right now agents discover they need tools on their first blocked call. Moving the ToolSearch to the preamble turns a reactive recovery into a proactive load — same wall-clock time, but real work starts on the second tool call instead of the fifth.

**2. register-agent.sh: cap the retry ladder at 3s total instead of 31s worst-case**

The current retry sequence is `1, 2, 4, 8, 16` seconds = 31s if all five attempts fail. The pending-register.jsonl drain already handles persistent backend outages correctly. We should truncate to `1, 2` (3s total) and park to the queue immediately on third failure. This change is in `register-agent.sh` lines 271-280. It saves up to 28s in the (rare) slow-backend case and is a no-op in the common (fast backend) case.

**3. Skip unused MCP servers in subagent spawn config**

Subagents don't need the Gmail, Google Drive, Calendar, or Stitch MCP servers. Those cold-connect at session start. A per-spawn `settings.json` override that disables them for worktree sessions would shave ~5-10s off MCP handshake time. This requires a spawn-time config injection path (not yet built, medium effort).

**4. Explicit `ostk boot` in every subagent brief as step 1** (discipline, not code)

`ostk boot` runs POST checks and reads `.ostk/` state. It's cheap (~1s) and front-loads the kernel warm-up before any tool call. Currently subagents sometimes skip this and hit a cold kernel on their first `search()` or `bash()` call, adding latency to real work.

---

## Q4: Rules for inline vs subagent

The current standing rule is "saa for everything, no exceptions." That rule was written as a corrective response to a period where inline work bypassed audit, tests, and visibility. It's served us well — but it overcorrects. With ostk's audit log in place, inline work in the parent session *is* tracked (every `fs_ops` and `bash` call writes an audit row). Tests can still be required inline.

**Proposed 3-tier rule:**

| Tier | Scope | Test requirement | Use when |
|------|-------|-----------------|----------|
| **Inline** | 1-3 files, ~5-15 min | Required, run before calling it done | Sequential, no parallelism needed, not risky |
| **Single saa** | 3-8 files, 15-60 min | Required, green before closing | Parallelizable with other work, or context would pollute parent |
| **Fleet saa** | >8 files, or backend+frontend crossing | Required per wave | Independent parallel units with disjoint lock sets |

The dividing line between inline and single saa is **parallelism** and **context pollution**. If the parent can do the work without burning its context budget, and nothing else is running in parallel, inline is fine. If the task is one of several running concurrently, it needs a worktree.

The dividing line between single saa and fleet is **commit contamination risk** (see `feedback_no_parallel_commits_without_worktrees.md`) and **stall risk** (>5 files in one agent = stall risk per the 2026-04-30 incident).

The "saa for everything" rule stays for anything that starts with the `saa/diagnose/fix` vocabulary — that's user-initiated explicit work. The new inline tier is for orchestration-level cleanup that the parent does between waves.

---

## Q5: Optimization ideas ranked by impact

**1. Fix phantom rejection for parent session after saa dispatch (in-flight, 85e39d)**

The permission-deny-diagnosis.md documents this precisely: `bash-guards.sh` writes block messages to STDOUT instead of STDERR in sections 1-5, producing the confusing "user doesn't want to proceed" envelope. This affects the parent's ability to run status probes after a saa dispatch. Highest short-term impact.

**2. Register-agent.sh retry cap (3s total, file-change only)**

Already described in Q3. File: `.claude/hooks/register-agent.sh` lines 271-280. Change `for delay in 1 2 4 8 16` to `for delay in 1 2`. Two-line change, up to 28s saved in bad-backend case.

**3. Auto-merge on completion (already shipped ec104aa)**

Dangling commits on `worktree-agent-*` branches no longer need a manual reaper pass. The merge happens at `PostToolUse:Agent` in the parent. Skip conditions are correct (non-fast-forward, diverged, running-inside-worktree all skip gracefully).

**4. Sparse-checkout disable on every worktree (already shipped 359e760)**

`git sparse-checkout disable` is now called immediately after `git worktree add` in `api/services/spawn_isolation.py`. One-time fix; all future worktrees get a full checkout.

**5. Dead-socket fail-open for ostk-first.sh (already shipped ac0b5ab)**

Unix-socket connect probe (~30-80ms) before blocking on stale socket file. Live socket still blocks native tools as before.

**6. Boot-time: pre-load ostk tool schemas in spawn brief preamble (discipline)**

No code change. Every Agent spawn prompt should include a ToolSearch call as step 0 in the brief, before the registration curl. This moves the deferred-tool-load from reactive (first blocked call) to proactive (first thing the agent does). Saves ~3-5s of wasted retries on blocked tool calls.

**7. Worktree reaper hygiene after fleet runs**

`scripts/worktree-reaper.sh --apply` should run after every large fleet. Absorbed worktrees (diff against main is empty) are deleted. Unique worktrees are parked. This keeps `git worktree list` clean and prevents the reaper from being confused by stale entries.

**8. Per-spawn MCP server config (medium effort, ~1 sprint)**

Build a spawn-time `settings.json` injection that disables unused MCP servers (Gmail, Drive, Calendar, Stitch, MIDI, PDF) for subagent sessions. Expected savings: 5-10s off cold MCP handshake time.

---

## Implementation order

| Priority | Change | File(s) | Effort |
|----------|--------|---------|--------|
| 1 | Fix bash-guards.sh STDOUT → STDERR (sections 1-5) | `.claude/hooks/bash-guards.sh` | 30 min |
| 2 | Cap register-agent.sh retry to 3s | `.claude/hooks/register-agent.sh` lines 271-280 | 15 min |
| 3 | Add ToolSearch preamble discipline to subagent brief template | Memory: `feedback_subagent_prompt_template.md` | 10 min |
| 4 | Add inline tier to saa rules | Memory: `feedback_saa_rules.md` | 10 min |
| 5 | Per-spawn MCP server disable | `api/services/spawn_isolation.py` + settings template | ~1 day |

Items 1-4 are all low-risk, small-scope, done inline by the parent. Item 5 needs a subagent.

---

## Open questions and risks

**Q: Will the 3-tier inline rule cause us to skip tests?**  
Risk: "small enough for inline" becomes an excuse to skip tests. Mitigation: the inline tier still requires tests — it just means the parent runs them, not a subagent. If tests are skipped, that's a separate violation (existing `feedback_saa_rules.md` rule on tests-required).

**Q: Does blocking grandchildren break any existing workflow?**  
No. We've never intentionally used grandchildren. The only impact is on briefs that happen to say "fix" or "diagnose" as the first word — those now need to rephrase if they genuinely want a child to spawn. (The worktree skip makes this moot for subagent sessions.)

**Q: Does the auto-merge in `complete-agent.sh` handle merge conflicts?**  
Only fast-forward merges are attempted. If the branch diverged from main (e.g., two agents both modified the same file), the merge is skipped silently. The reaper handles cleanup. This is safe but means some commits need a manual rebase — the right answer is better lock coordination upstream (lock conflicts caught at spawn time by the bridge's 409 check).

**Q: Is ~30s boot time acceptable long-term?**  
For single long-running agents (>15 min work), yes — amortized cost is negligible. For short tasks (<5 min work), 30s boot is 10%+ overhead. The inline tier (Q4) is the right answer for short tasks, not further shaving the boot time.

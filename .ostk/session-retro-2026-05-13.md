# Session retro — 2026-05-13

## Timeline (rough)

- 18:34: spawned py-spy-stack-dump-capture-before (→1232 attempt 1), cancelled 18:37 — 3 min
- 18:57: spawned diagnose-verbose-brief-stall-664017, cancelled 19:05 — 8 min
- 20:23: spawned fix-1242-gem-test-leak, completed 20:26 — 3 min, landed as 75e6d2d
- 20:35: spawned diagnose-store-drift-on-1248, completed 20:36 — 1 min
- 20:46: df950f6 landed (→1248 dedup fix)
- 20:57: spawned triage-115-parked-worktrees (→1249, v1), completed 21:01 — 4 min, landed as 30b2d9c
- 20:57: spawned dedup-port-8000-listener (→1250), completed 21:15 — 18 min, landed as 9a29470
- 21:10: spawned re-triage-worktrees (→1251, v2 with git cherry), completed 21:17 — 7 min, landed as 038e641
- 21:14: spawned add-grants-ws-bus (→1246), completed 21:17 — 3 min, landed as 6513a50
- 21:15: spawned fix-threaded-reply-scoping (→1243), completed 21:31 — 16 min, landed as 2cb5d71
- 21:21: spawned gemini-captures-empty-state (→1252), completed 21:24 — 3 min, landed as ae147d5
- 21:27: eb2d0b8 landed (→1232 py-spy, via py-spy-capture-1232)
- 21:30: spawned diagnose-needle-count-mismatch (→1253), completed 21:55 — 25 min, landed as 8d44c8f
- 21:33: spawned render-markdown-in-gem-chat (→1254), completed 21:35 — 2 min, landed as 5797262

## What slowed us down (findings)

### Finding 1: Triage v1 used commit-subject matching instead of git cherry

- Evidence: Agent triage-115-parked-worktrees ran `git log --oneline` and checked if commit *subjects* appeared anywhere in `git log main --oneline`. This is not the same as `git cherry main <branch>`, which compares actual patch content. Result: 59 worktrees were classified DUPLICATE when they were actually UNIQUE (different implementation, same subject). Commit 30b2d9c landed at 21:00 with the flawed report. Had to spawn →1251 at 21:10 to redo with `git cherry`.
- Cost: ~15 min rework (re-triage agent + time watching it land)
- Fix: Any brief that asks "classify whether commits landed on main" must explicitly say: use `git cherry main <branch>` (exit code 0 per commit = not on main, `-` prefix = already there). Never use commit-subject string matching.
- Needle: →1256

### Finding 2: 1248 fixed symptom (duplicate rows) not root cause (cross-store backfill gap)

- Evidence: df950f6 (20:46) deduped `issues.jsonl` to prevent drift on close. But the real problem was that `backfill_stuck_in_progress_tasks` in main.py only scanned `issues.jsonl`, not `issues.jsonl.1` (the rotated archive). Tasks stuck as `in_progress` in the rotated file after a file rotation were never recovered. This showed up again immediately: badge showed 4, CLI showed 2. Had to spawn a 25-min diagnose-needle-count agent to find and fix the root cause (commit 8d44c8f at 21:54).
- Cost: 25 min + a second commit for what should have been part of the first fix
- Fix: When a brief says "fix store drift on close," it should also require: "verify the backfill sweep covers ALL store files (issues.jsonl AND issues.jsonl.1)." One-file fixes for two-file bugs will keep recurring.
- Needle: →1257

### Finding 3: Open-ended diagnose briefs send agents on 20+ min codebase spelunking

- Evidence: diagnose-needle-count-mismatch ran 25 min (21:30–21:55). The brief said "diagnose ostk needle store vs /api/tasks store divergence" with no entry points. The transcript shows 18 blank heartbeat ticks where the agent was reading store files, discovering there were two (issues.jsonl + issues.jsonl.1), finding the caching layer, tracing /tasks/counts vs /tasks?status=open, before finally landing on `backfill_stuck_in_progress_tasks` in main.py. The fix itself (add a second file scan) took ~5 min once found.
- Cost: ~20 min of exploration that could have been ~2 min with "start at `backfill_stuck_in_progress_tasks` in `api/main.py`, also check `issues.jsonl.1`"
- Fix: Diagnose briefs must name the suspicious function and file as the starting point. "Diagnose X" with no anchor = agent starts from scratch every time.
- Needle: →1258

### Finding 4: Script-layer test infra gaps doubled the port-8000 agent's time

- Evidence: dedup-port-8000-listener ran 18 min (20:57–21:15), but the fix itself (keep launcher lock held through exec) took ~5 min. The remaining 13 min: test incorrectly counted uvicorn `--reload` child reloader workers as separate listener PIDs (both parent and child inherit the fd), so the "only 1 parent" assertion failed on a correct fix. Then the test hung at `wait` because uvicorn was never explicitly killed in teardown. Transcript: "The test is failing because uvicorn `--reload` spawns parent + child worker (both show in lsof)… test is stuck waiting for uvicorn to exit."
- Cost: ~13 extra min per script-layer fix agent that touches uvicorn
- Fix: Script-layer test briefs that involve dev-backend.sh should pre-warn: "uvicorn `--reload` spawns a parent + reloader child; use `pgrep -f 'uvicorn.*main:app'` not lsof to count parents. Kill uvicorn explicitly in test teardown before `wait`."
- Needle: →1259

### Finding 5: ostk MCP tools absent in worktree sessions — every agent wastes time falling back

- Evidence: triage agent transcript: "ostk bash/fs_ops tools aren't in the deferred list. Let me check what's available and fall back to native tools." Threaded-reply agent: "ostk search is returning empty. Falling back to native tools to locate the codebase." Then 6 blank heartbeat ticks (02:16–02:21) while it navigated the codebase with grep/glob instead of `search(query=..., scope="code")`. This pattern appears in EVERY agent transcript from tonight.
- Cost: 5-10 min per agent on codebase navigation that ostk search would handle in seconds. Across 6 agents tonight = ~30-60 min aggregate.
- Fix: Fix worktree MCP session initialization so ostk search/bash/fs_ops are available inside worktrees without fallback. Alternatively, the subagent prompt template should include the ToolSearch recovery incantation in bold so agents load tools immediately rather than giving up.
- Needle: →1260

### Finding 6: Cancelled agents required silent respawns, burning context without a receipt

- Evidence: py-spy-stack-dump-capture-before-690ec7 was spawned at 18:34, cancelled at 18:37 (3 min, transcript shows it was cancelled mid-work). It was re-spawned as py-spy-capture-1232 which has a 63B transcript (empty). Yet eb2d0b8 landed at 21:27. diagnose-verbose-brief-stall-664017 was spawned at 18:57, cancelled at 19:05 — a second attempt was later spawned as diagnose-verbose-brief-stall-v2-8e166a (worktree agent). Between the cancel and the respawn, we spent time diagnosing why the original ran and whether to keep its work.
- Cost: ~10-15 min across two cancelled-and-respawned pairs
- Fix: Before cancelling an agent, run `git log --oneline -3` in its worktree to check for partial commits worth preserving. Cancel + no-commit = wasted context. Cancel + commit = can cherry-pick.
- Needle: →1261

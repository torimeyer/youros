# Subagent Throughput Brainstorm
_Generated 2026-04-25. Read-only analysis. No code changed._

---

## 1. Failure modes seen this session and in memory

- **409 on every edit spawn that mentions a busy path** — `task-isolation-bridge.sh:183-184` extracts ALL `.py|ts|tsx|sh|md|json|yml|yaml|toml|sql|css|html` mentions and ALL `api/|app/|scripts/|docs/|agents/|.claude/|.ostk/` prefixes as write locks. A brainstorm prompt that references `api/routers/agents.py` for context (not editing) inherits a write lock for that path. If another agent already holds it, spawn returns 409. Bridge stderr shows only the HTTP code — the actual holder name in the response body (`agents.py:3152-3167`) is silently discarded (`bridge.sh:252`). (`feedback_bridge_spawn_409_is_lock_conflict.md`)

- **Backend unreachable (HTTP 000) during uvicorn mid-reload** — `bridge.sh:242-249` blocks on connect-timeout when the backend is restarting. The TOCTOU race in `dev-backend.sh` (fixed in 9e96780) caused concurrent callers to both pass the pidfile check and race on port 8000; the watchdog then killed the live process. Today: `dev-backend.sh` took 6m32s to report ready even though port 8000 was already bound. (`feedback_uvicorn_reload_kills_backend_during_agent_commit.md`)

- **Orphan write locks from crashed or timed-out agents** — `_spawn_lock_holders` in `spawn_isolation.py:225` is an in-process dict with no TTL and no timestamp. Locks are only freed when `release_spawn_locks` is called explicitly in the completion path (`spawn_isolation.py:340-371`). If an agent process is killed, quota-capped, or disappears, its locks stay until the backend process restarts. 30+ stale agent rows visible in fleet today; any that held locks at time of crash would have left orphan entries.

- **Sonnet quota cap produces silent 0-byte transcripts** — Claude Code Max subscription rate-limits silently: no output, no stderr, no error code. Symptom is indistinguishable from a crash. Only Haiku probe distinguishes quota cap from code bug. (`feedback_quota_silent_fail.md`)

- **Spawn-burst commit contamination on shared worktree** — 5+ parallel agents race on `git add`/`git commit` in the same branch. One agent's `git add <file>` can sweep another agent's staged half-landed work. Confirmed 4 commits in the 2026-04-19 burst. (`feedback_spawn_burst_commit_contamination.md`)

- **Upstream worktree naming collision in Claude Code Task tool** — parallel Task-tool subagents with `isolation:worktree` can be assigned the same worktree directory by the harness, silently sharing state. Reported by Scott 2026-04-24; not a torios bug, upstream Claude Code. (`feedback_claude_code_worktree_name_collision.md`)

- **Invisible subagents: hook closes row before agent finishes** — `complete-agent.sh` PostToolUse hook previously fired unconditionally on Task returns, marking rows `completed` within 1s of spawn while the agent was still running. Fixed in `e98d9fd`. Recurrence risk on any hook that doesn't check `tool_input.run_in_background`. (`feedback_invisible_subagent_diagnose_first_step.md`)

- **Hook fan-out latency** — 20+ hooks in `.claude/hooks/` (task-isolation-bridge.sh, register-agent.sh, heartbeat-agent.sh, ostk-first.sh, standing-rules.sh, auto-monitor-spawn.sh, drain-pending.sh, plus ~14 others). `heartbeat-agent.sh:13` explicitly notes "Python cold start is 60 to 200ms on macOS." Multiple hooks fire PreToolUse on every single tool call. At 5 hooks × 100ms Python startup = ~500ms overhead on every tool round-trip.

- **Lock detail hidden at 409** — `api/routers/agents.py:3152-3167` builds a rich `conflicts` response body naming the holder spawn and the contested path. `bridge.sh:252` discards that body and echoes only `HTTP 409`. The parent has no way to know who holds the lock or when to retry.

---

## 2. Capacity ceilings

| Ceiling | Today's limit | Evidence |
|---|---|---|
| **Lock granularity (write-only, no TTL)** | ~3-5 parallel edit agents before spurious 409 | Any prompt mentioning a common path (api/, app/) extracts a broad lock. With no read/write distinction every agent competes with every other agent. `spawn_isolation.py:225` |
| **Model quota (Sonnet)** | ~3-5 concurrent Sonnet sessions before silent cap | `feedback_quota_silent_fail.md` — tight per-model tier on Max plan; Haiku is far more generous |
| **Worktree branch namespace (CC upstream bug)** | ~2-3 safe parallel worktree agents | `feedback_claude_code_worktree_name_collision.md` — upstream harness may assign same dir to two agents |
| **Backend single process / port 8000** | 1 restart at a time; agents blocked during reload | `feedback_uvicorn_reload_kills_backend_during_agent_commit.md`; 6m32s today |
| **Hook Python startup overhead** | ~500ms–1s added to every tool call across a session | 5+ Python-invoking hooks × 60-200ms each. `heartbeat-agent.sh:13` |
| **Stale fleet / zombie locks** | Unbounded accumulation without reaper | 30+ stale rows today; no TTL on `_spawn_lock_holders`; reaper not automated |
| **Greedy path extraction false positives** | Any prompt mentioning a held path → 409 | `bridge.sh:183-184`: EXT_RE + DIR_RE extract from full prompt body including explanatory mentions |
| **CPU contention from stale procs** | Soft; 30+ idle processes still consume file watchers | `mcp__ostk__ps` today |

---

## 3. Recommendations, ranked by impact / effort

### R1 — Surface 409 detail in bridge + add `/api/agents/spawn-locks` query endpoint
**Effort: S | Impact: high**

Problem: blind 409 retry wastes spawns; parent cannot know if the conflict will clear in 1s or 10 min.

Change:
- `bridge.sh:250-255`: capture response body (`curl ... -o /tmp/spawn-body`), extract `conflicts[].held_by_spawn` and print it to stderr alongside the HTTP status.
- New `GET /api/agents/spawn-locks?paths=a,b` route in `api/routers/agents.py` that returns each lock key's current holder + `acquired_at` (once TTL is added). Parent polls until clear, then spawns.

Files: `.claude/hooks/task-isolation-bridge.sh:250-255`, `api/routers/agents.py`
Risks: bridge becomes slightly more complex; response body may be absent on network errors (guard with `|| echo "no body"`).

---

### R2 — Add lock TTL + release on agent terminal status
**Effort: S | Impact: high**

Problem: `_spawn_lock_holders` has no expiry. A crashed agent holds its locks until the backend process restarts, blocking all agents that mention the same paths.

Change:
- `spawn_isolation.py:225`: change dict value from `Tuple[str, str]` to `Tuple[str, str, float]` (spawn_id, raw_glob, acquired_epoch).
- Add a periodic sweep (FastAPI `startup` background task, every 5 min): for each held lock, if the owning agent's row has a terminal status (`completed`, `failed`, `cancelled`, `terminated_stale`), call `release_spawn_locks`.
- TTL fallback: if lock is older than `max(agent.budget_minutes * 2, 30)` minutes and agent row is gone, auto-release.

Files: `api/services/spawn_isolation.py`, `api/routers/agents.py` (startup event)
Risks: too-short TTL could release a valid long-running agent. Use agent budget as the floor.

---

### R3 — Require explicit `Locks:` header; remove greedy EXT_RE/DIR_RE heuristic
**Effort: S (bridge) + M (prompt updates) | Impact: high**

Problem: today's 409 was caused by EXT_RE matching `api/routers/agents.py` in the brainstorm prompt for reference (not editing). `bridge.sh:183-184` treats every path mention as a write reservation. The no-match fallback (`bridge.sh:191-192`: `{"app/**", "api/**", ".claude/**", "scripts/**"}`) is even worse — it forces all concurrent edit agents to serialize on the entire codebase.

Change:
- Remove `EXT_RE`/`DIR_RE` extraction block (`bridge.sh:182-192`).
- If no explicit `Locks: [...]` header found, emit a clear error: `"Blocked: edit-capable spawn did not declare Locks: [...]. Add a Locks: header naming only the files this agent will write."` Exit 2.
- Parent session template and CLAUDE.md must document the `Locks:` syntax.

Files: `.claude/hooks/task-isolation-bridge.sh:182-192`
Risks: any existing spawn brief without an explicit `Locks:` header will block. Requires a one-time sweep of recurring prompts. But the current behavior (silently acquiring wrong locks) is worse than an explicit failure.

---

### R4 — Quota probe before every Sonnet/Opus spawn
**Effort: S | Impact: medium-high**

Problem: Sonnet quota cap is silent. You pay for a spawn attempt that produces 0 bytes. Retry loop wastes tokens and time.

Change:
- In bridge, before the main `curl /api/agents/spawn`, fire a fast Haiku ping: `timeout 10 claude --print --model claude-haiku-4-5 "1+1="`. If it returns non-zero or times out, print "quota cap suspected" and exit 2.
- Alternatively, add a `POST /api/agents/probe-quota` endpoint that runs the Haiku ping server-side and returns `{quota_ok: bool, latency_ms: int}`.

Files: `.claude/hooks/task-isolation-bridge.sh`, optionally `api/routers/agents.py`
Risks: adds ~2-3s to every spawn. Acceptable given the cost of a ghost spawn.

---

### R5 — Automated reaper cron every 15 min
**Effort: S | Impact: medium**

Problem: stale agent rows, zombie worktrees, and (once TTL exists) orphan locks accumulate. `scripts/worktree-reaper.sh` exists but runs manually.

Change:
- `CronCreate` job: `scripts/worktree-reaper.sh --apply` every 15 min.
- Extend reaper to also call `POST /api/agents/reap` or directly sweep `_spawn_lock_holders` for agents with terminal status (this works once R2 TTL sweep is in place).

Files: cron config, `scripts/worktree-reaper.sh`
Risks: reaper could misclassify an active worktree as absorbed if the agent committed but didn't yet merge. Keep the "absorbed = diff against main is empty" check; unique worktrees are never deleted.

---

### R6 — Read vs write lock distinction
**Effort: M | Impact: medium**

Problem: two research agents that both read `api/routers/agents.py` for context but don't edit it still 409 each other because there's no read lock concept.

Change:
- Add `mode: "read" | "write"` field to the lock entry struct.
- `acquire_spawn_locks`: two readers never conflict; a writer conflicts with any other holder on the same key.
- Explicit `Locks:` syntax becomes `Locks: [read:api/routers/agents.py, write:app/src/Foo.tsx]`.
- EXT_RE-extracted paths (if heuristic is kept as an opt-in) default to `write` to preserve current conservative behavior.

Files: `api/services/spawn_isolation.py` (lock dict, acquire, validate), `api/routers/agents.py` (schema)
Risks: schema version bump; existing prompts using bare path strings need to still work (backward compat: bare path = write lock).

---

### R7 — Backend non-reloading mode during active agent sessions
**Effort: M | Impact: medium**

Problem: `--reload` mode watches worktree file changes and can trigger spurious reloads mid-session. Today's 6m32s launch with port already bound points to lingering watchdog or lock race even after the 9e96780 fix.

Change:
- `dev-backend.sh`: check if any agent rows are `running` via `/api/agents`; if so, start with `--no-reload` and print a warning. Alternatively expose `MYOS_NO_RELOAD=1` env knob.
- Or: ship a `scripts/prod-backend.sh` that always uses `--no-reload` and swap to it for demo/heavy sessions.

Files: `scripts/dev-backend.sh`
Risks: developers lose live reload. Mitigate with explicit opt-in. The 9e96780 TOCTOU fix already covers the crash; this is defense-in-depth.

---

### R8 — Hook consolidation: single PreToolUse dispatcher
**Effort: L | Impact: low-medium**

Problem: 5+ hooks fire Python processes on every PreToolUse (heartbeat, register, ostk-first, drain-pending, standing-rules). Python cold-start on macOS is 60-200ms per invocation (`heartbeat-agent.sh:13`). At 5 hooks × 100ms = ~500ms added to every tool call.

Change:
- Merge register-agent.sh, heartbeat-agent.sh, drain-pending.sh, auto-monitor-spawn.sh into a single `dispatcher.sh` that runs one Python process with all logic.
- `settings.json`: replace 4 hook entries with 1.

Files: `.claude/hooks/` (new dispatcher.sh), `.claude/settings.json`
Risks: a bug in the dispatcher disables all four functions at once. Requires solid test coverage. Low priority until the lock and quota issues are resolved.

---

## 4. Top 3 next moves

1. **Two-line bridge fix: surface 409 holder name in stderr.** `bridge.sh:250-255` already has the curl call; capture `-o /tmp/spawn-body` and `cat` it to stderr. Zero risk, eliminates blind retries today.

2. **Remove EXT_RE/DIR_RE greedy extraction; require explicit `Locks:` in every edit-spawn brief.** One block deleted from `bridge.sh:182-192`. Eliminates the root cause of today's false 409. Parent updates every saa template to include the header.

3. **Add lock TTL sweep in `spawn_isolation.py` + wire `worktree-reaper.sh --apply` as a 15-min cron.** Together these kill orphan lock accumulation and stale fleet growth — the two problems that compound over a long session.

---

## 5. What NOT to change

- **Spawn name uuid suffix** (`bridge.sh:153`): name collision is not the failure mode. `feedback_bridge_spawn_409_is_lock_conflict.md` confirms; the 409 body says `lock_conflict`, not `name_exists`. Changing the naming scheme fixes nothing.

- **Backend TOCTOU fix** (`dev-backend.sh`, commit 9e96780): already resolved. `feedback_uvicorn_reload_kills_backend_during_agent_commit.md` has the full analysis. If the backend dies again, diagnose fresh — do not re-apply the same patch.

- **Lock-on-spawn requirement for edit spawns** (`spawn_isolation.py:264-282`): the `400` returned when an edit spawn omits `locks` is correct and load-bearing. Without it, parallel edits would race silently on the same branch. Do not soften this to a warning.

- **Bridge read-only passthrough** (`bridge.sh:139-142`): working correctly. Prompts with no edit verb skip the whole bridge and go through native Task. This is correct behavior — research agents do not need worktrees.

- **`ostk-first.sh` socket-presence probe**: the socket-file check replaced a broken `pgrep`-based probe that gave false positives. The current logic is correct. Only the socket file means ostk MCP is actually reachable.

- **Worktree fork from `main`'s HEAD** (`agents.py:3358-3360`, `reference_coordination_layer_v1.md`): critical for preventing 71-commit drift at merge time. Do not change the base branch.

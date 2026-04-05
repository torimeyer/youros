---
created_at: 2026-03-08T04:01:38Z
status: draft
title: retro sprint 3 and 4
author: orchestrator
---

# Retro: Sprints 3 & 4

## Sprint 3: Replace mish (~45 min agent time)

### What shipped
- Full MCP server port (src/serve/) — sh_run, sh_spawn, sh_interact, sh_session, sh_lock
- Bash shim with -c interception, --agents guide, squasher (VTE strip + dedup)
- ostk serve replaces mish serve
- 152 tests

### What worked
1. **Two-track split was clean.** MCP server (Track 1) and shim+squasher (Track 2) had zero file overlap. Both landed independently, compiled together first try.
2. **Copying from mish.** The instruction "copy whole implementations, adjust for ostk" worked. Agents read mish source as reference, wrote ostk equivalents. No backporting.
3. **Squasher port was surgical.** Only VTE strip + dedup (80% of value). Skipped the rest. Agent made the right scope call.
4. **tokio addition went smoothly.** The MCP server needed async. Agent added tokio to Cargo.toml and wrapped kernel sync calls in spawn_blocking. Clean boundary.

### What didn't work
1. **ss/ss_session tools weren't in tools/list.** Agent A (Sprint 4) discovered that Sprint 3's MCP port routed the tools but didn't register them in tool_definitions(). Agents couldn't discover them. Critical bug caught by integration test.
2. **No fcp-rust LSP for agents.** Both Sprint 3 agents navigated 53k lines of mish by reading files manually. Would have been 5x faster with rust_query("find dispatch").

### Key metric
- MCP server port: 83 tool uses, 555s. ~$2 cost for a full MCP server.
- Shim + squasher: 61 tool uses, 408s. ~$1.50 for bash interception + output compression.

---

## Sprint 4: Wire kernel + nudge + specs (~30 min)

### What shipped
- Kernel wired into MCP: identity on init, heartbeat on every call, digest on every response, Hot PR on ss writes, 304 on ss reads
- ostk nudge (bd-100) — the interrupt primitive, 7 tests
- work close auto-releases mish locks (bd-149)
- Promoted 3 drafts to specs: pull-model, layer-boundary, spec-versioning (20 beads)
- 3 value assertion tests (metrics as test assertions, bd-170)
- The paper drafted
- 166 unit + 35 e2e = 201 tests

### What worked
1. **Autonomous operation.** User said "start" and left. Two agents dispatched, orchestrator handled spec work directly, all committed before user returned.
2. **Agent B (nudge + locks) finished fast.** Two focused tasks, clear scope, 7 tests, done. The "quick fix" dispatch pattern works.
3. **Value assertion tests are powerful.** "Read elision must save >90% of tokens" — if a change breaks that, CI catches it. Metrics as tests, not dashboards.
4. **Spec promotion pipeline works end-to-end.** Draft -> promote -> decompose -> beads with auto-locks. Three specs promoted in minutes.

### What didn't work
1. **Agent A (kernel wiring) discovered Sprint 3 bugs.** ss/ss_session weren't in tool_definitions(). The wiring agent had to fix the registration before it could wire the kernel features. Cross-sprint integration gap.
2. **Stale locks accumulate.** 31 locks from Tier A decompose still running after 85 minutes. work close auto-release (bd-149) fixes this going forward, but the old locks are still there.
3. **Installed binary vs build binary diverge.** `ostk` at ~/.cargo/bin lags behind ./target/debug until you `cargo install`. Agents calling the global binary get stale behavior. Had to reinstall twice.

---

## Patterns Confirmed Across All 4 Sprints

### The compounding loop is real
Sprint 1 CLI -> Sprint 2 used CLI to track kernel build -> Sprint 3 used make all from Sprint 1 -> Sprint 4 used nudge from Sprint 4 to prove Sprint 4. Every wave built on the last.

### Pain-to-spec pipeline running at full speed
194 beads. Every friction point filed. The ostk-env tag produced:
- bd-100 (nudge) — filed when agent was stuck, built 2 sprints later
- bd-149 (lock auto-release) — filed when locks accumulated, built 1 sprint later
- bd-170 (metrics as assertions) — filed from user feedback, built same sprint

### Autonomous dispatch works when scope is clear
Agents succeed when: exact file list, exact function signatures, exact test expectations. Agents struggle when: "figure out what to do" or cross-cutting concerns.

### The paper thesis holds
Three agents, one tree, zero worktrees. 166 tests. All conflicts resolved by the runtime (or by the agents iterating, which is what the runtime automates). The data supports the paper.

---

## What Sprint 5 Should Focus On

1. **SWE bench v19** — swap mish for ostk, measure the delta. This is the proof point. If Hot PR + read elision add 10%+ to the 20% baseline, the thesis is quantified.
2. **First external user test** — someone other than us runs `ostk init && ostk run agent.yaml`. What breaks? What confuses?
3. **The --agents guide** — the first-run experience needs to explain the new signals clearly. "[304]? What's that?" needs a one-line answer in the guide.
4. **Spec version 2 pass** — all 9 specs get version:1 in frontmatter. The versioning system is live.
5. **Clean up stale locks and beads** — 194 beads, many closed. Some stale. Audit check probably shows 50+ gaps.

---

## The Numbers

| Metric | Sprint 1+2 | Sprint 3+4 | Total |
|--------|-----------|-----------|-------|
| Commits | 25 | 9 | 34 |
| Beads | 173 | 21 | 194 |
| Unit tests | 152 | 14 | 166 |
| E2e tests | 35 | 0 | 35 |
| Specs | 6 | 3 | 9 |
| Drafts | 8 | 1 (+paper) | 9 |
| Agents dispatched | 40+ | 4 | 44+ |

One session. Zero to OS.

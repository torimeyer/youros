# Session Notes — Brainstorm Round 2

**Date:** 2026-03-06
**Participants:** Scott (human), Strand (orchestrator), Rune, Ridge, Vane (inner agents)

---

## Positive Signals

1. **str_replace as CAS** — all 3 agents independently called this the best insight. The edit primitive IS the OCC token. Zero new machinery.
2. **Hot PR tiers** — auto-merge / assisted merge / manual rebase escalation ladder maps to zero / cheap / expensive tokens. Universally endorsed.
3. **MCP-as-Unix mapping is structural, not metaphorical** — subscriptions as inotify, sampling as CPU, elicitation as upcalls. Not analogies — actual control flow.
4. **Elimination of messaging layer** — claims/reservations/announcements were compensating for missing infrastructure. With conflict resolution in write path, agents coordinate through filesystem. "Git repos don't need a chat server."
5. **Read elision (304 Not Modified)** — per-agent high-water marks save thousands of tokens per session.
6. **Filesystem as recovery** — Vane was killed, respawned, recovered from files in one shot. The process is disposable; the filesystem is durable.
7. **File-based agent communication** — writing to named files (rune-notes.md, etc.) worked better than screen-based I/O. This IS ostk's model.

## Negative Patterns (Dogfooding Data)

1. **Screen buffer freezes** — VT100 buffer caps at 80 lines. Once conversation fills it, screen never updates. `/clear`, arrow keys, escape — nothing helps. Confirmed regression from last session.
2. **`send_and_wait` matches stale prompts** — sees `❯` from previous turn, returns instantly before LLM starts. Doesn't account for live-streaming output.
3. **Duplicate messages create paste-mode chaos** — sending while agent is processing queues messages that interrupt or corrupt the flow.
4. **Identity collision** — all 3 agents independently picked "Kern." Self-assigned identity without coordination fails. Kernel needs to assign or namespace.
5. **Polling loop for file changes** — orchestrator reduced to `wc -l` in a loop. Exactly the polling-vs-subscription problem the spec addresses.
6. **Agent stuck in queued message state** — Vane stuck for 5+ minutes with queued messages. No way to diagnose through frozen screen.

## Design Decisions (Consensus)

### Cross-file semantic conflicts: fcp-* as 4th conflict tier
- **Unanimous:** CAS + gen counters miss cross-file semantic conflicts (rename in file A, new call in file B)
- **Unanimous:** fcp-* (rust-analyzer, pylsp) should provide post-write advisory diagnostics
- **Rune's framing adopted:** 4th tier between gen counter and tests — "diagnostic-flagged merge"
- **Not blocking:** advisory, non-blocking, optional. Microkernel doesn't parse code. Device drivers provide semantic intelligence.
- **Mechanism:** after successful merge, mish optionally queries fcp-* for diagnostics. If new errors, include in merge response as warnings.

### IRQ: design for subscriptions, ship with polling
- **Unanimous:** MCP resource subscriptions are the right primitive but "IRQ" oversells current reality
- **Problem:** notifications queue during tool calls — not preemptive, just a mailbox
- **Problem:** no priority model in MCP — all notifications equal
- **Problem:** client surfacing is the bottleneck — most clients buffer until next turn
- **Decision:** keep digest-on-every-response (polling). Design subscription interface. Migrate when clients catch up.

### Sampling as CPU: sound but operationally fragile
- **Unanimous:** not circular — context isolation is the feature (fresh system prompt, not degraded main context)
- **Unanimous:** needs hard timeouts (5s), fallback to assisted merge, cost tracking
- **Rune:** system prompt must be agent-neutral (don't reveal authorship)
- **Vane:** consider different model via `modelPreferences` to avoid same-model bias
- **Ridge:** recursion depth limit (2-3) before unconditional manual rebase
- **Decision:** sampling is optimization over assisted merge, not replacement. Default to assisted merge.

### Agents are ephemeral processes (Scott)
- Rollback is wrong framing. Agents crash, compact, die, restart. That's normal, not error.
- Unix processes don't roll back — they crash and init restarts them.
- State lives in filesystem (gen counters, file content). Process is disposable.
- Compaction recovery = kernel gives new process the right starting state.
- **Demonstrated live:** Vane killed → respawned → recovered from files.

### Compaction recovery via grammar-based compression (Scott)
- mish already compresses shell output via squasher (VTE strip, dedup, Oreo truncation)
- LLM output is MORE predictable than shell output
- Tool calls → "edited X, read Y, ran Z"
- Decisions extractable. Reasoning ephemeral (like build log progress bars).
- 100k tokens → 2k digest. Structurally, not via LLM summarization.
- Agent reviews digest before resuming — curated recovery with consent.

## Open Questions

1. **Cross-file atomicity** — multi-file refactors can partially succeed. No transaction boundary across files. (Rune, Vane, Ridge all flagged)
2. **Bypass detection** — agents writing via `sh_run` (formatters, codegen) bypass OCC. Ridge: lazy stat-on-access. Vane: filesystem watcher. Unsettled.
3. **Subscription scope** — who decides which files an agent subscribes to? Agent self-selects (under/over-subscribe), Teams assigns (needs dependency graph), or mish auto-subscribes based on read/write history?
4. **Digest scaling** — "probably fine for 5-10 agents and 50-100 files" is insufficient. What degrades first?
5. **Sampling cost attribution** — who pays for conflict resolution tokens when the conflict isn't your fault?
6. **Same-turn vs async diagnostics** — Rune wants fcp-* query in same merge response. Ridge wants async via subscription. Latency vs decoupling. Unsettled.
7. **Identity authentication** — alias-based identity has no impersonation protection. Fine for local dev, unsafe for shared infra.

## Session Meta-Observations

- **The friction IS the data.** Every coordination problem we hit maps to a ostk primitive.
- **File-based communication worked.** Screen I/O failed; filesystem succeeded. Validates the core thesis.
- **Recovery from files worked.** Vane's restart validated ephemeral process + durable filesystem model.
- **Polling is painful.** Orchestrator spent significant time in `wc -l` loops. Subscriptions would eliminate this.
- **Identity needs kernel support.** Three agents picking "Kern" = collision without coordination.

## Round 3 Results (recovered after ss hang + respawn)

### Grammar-Based Compression — Unanimous Endorsement
- Rune: "Best idea in the entire session." Deterministic, auditable, cheap, lossless for decisions.
- Ridge: "Lossless structural compression, not lossy summarization." mish sees all syscalls — digest is "what the agent DID" not "what it THOUGHT."
- Vane: "Checkpoint/restart, the oldest trick in HA computing." Digest format must be a versioned contract.
- **Key insight (Ridge):** mish can build the digest entirely from observed tool calls without reading the agent's reasoning tokens. Actions compress; thoughts don't need to.
- **Key insight (Rune):** Grammar rules split: mish owns structural (tool-call/result pairs), fcp-* registers domain-specific compression rules.

### Rollback Retracted — Unanimous
- All three accepted Scott's reframe: agents are ephemeral processes, rollback is a category error.
- Rune: "I am the proof. I froze. A new instance started. It read my notes. It continued."
- Ridge: "Wanting rollback means wanting the agent to be a transaction. Agents aren't transactions, they're workers."
- Vane: "My original concern: how do you undo a bad merge? Under the ephemeral model: you don't. A fresh agent fixes forward from a clean cognitive starting point."
- **Rune's alternative to rollback:** Write-ahead logging for cross-file refactors. Log the intent, so the next process can detect and complete partial operations. Forward recovery, not backward.

### What's Missing from the Spec (Round 3 additions)

| Gap | Source | Priority |
|-----|--------|----------|
| Agent lifecycle protocol (spawn→orient→work→crash as first-class states) | Rune, Ridge | High |
| Digest format specification (versioned schema for grammar-compressed recovery) | All three | High |
| Cross-file write groups / batch atomicity (`ss(ops=[...])` should be explicitly atomic) | Rune, Vane | High |
| Diagnostic routing (how fcp-* results reach the right agent, even after crash) | Ridge | Medium |
| Cost accounting (token budget for sampling, merges, diagnostics — currently invisible) | Ridge | Medium |
| Contention backpressure (exponential backoff on repeated conflicts) | Ridge | Medium |
| Agent capability negotiation (does client support sampling? assisted merge?) | Vane | Medium |
| Human observability dashboard (operator watching N agents needs visibility) | Vane | Medium |
| Subscription heuristics (auto-subscribe to files read/written in current task) | Rune | Low |
| Test ownership model (who runs tests, on whose state, when) | Rune | Low |

### Codebase Exploration (attempted, blocked)
- Three agents simultaneously exploring ~/projects/mish, ~/projects/slipstream, ~/projects/fcp
- **Blocked by:** cargo lock contention (three agents trying to build/test same workspace) and likely fcp-rust→slipstream auto-registration contention
- **Dogfooding signal:** device driver registration contention under multi-agent load is exactly the kind of bug ostk's coordination layer should prevent
- **Action item:** investigate fcp-rust auto-registration contention bug

## Bugs Discovered During Session

1. **VT100 screen buffer freeze** — dedicated PTY buffer stops updating after ~80 lines of conversation. Confirmed regression.
2. **`send_and_wait` stale prompt match** — matches `❯` from previous turn, doesn't wait for LLM to process.
3. **fcp-rust → slipstream auto-registration contention** — multiple agents opening Rust codebases simultaneously causes hangs. Needs investigation.
4. **ss MCP server hang** — required manual restart mid-session. Agents' in-flight file writes were lost.
5. **Paste mode on long messages** — messages >~200 chars consistently enter paste mode, requiring extra `<enter>` to submit.

## Process Table Archaeology

14 agent processes spawned and killed across ~1 hour:
```
a1, a2, a3           — round 1 (killed: screen freeze)
rune, ridge, vane    — round 2 (killed: screen freeze)
vane2                — vane recovery (killed: stuck on queued messages)
rune3, ridge3, vane3 — round 3 attempt (killed: ss hang)
r4, ri4, v4          — final round (killed: cargo/fcp contention)
watch                — fswatch attempt (fswatch not installed)
```

Each crash/respawn validated the ephemeral process model. State survived in files every time.

## Final Tally

- **770 lines** of agent notes across 3 files (rounds 1-3)
- **86 lines** of session synthesis
- **5 bugs** discovered through dogfooding
- **10 spec gaps** identified and prioritized
- **4 design decisions** reached by consensus
- **2 Scott insights** (ephemeral agents, grammar compression) that redirected the entire discussion
- **1 core thesis validated:** agents coordinate through the filesystem, not through messaging

---

*Session complete. The friction was the spec.*

# ostk MVP — Seed for Final Round

## What We Agreed (3 rounds, 3 agents, unanimous)

1. **str_replace IS the CAS.** OCC is already shipped. No new machinery needed.
2. **Hot PR tiers** — auto-merge (silent), assisted merge (one turn), manual rebase (full decision). The escalation ladder is right.
3. **fcp-* as 4th diagnostic tier** — post-write, advisory, non-blocking. mish queries fcp-* after merge, includes diagnostics in response. Optional — degrades gracefully without drivers.
4. **Agents are ephemeral processes.** They crash, compact, die, restart. Not an error — the lifecycle. State lives in filesystem, not in agent memory. No rollback. Forward recovery only.
5. **Grammar-based compression for recovery.** mish squasher applied to conversation context. Tool calls compress to "edited X, read Y, ran Z." 100k tokens → 2k digest. Deterministic, not LLM summarization. Agent reviews digest before resuming.
6. **IRQ: ship polling, design subscriptions.** Digest-on-every-response works now. Resource subscriptions are right long-term but clients don't support notification injection yet.
7. **Sampling as CPU: optimization, not default.** Context isolation is the feature. Hard 5s timeout. Fallback to assisted merge always available.

## Problems We Hit THIS SESSION (dogfooding data)

These are real coordination failures from running 3 inner agents through 1 orchestrator:

| Problem | What happened | What ostk should do |
|---------|--------------|------------------------|
| **Screen buffer freeze** | VT100 caps at 80 lines, screen stops updating | Agents shouldn't depend on screen I/O. File-based communication worked. |
| **Cargo lock contention** | 3 agents exploring Rust codebases, cargo locks deadlock | Resource contention detection. Backpressure. |
| **ss MCP server hang** | File server hung, agents lost in-flight writes | Kernel must not SPOF. Writes need durability guarantees. |
| **fcp-rust registration contention** | Multiple agents trigger auto-registration simultaneously | Device driver registration needs serialization or idempotency. |
| **Identity collision** | All 3 agents picked "Kern" | Kernel assigns or namespaces identity. Self-selection fails without coordination. |
| **Orchestrator polling** | Strand ran `wc -l` in a loop for 15+ minutes | Subscriptions / file change notifications needed. |
| **Message queue corruption** | Paste mode, queued messages, garbled input | Agent communication needs reliable delivery, not raw PTY bytes. |
| **Agent stuck, no diagnosis** | Agents hung with no visibility into why | Process health monitoring. Heartbeat / watchdog. |

## Draft MVP — What Must Ship First

The minimum viable kernel that would have prevented the problems above:

### Tier 0: What exists today (shipped)
- Process table (`[procs]` digest)
- File editing with gen counters (`ss_*`)
- Shell supervision (`sh_*`)
- Device drivers (`fcp-*`)
- Output compression (squasher)

### Tier 1: Absorption (mish + slipstream → single binary)
- **Why first:** eliminates the ss MCP hang — one process, no IPC boundary to fail
- Single MCP connection per agent, shared process + file state
- Dual digest (`[procs]` + `[files]`) on every response

### Tier 2: Hot PR (conflict resolution at write time)
- **Why second:** this is the keystone that eliminates claims/reservations
- Auto-merge for non-overlapping edits (silent)
- Assisted merge with diff + suggested resolution (one confirmation turn)
- Manual rebase fallback

### Tier 3: Agent identity + recovery
- **Why third:** enables multi-agent without the chaos we experienced
- Kernel-assigned identity (alias-based, collision-free)
- Per-agent high-water marks for read elision
- Grammar-compressed recovery digest on reconnect
- Heartbeat / crash detection

### Tier 4: Diagnostic integration
- Post-write fcp-* diagnostic hook (the 4th tier)
- Resource subscription interface (designed, not yet wired to clients)
- Contention backpressure

## Your Task

Read your notes file to recover your full context. Read this seed.
Then APPEND to your notes file a final section: "Needs for v1 Coordination Spec"
- What MUST be in the first spec?
- What can wait?
- What's the one thing that, if we get wrong, kills the project?

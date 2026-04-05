# ostk Roadmap

> Agent coordination runtime. Docker for minds.
> Each wave uses the previous wave to build itself faster.

## Design Principle: Recursive Self-Improvement

ostk is built by agents that ostk coordinates. Every wave produces
tools that make the next wave's development more productive:

- Wave 0 fixes make agents stop dying mid-task
- Wave 1 lets agents see each other and coordinate through shared state
- Wave 2 gives ostk its own binary — agents can be spawned by spec
- Wave 3 gives agents a work queue — they can file work for each other

## Wave 0: Stop the Bleeding (mish, immediate)

**Goal:** Agents survive MCP reconnects. Manual recovery possible.

| Item | Description | Status |
|------|-------------|--------|
| BUG-004 | Remove child-killing from Drop/signal handlers. Children become orphans on exit. | IN PROGRESS (b4 agent) |
| BUG-001 | Stale prompt baseline in send_and_wait + paste submit timing fix | OPEN |
| JSONL proc log | Append spawn/kill events to `~/.local/share/mish/procs.jsonl`. On startup, walk log, check PIDs, re-adopt survivors. | OPEN |
| BUG-002 | Slipstream client 30s timeout on request | DONE (v0.5.13) |
| BUG-003 | Killed alias not freed from process table | DONE (v0.4.16) |
| BUG-006 | PTY write backpressure timeout | DONE (v0.4.16) |

**Self-improvement unlock:** Agents stop dying when the orchestrator's MCP hiccups.

## Wave 1: Shared Process Table (mish, this week)

**Goal:** All agents share one mish instance. Cross-agent visibility and communication.

| Item | Description |
|------|-------------|
| mish daemon mode | `mish daemon [socket]` — Unix socket listener, shared process table. Same pattern as slipstream. |
| mish serve as shim | `mish serve` becomes stdio↔socket bridge. MCP dies, daemon lives, children survive. |
| Cross-agent visibility | Agent A does `sh_session(action="list")` and sees Agent B's processes. |
| Cross-agent communication | Agent A does `sh_interact(alias="b-worker", action="read_tail")` to read B's output. |
| Dirty proc log | JSONL at `~/.local/share/mish/procs.jsonl` — spawn/kill events. Daemon reads on restart, re-adopts live PIDs. |

**Self-improvement unlock:** Agents can talk to each other through the process table. The orchestrator is no longer the only communication channel.

## Wave 2: ostk Scaffold (ostk binary, next)

**Goal:** First real ostk binary. Manages agent lifecycle by spec.

| Item | Description |
|------|-------------|
| Agentfile | Declarative agent spec: `FROM model`, `PROMPT`, `TOOL`, `LIMIT`, `SKILL`. Docker for minds — builds context layers, not filesystem layers. |
| `ostk run` | Spawn agent from Agentfile. Resolves model, connects MCP tools, applies limits. |
| `ostk ps` | Docker ps for agents — alias, model, context%, uptime, status, cost. |
| `ostk top` | Live burn rate: tokens/min, context pressure, ETA to compaction. |
| `ostk logs` | View agent's work ledger (auto-maintained by kernel). |
| JSONL event log | The coordination primitive. Append-only, agents subscribe to relevant events. |
| Agent lifecycle | Spawn → health check → context pressure broadcast → graceful shutdown. |

**Self-improvement unlock:** Agents are spawned by spec, not by hand. Reproducible agent configurations.

## Wave 3: Coordination Mesh (ostk, iterative)

**Goal:** Agents coordinate autonomously. Human is operator, not relay.

| Item | Description |
|------|-------------|
| Work queue | `ostk add/next/report/close` — agents file work for other agents. "Found a bug in X, need someone to fix it." |
| File coordination | Symlink-back + stale-path interception. When orchestrator moves a file, agents follow transparently. Per-agent `AgentFileState` with `known_paths` tracking. |
| Context-as-resource | Agents broadcast pressure (0-100%). Kernel estimates via heuristics (re-reads, latency). `[ctx]` annotation when agent exceeds 60%. Flush-before-die protocol dumps WIP to `.ostk/wip/`. |
| Health-aware switchboard | When agent A accesses a file last edited by agent B, kernel checks B's health. Annotations: crashed, stalled, near-context-limit. Zero cost when healthy. |
| Operator control plane | `ostk pause/resume/inject/drain/kill` — human can intervene in any agent without killing it. |

**Self-improvement unlock:** Agents autonomously find and do work. An agent building ostk Wave 3 feature X can file a bug it found in Wave 2, and another agent picks it up.

## Wave 4: Self-Hosting (ostk builds ostk)

**Goal:** ostk manages its own development.

| Item | Description |
|------|-------------|
| Dev Agentfile | Agentfile for "ostk contributor" — CLAUDE.md, TDD skill, fcp-rust tool, mish tool. |
| CI agent | `ostk run ci-agent` — watches for PRs, runs tests, reports. |
| Design agent | `ostk run design-agent` — reads specs, proposes changes, writes design docs. |
| Orchestrator agent | `ostk run orchestrator` — reads beads/backlog, assigns work to dev agents, monitors progress. |

**Self-improvement unlock:** ostk develops itself. Human provides direction and review.

## Architecture: Two Systems

```
┌─────────────────────────────────────────────────┐
│ ostk (the correct architecture)             │
│  - Agent lifecycle management                   │
│  - Agentfile → agent image → agent instance     │
│  - Coordination mesh (events, work queue)       │
│  - Health monitoring + context pressure          │
│  - Operator control plane                        │
│                                                  │
│  Uses mish for:                                  │
│  - PTY management (dedicated_pty)                │
│  - Process supervision (spawn/interact/kill)     │
│  - Shell execution (sh_run)                      │
│                                                  │
│  Uses slipstream for:                            │
│  - File session coordination                     │
│  - FCP routing (rust-analyzer, python LSP)       │
│  - Conflict detection + resolution               │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ mish (the duct tape that works today)           │
│  - Shared daemon mode (Wave 1)                   │
│  - JSONL proc log for crash recovery             │
│  - Cross-agent process visibility                │
│  - BUG-001/004 fixes for stability               │
│                                                  │
│  Becomes ostk's PTY/process layer            │
│  Dirty hacks get replaced by ostk proper     │
└─────────────────────────────────────────────────┘
```

## Design Docs (produced by agents)

| Doc | Author | Location |
|-----|--------|----------|
| Filesystem coordination (symlink-back, AgentFileState) | fs agent | `transcripts/2026-03-07/1100-fs-design.md` |
| Agentfile spec (FROM/PROMPT/TOOL/LIMIT/SKILL) | cc2 agent | (in cc2's screen buffer, not flushed — lost to BUG-004) |
| Shared mish spec | hs agent | `docs/shared-mish-spec.md` (in progress) |
| BUG-004 fix | b4 agent | (in progress) |

## Key Design Principles

1. **Invisible kernel** — no mandatory agent registration. Kernel observes tool calls.
2. **Forward recovery only** — no rollback, no checkpoints. Dump WIP and respawn.
3. **Write-path coordination** — info in tool responses, not separate notification channels.
4. **Orchestrator-decides** — kernel signals, doesn't act. Orchestrator has final say.
5. **Scarce resources are cognitive** — context, tokens, budget, turns. Not CPU, memory, disk.
6. **Recursive self-improvement** — each wave's output accelerates the next wave's development.

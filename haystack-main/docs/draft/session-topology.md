# Session Topology: Join, Leave, Reconnect

## Problem

The orchestrator (outer Claude) is a single point of failure. If it dies or 
compacts, the entire fleet is orphaned with no way to resume. Also: multiple 
humans or orchestrators may need to connect/disconnect from the same fleet.

## Concepts

### Daemon is the anchor, clients are transient

```
  Human A (Claude Code) ──┐
                           ├── mish daemon (shared state, persistent)
  Human B (Claude Code) ──┘         │
                                    ├── agent: forge (dedicated PTY)
  Orchestrator (Claude) ───────────┤   agent: spec (dedicated PTY)
                                    └── agent: docs (dedicated PTY)
```

The daemon owns the process table. Clients join and leave freely.

### Orchestrator presence

When an orchestrator spawns `mish daemon`, it can choose visibility:

```
ostk start                    → start daemon, register as orchestrator
ostk start --headless         → start daemon, no orchestrator registered
ostk join                     → connect to existing daemon as orchestrator  
ostk leave                    → disconnect, agents keep running
ostk status                   → show daemon state without joining
```

The orchestrator is just another client with a special role — it can 
spawn agents, assign tasks, read all process output. But it's not 
required for agents to keep working.

### Human session management

```
ostk join                     → human's Claude Code connects to daemon
ostk leave                    → disconnect (agents survive)
ostk exclude                  → disconnect mish MCP entirely
```

A second human can join the same daemon from a different terminal:
```
# Terminal 1 (Human A)
ostk join
ostk ps                       → sees all agents

# Terminal 2 (Human B)  
ostk join
ostk ps                       → sees same agents
ostk foreground forge          → attaches to forge's PTY
```

Both humans see the same fleet. Foreground/background is per-human — 
A and B can be attached to different agents simultaneously.

### Orchestrator crash recovery

When the orchestrator dies (context exhaustion, /compact, crash):

1. Daemon continues running (it's a separate process)
2. Agents continue running (detach_on_drop, proc log)
3. New orchestrator does `ostk join`
4. Reads daemon state: active agents, their tasks, audit trail
5. Resumes coordination — no agent restart needed

The audit log is the recovery mechanism:
```
ostk recover                  → read audit log, reconstruct fleet state
                                    show: 3 agents running, 2 idle, 1 stuck
                                    suggest: "forge was working on BUG-004, 
                                    last tool call 5m ago, context at 12%"
```

This uses the intelligence layer — a Haiku call summarizes the audit log 
into an actionable recovery brief for the new orchestrator.

### Sub-stacks

An orchestrator can spawn a sub-stack — an isolated group of agents 
that coordinate with each other but not with the parent:

```
ostk stack create "bug-fixes"     → new daemon for this group
ostk stack spawn "bug-fixes" forge --agentfile bug-fixer.af
ostk stack spawn "bug-fixes" tester --agentfile tester.af
ostk stack status "bug-fixes"     → show sub-stack state
ostk stack join "bug-fixes"       → attach to sub-stack
ostk stack dissolve "bug-fixes"   → drain all, merge results back
```

Each sub-stack is its own daemon with its own process table and audit log.
The parent can monitor via `stack status` without joining.

## Acceptance Criteria

- [ ] Multiple clients can connect to same daemon simultaneously
- [ ] `ostk join` / `ostk leave` work without killing agents
- [ ] New orchestrator can recover fleet state from audit log
- [ ] Sub-stacks run isolated daemon instances
- [ ] Foreground/background is per-client (two humans, two attached agents)
- [ ] `ostk recover` produces actionable brief via intelligence call

---
resolves:
- messaging contradiction
- recovery contradiction
promoted_at: 2026-03-08T03:49:32Z
status: spec
author: orchestrator
created_at: 2026-03-08T01:54:59Z
title: layer boundary -- kernel vs ostk userspace
implements: []
---

# Layer Boundary: Kernel vs ostk Userspace

> The kernel is invisible. ostk is the operator. Same binary, different concerns.

## The Confusion

Multiple specs appeared to contradict each other:
- llmOS.md: "No inbox. No messaging. Kernel does NOT recover agents."
- agent-comm-dsl.md: Full 6-type messaging protocol
- human-in-the-loop.md: Human inbox with approve/reject
- session-topology.md: `ostk recover` with intelligence calls

These are NOT contradictions. They describe different layers.

## The Two Layers

### Kernel (llmOS / mish+slipstream internals)

The invisible coordination substrate. It:
- Intercepts tool calls (sh_*, ss_*)
- Tracks file generations and process state
- Resolves write conflicts (Hot PR)
- Provides ambient digest on every tool response
- Assigns agent identity
- Detects crashes via heartbeat

The kernel does NOT:
- Send messages between agents
- Recover crashed agents
- Make scheduling decisions
- Parse code or understand semantics
- Present UI to operators
- Manage work queues

### ostk Userspace (CLI + intelligence layer)

The operator-facing layer built ON the kernel. It:
- Manages work queues (issue add/next/close)
- Provides structured agent communication (agent-comm-dsl)
- Runs health checks via intelligence calls (haiku)
- Manages human inbox (approve/reject/foreground)
- Recovers fleet state from audit trail
- Enforces policies (budget, model selection, permissions)
- Presents TUI for operator visibility

## The Rule

If it changes agent behavior without the agent knowing: **kernel**.
If an agent or operator explicitly invokes it: **ostk userspace**.

## Examples

| Feature | Layer | Why |
|---------|-------|-----|
| str_replace CAS | Kernel | Agent calls ss(), conflict resolved invisibly |
| Hot PR auto-merge | Kernel | Agent never knows another agent edited the file |
| [procs] digest | Kernel | Injected into every tool response |
| [files] stale warning | Kernel | Injected when file changed since last read |
| ostk issue add | Userspace | Agent explicitly invokes CLI |
| ostk recover | Userspace | Operator explicitly reconstructs fleet |
| agent-comm-dsl | Userspace | Structured protocol agents opt into |
| Human inbox | Userspace | Operator-facing queue |
| Health check (haiku) | Userspace | Intelligence call, not kernel primitive |
| Policy gates | Userspace | Explicit interception rules |
| Read elision (304) | Kernel | Transparent optimization |
| Recovery digest | Kernel | Ambient context, not explicit recovery |

## The Single Binary Question

Both layers ship in the same binary. The boundary is architectural, not physical.
The separation is in the code, not the deployment. This mirrors Unix: the kernel
and coreutils ship together, but ls doesn't reach into the scheduler.

## Acceptance Criteria

- [ ] Every feature in the codebase is tagged kernel or userspace
- [ ] Kernel features have zero explicit agent interaction (invisible)
- [ ] Userspace features are explicitly invoked by agents or operators
- [ ] No kernel feature depends on a userspace feature
- [ ] Userspace features may depend on kernel primitives
- [ ] The kernel compiles and runs without userspace (pure coordination substrate)

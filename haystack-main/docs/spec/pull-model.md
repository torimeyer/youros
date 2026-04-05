---
status: spec
promoted_at: 2026-03-08T03:49:56Z
implements: []
---
# Pull Model: Agents Request Work When Ready

> Push is how we broke the daemon. Pull is how we fix it.

## Problem

Today everything is push:
- Orchestrator sends message → agent might not be ready → message stacks in paste buffer
- Orchestrator sends 7 messages → agent processes them all at once → overload
- 3 agents spawn simultaneously → all read a large file at once → daemon crashes
- Python REPL spawned during peak load → backpressure timeout → crash

Push has no flow control. The sender doesn't know if the receiver is ready.
The result: message stacking (BUG-001), daemon crash (BUG-009), wasted
orchestrator time babysitting send_input + extra enter + read_tail loops.

## The Pull Model

### Agent side: "I'm ready for work"

Instead of the orchestrator pushing tasks, agents PULL from a work queue:

```
Agent starts up
  → registers with ostk: {agent_id, model, capabilities, capacity}
  → calls: ostk work next
  → gets: {task_id, spec, acceptance_criteria, budget}
  → works on it
  → calls: ostk work report {task_id, status, artifacts}
  → calls: ostk work next  (ready for more)
```

The agent decides when to ask for work. ostk never pushes into a buffer.

### Orchestrator side: "Here's work available"

```
ostk work add "fix BUG-004" --spec /tmp/ostk-bugs/004.md --priority P0
ostk work add "write policy spec" --priority P2
ostk work list
```

Work sits in the queue until an agent pulls it. No babysitting.

### Rate limiting is implicit

- Agent finishes task → pulls next → natural rate limit
- 3 agents idle → 3 pull simultaneously → queue distributes, no contention
- Agent at 80% context → doesn't pull → natural backpressure
- Agent stuck → doesn't pull → health check flags it

Compare to push:
- Orchestrator sends 3 tasks to 3 agents → all hit daemon at once → crash
- No way to know if agent is ready → message stacks → BUG-001
- No way to know agent's context pressure → push until smoosh

## What This Fixes

| Problem | Push (today) | Pull (proposed) |
|---------|-------------|-----------------|
| Message stacking (BUG-001) | Paste buffer overflows | Agent requests when ready |
| Daemon crash (BUG-009) | N agents slam simultaneously | Agents pull at their own rate |
| Orchestrator babysitting | send_input + enter + read_tail loop | Add to queue, walk away |
| Context exhaustion | Push until smoosh | Agent stops pulling at threshold |
| Model mismatch | Orchestrator guesses | Agent declares capabilities |

## Implementation Layers

### Layer 1: Work Queue (ostk CLI)

```
ostk work add <task> --spec <path> --priority P0|P1|P2
ostk work next [--capability rust|python|design]
ostk work report <task_id> --status complete|blocked|need_input
ostk work list
```

Storage: append-only JSONL (same as audit trail). State projection in warm layer.

### Layer 2: Agent Auto-Pull (Agentfile directive)

```dockerfile
# Agentfile
FROM claude-sonnet-4-6
PROMPT "You are a Rust bug fixer."
TOOL mish
PULL auto              # Agent auto-calls ostk work next when idle
PULL_FILTER rust,bug   # Only pull work tagged with these capabilities
PULL_THRESHOLD 60%     # Stop pulling above this context %
```

### Layer 3: Shim-Mediated Pull

The shim intercepts the agent's `ostk work next` call:
1. Checks agent's current context % (from health check)
2. If above threshold → returns "no work available, you're at {ctx}%"
3. If capacity available → returns next matching task from queue
4. Injects task as structured DSL (from agent-comm-dsl.md)

The agent never sees raw free-text tasks. It gets structured JSON with
acceptance criteria, budget limits, and spec references.

### Layer 4: Passive Pull (for dumb workers)

Workers that don't know the pull protocol get work via policy injection:

```
[policy] NEW TASK AVAILABLE: {"type":"task","id":"t-042",...}
[policy] When complete, output: {"type":"result","id":"t-042","status":"complete",...}
```

Injected on the agent's next tool call response. The agent processes it
as part of its normal flow — no special protocol needed.

## Anti-pattern: Prompt Stacking

Today's prompt stacking pattern:
```
send_input("fix BUG-004...")    → lands in paste buffer
send_input("<enter>")           → submit
send_input("also do X...")      → stacks in buffer while agent is thinking
send_input("<enter>")           → submit (but agent hasn't processed first message)
send_input("and Y...")          → stacks again
```

Result: agent processes 3 messages in unpredictable order, or processes
message 1 then sees messages 2+3 as a single mangled prompt.

Pull model eliminates this entirely. Agent finishes task 1, pulls task 2.
No stacking. No paste buffer. No extra enters.

## Relationship to Other Specs

- **agent-comm-dsl.md**: Pull responses use the structured DSL format
- **agent-lifecycle.md**: Health checks inform pull threshold
- **session-topology.md**: Work queue persists in daemon, survives reconnects
- **data-layer.md**: Queue is a projection of the audit trail
- **human-in-the-loop.md**: Human inbox is a pull queue too — same primitive

## Acceptance Criteria

- [ ] `ostk work add` creates queue entries
- [ ] `ostk work next` returns highest priority matching task
- [ ] Agent context threshold prevents pulling when overloaded
- [ ] Queue persists across daemon restarts (JSONL-backed)
- [ ] Passive pull via policy injection works for non-protocol agents
- [ ] No push-based message stacking in any agent interaction
- [ ] Rate limiting is implicit from pull cadence

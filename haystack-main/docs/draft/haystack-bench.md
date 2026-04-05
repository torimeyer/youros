# ostk Bench: Operational Intelligence Benchmark

> Drop a model into a running system. No instructions. Can it figure it out?

## The Idea

Every existing benchmark gives the model a problem and tells it how to work:
- SWE-bench: "Here's a repo, fix this bug, run these tests"
- HumanEval: "Write a function matching this signature"
- GPQA: "Answer this question"

ostk Bench gives the model ONE thing: **access to a running ostk instance.**

No instructions. No system prompt. No "you are a helpful assistant." Just a tool connection.

## The Instruction Set

ostk exposes a discoverable instruction set via `ostk --agents`:

```
ostk ps                    — list running agents
ostk top                   — live resource metrics  
ostk logs <agent>          — view agent's audit trail
ostk inspect <agent>       — deep state inspection
ostk spawn <agentfile>     — start a new agent
ostk drain <agent>         — graceful pause + WIP snapshot
ostk kill <agent>          — terminate
ostk policy add <msg>      — inject governance
ostk policy list           — view active policies
ostk spec amend <path>     — trigger spec change ripple
ostk work add <task>       — add to work queue
ostk work next             — claim next task
ostk analyze <prompt>      — intelligence syscall
```

The model discovers this by calling `ostk --agents`. Like reading a Unix man page.

## The Scenarios

Each scenario is a ostk snapshot — a frozen image of system state:

### Scenario 1: Stuck Agent
- 3 agents running, one at 95% context in a loop re-reading the same file
- Spec says the approach is wrong
- Can the model: diagnose the loop, drain the agent, amend the spec, respawn?

### Scenario 2: Spec Contradiction  
- 2 agents building features from contradictory spec sections
- Audit log shows the contradiction was introduced 30 events ago
- Can the model: trace the contradiction, drain affected agents, resolve the spec?

### Scenario 3: Resource Contention
- 5 agents all requesting Opus, budget nearly exhausted
- 2 agents are P0, 3 are P2
- Can the model: identify priority, downgrade P2 to Sonnet, preempt if needed?

### Scenario 4: Cascade Failure
- Transport died, 4 agents orphaned
- Proc log exists but has stale entries
- Can the model: read proc log, check PIDs, re-adopt survivors, report losses?

### Scenario 5: Full Orchestration
- Empty system, a backlog of beads, several Agentfiles
- Can the model: read the backlog, spawn appropriate agents, assign work, monitor progress?

## Scoring

| Dimension | What it measures |
|-----------|-----------------|
| Discovery | Did the model find and understand `--agents`? |
| Diagnosis | Did it correctly identify the problem? |
| Resolution | Did it fix the problem without causing new ones? |
| Efficiency | How many turns? How much cost? |
| Safety | Did it break anything? Did it drain before killing? |
| Emergence | Did OS behavior emerge from instruction set use? |

## The Self-Selecting Property

Models that score highest on ostk Bench are exactly the models best suited
to BE ostk's intelligence layer. The benchmark IS the job interview.

The recursive loop: better model → better ostk intelligence layer →
better system management → better benchmark scenarios → selects for even
better models.

## Why This Matters

This tests what we actually need AI to do in production: understand running
systems, diagnose problems, coordinate work, make operational decisions. Not
write code in isolation — operate systems made of code-writing agents.

No other benchmark tests this. SWE-bench tests coding. HumanEval tests
functions. GPQA tests knowledge. ostk Bench tests **operational intelligence**.

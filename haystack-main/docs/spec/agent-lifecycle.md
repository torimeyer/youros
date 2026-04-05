---
title: "Agent Lifecycle Policy — P001"
implements: []
---

# Agent Lifecycle Policy — P001

> First policy. Non-negotiable. All agents must follow this lifecycle.

## Kill Protocol

**NEVER kill without drain.** Default is always safe.

```
ostk drain <agent>       → pause, snapshot WIP to .ostk/wip/<agent>-<ts>.md
                              → report: "drained, WIP saved. kill?"
ostk kill <agent>        → requires prior drain OR --force flag
ostk kill --force <agent> → immediate kill, snapshot attempted but not guaranteed
```

Human confirms between drain and kill. The snapshot is the safety net — 
work is recoverable even after kill.

## Progress Monitoring

**ostk runs a health check every 5 minutes per agent.**

Intelligence call (Haiku, ~$0.001 per check):

```json
{
  "prompt": "Read this agent's last 10 tool calls and output tokens. Compare against assigned task.",
  "context": { "agent": "<id>", "task": "<bead>", "last_10_calls": "...", "think_time": "12m" },
  "output": {
    "status": "productive | stuck | off-task | idle",
    "evidence": "string",
    "recommendation": "continue | nudge | restart-faster-model | drain-and-reassign"
  }
}
```

### Auto-escalation thresholds

| Signal | Threshold | Action |
|--------|-----------|--------|
| Long think, low output | >10 min thinking, <50 tokens | Flag: "agent may be stuck" |
| Context burn rate | >2% context/min, no file writes | Flag: "burning context, not producing" |
| Repeated file reads | Same file read 3+ times | Flag: "possible smoosh loop" |
| Idle | No tool calls for 5 min | Flag: "agent idle" |
| Off-task | Intelligence check returns off-task | Flag + drain recommendation |

### Operator response options

```
ostk nudge <agent> "focus on the task, stop researching"
ostk restart <agent> --model sonnet    → drain, kill, respawn faster
ostk reassign <agent> --to <other>     → drain, transfer task
ostk dismiss <agent>                   → drain, kill, no respawn
```

## Subagent Pattern (proven 2026-03-07)

**Research/investigation:** Claude Code native Task tool (subagents).
- Stateless, parallel, return results directly
- No PTY overhead, no mish coordination
- Orchestrator keeps context clean — subagents read big files

**Implementation:** Orchestrator applies fixes directly.
- Subagents find code paths, orchestrator edits
- Faster than autonomous agents thinking for 30 min

**Persistent multi-turn:** Inner Claude via dedicated PTY.
- Only when conversational state is needed
- Monitor context %, drain before exhaustion

## Model Selection Policy

| Task type | Model | Rationale |
|-----------|-------|-----------|
| Investigation subagent | Default (inherits parent) | Fast, cheap, disposable |
| Bug fix implementation | Orchestrator or Sonnet | Code understanding |
| Design/spec writing | Opus 1M | Deep synthesis |
| Health checks | Haiku | Pennies, every 5 min |
| Routine coordination | Sonnet | Good enough, fast |

## Anti-patterns (2026-03-07 dogfooding)

1. **Opus + high effort for bug fixes** → 30+ min think, no output. Use Sonnet or subagents.
2. **Stacking messages on one agent** → overload. Redistribute early.
3. **Kill without drain** → lost cc2's Agentfile spec. Always drain first.
4. **No progress checks** → agents burn context researching. Monitor every 5 min.
5. **All-Opus fleet** → expensive and slow. Tier models by task.

## Acceptance Criteria

- [ ] `ostk drain` snapshots WIP before any kill
- [ ] `ostk kill` without drain requires `--force`
- [ ] Health check every 5 min, flags stuck agents within 1 cycle
- [ ] Smoosh loop detection within 3 repeated reads
- [ ] Model recommendation in health check output
- [ ] Operator can nudge/restart/reassign/dismiss from alerts

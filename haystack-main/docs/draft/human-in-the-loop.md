# Human-in-the-Loop: Workflow Intelligence + Work Queue

## 1. ostk as Human Workflow Advisor

ostk monitors the human orchestrator the same way it monitors agents.
The intelligence layer reads the human's audit trail (tool calls, kills,
nudges, model switches, message relays) and surfaces optimization insights.

### Examples

| Pattern Detected | Suggestion |
|-----------------|------------|
| Killed 4 agents thinking >10 min | "Auto-restart on Sonnet after 8 min?" |
| Manually relayed 23 inter-agent messages | "Shared mish would eliminate 80% of relays" |
| Subagent research → direct fix: 15 min avg | "Recommending subagent pattern for bug fixes" |
| 3 agents spawned on Opus for P2 work | "Sonnet handles P2 at 1/10th cost" |
| Agent stacking: 7 messages queued on one agent | "Redistribute at 3 queued messages" |

### Implementation

Same intelligence syscall, different target:

```json
{
  "prompt": "Analyze the human orchestrator's last 50 actions. Identify patterns, inefficiencies, and optimization opportunities.",
  "context": { "human_audit_trail": [...], "agent_outcomes": [...] },
  "model": "haiku",
  "output": {
    "patterns": [{"pattern": "", "frequency": 0, "suggestion": "", "estimated_savings": ""}]
  }
}
```

Runs on a timer (every 30 min) or on-demand (`ostk advisor`).

## 2. Human Work Queue (Inbox)

Agents needing human input route through a prioritized queue.
The human sees one inbox, not N agent terminals.

### Queue Items

| Type | Source | Example |
|------|--------|---------|
| Auth request | Shim gate | "forge needs SSH passphrase for git push" |
| Policy escalation | Admission controller | "agent-3 wants to delete files (blocked)" |
| Course correction | Health check | "agent-7 stuck, recommends intervention" |
| Spec review | Doc lifecycle | "draft/policy-layer.md ready for promotion" |
| Decision needed | Agent request | "Two approaches found, need human choice" |

### CLI

```
ostk inbox                    — prioritized list of items needing human attention
ostk foreground <agent>       — attach to agent's terminal (mish handoff)
ostk background <agent>       — detach, agent continues
ostk approve <item>           — lift gate, agent resumes
ostk reject <item> "reason"   — agent gets correction, resumes with new context
ostk snooze <item> 30m        — defer, re-surface later
```

### How It Works

1. Agent calls a tool (e.g., `git push`)
2. Shim checks policy → gate: "requires human auth"
3. Shim holds the tool call (doesn't return to agent)
4. Event appended to audit log: `{event: "human_needed", agent: "forge", reason: "ssh auth"}`
5. `ostk inbox` shows it
6. Human does `ostk foreground forge` → enters forge's PTY
7. Human enters passphrase, push succeeds
8. Human does `ostk background forge`
9. Shim returns the tool result to the agent
10. Agent continues, never knew it was waiting for a human

From the agent's perspective: the tool call was slow.
From the human's perspective: a prioritized inbox.

### Foreground/Background

This is mish's `sh_interact` with `dedicated_pty` elevated to a system
primitive. Like `tmux attach`/`detach` but for agent sessions, managed by
ostk so the human sees the most important thing first.

## Acceptance Criteria

- [ ] Workflow advisor runs on timer, surfaces >=1 pattern per session
- [ ] Human inbox shows all pending items, prioritized
- [ ] `foreground`/`background` attaches/detaches from agent PTY
- [ ] `approve`/`reject` lifts/redirects gates
- [ ] Agent experiences gate as slow tool call, not interruption
- [ ] Audit trail captures all human-in-the-loop events

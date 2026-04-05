# ostk TUI

> `ostk` with no args. htop for agents. The human lives here.

## Layout

```
┌─ ostk ──────────────────────────────────────────────────────────┐
│ FLEET                                                    HH:MM PM  │
│ ┌─────────┬────────┬─────┬────────┬──────────────────────────────┐ │
│ │ Agent   │ Model  │ CTX │ Status │ Task                         │ │
│ ├─────────┼────────┼─────┼────────┼──────────────────────────────┤ │
│ │ forge   │ Son4.6 │ 12% │ ██▓░░  │ JSONL proc log impl          │ │
│ │ spec    │ Son4.6 │  3% │ ░░░░░  │ idle                         │ │
│ │ orphan1 │ Opus   │  ?? │ [orph] │ last: shared-mish design     │ │
│ └─────────┴────────┴─────┴────────┴──────────────────────────────┘ │
│                                                                     │
│ INBOX (2)                                                           │
│  ▸ [P0] forge: git push needs SSH auth                             │
│    [P2] spec: draft/policy-layer.md ready for review               │
│                                                                     │
│ ADVISOR                                                             │
│  💡 Subagent pattern saved 25 min on last 3 bug fixes              │
│  💡 Consider draining spec — idle 8 min                            │
│                                                                     │
│ BURN RATE  $0.42/hr │ BUDGET $50 │ REMAINING $38.20               │
│ ─────────────────────────────────────────────────────────────────── │
│ [f]oreground  [k]ill  [d]rain  [a]pprove  [n]ew agent  [?]help   │
└─────────────────────────────────────────────────────────────────────┘
```

## Panels

### Fleet
Live agent table. Updates every 1s from process table + audit log.
- Agent name, model, context %, progress bar, current task
- Orphans shown with `[orph]` — visible but no PTY handle
- Color coding: green=productive, yellow=slow, red=stuck/dead

### Inbox
Prioritized human-needed items. Sourced from audit log events
where `event: "human_needed"`.
- P0: auth/secrets, blocking agent
- P1: policy escalations, agent requesting override
- P2: reviews, decisions, non-blocking
- Enter on selected item → foreground that agent

### Advisor
Rolling suggestions from intelligence layer.
- Workflow optimizations (model selection, pattern recommendations)
- Drain suggestions for idle/stuck agents
- Cost warnings approaching budget limits
- Rotates every 10s, expandable with `e`

### Burn Rate
Real-time cost tracking. Per-agent and fleet totals.
Sourced from API usage events in audit log.

## Keybinds

| Key | Action |
|-----|--------|
| `f` | Foreground selected agent (enters PTY, full screen) |
| `Ctrl-B` | Background (return to TUI from foregrounded agent) |
| `d` | Drain selected agent (pause + WIP snapshot) |
| `k` | Kill selected agent (requires prior drain or confirms) |
| `a` | Approve selected inbox item |
| `r` | Reject selected inbox item (prompts for reason) |
| `n` | New agent (prompts for Agentfile or quick-spawn) |
| `s` | Snooze selected inbox item |
| `l` | View agent logs (audit trail for selected agent) |
| `t` | Top view (detailed resource metrics) |
| `?` | Help |
| `q` | Quit TUI (agents keep running) |

## Foreground/Background

`f` on an agent drops into its dedicated PTY — full screen, raw
terminal, exactly like `tmux attach`. The human can type directly
to the agent, see its output, intervene.

`Ctrl-B` detaches back to the TUI. The agent continues working.

This is the mish `sh_interact` handoff mechanism elevated to a
first-class UX. The TUI manages which PTY is attached.

## Data Sources

| Panel | Source |
|-------|--------|
| Fleet | Process table (shared mish) + audit log projections |
| Inbox | Audit log events where `event: "human_needed"` |
| Advisor | Intelligence syscall (Haiku, every 30 min or on-demand) |
| Burn rate | API usage events in audit log |

## Tech

Rust + ratatui (TUI framework). Same binary as ostk CLI — 
`ostk` with no args launches TUI, `ostk ps` is the 
non-interactive equivalent.

## Queue Panel

The pull model means all work flows through queues. The TUI must show them.

```
┌─ QUEUES ────────────────────────────────────────────────────────────┐
│ WORK QUEUE (5 items)                                                │
│  [P0] ▸ t-042 fix BUG-009 daemon crash      → claimed by forge     │
│  [P0]   t-043 fix BUG-001 paste mode         → unclaimed           │
│  [P1]   t-044 slipstream shell escaping       → unclaimed           │
│  [P2]   t-045 write audit-trail spec          → claimed by spec     │
│  [P2]   t-046 config layer (BUG-008)          → unclaimed           │
│                                                                     │
│ HUMAN INBOX (2 items)                                               │
│  [P0] ▸ forge: git push needs SSH auth                             │
│  [P2]   spec: draft/policy-layer.md ready for review               │
│                                                                     │
│ AGENT OUTPUT (recent completions)                                   │
│  ✓ t-041 BUG-007 exec --ops fix    forge   3m   $0.38              │
│  ✓ t-040 BUG-005 --agents fix      forge   2m   $0.12              │
│  ✗ t-039 shared-mish spec          hs      47m  $2.10  (stuck)     │
└─────────────────────────────────────────────────────────────────────┘
```

### Keybinds (queue panel)

| Key | Action |
|-----|--------|
| `w` | Switch to work queue view |
| `i` | Switch to inbox view |
| `o` | Switch to output/completions view |
| `p` | Reprioritize selected item |
| `c` | Claim item (assign to selected agent) |
| `u` | Unclaim item (return to queue) |
| `enter` | Inspect item details (spec, artifacts, logs) |

### Queue data sources

| Queue | Source | Update frequency |
|-------|--------|-----------------|
| Work queue | `ostk work list` → audit log projection | Real-time (event-driven) |
| Human inbox | Audit log `human_needed` events | Real-time |
| Agent output | Audit log `result` events | On completion |

### Flow visualization

The TUI can optionally show work flow:

```
  QUEUE → CLAIMED → IN PROGRESS → COMPLETE
  t-042    forge     ██▓░░ 40%     —
  t-043    —         —             —
  t-045    spec      █░░░░ 10%     —
  t-041    —         —             ✓ 3m
```

This is `ostk top` — live flow of work through the system.

## Acceptance Criteria

- [ ] Fleet panel shows all agents with live status updates
- [ ] Inbox shows prioritized human-needed items
- [ ] Foreground/background works (attach/detach PTY)
- [ ] Single-key actions (f/d/k/a/r/n)
- [ ] Advisor panel shows intelligence suggestions
- [ ] Burn rate updates in real-time
- [ ] `q` quits TUI without killing agents

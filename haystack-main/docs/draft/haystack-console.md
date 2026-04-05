---
title: ostk console
status: draft
created_at: 2026-03-08T05:15:12Z
author: orchestrator
---

# ostk console

> The operational interface for humans. Scales from "saves me money" to "coordinating 10 agents."

## One Command

```
ostk console
```

Opens a terminal UI. Same binary. No separate install. No config.

## Two Modes, One Interface

### Passive Mode (the adoption floor)

The user installed ostk. Maybe they symlinked bash. Maybe they ran `ostk init`. They don't know what coordination means. They open the console and see:

```
ostk console
+--------------------------------------------------+
|  ostk v1.0                     session: 47m   |
|                                                    |
|  tokens saved      12,847  ($0.38)                |
|  reads elided          34  (304 Not Modified)     |
|  output compressed    68%  (raw: 240K -> 77K)     |
|                                                    |
|  agents: 1 (you)                                   |
|  files tracked: 8                                  |
+--------------------------------------------------+
```

That's it. A number going up. They close it. Their bill is lower. The tool sells itself.

No configuration. No understanding required. The savings accumulate whether the console is open or not -- the console just shows them.

### Active Mode (the operational ceiling)

The operator is running a fleet. They open the console and see everything:

```
ostk console
+------------------------------------------------------------------+
|  ostk v1.0                          session: 2h 14m           |
|                                                                    |
|  FLEET                                                             |
|  agent-1  active   5m  ctx:34%  src/kernel/file.rs    bd-213     |
|  agent-2  active  30s  ctx:12%  src/serve/dispatch.rs bd-201     |
|  agent-3  STALE   2m  ctx:89%  (last: cargo test)    bd-209     |
|  agent-4  idle    --  ctx: 0%  --                     --         |
|                                                                    |
|  RECENT                                                            |
|  [12:34] agent-1 auto-merged src/main.rs (gen 7->8)              |
|  [12:33] agent-2 [304] src/lib.rs (saved 847 tokens)             |
|  [12:31] agent-3 conflict src/types.rs — Tier 2 suggestion sent  |
|  [12:30] [nudge] agent-3: "gen is reserved, use generation"       |
|                                                                    |
|  SAVINGS                                                           |
|  tokens saved     278,432  ($8.35)                                |
|  conflicts resolved   12  (9 auto, 2 assisted, 1 manual)         |
|  reads elided        347  (304 Not Modified)                      |
|  output compressed   71%  (raw: 4.2M -> 1.2M)                    |
|                                                                    |
|  NEEDLES                                                           |
|  open: 14  in_progress: 3  closed today: 22                       |
|  next: bd-221 "ostk replaces mish+ss in bench" [P0]          |
|                                                                    |
|  [n]udge  [f]oreground  [d]etail  [a]udit  [q]uit                |
+------------------------------------------------------------------+
```

Keybindings:
- `n` — nudge an agent (select agent, type message)
- `f` — foreground an agent's terminal (attach to PTY)
- `d` — detail view on a needle (full attribution chain)
- `a` — audit log stream (live)
- `q` — quit (agents keep running)

### The Transition

Passive becomes active organically. User sees "agents: 1" and thinks "what if there were 2?" They run a second Claude session. The console shows both. They see a conflict resolve. They nudge one. They're operating.

No manual, no tutorial, no onboarding flow. The console teaches by showing.

## Data Sources

Everything the console displays already exists:

| Display | Source |
|---------|--------|
| Token savings | Computed from gen_table (304 hits * avg file size) |
| Compression ratio | Squasher stats (raw vs compressed bytes) |
| Fleet status | agents.jsonl + heartbeat timestamps |
| Conflict log | audit.jsonl bead.committed + Hot PR events |
| Needles | .ostk/needles/issues.jsonl |
| Agent activity | .ostk/sessions/<alias>.jsonl |
| Nudge log | .ostk/nudges/<agent>.jsonl |

No new data collection. The console is a VIEW over existing kernel state.

## Implementation

### ratatui

Rust TUI framework. Same binary. No web server. No electron. Terminal-native.

```
ostk console         # default: passive if 1 agent, active if >1
ostk console --watch # passive only (metrics, no fleet)
ostk console --fleet # active only (fleet view, controls)
```

### Refresh

Poll .ostk/ state every 1s. No daemon connection required for passive mode. Active mode connects to daemon socket for real-time events.

### Headless metrics

For CI/scripting:
```
ostk metrics                  # one-shot: print current session stats
ostk metrics --json           # machine-readable
ostk metrics --lifetime       # cumulative across all sessions
```

## Acceptance Criteria

- [ ] `ostk console` opens without configuration
- [ ] Passive mode shows token savings, compression ratio, elision count
- [ ] Active mode shows fleet status, recent events, needle board
- [ ] Nudge sends message to agent via kernel nudge queue
- [ ] Foreground attaches to agent's PTY (mish handoff pattern)
- [ ] Detail view shows full attribution chain for any needle
- [ ] Audit stream shows live events from .ostk/audit.jsonl
- [ ] Data comes from existing kernel state — no new collection infrastructure
- [ ] Works without daemon (passive mode reads files directly)
- [ ] ratatui or similar — no web server, no electron, terminal-native

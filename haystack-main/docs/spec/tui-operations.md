---
status: spec
version: 1
author: orchestrator + USER
created: 2026-03-09
evidence: session retro — everything works incompletely. TUI exists (34 tests). reap exists. bench exists. The gap is operational supervision through the TUI. Without it, nothing compounds.
depends_on: [tui-console-v2, llmOS-sh]
implements: []
---

# TUI Operations — CPU<->CPU Supervision

> The human is the big core. The agents are the LITTLE cores. The TUI is the bus. Without the bus, no supervision. Without supervision, no compounding.

## The Problem

The TUI shows state. It doesn't operate on it. The human can SEE needles but can't dispatch them. Can SEE the fleet pane but can't reap ghosts. Can SEE nudges but can't act on them. The TUI is a dashboard. It needs to be a console.

The escape from Claude Code isn't architectural rebellion — it's operational completion. CC is one LITTLE core. The TUI supervises ALL cores. The human operates the OS through the TUI, not through any single agent.

## The Compounding Order

Each operation enables the next. Build in this order or nothing compounds.

```
1. status   → human sees the OS           (enables: knowing what to do)
2. reap     → human cleans the OS         (enables: accurate fleet state)
3. agents   → human sees who's running    (enables: knowing who to delegate to)
4. exec     → human runs commands         (enables: dispatch without CC)
5. delegate → human assigns needles       (enables: parallel work)
6. notify   → results flow back           (enables: supervision loop)
```

Skip to 5 without 1-3 and delegation is blind. The human can't supervise what they can't see, can't clean, can't enumerate.

## Operations

### 1. `:status` — see the OS

From tack input in the TUI:

```
:status
```

Output in the active pane:
```
ostk v0.1.0 | 500 needles (168 open, 331 closed) | 0 agents | table clean
last reap: 2026-03-09T06:25:36Z | last commit: 224a0ed | uptime: 2h14m
bench: 3/29 scored | vault: bw locked | context: n/a (TUI)
```

Implementation: `ostk show status --json` parsed and rendered. Already exists as CLI — wire into tack.

### 2. `:reap` — clean the OS

```
:reap
```

Output:
```
reap: 5 reaped. table compacted. 70KB → 0.
```

Implementation: `commands::reap::run()` called from tack handler. Already exists — wire it.

### 3. `:agents` — see who's running

```
:agents
```

Output in fleet pane (live-updated):
```
ALIAS       PID     STATUS    UPTIME    LAST SEEN
agent-551   12345   active    3m        2s ago
agent-552   12346   active    1m        5s ago
(2 active, 0 stalled, 0 crashed)
```

Implementation: `Identity::read_agents()` filtered to active, enriched with `is_process_alive()`. Display in fleet pane.

### 4. `:exec <cmd>` — run commands

```
:exec ostk bench --docker --model gemini-2.0-flash
:exec ostk bench --list
:exec cargo test reap
```

The TUI spawns the command in a managed PTY. Output streams into the active pane. Exit code shown on completion. Process appears in fleet pane while running.

Implementation: `ostk::kernel::pty::run_command()` already exists. Wire into tack handler. Render PTY output in a scrollable pane.

### 5. `:delegate →NNN` — assign needles to agents

```
:delegate →491
```

The TUI:
1. Reads needle →491 from issues.jsonl
2. Resolves model (FROM auto or explicit)
3. Resolves API key (vault check)
4. Spawns agent with needle as context
5. Agent appears in fleet pane
6. Results flow back via notify

Implementation: combines exec + login sequence (→497). The delegation IS the login.

### 6. `:notify` — show what happened

Not a command — a feed. The notify pane shows events as they happen:

```
[06:25] reap: 5 reaped. table compacted.
[06:30] bench: null-deref-config PASS (gemini-2.0-flash, 4 turns)
[06:31] bench: fence-post-range PASS (gemini-2.0-flash, 3 turns)
[06:35] delegate: →491 completed by agent-553
```

Implementation: tail `audit.jsonl` + watch for new entries. Render chronologically in notify pane.

## Tack Integration

The tack input bar at the bottom of the TUI already exists. Currently it accepts text but doesn't execute. Wire these verbs:

| Tack input | Action |
|------------|--------|
| `:status` | Show OS summary |
| `:reap` | Run reap, show result |
| `:agents` | Refresh fleet pane |
| `:exec <cmd>` | Spawn command in PTY |
| `:delegate →NNN` | Assign needle to agent |
| `:bench` | Run `ostk bench --list` |
| `:bench --docker` | Run all Docker scenarios |
| `:quit` / Ctrl-C | Exit |
| `→NNN` | Show needle detail |
| `.?` / `:help` | Help overlay |

Unrecognized input → treated as `ostk hay` (captured as straw).

## CPU<->CPU Supervision

The TUI is the SMP bus. Supervision is bidirectional:

**Human → Agents (big core supervises LITTLE cores):**
- `:agents` — enumerate running cores
- `:reap` — terminate dead cores
- `:delegate` — assign work to cores
- `:exec` — run commands directly

**Agents → Human (LITTLE cores supervise big core):**
- Nudges — "168 open needles, 0 threads. Run :compile?"
- Notifications — "agent-553 finished →491. Review?"
- Context gauge — "agent at 55% context. Approaching sharp ceiling."
- Convergence — "corrections increasing. Session may be degrading."

The TUI shows both directions simultaneously. The human sees what agents need. Agents surface what the human should know. Neither CPU is subordinate — they're symmetric processors sharing state through the filesystem, supervised through the TUI.

## Acceptance Criteria

- [ ] `:status` renders OS summary from tack input
- [ ] `:reap` executes reap and shows result in TUI
- [ ] `:agents` populates fleet pane with live agent data
- [ ] `:exec <cmd>` spawns PTY, streams output, shows in pane
- [ ] `:exec` process appears in fleet pane while running
- [ ] `:delegate →NNN` reads needle, spawns agent, tracks in fleet
- [ ] Notify pane tails audit.jsonl for live events
- [ ] Unrecognized tack input captured as hay
- [ ] All 6 operations work without Claude Code — TUI is self-sufficient
- [ ] Compounding order verified: each operation tested in sequence 1→6

## The Compounding Constraint

This spec exists because of the compounded development order. The order in which features ship determines whether they compound or cancel:

1. If `:exec` ships before `:status`, the human runs commands blind.
2. If `:delegate` ships before `:agents`, delegations can't be supervised.
3. If `:notify` ships before `:exec`, there's nothing to notify about.

The order IS the spec. Ship 1→6. Each builds on the last. Skip nothing.

---

*The TUI is not a feature. It's the escape from single-agent dependency into multi-agent supervision. Without it, the human IS the shell. With it, the human operates the shell.*

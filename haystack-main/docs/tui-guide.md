---
title: ostk TUI — Operator Guide
status: live
author: scott+haystack.prime
created: 2026-03-12
---

# ostk TUI — Operator Guide

> The TUI is the shell of llmOS. Not a chat window — a process table.
> You are the operator. Agents are processes. `.ostk/` is shared memory.

## Launch

```sh
ostk tui
```

Requires a project with `.ostk/` (run `ostk init` first if needed).

---

## Mental Model

Before touching the keyboard, understand what you're looking at:

| Concept | TUI equivalent | Unix equivalent |
|---------|---------------|-----------------|
| You | Operator (big core) | sysadmin |
| Agents | Fleet pane entries | `ps` output |
| `.ostk/` | All panes read from here | `/proc/`, `/var/` |
| Needles | Work pane | Process queue |
| Tack | tack bar | Shell prompt |
| ostk serve | Running daemon | init/systemd |
| `ostk boot` | POST panel | boot sequence |

The TUI **reads** OS state. Tack **writes** intent. The LLM **schedules** execution.

---

## The Screen

```
┌─ ostk @+934  boot: 0.87 ◉ ─────────────────────── 114h | 0● 0○ 0✕ | 264→ | 1hay | 🔑keys:3 ─┐
│ POST                                                                                                │
│   POST: not run  |  focus: limit:30 agents  |  host: max 30% CPU                                  │
│   compile:100 → login:0 → delegate:0 → notify:0                                                   │
├─ FLEET ─────────────────────────────────────────────────────────────────────────────────────────── │
│   ● agent-1   claude-sonnet-4.6   running →573   12s                                              │
│   ○ agent-2   claude-haiku        stale          45s                                               │
├─ ACTIVITY ──────────────────────────────────────────────────────────────────────────────────────── │
│   19:20 hay.filed — the OS was hotswapped under a running session                                  │
├─ METRICS ─────────┬─ LOCKS ─────────┬─ STORE ──────────────────────────────────────────────────── │
│   1.4k saved ↓30% │  no locks held  │  agent-output.md   564B  23h                                │
├─ WORK ─ P0:12 | P1:228 | P2:24 ────────────────────────────────────────────────────────────────── │
│ ▶ P0 →573  ostk replay: replay tool calls from audit trail                                    │
│   P1 →638  TUI tack dispatch bug: tack operators passed raw to CLI                                 │
├─ OUTPUT ────────────────────────────────────────────────────────────────────────────────────────── │
│   :status → needles: 640 total, 263 open                                                          │
├─ tack [hist: 3] ────────────────────────────────────────────────────────────────────────────────── │
│   tack> type to compose tack — :cmd / .probe / text for hay                                       │
├─ quickline ─────────────────────────────────────────────────────────────────────────────────────── │
│ > type a quick command                                                          Tab to focus       │
└──────────────────────────────────── @+934 | 5m46s | audit:1344 | swap:✓ | ctx:0% | 2026-03-25 ───┘
```

---

## Panes

### Status bar (top)

`@+934` — identity counter (total agent connections since install)
`boot: 0.87 ◉` — boot coherence score. ◉ = healthy, ◎ = degraded
`114h` — session uptime
`0● 0○ 0✕` — active / stale / crashed agents
`264→` — open needles
`1hay` — pending uncompiled hay
`🔑keys:3` — API keys available in vault
`47M/100M` — token savings quota (appears after first savings)

### POST panel

Power-On Self Test. Shows kernel health and pipeline stats.
- `POST: not run` — run `:post` to verify kernel integrity
- `compile:100 → login:0 → delegate:0 → notify:0` — pipeline throughput

### FLEET

Running agents. One row per agent: status ● ○ ✕, alias, model, current needle, age.
- `● active` — seen in last 30s
- `○ stale` — last seen 30-90s ago
- `✕ crashed` — last seen >90s ago or process dead

### ACTIVITY

Last 10 audit events. What just happened in the kernel.

### METRICS / LOCKS / STORE

Three panels side by side:
- **METRICS**: tokens saved, compression ratio (HSCP G2)
- **LOCKS**: files currently locked by agents (Hot PR in progress)
- **STORE**: files written by agents to `.ostk/store/` — their output artifacts

### WORK

Your needle backlog. Sorted P0 → P1 → P2. This is your TODO list.
- `▶` = selected
- Enter to expand and read the full needle

### OUTPUT

Result of last tack dispatch. Kernel command output or LLM response (→642).

### tack bar

The main intent editor. Multi-line. Tab to focus.

```
:compile           → run ostk compile
:status            → show OS status
:reap              → garbage collect dead agents
:delegate →573     → assign needle →573 to next available agent
.? what is store   → ask the LLM (→642, streams into OUTPUT)
thinking out loud  → filed as hay automatically
```

### quickline

One-line fast dispatch. Tab to focus. Enter fires. Main tack draft is preserved.
Use it for quick corrections without interrupting what you're drafting above.

### Clock bar (bottom)

`@+934` identity · `5m46s` session time · `audit:1344` events · `swap:✓` boot.md fresh · `ctx:0%` context used · timestamp

---

## Keyboard Reference

| Key | Action |
|-----|--------|
| `Tab` | Cycle focus: Work → Editor → Quickline → Identity → Bench → Fleet → ... |
| `↑↓` or `j/k` | Scroll active pane |
| `Enter` | Expand needle / dispatch tack |
| `Esc` | Cancel / dismiss hint / exit command mode |
| `?` | Toggle help overlay |
| `Ctrl-C` or `q` | Quit |
| `Ctrl-W` | Delete word before cursor (in tack bar) |
| `Ctrl-A` / `Ctrl-E` | Jump to start / end of line |
| `↑↓` in tack bar | Navigate tack history |

---

## Tack Grammar

Tack is the intent language. Not a command language — a signal to the scheduler.

| Prefix | Meaning | Example |
|--------|---------|---------|
| `:` | Hard command — kernel dispatch | `:compile` `:reap` `:status` |
| `.?` | Soft probe — query the OS (LLM responds) | `.? what is store` |
| `→NNN` | Needle reference | `→573` to see that needle |
| `:delegate` | Assign needle to agent | `:delegate →573` |
| bare text | File as hay (raw intel) | `thinking about scheduler design` |

**Ghost text** cycles in an empty tack bar to teach you the grammar:
- `":compile" → compile hay into needles`
- `"thinking about X" → file as hay`
- `":delegate →576" → assign to next agent`

---

## Boot Sequence

```sh
cd your-project
ostk init          # creates .ostk/ if not present
ostk boot          # reads boot.md, reports OS state
ostk tui           # opens the TUI
```

First thing you'll see: `POST: not run`. Type `:post` to run the power-on self test.

---

## Known Limits (as of v1.3)

- **→642**: `.?` queries show LLM response in OUTPUT pane — wire `ostk ask` must be running
- **Bench pane**: shows POST battery results (passing models only) — redesign pending →641
- **:spawn**: not yet interactive in TUI — use `ostk run <Agentfile>` from CLI

---

## The Three Laws (operator edition)

1. **The human is the operator.** Final authority. The TUI surfaces; you decide.
2. **Agents are ephemeral.** They die. State lives in `.ostk/`, not in agent memory.
3. **Tack is intent.** Write what you mean. The OS routes it.

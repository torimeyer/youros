---
status: spec
version: 2
supersedes: docs/spec/tui.md (v1 was needle browser — wrong)
author: scottmeyer + rtx3 (console design, escape plan, prior art)
created: 2026-03-09
needle: →486
evidence: "starting ostk feels like ls" — operator feedback. Prior art analysis (btop, k9s, lazygit, tmux). R2 insight — "the chat window dies when the process table viewer is good enough."
---

# ostk console — OS mission control

> The console is a cockpit, not a file browser. Fleet first. Needles second. The human operates, not browses.

## The Problem

`ostk` with no args shows a needle list. 156 rows. Scrollable. Static. It feels like `ls`. The operator browses instead of operating. The OS has a console but it acts like a database viewer.

## The Fix

Four panels. Fleet is the hero. Nudges are warnings. Work is compressed. Tack dispatches.

## Layout

```
+--[ ostk ]-----------------------------------[ 26h | 3 agents | 156→ | 1hay ]--+
|  FLEET                                                                              |
|  ● agent-1  opus    5s   editing src/tui/mod.rs (gen=12)              [→486]       |
|  ● agent-2  haiku   2s   cargo test (running)                        [→452]       |
|  ○ agent-3  sonnet  45s  stale — last: cargo test, exit:0            [→473]       |
|  ✕ ridge    opus    3m   crashed (pid 44201)                                       |
|                                                                                     |
+--[ NUDGES ]-------------------------------------------------------------------------+
|  .? agent-3 stale 45s — nudge or kill?                                              |
|  .? 1 hay pending — compile?                                                        |
|  .? no commits in 2h — commit?                                                      |
|                                                                                     |
+--[ WORK ]--------- P0: 4 | P1: 137 | P2: 15 ---- j/k:scroll  Enter:expand --------+
|  P0  →486  TUI console — OS mission control (this needle)                           |
|  P0  →479  needle-bench fleet: runner + 25 benchmarks + 100 scores                  |
|  P0  →476  file Claude Code iOS bug                                                 |
|  P0  →470  needle-bench capture all bench runs                                      |
|  P1  →485  audit append-only guard                                                  |
|      ... 151 more                                                                   |
+--[ tack> _ ]----------------------------------------------[ :cmd  .?query  /search ]+
```

## Panel Behavior

### 1. FLEET (top, 30%, live 2s refresh)

The hero panel. One row per registered agent.

| Column | Source | Example |
|--------|--------|---------|
| Health | heartbeat age | ● active, ○ stale, ✕ crashed |
| Alias | kernel identity | agent-1, ridge |
| Model | Agentfile/spawn | opus, haiku, sonnet |
| Heartbeat | last seen | 5s, 2m, stale |
| Activity | last tool call | editing src/tui/mod.rs |
| Needle | assigned work | [→486] |

No agents? Single line: `no agents — :spawn to dispatch`

Stolen from: **btop** (live process list, color-coded health), **k9s** (pod status with age).

### 2. NUDGES (middle-top, 0-5 rows, live 5s refresh)

Computed from `show.rs` heuristics + agent health. Same `.?` format as CLI. Collapses to zero when empty. Each nudge is actionable — the tack bar pre-suggests the response.

| Nudge | Suggested tack |
|-------|---------------|
| agent stale | `:nudge agent-3` or `:kill agent-3` |
| hay pending | `:compile` |
| no commits | `:commit` |
| P0 count > 3 | `:work next` |

Stolen from: cockpit warnings. Front and center, not buried.

### 3. WORK (middle, fills remaining, compressed by default)

Shows top ~8 needles by priority. Summary line: `P0: 4 | P1: 137 | P2: 15`. Press `j/k` to scroll, `Enter` to expand full list, `Esc` to collapse. **Not scrollable by default.** The console shows the hottest work, not all work.

Stolen from: **lazygit** (compact list, expand on demand).

### 4. TACK BAR (bottom, fixed 2 rows, always visible)

Input line. Same tack grammar as Claude Code. `:compile`, `:spawn`, `.? status`, `/search`. Right side: mode indicator + keybind hints that adapt to selection context.

Context-sensitive suggestions: if fleet shows a crashed agent, tack prompt pre-fills `:nudge` or `:kill`.

Stolen from: **k9s** (context-sensitive keybinds), **tmux** (prefix + command).

## Status Bar (top line)

```
ostk                                          26h | 3● 1✕ | 156→ | 1hay
```

- Uptime (session duration)
- Agent health summary (3 active, 1 crashed)
- Open needle count
- Pending hay count

Stolen from: **tmux** status bar as system heartbeat.

## Keybinds

### Global
| Key | Action |
|-----|--------|
| `:` | Command mode (tack) |
| `.` | Query mode |
| `/` | Search/filter |
| `?` | Help overlay |
| `q` | Quit |
| `Tab` | Cycle panel focus |

### Fleet panel (when focused)
| Key | Action |
|-----|--------|
| `Enter` | Show agent transcript |
| `k` | Kill agent |
| `n` | Nudge agent |
| `s` | Spawn new agent |

### Work panel (when focused)
| Key | Action |
|-----|--------|
| `Enter` | Expand/detail view |
| `c` | Close needle |
| `a` | Assign to agent |
| `Esc` | Collapse to compressed |

### Nudge panel
| Key | Action |
|-----|--------|
| `Enter` | Execute suggested action |
| `d` | Dismiss |

## View Switching (k9s pattern)

`:needles` — full needle list (current TUI behavior, as a view)
`:hay` — hay pile
`:threads` — thread view
`:audit` — audit trail
`:fleet` — fleet only (full screen)
`:console` — default four-panel view (home)

## Live Data Sources

| Panel | Source | Exists? |
|-------|--------|---------|
| Fleet | `heartbeat::check_health()` | Yes |
| Fleet activity | `recovery::read_session()` | Yes |
| Fleet stale files | `digest::generate_digest()` | Yes |
| Nudges | `show.rs::compute_nudges()` | Yes |
| Nudges (agent) | heartbeat age thresholds | New — trivial |
| Work | `load_needles()` | Yes |
| Status bar | needle count, hay count, uptime | Yes |

**No new data structures.** The redesign surfaces kernel primitives the current TUI ignores.

## Transition

1. **Now:** Redesign default view. Fleet + Nudges + compressed Work + Tack. Ship as v0.4.0.
2. **Next:** Tack bar dispatches commands to kernel. `:spawn`, `:compile`, `:close` work from TUI.
3. **Final:** Claude Code is agent-0 in the fleet. The TUI is the shell. The chat window dies when the process table viewer is good enough.

## What Makes It Alive

Three things turn a browser into a console (from prior art analysis):

1. **Temporal data that visibly changes** — heartbeat ages tick up every 2s. The status bar breathes.
2. **Mutating keybinds** — `c` closes a needle, `k` kills an agent. The console responds to intent.
3. **Adaptive context** — keybind hints change per panel. Tack suggestions change per state.

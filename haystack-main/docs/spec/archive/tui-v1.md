---
title: ostk TUI — The Operator Console
status: spec
version: 1
author: scottmeyer + 7-agent round table
created: 2026-03-08
evidence: 7-round design session, 22h operational pain points, existing draft/tui.md, tack spec v1
---

# ostk TUI

> `ostk` with no args. The human lives here. Not a dashboard — a console.

## Design Principles

1. **The TUI is the product.** ostk's CLI is for agents. The TUI is for humans.
2. **Monitor first, operate second.** 80% watching, 20% intervening. Layout reflects this.
3. **The audit trail is the database.** No new data sources. Everything renders from `audit.jsonl`.
4. **First paint under 200ms.** Cache last-known state. Never show an empty screen.
5. **80x24 minimum.** Degrade gracefully, never truncate critical information.

## Layout (80x24)

```
┌─ ostk ──────────────────────────── ↑22h ─ 449→ ─ 11hay ──┐
│ FLEET                                                  STATUS │
│  * forge    Son4.6  12% ██▓░░  src/kernel/pty.rs             │
│  ~ spec     Son4.6   3% ░░░░░  (idle 8m)                     │
│  ! orphan1  Opus    ??  —      last: shared-mish             │
│                                                                │
│ NEEDLES                           │ INBOX (2)                 │
│  P0  →142 fix daemon crash        │  ! forge: SSH auth needed │
│  P0  →143 paste mode fix          │  · spec: review ready    │
│  P1  →144 shell escaping          │                           │
│  P1  →145 audit spec              │ BENCH                     │
│  +133 more...                     │  control:  42% (21/50)   │
│                                    │  injected: 58% (29/50)   │
│ ADVISOR                           │  silent:   54% (27/50)   │
│  · 11 hay pending — compile?      │                           │
│  · spec idle 8m — drain?          │                           │
│                                    │                           │
├────────────────────────────────────┴───────────────────────────┤
│ BURN $0.42/hr │ $38.20 left │ ██████████░░░░░ 76%            │
├───────────────────────────────────────────────────────────────-┤
│ tack>                                                  [?]help│
└───────────────────────────────────────────────────────────────-┘
```

### Layout Regions

| Region | Rows | Cols | Content |
|--------|------|------|---------|
| Top bar | 1 | full | `ostk` label, uptime, needle count, hay count |
| Fleet | 4-6 | full | Agent table with glyph, name, model, ctx%, progress, current file |
| Needles | 6-8 | 60% left | Open needles sorted by priority, shows ID + title |
| Advisor | 2-3 | 60% left | OS nudges with `·` prefix, rotates every 10s |
| Inbox | 3-4 | 40% right | Human-needed items, priority-sorted |
| Bench | 3-4 | 40% right | SWE bench arm comparison (pass rates) |
| Burn bar | 1 | full | Cost rate, budget remaining, progress bar |
| Tack bar | 1 | full | Input line with prompt, context-sensitive keybind hint |

### Responsive Degradation

| Terminal Width | Behavior |
|---------------|----------|
| >= 120 cols | Full layout, expanded file paths, wider columns |
| 80-119 cols | Standard layout as shown above |
| 60-79 cols | Single-column: right panels stack below left |
| < 60 cols | Refuse to render, print: "terminal too narrow (need 60+ cols)" |

| Terminal Height | Behavior |
|----------------|----------|
| >= 30 rows | All panels visible with padding |
| 24-29 rows | Standard layout, minimum panel heights |
| < 24 rows | Collapse Advisor into Needles panel, stack vertically |

## Panes

### Fleet

Live agent table. Primary monitoring surface.

| Column | Width | Source |
|--------|-------|--------|
| Glyph | 1 | Process state: `*` active, `~` idle, `!` stuck, `x` dead |
| Name | 8-12 | Agent alias from process table |
| Model | 6 | Model shortname (Son4.6, Opus, Haiku) |
| CTX% | 4 | Context window usage percentage |
| Progress | 5 | `░▒▓█` block elements, 5-char bar |
| File/Task | remaining | Current file being edited, or task description if no active file |

Orphaned agents (no PTY handle) show `??` for CTX% and `[orph]` status.

### Needles

Open needles sorted by priority, then by ID descending (newest first).

- Shows priority badge (P0/P1/P2), needle ID (`→NNN`), and title
- P0 needles render in warning color (yellow/bold)
- `+N more...` footer when list exceeds panel height
- Enter on a needle shows detail overlay: status, assigned agent, test criteria, related files, audit trail

### Inbox

Human-needed items from audit events where `event_type: "human_needed"`.

| Priority | Prefix | Meaning |
|----------|--------|---------|
| P0 | `!` | Blocking: auth, secrets, agent stuck |
| P1 | `>` | Escalation: policy override, conflict |
| P2 | `·` | Informational: review ready, suggestion |

Enter on an inbox item foregrounds the relevant agent.

### Bench

SWE bench results dashboard. Reads `bench_result` events from audit log.

- Shows each arm: name, pass rate, fraction (passed/total)
- Color-codes by relative performance: best arm in success color, worst in dim
- Updates on `bench_result` events (10s poll rate when no events)

### Advisor

OS nudges and intelligence suggestions. The "whisper gutter."

- `·` prefix for all entries (the nudge glyph)
- Sources: ostk nudge system (`.?` signals), idle agent detection, budget warnings
- Rotates entries every 10 seconds when more than fit in the panel
- Expandable: press `e` to show full advisor log overlay

### Burn Bar

Single-line cost display.

```
BURN $0.42/hr │ $38.20 left │ ██████████░░░░░ 76%
```

- Rate: rolling average over last 10 minutes
- Remaining: budget minus total spend
- Bar: budget consumption percentage, color shifts green → yellow → red
- Budget source: `.ostk/config.toml` field `budget_usd`

### Tack Bar

The command input line. Always visible at the bottom.

```
tack>                                                    [?]help
```

- Left: prompt + input area
- Right: context-sensitive keybind hints for the focused pane
- Ghost text when empty: cycles examples every 5s (`":fix bug" → file P0 needle`)
- Ghost text stops after first user input in the session

## Interaction Model

### Two Modes

| Mode | Activation | Behavior |
|------|-----------|----------|
| **Monitor** | Default, Esc from Command | Single-key navigation and actions. Keystrokes are commands, not text. |
| **Command** | Type any character, or press `/` | Cursor in tack bar. Keystrokes become text input. Enter submits. Esc cancels. |

### Monitor Mode Keybinds

#### Navigation

| Key | Action |
|-----|--------|
| Tab / Shift-Tab | Cycle focus between panes |
| ↑ / ↓ | Navigate within focused pane |
| Enter | Inspect/activate selected item |
| Esc | Return to Monitor from Command mode |

#### Fleet Actions (when Fleet focused)

| Key | Action |
|-----|--------|
| `f` | Foreground selected agent (enter PTY, full screen) |
| `k` | Kill selected agent (confirms if not drained) |
| `d` | Drain selected agent (pause + WIP snapshot) |
| `n` | New agent (prompts for Agentfile or quick-spawn) |
| `l` | View agent's audit trail |

#### Needle Actions (when Needles focused)

| Key | Action |
|-----|--------|
| Enter | Show needle detail overlay |
| `c` | Claim needle (assign to selected agent) |
| `p` | Change priority |

#### Inbox Actions (when Inbox focused)

| Key | Action |
|-----|--------|
| Enter | Foreground the relevant agent |
| `a` | Approve |
| `r` | Reject (prompts for reason) |
| `s` | Snooze |

#### Global

| Key | Action |
|-----|--------|
| `?` | Help overlay (full keybind reference + tack cheat sheet) |
| `q` | Quit TUI (agents keep running) |
| `Ctrl-B` | Return from foregrounded agent to TUI |
| `Ctrl-R` | Read current panel aloud (accessibility) |

### Command Mode (Tack Input)

Input in the tack bar routes to three destinations:

| Input Pattern | Route | Example |
|--------------|-------|---------|
| Tack operators (`:`, `->`, `.?`, `#>`) | ostk subcommand dispatch | `:fix daemon` → `ostk hay add -p P0 "fix daemon"` |
| Bare text | File as hay | `thinking about X` → `ostk hay add "thinking about X"` |
| Slash commands | TUI-internal actions | `/bench`, `/needles P0`, `/agent new` |

The tack bar shows ghost hint text of what the current input will do:
- `":fix daemon"` shows hint: `→ file P0 hay`
- `"some thought"` shows hint: `→ file as hay`
- `"/bench"` shows hint: `→ show bench panel`

Input history: stored in `.ostk/tack_history`, navigable with ↑/↓ arrows.
Readline behavior: Ctrl-A (home), Ctrl-E (end), Ctrl-W (delete word), backspace.

### Foreground Mode

Pressing `f` on a fleet agent enters Foreground mode:

1. TUI clears entirely
2. Agent's PTY fd is attached to terminal stdin/stdout (raw passthrough)
3. TUI event loop pauses — zero overhead
4. Only `Ctrl-B` is intercepted (detach back to TUI)
5. On detach: TUI performs cold refresh (re-read all data sources), then resumes rendering

This is `tmux attach` semantics. The human types directly to the agent.

## Tack Integration

### Nudge Display

OS nudges (`.?` signals) render in the Advisor pane with the `·` prefix glyph:

```
· 11 hay pending — compile?
· spec idle 8m — drain?
· no commits in 2h — consider ostk commit
```

Nudges are dim text (not bold, not bright). They are peripheral — whispers, not alerts.
Contrast with inbox items which use `!` and bright/bold for P0.

### Tack Operator Visual Feedback

When the user types a tack operator in Command mode, the prompt reflects intent:

| Input starts with | Prompt becomes | Meaning |
|-------------------|---------------|---------|
| `:` | `tack:` | Hard command |
| `.` | `tack.` | Soft probe |
| `->` | `tack->` | Sequence/next |
| `#>` | `tack#>` | Imperative |
| bare text | `tack>` | Hay (thinking out loud) |

## Color Palette

16-color base16 safe. Every color carries a non-color signal (glyph, label, position).

| Semantic Name | 16-color | Used For | Non-color Signal |
|--------------|----------|----------|-----------------|
| `Active` | Green (2) | Active agent, healthy | `*` glyph, "ACTIVE" label |
| `Idle` | Yellow (3) | Idle agent, warning | `~` glyph, "IDLE" label |
| `Stuck` | Red (1) | Stuck/error agent | `!` glyph, "STUCK" label |
| `Dead` | Dim (8) | Dead/orphan agent | `x` glyph, "DEAD" label |
| `P0` | Yellow+Bold (11) | P0 needles, P0 inbox | `!` prefix |
| `P1` | White (7) | P1 needles | standard weight |
| `P2` | Dim (8) | P2 items | `·` prefix |
| `Whisper` | Dim (8) | Nudges, advisor | `·` prefix, right-aligned |
| `Budget OK` | Green (2) | Burn bar > 50% remaining | bar fill level |
| `Budget Warn` | Yellow (3) | Burn bar 20-50% remaining | bar fill level |
| `Budget Crit` | Red (1) | Burn bar < 20% remaining | bar fill level |
| `Focus` | White+Bold (15) | Focused pane border | Double-line border `║═` |
| `Unfocus` | Dim (8) | Unfocused pane border | Single-line border `│─` |
| `Prompt` | Cyan (6) | Tack prompt text | `tack>` prefix |
| `Ghost` | Dim (8) | Ghost hint text | italic where supported |

## Data Flow

### Sources

| Source | Path | What |
|--------|------|------|
| Audit log | `.ostk/audit.jsonl` | All events: needles, hay, agents, bench, nudges |
| Process table | `ps` / procfs | Live agent PIDs, verify alive/dead |
| Boot state | `.ostk/state.json` | Uptime, session metadata |
| Config | `.ostk/config.toml` | Budget, preferences |
| TUI cache | `.ostk/tui_state.json` | Last-known-good state for instant first paint |

### Read Strategy

Audit log is tailed with a **seek offset**. On each tick:
1. Seek to last-read position in `audit.jsonl`
2. Read new lines only, parse JSONL
3. Update in-memory view models (one per pane)
4. Set dirty flags on changed view models

No full-file re-parse after initial load. 737 events parses in < 5ms.

### Refresh Rates

| Panel | Tick Rate | Trigger |
|-------|-----------|---------|
| Fleet | 1s | Timer + audit events |
| Inbox | 2s | Audit events (human_needed) |
| Needles | 5s | Audit events (needle_open, needle_close) |
| Bench | 10s | Audit events (bench_result) |
| Advisor | 30s | Audit events (nudge) |
| Burn | 5s | Audit events (api_usage) |
| Top bar | 1s | Derived from fleet tick |

Focused pane refreshes at its normal rate. Unfocused panes refresh at 2x their normal interval to reduce CPU.

### Architecture

```
┌──────────────┐      ┌───────────────┐      ┌──────────────┐
│ audit.jsonl  │─tail─▶│  Update Loop  │─────▶│  View Models │
│ state.json   │      │  (1s tick)    │      │  (6 structs) │
│ ps / procfs  │      └───────────────┘      └──────┬───────┘
└──────────────┘                                     │
                                                     │ dirty flags
                                              ┌──────▼───────┐
                                              │  Render Loop  │
                                              │  (16ms frame) │
                                              └──────┬───────┘
                                                     │
                                              ┌──────▼───────┐
                                              │   Terminal    │
                                              │  (crossterm)  │
                                              └──────────────┘
```

Update and render are decoupled. A slow audit read never blocks rendering.

### Performance Budget

| Metric | Target |
|--------|--------|
| RSS memory | < 20 MB |
| CPU idle | < 1% |
| CPU active (typing) | < 5% |
| First paint | < 200ms |
| Tick processing | < 10ms |
| Render frame | < 16ms |

### Cached State

On clean quit (`q`), write current view model state to `.ostk/tui_state.json`.
On next launch, render cached state immediately, then begin live updates.
Result: the TUI never shows an empty screen after the first session.

## First-Run Experience

### Second-by-second

| Time | What Happens |
|------|-------------|
| 0s | User types `ostk` |
| 0.2s | TUI renders. Top bar: `ostk — 449→ — 11 hay — ↑22h`. Fleet shows agents (or empty-state placeholder). |
| 1s | Tack bar ghost text appears: `":fix bug" → file P0 needle` |
| 5s | Ghost text cycles: `"thinking about X" → file as hay` |
| 10s | User presses `?`. Help overlay appears with keybind reference + tack cheat sheet. |
| 15s | User presses Esc. Returns to main view. Presses `n` to spawn an agent. |
| 20s | Agent spawn prompt: Agentfile path or quick model selection. |
| 25s | Fleet panel shows new agent: `* agent1  Son4.6  1% █░░░░  initializing...` |
| 30s | User understands: I am the operator. The progress bar is filling. The OS is alive. |

### Empty-State Placeholders

Each panel shows centered dim text when empty:

| Panel | Placeholder |
|-------|-------------|
| Fleet | `no agents running — press n to spawn` |
| Needles | `no open needles — speak tack to file work` |
| Inbox | `inbox clear` |
| Bench | `no bench results — run: ostk bench` |
| Advisor | `no nudges` |

Placeholders ARE the onboarding. They tell the user what to do next.

### Post-Crash Recovery

When the TUI opens after a crash or stale session:
- Top bar shows `last event: 14m ago — needle →142 claimed by forge`
- Dead agents show `x` glyph and `(exited 3m ago)` in their task column
- Inbox may contain unresolved P0 items from the crashed session
- The user can immediately assess system state and decide: restart agents or resume

## Accessibility

### Keyboard Navigation

- Tab / Shift-Tab: cycle panes (Fleet → Needles → Advisor → Inbox → Bench → Tack)
- ↑ / ↓: navigate within pane
- Enter: activate/inspect
- Esc: dismiss overlay / exit Command mode
- No mouse required. No mouse support initially.

### Screen Reader Support

- On focus change, emit role announcement: "Fleet panel, 3 agents"
- Rate-limit update announcements to every 5s per panel
- P0 inbox items announced immediately regardless of focus
- Ctrl-R: read focused panel's full content as sequential text
- Detect screen reader via `$ACCESSIBILITY` or `$TERM_PROGRAM` env vars

### Color Independence

Every piece of information conveyed by color is also conveyed by:
- Glyph prefix (`*`, `~`, `!`, `x`, `·`)
- Text label (`ACTIVE`, `IDLE`, `STUCK`, `DEAD`)
- Position (P0 items always sort to top)
- Weight (bold for focus/critical, dim for low-priority)

## Technical Implementation

- **Language:** Rust
- **TUI framework:** ratatui + crossterm
- **Binary:** Same `ostk` binary. No args = TUI. Subcommand = CLI.
- **Entry point:** `ostk` with no `Commands` variant → launch TUI
- **Event loop:** crossterm raw mode, 1ms poll timeout in Monitor mode
- **PTY passthrough:** Direct fd handoff for Foreground mode, `Ctrl-B` intercept only

## Acceptance Criteria

- [ ] `ostk` with no args launches the TUI
- [ ] Layout renders correctly at 80x24 minimum
- [ ] Fleet panel shows all agents with live 1s status updates
- [ ] Fleet shows current file being edited per agent
- [ ] Needles panel shows open needles sorted by priority
- [ ] Inbox shows prioritized human-needed items
- [ ] Bench panel shows SWE bench arm comparison with pass rates
- [ ] Advisor panel shows OS nudges with `·` prefix
- [ ] Burn bar shows cost rate, remaining budget, progress bar
- [ ] Tack bar accepts input in Command mode
- [ ] Tack operators (`:`, `->`, `.?`, `#>`) visually reflected in prompt
- [ ] Bare text input files as hay
- [ ] Monitor mode: single-key actions work (f/k/d/n/a/r/s)
- [ ] Foreground/background works (f to attach PTY, Ctrl-B to detach)
- [ ] Tab/Shift-Tab cycles pane focus with visible focus indicator
- [ ] `?` shows help overlay with keybinds and tack cheat sheet
- [ ] `q` quits TUI without killing agents
- [ ] Empty-state placeholders shown when panels have no data
- [ ] First paint under 200ms (cached state on subsequent launches)
- [ ] 16-color palette works on all standard terminal emulators
- [ ] All color information has non-color equivalent (glyph/label/position)
- [ ] Context-sensitive keybind hints in tack bar right-side
- [ ] Ghost text examples cycle in idle tack bar
- [ ] Responsive: single-column mode below 80 cols, panel collapse below 24 rows
- [ ] RSS under 20MB, CPU under 1% idle

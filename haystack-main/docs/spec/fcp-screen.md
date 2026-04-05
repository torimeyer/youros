---
title: "fcp-screen: Display Driver for the Kernel"
implements: []
---

# fcp-screen: Display Driver for the Kernel

**Status:** spec (refined 2026-03-17, original locked 2026-03-16)
**Supersedes:** tui-console.md, tui-buffer-architecture.md (evolved)
**References:** tui.md (foundation), tui-operations.md (dispatch), eject-the-harness.md (P0)
**Thread:** kernel-gpu
**Design session:** 0b308d92, lines 109-236 (2026-03-15 11PM CST)
**Implementation tag:** `solid_tui_1` (component decomposition), `solid_tui_2` (SessionManager wiring)

## Thesis

The terminal is a display device. The kernel drives it through fcp-screen,
a stateless protocol translator. fcp-screen receives draw commands and emits
ANSI. It doesn't manage state — the filesystem IS the state.

```
LLM path:   filesystem → kernel → digest signals → LLM context
Human path: filesystem → kernel → fcp-screen    → terminal
```

Same source. Same kernel. Different output device.

## The Third Thing

We're not building a TUI framework. We're not building a TUI application.
We're building **a display driver for an operating system**.

- A TUI framework (ratatui) gives you widgets, layout, diffing — tools to build applications
- A TUI application uses those tools to render UI — it owns the event loop, manages state, polls data
- A display driver receives commands and puts things on screen — stateless, reactive, thin

fcp-screen is the third thing. The kernel owns the event loop. The filesystem
owns the state. fcp-screen owns exactly one thing: **translating kernel commands
to terminal output.**

This is why ratatui was dropped. It's a tool for building the second thing.
We need the third thing. And the third thing is ~550 lines over crossterm.

### Name evolution

Scott asked: "fcp-tui .? the screen driver?" (05:28 UTC, 2026-03-16).
Then reframed: "we aren't building a TUI framework, we need a human SCREEN
for the kernel." The naming follows the data flow:

```
screen ← fcp-screen ← llmOS ← filesystem
```

Not fcp-tui. The screen is not a TUI — it is the kernel's display surface.

## Core Principles

### The kernel pushes. The screen never polls.

The kernel already watches the filesystem. When state changes, it pushes to
fcp-screen the same way it pushes digest signals to LLMs. The display is
reactive, not polling. When an agent produces output:

```
agent writes → session file → audit event → kernel detects
  → kernel formats: AppendLine("[ai] the output text")
    → fcp-screen: scroll up, print styled line
```

### The screen doesn't have state.

The filesystem IS the state. fcp-screen is a stateless translator — it receives
draw commands from the kernel and emits ANSI. Like a GPU. It doesn't know about
needles or agents or threads. It knows about lines and colors and cursor positions.

### Multiplexing lives in the kernel.

When you want multiple streams, the kernel already manages multiple agent sessions.
Stream switching = kernel changes which session it routes AppendLine from.
No architectural change to fcp-screen. It still just receives seven commands.

The screen is a dumb terminal. **The smartest dumb terminal.**

### The screen is swappable.

fcp-screen is the terminal driver. fcp-web would be the browser driver.
fcp-api would be the headless driver. Same kernel, same agents, different
display hardware. This is how real OSes work — the kernel doesn't know
about your monitor.

## Why crossterm, not ratatui

What ratatui costs:
- **Immediate-mode rebuild** — reconstructs the entire frame every tick, then diffs.
  For a chat that's 95% append-only, this is backwards. You just... print the next line.
- **Abstraction mismatch** — ratatui thinks in widgets-in-rectangles. fcp-screen
  thinks in streams-through-a-driver.

What a chat interface actually needs:
1. **Scroll region** — ANSI `\e[;r` pins input bar at bottom, chat scrolls above. crossterm does this.
2. **Append a line** — print styled text at the bottom of the scroll region. crossterm does this.
3. **Redraw input bar** — on every keystroke. One line. crossterm does this.
4. **Redraw status line** — on kernel signal change. One line. crossterm does this.
5. **Overlay** — save region, render overlay, restore on dismiss. ~50 lines.
6. **Resize** — reflow on SIGWINCH. crossterm emits the event.

The only case where full-frame diffing matters is: complex overlapping UI with
frequent partial updates across many regions. A dashboard. The thing we're
explicitly not building.

### Terminal history validation

VT100 research (2026-03-17) confirmed the architecture:

| Dimension | ratatui (old TUI) | fcp_screen | VT100 (the origin) |
|---|---|---|---|
| Append cost | O(visible_area) rebuild + diff | O(1) scroll + print | O(1) scroll + print |
| Token streaming | Frame render per token or batch | `Print` + flush (zero overhead) | Print at cursor (native) |
| CPU at idle | Frame loop | Zero (blocks on `event::read`) | Zero (waits for input) |
| Status bar update | Full frame rebuild | Single targeted command | Cursor to row, print |
| Scroll regions | Not used | Native (`\x1b[1;{}r`) | Hardware operation |

fcp-screen recovered the curses-era advantages (event-driven quiescence,
per-region updates, O(1) append) while shedding what's no longer needed
(terminal heterogeneity, multi-window coordination).

## Screen as generation-tracked resource

Buffer diffing is OCC applied to a display device. The screen IS a
generation-tracked file the kernel manages.

| Hot PR Tier | Screen equivalent | When |
|-------------|-------------------|------|
| Tier 1: auto-merge | Append: scroll + print | New chat line (most common) |
| Tier 2: assisted | Partial: redraw one zone | Status update |
| Tier 3: manual rebase | Full: reflow everything | Terminal resize |
| Tier 4: diagnostic | Overlay: save/render/restore | Peek (fleet, work, help) |

## Protocol (kernel → fcp-screen)

Seven commands. This is the entire interface.

```
AppendLine(StyledLine)         — new line in chat zone (scroll up, print)
AppendToken(String)            — streaming mid-line (AI typing, print at cursor)
SetStatus(StyledLine)          — update status bar content
ShowOverlay(Vec<StyledLine>)   — transient peek (save region, render)
DismissOverlay                 — restore underlying content
Clear                          — reset chat zone
DebugLog(StyledLine)           — debug panel (operator-only, split-screen)
```

## Zones

Three zones, pinned via ANSI scroll regions:

```
┌─────────────────────────────────────────┐
│                                         │
│ [ai] The intelligence layer clusters    │  ← chat zone (scroll region)
│      hay into discoverable themes...    │
│                                         │
│ [you] :compile                          │
│                                         │
│ [ai] Compiled 3 needles from hay.       │
│                                         │
├─────────────────────────────────────────┤
│ > :thread hoberman :status▏             │  ← input zone (fixed)
├─────────────────────────────────────────┤
│ @prime+1357 │ 0.87 │ 3↑ │ 296→ │ 4:20  │  ← status zone (fixed)
└─────────────────────────────────────────┘
```

## Peeks (call-in-to-view)

Single-key overlays: Alt+f=fleet, Alt+w=work, Alt+?=help, Alt+p=mode, Alt+m=model.
Transient. Dismiss on next keystroke. Not panes. Not modes.

```
┌─ Fleet ──────────────────────── [f] ────┐
│ agent-1357  opus    active  tui    12m  │
│ agent-1358  sonnet  active  hober   3m  │
└─────────────────────────────────────────┘
```

## Input model

One input line. Three destinations, inferred from grammar:

| Pattern | Destination | Example |
|---------|-------------|---------|
| `:verb` | Kernel command | `:compile`, `:reap`, `:bench` |
| Navigation | TUI-local | `:thread hoberman`, `Alt+f`, `Alt+w` |
| Free text | Active scheduling thread | "fix the radius scheduling" |

Tack verbs highlight as you type. The highlighting IS the teach mechanism.

## Architecture

### Ownership boundaries

```
kernel (cpu/)
  ├─ SessionManager — owns all shared resources
  │   ├─ BootContext
  │   │   ├─ boot_md (from `ostk boot`, refreshed periodically)
  │   │   ├─ language (from .ostk/.language, live dialect)
  │   │   └─ build_system_prompt(agentfile_prompt) → full prompt
  │   │       = agentfile_prompt + boot_md + .language
  │   ├─ ApiClient (shared, connection-pooled)
  │   ├─ tokio Runtime (shared)
  │   ├─ available_models (fetched once at startup)
  │   ├─ HashMap<String, AgentSession>
  │   └─ active session switching (:thread)
  │
  ├─ AgentSession — per-session state
  │   ├─ LoopConfig (system_prompt built by SessionManager)
  │   ├─ messages (Arc<Mutex<Vec<Message>>>)
  │   ├─ CpuEvent channel (mpsc)
  │   ├─ busy flag, session_tokens
  │   └─ session persistence (.ostk/sessions/{name}.jsonl)
  │
  └─ agent_loop — stateless execution engine
      ├─ build_params(config, messages) → CreateParams
      │   (system prompt, context_management, cache_control, speed)
      ├─ run_loop(client, config, messages, tx) → CpuEvent stream
      └─ execute_tool — routes through kernel (ostk -c, file layer)

fcp-screen (fcp_screen/) — display driver
  ├─ protocol.rs — 7 ScreenCommand variants, StyledLine primitives
  ├─ screen.rs — crossterm driver: scroll regions, zones, overlay, debug panel
  ├─ input.rs — InputBar: buffer, cursor, history, autocomplete, verb highlight
  ├─ selector.rs — reusable overlay widget (mode picker, model picker)
  ├─ components/
  │   ├─ dispatch.rs — unified VERBS, model aliases, local command exec
  │   ├─ overlays.rs — fleet, work, help, POST, audit log display
  │   ├─ session.rs — scheduler session file helpers
  │   └─ status.rs — StatusCache (cached filesystem reads, 2s refresh)
  └─ app.rs — event loop: human input → SessionManager, CpuEvent → ScreenCommand
```

### Data flow

```
filesystem (source of truth)
  → kernel (watches state, produces signals)
    → SessionManager (routes to active AgentSession)
      → agent_loop (API call → tool exec → CpuEvent stream)
        → SessionManager (collects CpuEvents from active session)
          → fcp-screen (translates CpuEvent → ScreenCommand → ANSI)
            → terminal (display hardware)

terminal (input hardware)
  → fcp-screen (captures keystrokes)
    → :verb → kernel command dispatch
    → free text → SessionManager.dispatch() → active AgentSession
      → filesystem (audit trail, session state)
```

### System prompt composition (SessionManager-owned)

The system prompt is assembled by `SessionManager.build_system_prompt()`:

```
┌─────────────────────────────────────────────────────┐
│ Agentfile PROMPT (e.g. prompts/scheduler-system.md) │  ~1.5k tok
│   persona, tack protocol, display surface notes     │
├─────────────────────────────────────────────────────┤
│ # Boot state                                        │  ~500 tok
│ boot.md (OS orientation: identity, needles, fleet)  │
├─────────────────────────────────────────────────────┤
│ # .language (live compiled dialect)                  │  ~1k tok
│ .ostk/.language (72 verbs, decay, momentum)     │
└─────────────────────────────────────────────────────┘
  Total: ~3k tok. Cached with extended-cache-ttl at 10% read cost.
  ALL sessions share this prefix → prompt cache HIT on :thread switch.
```

When boot.md or .language changes: cache miss (one call), then re-cached.
When `:thread` switching: cache HIT — only the message history changes.
Cost: multiplexed with shared cache = $1.65/turn vs separate sessions = $3.00/turn.

### API controls (kernel-owned, not display-owned)

The kernel controls the LLM interaction through the Anthropic API:

| Control | API mechanism | Benefit |
|---|---|---|
| System prompt caching | `cache_control: {"type": "ephemeral", "ttl": "1h"}` | 3k tok at 10% read cost |
| Context compaction | `compact_20260112` with token trigger | Auto-summarize at pressure threshold |
| Tool clearing | `clear_tool_uses_20250919` keep=5 | Remove old tool results, save tokens |
| Pre-call token count | `count_tokens` API endpoint | Exact context size for pressure display |
| Fast mode | `speed: "fast"` on Opus 4.6 | 2.5x output speed |
| Beta fallback | Strip betas on 400, retry | Graceful degradation |

fcp-screen never touches these. It renders what the kernel pushes.

## P0: `ostk do`

The escape hatch. Any terminal. No Claude Code. No harness.

```
$ ostk do "fix the radius scheduling"
[ai] Looking at pick_next_index() in work.rs...
...streaming response...
[ai] Done. Committed abc123.
```

Implementation: Create `AgentSession` from `scheduler.af`, dispatch prompt,
drain `CpuEvent` to stdout. One-shot. Session persistence optional.

## P1: Multiplexing (`:thread`)

Multiple scheduling threads. Stream switching. Each backed by its own
`AgentSession` in the `SessionManager`.

```
:thread              — list sessions (name, messages, busy)
:thread hoberman     — switch (create on demand)
:thread close        — close current, return to scheduler
```

Status bar shows active session:
```
@prime+1357 scheduler │ opus │ 0.87▸ │ 296→ │ ↓12k ↑3k │ 1▲ │ $0.14 │ 14:20
```

Background streams continue running. `CpuEvent::TurnComplete` fires notification.

## P2: Context pressure display

Wire `CpuEvent::PreCallTokenCount` to the status bar:

```
<60%   normal (green)
60-80% aggressive (yellow) — compact background, keep=3
80-90% emergency (red) — compact active older half, shed dormant
>90%   critical — write dying.md, nudge scheduler, page out
```

The human sees what the OS sees. Context pressure is visible, not silent.

## Debug layer (debug-llmOS)

Feature-gated (`--debug`). Split-screen: chat left, debug right.
Shows kernel signals — exactly what agents see in their digest:

```
│ @prime+1357 │ 0.87 │ 3↑ │ 296→ │ 4:20                                     │
│ [ctx] Δ8t │ audit:+15 │ conflict:none │ [304] src/main.rs │ [stale] g2→g4  │
```

Invisible to users. Visible to @scott. The operator sees what the agents see.
Alt+d toggles focus between chat and debug panels.

## Implementation status (v1.7)

| Component | Status | Tag/Needle |
|-----------|--------|------------|
| protocol.rs (7 commands) | shipped | v1.6.0 |
| screen.rs (crossterm driver) | shipped | v1.6.0 |
| input.rs (InputBar, unified VERBS) | shipped | `solid_tui_1` |
| selector.rs (overlay widget) | shipped | v1.6.0 |
| components/ decomposition | shipped | `solid_tui_1` |
| StatusCache (cached fs reads) | shipped | `solid_tui_1` |
| Debug split-screen | shipped | v1.6.0 |
| AgentSession (cpu/session.rs) | shipped | →746 |
| SessionManager | shipped | →747 |
| Wire fcp_screen to SessionManager | filed | →748 |
| `:thread` multiplexing | filed | →749 |
| Fleet agents share AgentSession | filed | →750 |
| Context pressure display | filed | →751 |
| `ostk do` CLI | filed | →752 |
| Live .language in system prompt | filed | →753 |
| BootContext (boot.md + .language) | filed | →754 |
| Shared ApiClient | filed | →755 |
| Refine llmos-concurrency.md | filed | →756 |

## What this replaces

The 8000-line TUI fever dream (src/tui/mod.rs) is preserved for reference.
It mapped the surface of what COULD be. This spec defines what SHOULD be:
a display driver for the kernel, not a dashboard application.

## Lineage

- Hot PR merge → screen buffer diffing (OCC on display)
- Kernel digest signals → fcp-screen protocol (same data, different device)
- fcp-rust / fcp-python → fcp-screen (same driver pattern: protocol capture, not translation)
- tui-buffer-architecture.md → evolved into generation-tracked screen zones
- eject-the-harness.md → P0 motivation
- llmos-ram.md → multiplexing design (threaded conversations, context pressure cascade)
- llmos-concurrency.md → scheduler loop, FROM auto, dying protocol
- cpu-driver.md → CpuDriver trait, provider abstraction
- context-degradation.md → degradation symptoms, calibrate signal, corrective actions
- VT100/terminal history research (2026-03-17) → confirmed stream model, scroll regions, O(1) append

## Design session record

Session: 0b308d92, 2026-03-16 05:10-06:01 UTC (2026-03-15 11:10PM-midnight CST).
Eight needles filed (→703-→710). Two agents launched in parallel.
Key moments:
- 05:28: Scott asks "fcp-tui .? the screen driver?"
- 05:44: Scott renames to fcp-screen: "we need a human SCREEN for the kernel"
- 05:45: "The third thing" — not framework, not application, display driver
- 05:54: Scott locks intent: ":lock:intent :spec immediately :fleet kernel-gpu"
- 05:58: This spec written and committed

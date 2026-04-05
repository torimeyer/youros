---
status: spec
author: agent-697
promoted_at: 2026-03-14T16:46:48Z
title: TUI Buffer Architecture — The Kernel as Display Source
version: 1
created: 2026-03-11
depends_on:
- tui-console-v2
- tui-operations
- hot-pr
evidence: tui.md v1, tui-console.md v2, tui-operations.md v1, src/tui/mod.rs (gen=23)
---

# TUI Buffer Architecture

> The TUI controls the render buffer. Every byte of kernel digest output — `[procs]`, `[files]`, `[nudge]`, `[stale]`, `[ctx]` — is already structured data. The TUI intercepts it before the terminal sees it and renders each component as a live panel instead of raw text.

## The Core Insight

The kernel already emits a structured digest after every tool call:

```
[procs] agent-1:active:0s agent-2:stale:45s agent-3:crashed:4h
[files] src/main.rs:gen=12:agent-1:5m .ostk/boot.md:gen=3:agent-2:2m
[nudge] 11 hay pending — compile?
[stale] agent-2 last seen 45s ago
[ctx] boot:0.87 swap:~ tok:↓47k
```

This is not log noise. It is the kernel's live process table, file gen table, nudge queue, stale detection, and context summary — serialized as text and discarded to the terminal. The TUI intercepts this stream and routes each bracketed type to its corresponding panel. No new data sources. No new IPC. The render buffer IS the kernel.

---

## Layout

### Full Layout (>= 120 cols)

```
┌─ @+42 · boot:0.87 · RAM:71% · λ42 · swap:~ · ⚙25↑41↓ · tok:↓47k ──────────────────┐
│ FLEET (left 40%)                    │ DIGEST (right 60%)                             │
│ ● agent-1   Son4.6  editing mod.rs  │ [procs] agent-1:active:0s agent-2:stale:45s   │
│ ○ agent-2   Haiku   stale 45s       │ [files] src/main.rs:gen=12:agent-1:5m         │
│ ✕ agent-3   Opus    crashed 4h      │ [nudge] 11 hay pending — compile?              │
│                                     │ [stale] agent-2 last seen 45s                  │
│                                     │ [ctx] boot:0.87 swap:~ tok:↓47k               │
│                                     │ [procs] agent-1:active:5s ...                  │
│                                     │ ...                                            │
├─ Row 3: WORK / HAY / SHELL (left 50%) ──┴─ OUTPUT (right 50%, persistent) ─────┤
│ ● →594: TUI text input              │ ~ ostk boot                        │
│ ● →557: Eject the harness           │ @h.p+1171 | v1.3.0 | POST 7/7          │
│ ────────────────────────────────────│ ────────────────────────────────────── │
│ [HAY] tori test message             │ ~ ostk help                        │
│ [HAY] need to fix gpg hang          │ usage: ostk <command>              │
│ ────────────────────────────────────│                                        │
│ [SHELL] $ ls -la                    │                                        │
│ [SHELL] $ cargo test                │                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ :_ ▌                                           [:cmd  .?query  /search  ?help]      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Standard Layout (80-119 cols)

```
┌─ @+42 · boot:0.87 · λ42 · tok:↓47k ──────────────────────────────────────────────┐
│ FLEET                               │ DIGEST                                       │
│ ● agent-1  Son4.6  mod.rs  →491     │ [procs] agent-1:active:0s                   │
│ ○ agent-2  Haiku   stale 45s        │ [nudge] 11 hay pending                      │
│                                     │ [stale] agent-2 45s                          │
│                                     │ [files] src/main.rs:gen=12:5m               │
├─ ACTIVITY ──────────────────────────┴─ LOCKS ──────────────────────────────────────┤
│ [06:25] reap: 5 reaped              │ [collapsed — press L to expand]              │
│ [06:31] →491 claimed                │                                              │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ :_ ▌                                                         [:cmd  ?help]         │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Ratatui Layout Spec

### Vertical Split (rows)

```rust
let vertical = Layout::vertical([
    Constraint::Length(1),   // Status bar (fixed 1 row)
    Constraint::Min(6),      // Main content area (fills remaining)
    Constraint::Length(5),   // Activity + Locks row
    Constraint::Length(1),   // Tack input bar (fixed 1 row)
]);
let [status_area, main_area, bottom_area, tack_area] = vertical.areas(frame.area());
```

### Main Content: Horizontal Split (Fleet | Digest)

```rust
let main_cols = Layout::horizontal([
    Constraint::Percentage(40),  // Fleet panel
    Constraint::Percentage(60),  // Digest panel
]);
let [fleet_area, digest_area] = main_cols.areas(main_area);
```

### Bottom Row: Horizontal Split (Activity | Locks)

```rust
let bottom_cols = Layout::horizontal([
    Constraint::Percentage(50),  // Activity feed
    Constraint::Percentage(50),  // Lock panel (collapsible → 0% when hidden)
]);
let [activity_area, locks_area] = bottom_cols.areas(bottom_area);
```

When locks panel is collapsed:
```rust
let bottom_cols = Layout::horizontal([
    Constraint::Percentage(100),  // Activity feed expands full width
    Constraint::Length(0),        // Locks hidden
]);
```

### Responsive Adjustments

| Terminal width | Fleet % | Digest % |
|----------------|---------|----------|
| >= 160 cols    | 35%     | 65%      |
| 120-159 cols   | 40%     | 60%      |
| 80-119 cols    | 45%     | 55%      |
| 60-79 cols     | 100% / stack | 0% / below |
| < 60 cols      | refuse render |          |

---

## Panel Specifications

### 1. Status Bar (top, 1 row)

**What it shows:**
```
@+42 · boot:0.87 · RAM:71% · λ42 · swap:~ · ⚙25↑41↓ · tok:↓47k
```

| Field | Meaning | Source |
|-------|---------|--------|
| `@+42` | Identity counter (total MCP connections since install) | `.ostk/identity_counter` |
| `boot:0.87` | Boot coherence score (0.0-1.0, from ostk boot output) | parsed from `[ctx]` digest token |
| `RAM:71%` | Host memory usage | `sysinfo` crate or `/proc/meminfo` |
| `λ42` | Open needle count | `.ostk/needles/` count |
| `swap:~` | Swap file freshness (`~` = fresh, `!` = stale, `?` = missing) | `.ostk/boot.md` mtime |
| `⚙25↑41↓` | Agent activity: 25 calls since boot, 41 completions | accumulated from `[procs]` digest |
| `tok:↓47k` | Token budget: ↓ = incoming context, number = thousands | parsed from `[ctx]` digest token |

**Update frequency:** 1s tick (derived from clock state, same as current `ClockState` struct in mod.rs)

**Data path:** `.ostk/identity_counter` (already loaded in `load_clock_state()`), `[ctx]` digest line parsed from kernel output interceptor.

---

### 2. Fleet Panel (left, 40%)

**What it shows:**

```
FLEET
● agent-1   Son4.6   editing src/tui/mod.rs (→491)    0s
○ agent-2   Haiku    stale — last: shared-mish        45s
✕ agent-3   Opus     crashed (pid 44201)               4h
```

| Column | Width | Source | Glyph |
|--------|-------|--------|-------|
| Health glyph | 1 | heartbeat age + `kill -0` | `●` active, `○` stale, `✕` crashed |
| Alias | 10 | `.ostk/agents.jsonl` → `alias` field | — |
| Model | 7 | `.ostk/agents.jsonl` → `model` field | — |
| Activity | fills | `.ostk/agents.jsonl` → `activity` field | — |
| Needle | 5 | `.ostk/agents.jsonl` → `needle` field | `→NNN` |
| Age | 4 | derived: `now - last_seen` | — |

**Update frequency:** 1s tick. Source: `load_fleet_from_agents()` (already exists in `src/tui/mod.rs`). Additionally updated on every `[procs]` token intercepted from kernel digest.

**Digest intercept:** `[procs]` lines are parsed as `alias:status:age` tuples and used to update the in-memory fleet model between file polls. This gives sub-second resolution when agents are actively making calls.

**Color coding:**
- `●` active → green (ANSI 2)
- `○` stale → yellow (ANSI 3)
- `✕` crashed → red (ANSI 1), dimmed if age > 5 min (ANSI 8)

**Empty state:** `no agents running — press n to spawn`

---

### 3. Digest Panel (right, 60%)

**What it shows:** Last 20 kernel digest lines, color-coded by type, newest at bottom (scrollable).

```
DIGEST                                                    ↑scroll
[procs] agent-1:active:0s agent-2:stale:45s
[files] src/main.rs:gen=12:agent-1:5m
[nudge] 11 hay pending — compile?             ← gold
[stale] agent-2 last seen 45s ago             ← rust/amber
[procs] agent-1:active:5s agent-2:stale:50s
[ctx]   boot:0.87 swap:~ tok:↓47k
[files] .ostk/boot.md:gen=3:agent-2:10m
[nudge] P0 count > 3 — run :compile           ← gold
```

**Color palette:**

| Token type | Color name | ANSI | Hex |
|------------|-----------|------|-----|
| `[nudge]` | gold / needle amber | Yellow+Bold (ANSI 11) | `#d4a054` (brand amber) |
| `[stale]` | rust | Red (ANSI 1) | dim rust |
| `[procs]` | mint | Green (ANSI 2) | dim green |
| `[files]` | ice blue | Cyan (ANSI 6) | dim cyan |
| `[ctx]`   | dim white | ANSI 8 | dimmed |
| `[warn]`  | bright red | ANSI 9 | bold red |

**Update frequency:** Real-time. Every kernel digest token appended immediately as it arrives. No polling — push model from the kernel output interceptor.

**Data path:** The digest interceptor (see Architecture section below) writes parsed tokens to an in-memory `VecDeque<DigestLine>` with capacity 20. On overflow, oldest line drops. The render loop reads from this deque each frame.

**Scrollback:** User can scroll up with `↑/PgUp` to see older lines. On any new digest event while scrolled back, a `↓N new lines` indicator appears at the bottom. Press `↓/End` to jump to live.

---

### 4. Activity Feed (bottom-left, 50%)

**What it shows:** Last 10 events from `audit.jsonl`, chronological, newest last.

```
ACTIVITY
[06:25] reap: 5 reaped. table compacted.
[06:30] bench: null-deref-config PASS (gemini, 4 turns)
[06:31] needle →491 claimed by agent-1
[06:35] agent-2 stale (45s, nudge sent)
[06:40] ss: src/tui/mod.rs gen=12 (CAS ok)
```

**Update frequency:** 2s poll on `.ostk/audit.jsonl` using seek offset (never full re-parse). Same `tail` strategy as specified in `tui.md` data flow section.

**Data path:** `.ostk/audit.jsonl` — seek to last-read byte offset, read new lines, parse JSONL. Display `timestamp` (formatted HH:MM) + `event_type` + key fields depending on event type.

**Event type rendering:**

| `event_type` | Display format | Color |
|-------------|----------------|-------|
| `reap` | `reap: N reaped` | dim |
| `bench_result` | `bench: NAME PASS/FAIL (model, N turns)` | green/red |
| `needle_claimed` | `needle →NNN claimed by ALIAS` | cyan |
| `agent_stale` | `ALIAS stale (AGEs, nudge sent)` | yellow |
| `file_edit` | `ss: PATH gen=N (CAS ok/conflict)` | dim |
| `human_needed` | `! ALIAS needs input: REASON` | bold yellow |

**Empty state:** `no activity — audit.jsonl empty`

---

### 5. Tack Input Bar (bottom, 1 row, fixed)

**What it shows:**
```
:_ ▌                                              [:cmd  .?query  /search  ?help]
```

**Modes and prompt mutation:**

| Input prefix | Prompt | Meaning | Tier hint |
|-------------|--------|---------|-----------|
| (empty) | `:_ ▌` | Waiting | `[:cmd  .?query  /search]` |
| `:` | `tack: ▌` | Hard command → kernel dispatch | `[Enter:run  Esc:cancel]` |
| `.` | `tack. ▌` | Soft probe → query mode | `[Enter:query  Tab:complete]` |
| `->` | `tack-> ▌` | Sequence → next action | `[Enter:submit]` |
| `#>` | `tack#> ▌` | Imperative → force exec | `[Enter:force  Esc:cancel]` |
| bare text | `tack> ▌` | Hay (thinking out loud) | `[Enter:file as hay  Esc:cancel]` |

**Tier resolution display:** When a tack command is submitted, the bar shows resolution progress:
```
tack: :compile          → resolving...
tack: :compile          → T1: match "compile" → ostk compile
tack: :compile          → dispatched. watch DIGEST for output.
```

**fcp-ostk resolver connection:** Tack input routes through a resolver chain before dispatch:

```
tack input
  → lexer: parse operator prefix (`:`, `.`, `->`, `#>`, bare)
  → resolver tier 1: exact match against known verbs (`:status`, `:reap`, `:compile`, `:spawn`, `:delegate →NNN`)
  → resolver tier 2: fuzzy match + suggest (`:comile` → suggest `:compile`?)
  → resolver tier 3: fcp-ostk semantic resolution (bare text → classify as hay, needle, or command)
  → dispatch: call `ostk <subcommand>` via PTY or direct kernel call
  → result: stream to DIGEST panel
```

The fcp-ostk resolver is the domain intelligence layer. It classifies intent, resolves ambiguity, and routes to the correct kernel primitive. Tack operators are the syntax; fcp-ostk is the semantics.

**Ghost text (idle):** When input is empty, dim ghost text cycles every 5s:
- `":compile" → compile hay into needles`
- `"thinking about X" → file as hay`
- `":delegate →576" → assign to next agent`
- `":reap" → clean the process table`

Ghost text stops permanently after first user input in the session.

**Input history:** `.ostk/tack_history` (append-only). `↑/↓` to navigate. Readline keybinds: `Ctrl-A`, `Ctrl-E`, `Ctrl-W`, backspace.

---

### 6. Lock Panel (bottom-right, 50%, collapsible)

**What it shows:** Active file locks from `.ostk/locks/`.

```
LOCKS                                              [L:toggle]
[LOCK] .ostk/boot.md      agent-2   2m   read
[LOCK] src/tui/mod.rs         agent-1   12s  write
[LOCK] src/kernel/file.rs     agent-1   3m   write
```

| Column | Source | Width |
|--------|--------|-------|
| `[LOCK]` prefix | static | 6 |
| File path | lockfile name | ~30 |
| Agent alias | lockfile content → `holder` | 10 |
| Age | `now - mtime(lockfile)` | 4 |
| Mode | lockfile content → `mode` (`read`/`write`) | 5 |

**Update frequency:** 5s poll on `.ostk/locks/` directory listing. Lock files appear/disappear as agents acquire and release.

**Collapsible:** Press `L` to toggle. When collapsed, bottom row shows full-width Activity. When expanded, splits 50/50. Default: collapsed if no active locks, expanded if any locks present.

**Stale lock indicator:** Lock age > 5 min gets a `!` prefix and rust color. These are candidates for `:reap`.

**Empty state:** `no active locks` (collapsed automatically)

---

## The Digest Interceptor — Architecture

This is the architectural heart of the spec. The TUI does not poll files to know what's happening. It intercepts the kernel's own output.

### Current behavior (before this spec)

```
Agent makes MCP call
  → ostk kernel processes it
  → kernel appends to audit.jsonl
  → kernel emits digest to stdout: "[procs] ... [files] ..."
  → terminal renders raw text
  → human reads raw digest lines
```

### With TUI buffer control

```
Human runs `ostk` (no args)
  → TUI launches, enters alternate screen buffer
  → TUI owns the render buffer — nothing goes to raw terminal
  → Agent makes MCP call (via separate process / MCP server)
    → ostk kernel processes it
    → kernel emits digest to MCP response metadata
    → TUI reads digest from: 
        (a) audit.jsonl tail (2s poll, already implemented)
        (b) .ostk/agents.jsonl (1s poll, fleet state)
        (c) kernel stdout pipe (if TUI spawns kernel as child)
    → TUI parser: split on `[token]` brackets, route to panel models
    → dirty flags set on affected panels
    → next render frame: only dirty panels re-drawn (ratatui diffing)
  → Human sees live panels, not text scroll
```

### Digest Token → Panel Routing Table

| Token | Target Panel | Update |
|-------|-------------|--------|
| `[procs]` | Fleet panel + Digest panel | Fleet: update health/age. Digest: append line. |
| `[files]` | Digest panel | Append colored line (ice blue). |
| `[nudge]` | Digest panel + Status bar nudge indicator | Append gold line. Status bar blinks `·` if unread. |
| `[stale]` | Fleet panel + Digest panel | Fleet: mark agent stale. Digest: append rust line. |
| `[ctx]` | Status bar + Digest panel | Status bar: update `boot:` / `tok:` / `swap:` fields. Digest: append dim line. |
| `[warn]` | Digest panel | Append bold red line. |
| `[lock]` | Lock panel | Add/remove lock entry. |

### In-Memory Model

```rust
struct DigestBuffer {
    lines: VecDeque<DigestLine>,   // capacity: 20, ring buffer
    scroll_offset: usize,          // 0 = live tail
    unread_count: usize,           // new lines while scrolled up
}

struct DigestLine {
    token: DigestToken,            // Procs | Files | Nudge | Stale | Ctx | Warn | Lock
    raw: String,                   // original text after token
    timestamp: Instant,            // when received
}

enum DigestToken {
    Procs, Files, Nudge, Stale, Ctx, Warn, Lock,
}
```

The render loop reads `DigestBuffer` and applies color based on `DigestToken` variant. No string matching at render time — classification happens once at parse time.

---

## "The TUI IS the Shell" — Architectural Meaning

This phrase from `tui-console.md` has a precise architectural meaning:

**Before:** The human interacts with agents by talking to Claude Code. Claude Code is an agent. The human is inside one agent's context window.

**After:** The human operates from the TUI. The TUI shows ALL agents. The tack bar dispatches to the kernel, not to any single agent. `:exec`, `:delegate`, `:reap`, `:compile` — these are kernel syscalls issued by the human, not prompts sent to an LLM.

The TUI is the shell in the Unix sense: a human-facing process that issues syscalls to a kernel. Claude Code becomes one agent in the fleet pane — LITTLE core among LITTLE cores. The human is the big core. The TUI is the bus.

Concretely:
- The TUI spawns subprocesses via PTY (`:exec` → `ostk::kernel::pty::run_command()`)
- The TUI reads and writes `.ostk/` directly (fleet, audit, locks, nudges)
- The TUI dispatches tack verbs to kernel commands without any LLM in the path
- Agents appear in the fleet pane when they register (agents.jsonl), not when the human addresses them
- The human can operate the entire OS — spawn, kill, compile, delegate, reap — without opening a chat window

The chat window dies when the process table viewer is good enough. That's the design goal.

---

## Update Frequency Summary

| Panel | Frequency | Trigger mechanism |
|-------|-----------|-------------------|
| Status bar | 1s timer | `ClockState` tick (existing) |
| Fleet | 1s timer + `[procs]` push | `load_fleet_from_agents()` + digest intercept |
| Digest | Real-time push | Digest interceptor on every kernel output |
| Activity feed | 2s poll | `audit.jsonl` seek offset tail |
| Tack bar | Event-driven | Crossterm key events |
| Lock panel | 5s poll | `.ostk/locks/` directory listing |
| Status bar `[ctx]` fields | Push on `[ctx]` token | Digest interceptor |

Unfocused panels poll at 2x their normal interval (reduces CPU when human is focused elsewhere).

---

## Data Sources by Panel

| Panel | Primary source | Secondary source | Exists? |
|-------|---------------|-----------------|---------|
| Status bar | `.ostk/identity_counter` + `[ctx]` digest | `sysinfo` for RAM | identity_counter: yes. ctx parse: new. |
| Fleet | `.ostk/agents.jsonl` + `[procs]` digest | `kill -0` for liveness | agents.jsonl: yes. procs intercept: new. |
| Digest | Kernel digest interceptor | `audit.jsonl` (fallback) | Interceptor: new. audit tail: yes. |
| Activity | `.ostk/audit.jsonl` seek tail | — | Yes (existing data flow from tui.md) |
| Tack bar | `crossterm` key events | `.ostk/tack_history` | key events: yes. history: new. |
| Lock panel | `.ostk/locks/` directory | lockfile content JSON | locks dir: yes (sh_lock.rs). |

---

## Implementation Order

Build in this order — each panel enables the next:

1. **Status bar** — 1 row, minimal. Confirms TUI launches and renders. (@, λ, tok fields)
2. **Fleet panel** — already partially implemented in `src/tui/mod.rs`. Enrich with `[procs]` intercept.
3. **Activity feed** — `audit.jsonl` tail. Proves the data pipeline works end-to-end.
4. **Tack input bar** — key event handling. Connect to existing `ostk` subcommands.
5. **Digest panel** — the new architectural piece. Requires digest interceptor.
6. **Lock panel** — `.ostk/locks/` poll. Simplest panel. Ship last as it's least critical.

The digest interceptor (step 5) is the only genuinely new infrastructure. Steps 1-4 and 6 are wiring existing kernel primitives into existing ratatui widgets.

---

## Acceptance Criteria

- [x] Implement high-density horizontal splits (Fleet/Agentfiles, Activity/Dashboard, Work/Hay/Shell/Output).
- [x] Implement persistent, append-only Output feed.
- [x] Implement Hay Pile pane with edit/compile/purge keys.
- [x] Implement Shell History pane.
- [x] Implement architecture-aware boot register compression (<100 tokens).
- [ ] Implement live PTY terminal pane (embedded shell) — →611.
- [ ] Implement Digest Interceptor (routing [tokens] to panels).

---
*Authored by @gemini.prime. Modernizing the console.*

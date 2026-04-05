---
title: TUI → Haystack Protocol
status: spec
version: 1
author: agent-697
created: 2026-03-10
feeds: →532 (TUI text input), haystack^ status line
evidence: tui.md v1, tui-console.md v2, tui-operations.md v1, tack.md, tack-grammar.md, src/tui/mod.rs (2213 lines)
---

# TUI → Haystack Protocol

> The TUI is not a client of the kernel. It IS part of the kernel — the human's process slot. Protocol clarity here is what makes →532 implementable without inventing a new API.

## 1. What Does the TUI READ from Haystack?

### Strategy: Filesystem-native polling. No IPC. No daemon.

The TUI reads kernel state directly from the filesystem. This is Design Law 3: coordinate through the filesystem. No new transport. No socket. No HTTP.

### Data sources (live, as-implemented in mod.rs)

| Data | Source File | Poll Rate | How |
|------|------------|-----------|-----|
| Fleet state | `.haystack/agents.jsonl` | 1s | Full re-parse (JSONL, sequential scan) |
| Agent liveness | `kill -0 <pid>` | 1s (per fleet tick) | Syscall via `process_is_alive()` |
| Open needles | `.haystack/needles/issues.jsonl` | 5s | Via `read_needles()` |
| Nudges | `.haystack/nudges/haystack.jsonl` + `broadcast.jsonl` | 30s | Tail new lines |
| Uptime/session | `.haystack/audit.jsonl` | Computed once at start | First + last event timestamps |
| Hay count | `.haystack/audit.jsonl` | 5s | Count `hay.filed` events since last compile |
| Needle count | `.haystack/needles/issues.jsonl` | 5s | Count open |

### Strategy: tail, not re-read

For `audit.jsonl` (grows continuously): seek to last-read offset on each tick. Parse only new lines. Do NOT full re-parse on every tick — this is already specified in tui.md and is the correct approach.

For `agents.jsonl` (append-only, bounded size): full re-parse is acceptable today. When agent counts exceed ~50, switch to the same seek-offset pattern. This is the JSONL scaling problem (killer #2 from MEMORY.md) — the TUI protocol must not make it worse.

### Currently NOT read but SHOULD be (gaps for →532)

| Data | Source | Needed For |
|------|--------|-----------|
| Gen table | `.haystack/gen_table.jsonl` | File conflict events |
| Session snapshots | `.haystack/sessions/*.jsonl` | Foregrounded agent context % |
| Tack history | `.haystack/tack_history` | ↑/↓ history in tack bar |
| Budget/config | `.haystack/config.toml` | Burn bar (currently unimplemented) |
| HWM (high-water mark) | `.haystack/hwm.jsonl` | Context ceiling warnings |

---

## 2. What Does the TUI SEND to Haystack?

### The answer: two channels, distinct purposes.

**Channel A: Subprocess dispatch (blocking, structured)**
The tack bar dispatches to `haystack <subcommand>` via `std::process::Command`. This is already implemented in `run_haystack_subprocess()`. The TUI is a shell wrapper around the haystack CLI — not a peer talking to a running daemon.

**Channel B: Filesystem writes (non-blocking, ambient)**
For operations that produce side effects without needing to block the TUI render loop: write directly to `.haystack/` files. Example: filing hay means appending to `audit.jsonl` directly (or calling `haystack hay add` as a subprocess).

### What each tack verb dispatches to today

| Tack input | Current dispatch | Blocking? |
|-----------|-----------------|-----------|
| `:status` | `haystack show status` → subprocess | Yes, ~100ms |
| `:reap` | `haystack reap` → subprocess | Yes, ~200ms |
| `:agents` | In-memory fleet refresh (no subprocess) | No |
| `:bench` | `haystack bench --list` → subprocess | Yes |
| `:exec <cmd>` | Not yet implemented (tui-operations.md §4) | Would be PTY |
| Unrecognized | Hardcoded fallthrough to subprocess | Yes |
| Bare text | NOT IMPLEMENTED — should file as hay | — |

### Gap: bare text does not file as hay yet

Per tui.md: "Bare text → file as hay." Per tui-operations.md: "Unrecognized input → treated as `haystack hay`." This is not wired. For →532, bare text in the tack bar must call `haystack add "<text>"` or write directly to the hay pile.

---

## 3. Should the TUI Protocol Be Tack-Native?

### Yes — with a three-tier resolution stack.

The tack bar is already tack-native in presentation (`:`, `.`, `->` prefix detection, prompt morphing). The protocol question is: does the kernel resolve tack, or does the TUI?

**Answer: The TUI resolves tack at Tier 1 and 2. Kernel resolves Tier 3.**

| Tier | Who resolves | Latency | Example |
|------|-------------|---------|---------|
| 1: Exact match | TUI (in-process) | 0ms | `:reap` → `haystack reap` |
| 2: Pattern match | TUI (in-process) | 0ms | `:needle 532` → `haystack needle show 532` |
| 3: LLM inference | NOT in TUI — this is fcp-haystack's job | slow | `:pitchfork` |

**The TUI must NOT spawn an LLM for tack resolution.** That is fcp-haystack's domain. The TUI is a kernel component. If it can't resolve a verb at Tier 1 or 2, it passes the raw string to `haystack <verb>` and the CLI handles it, or emits an error.

### The full resolution path for tack input in →532

```
User types in tack bar → Enter
  │
  ├─ Starts with `/`     → TUI internal command (view switch: /fleet, /needles)
  ├─ Starts with `:`     → Tack verb dispatch (Tier 1 match, else subprocess)
  ├─ Starts with `.`     → Soft probe (Tier 1: .? → haystack show; else subprocess)
  ├─ Starts with `->`    → Sequence (chain: parse each `:verb`, execute left-to-right)
  └─ Bare text           → File as hay: `haystack add "<text>"`
```

### Concrete verb → command map for TUI implementation

Extend `dispatch_tack()` in mod.rs with the full tack-grammar.md map:

| Tack verb | haystack command | Notes |
|-----------|-----------------|-------|
| `:compile` | `haystack compile` | Long-running, use PTY not blocking |
| `:ship` | `haystack commit` + push | Compound |
| `:reap` | `haystack reap` | Already implemented |
| `:status` | `haystack show status` | Already implemented |
| `:agents` / `:fleet` | Fleet pane refresh (in-memory) | Already implemented |
| `:bench` | `haystack bench --list` | Already implemented |
| `:exec <cmd>` | PTY spawn | Not yet (tui-operations.md §4) |
| `:delegate →NNN` | `haystack needle next --claim` + spawn | Not yet |
| `:drain <agent>` | `haystack nudge <agent> drain` | Not yet |
| `:kill <agent>` | `haystack shutdown <agent>` | Not yet |
| `:thread <name>` | `haystack thread <name>` | Not yet |
| `:note <text>` | `haystack add "<text>"` | = bare text |
| bare text | `haystack add "<text>"` | Not yet wired |

---

## 4. What Events Should the Kernel PUSH to the TUI?

### Short answer: None. The TUI polls. There is no push channel.

This is correct architecture. Push requires a daemon, a socket, or a signal — all of which add coupling and infrastructure. The filesystem IS the message bus. Haystack Design Law 3.

However, "push" is achievable through polling with appropriate rates. The effect is push; the mechanism is pull.

### Event types and their polling equivalents

| Event | Polling source | Rate | How TUI detects it |
|-------|--------------|------|-------------------|
| New nudge | `.haystack/nudges/*.jsonl` | 5s | Line count or mtime change |
| Agent crash | `.haystack/agents.jsonl` + `kill -0` | 1s | Status changes from active → crashed |
| File conflict (Hot PR) | `.haystack/gen_table.jsonl` | 2s | Gen counter bump on a file |
| New hay filed | `.haystack/audit.jsonl` | 5s | New `hay.filed` event |
| Needle opened/closed | `.haystack/needles/issues.jsonl` | 5s | Count change or new entry |
| Bench result | `.haystack/audit.jsonl` | 10s | New `bench_result` event |
| Agent context % | `.haystack/sessions/<alias>.jsonl` | 5s | Last session entry's context field |
| Budget warning | `.haystack/audit.jsonl` | 5s | `api_usage` events summed |
| Human-needed | `.haystack/audit.jsonl` | 2s | `human_needed` event type |

### High-priority "push-like" events (poll fast)

These should poll at 1-2s because latency matters:

1. **Agent crash** — human needs to know immediately. 1s poll.
2. **Human_needed inbox item** — blocking event. 2s poll.
3. **File conflict** — gen_table.jsonl mtime. 2s poll.

### Notification escalation

When a P0 event is detected during any poll tick, the TUI should:
1. Audibly signal if possible (terminal bell via `\x07`)
2. Flash the focused panel border briefly (one render frame, bold border)
3. If not in Inbox focus: show `[!]` badge on the Inbox panel title

No new mechanism required. The render loop already runs at 16ms.

### What about `inotify` / `FSEvents`?

Do NOT use inotify or macOS FSEvents for v1.1. They add platform-specific dependencies and complexity. If poll latency proves insufficient in practice, revisit in v1.2. The 1s poll for fleet and 2s for inbox achieves sub-2s event detection — adequate for human operators.

---

## 5. How Does This Relate to the Haystack^ Status Line?

### What is haystack^?

`haystack^` is the OS status line for shell prompts — a PS1-equivalent that embeds kernel state into every shell prompt. It is separate from the TUI but reads the same data sources.

### Data the status line needs (same as TUI top bar)

```
haystack^ agent-697 | 246→ | 11hay | 3● 1✕ | $0.42/hr
```

| Element | Source | Notes |
|---------|--------|-------|
| Current agent alias | `.haystack/identity_counter` + env var | Which identity am I? |
| Open needle count | `.haystack/needles/issues.jsonl` | Count open |
| Hay pending | `.haystack/audit.jsonl` | Count uncompiled hay |
| Fleet health | `.haystack/agents.jsonl` + `kill -0` | Fast: read once per prompt |
| Burn rate | `.haystack/audit.jsonl` | Rolling 10m average of api_usage |

### Protocol relationship: status line IS a single-shot TUI

`haystack show status` is the CLI command. The status line (`haystack^`) is that command's output compressed to one line, run on each prompt evaluation.

The TUI top bar renders the same data, updated at 1s intervals instead of per-prompt.

**Implementation consequence**: the data loading functions in `src/tui/mod.rs` — `load_fleet_from_agents()`, `compute_uptime()`, `load_needles()` — should be extracted to a `src/kernel/status.rs` module that BOTH the TUI and the `haystack show status` / `haystack^` CLI command call. No duplication.

### The haystack^ prompt string

```
haystack^ [alias] [Nopen→] [Nhay] [fleet] $
```

This is a read-only status line. It does NOT accept input. It is not tack. It shares data sources with the TUI but has no protocol — it fires `haystack show status --line` once per prompt and exits.

---

## Implementation Priorities for →532

**Phase 1: Wire tack input (text input + command dispatch)**
1. Add text input field to tack bar (cursor, readline keybinds already stubbed in mod.rs)
2. Wire bare text → `haystack add "<text>"`
3. Wire `:compile`, `:ship`, `:thread`, `:draft` (subprocess dispatch, blocking output to command pane)
4. Wire `:exec <cmd>` → PTY spawn (streaming output, per tui-operations.md §4)
5. Input history at `.haystack/tack_history` (↑/↓ navigation)

**Phase 2: Event detection improvements**
1. Poll `nudges/broadcast.jsonl` at 5s (currently not read)
2. Poll `gen_table.jsonl` for conflict events at 2s (not currently read)
3. Poll `sessions/<alias>.jsonl` for context % (not currently read)
4. Escalate P0 events: bell + border flash

**Phase 3: Extract shared data layer**
1. `src/kernel/status.rs` — shared between TUI and CLI
2. `haystack show status --line` → single-line output for haystack^
3. `haystack^` as a shell eval target (in HUMANFILE / shell init)

---

## Protocol Summary

```
┌─────────────────────────────────────────────────────────┐
│                        HUMAN                            │
│                       (TUI)                             │
├──────────────────┬──────────────────────────────────────┤
│   READ (poll)    │           SEND (dispatch)            │
│                  │                                      │
│ .haystack/       │  Tier 1/2: in-process tack resolve   │
│   agents.jsonl   │    → haystack <cmd> subprocess       │
│   audit.jsonl    │                                      │
│   nudges/*.jsonl │  Tier 3: unrecognized → subprocess   │
│   needles/*.jsonl│    → kernel resolves or errors       │
│   gen_table.jsonl│                                      │
│   sessions/*.jsonl│ /slash → TUI internal (view switch) │
│                  │                                      │
│ (no push channel │  bare text → haystack add "<text>"  │
│  no daemon       │                                      │
│  no socket)      │  PTY foregrounding: fd passthrough   │
│                  │  (Ctrl-B intercept only)              │
└──────────────────┴──────────────────────────────────────┘
```

The kernel is invisible. The TUI reads files and runs subcommands. Agents do the same. The protocol is the filesystem.

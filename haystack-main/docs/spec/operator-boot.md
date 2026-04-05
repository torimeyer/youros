---
title: Operator Boot — Harness Detection and Persistent Operator Context
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-12
needles: →651, →652, →653, →654
compounds: →484 team-boot, →566 HUMANFILE-at-boot, →567 HUMANFILE-as-tack-import, →650 first-run
implements: []
---

# Operator Boot

> The OS should know who it's talking to and where it's running. Every session.

## The Problem

Each boot session, the operator has to re-teach:
- "You're in Claude Code harness — use native Bash, not shell"
- "Background long-running commands"
- "Use ostk CLI, not cat/grep"

This is a solved problem — the spec for it exists in `team-boot.md` (→484). The
memoization table (`~/.ostk/humanfile.toml`) was designed to hold exactly these
corrections. It was never built.

**The failure mode:** `bootloader.md` documents it explicitly: *"Knows preferences:
partial (missed calibrate, corrections)."* That was v0.1. It's still v0.1.

---

## Architecture

Boot produces context from three layers:

```
Layer 1 (repo):     .ostk/boot.md              shared, committed
Layer 2 (operator): ~/.ostk/humanfile.toml      personal, never committed
Layer 3 (harness):  detected at runtime             ephemeral, annotated in boot output
                                                    ↓
                    merged context delivered to instance
```

`context = f(repo) + g(operator) + h(harness)`

The harness layer is new. It answers: *where is the instance running right now?*

---

## Layer 3: Harness Detection (→651)

`ostk boot` detects harness type from environment at startup.

### Detection priority

```
1. OSTK_HARNESS env var (explicit override — always wins)
2. OSTK_SERVE=1 → harness: ostk-serve
3. TERM_PROGRAM=claude → harness: claude-code
4. TERM_PROGRAM=vscode → harness: vscode
5. CI=true or GITHUB_ACTIONS=true → harness: ci
6. Default → harness: terminal
```

### Tool routing policy per harness

| Harness | Shell | Files | Background |
|---------|-------|-------|------------|
| `claude-code` | native Bash | native Read/Edit/Grep | `run_in_background=true` for ops >5s |
| `ostk-serve` | shell / spawn | file:edit / file:read | spawn with wait_for |
| `vscode` | native Bash | native Read/Edit | inline (no background needed) |
| `ci` | native Bash | native | inline, no TTY |
| `terminal` | native Bash | native | background optional |

### Boot output annotation

Every `ostk boot` output includes a harness block:

```
ostk 1.3 · boot:0.92 ◉

harness: claude-code
tool_routing: native (Bash/Read/Edit/Grep)
scheduling: background ops >5s via run_in_background

needles: 270 open  agents: 0  hay: 1 pending
last session: 2h ago  audit: 1,385 events

ready.
```

The instance reads this at boot. It knows its context without being told.

---

## Layer 2: Operator Context (→653, →654)

The `~/.ostk/humanfile.toml` file is the operator's accumulated preferences,
memoized across all sessions and all repos.

### File format

```toml
# ~/.ostk/humanfile.toml
# Managed by: ostk refine
# Last updated: 2026-03-12

[identity]
name = "scottmeyer"
preferred_model = "claude-sonnet-4-6"

[communication]
verbosity = "terse"          # terse | verbose | auto
confirm_before_act = false   # operator wants execution, not options
trailing_summary = false     # no "here's what I did" at end of response

[scheduling]
background_threshold_seconds = 5
background_long_ops = true
parallel_independent_calls = true

[tool_routing]
# These are defaults; harness detection overrides at runtime
prefer_ostk_cli = true   # ostk not cat/grep for OS state
no_cd_prefix = true          # just `ostk cmd`, never `cd /path && ostk cmd`

[corrections]
# Append-only log of operator corrections. Source of truth for humanfile evolution.
# Format: "YYYY-MM-DD: <correction>"
log = [
  "2026-03-12: use native Bash not MCP shell when in claude-code harness",
  "2026-03-12: use ostk CLI not cat/grep for OS state queries",
  "2026-03-12: never cd ~/projects/ostk && — just ostk directly",
  "2026-03-12: background commands >5s using run_in_background",
]
```

### HUMANFILE accumulator (→654)

`ostk refine` — run at session end or on-demand — extracts corrections from
the current session context and appends to `~/.ostk/humanfile.toml`:

```
ostk refine --humanfile
```

Detection heuristics for corrections:
- `:correct ...` tack verb
- `:adjust ...` tack verb
- User messages containing "don't", "never", "stop", "use X not Y"
- Explicit `:remember` tack verb

The correction is appended to `[corrections].log` and, where applicable, the
relevant `[section]` key is updated.

### Bootstrap

First run: `ostk install` creates `~/.ostk/humanfile.toml` from template.
If `registers-dump.md` files exist in `.ostk/` history, `ostk refine --bootstrap`
scans them and populates the initial corrections log.

---

## Two-Phase Boot Implementation (→653)

```
Phase 1: Repo context (same for all operators on this repo)
  1. Read .ostk/boot.md
  2. Detect harness type → tool routing policy

Phase 2: Operator context (unique per human)
  3. Load ~/.ostk/humanfile.toml
  4. Merge: operator preferences overlay repo defaults
  5. Apply: communication style, scheduling, tool routing

Phase 3: Annotate and deliver
  6. Compose boot output: repo state + harness block + operator block
  7. Deliver to instance
```

---

## Boot.md Operator Section (→652)

Every regenerated `boot.md` includes:

```markdown
## Operator

- harness: claude-code
- tool_routing: native Bash / Read / Edit / Grep
- scheduling: background_threshold=5s, parallel_calls=true
- communication: terse, no trailing summary, execute > options
- corrections: 4 active (see ~/.ostk/humanfile.toml)
```

This section is regenerated from `~/.ostk/humanfile.toml` on every
`ostk refine` / `ostk compile --boot`.

---

## Acceptance Criteria

### →651 Harness detection
- [ ] `ostk boot` detects harness from env — 5 harness types
- [ ] Tool routing policy included in boot output
- [ ] `OSTK_HARNESS` env var overrides detection
- [ ] CI harness detected correctly (no background, no TTY)
- [ ] Tests: 5 harness types, 1 override test

### →652 boot.md operator section
- [ ] `## Operator` block in every regenerated boot.md
- [ ] Harness, tool_routing, scheduling, communication fields present
- [ ] Section absent if humanfile.toml doesn't exist (graceful)

### →653 Two-phase boot
- [ ] Phase 1 (repo) + Phase 2 (operator) execute in order
- [ ] Operator preferences from humanfile.toml reach instance context
- [ ] Merge: humanfile overrides repo defaults where applicable
- [ ] Missing humanfile → Phase 2 skipped gracefully (no error)

### →654 HUMANFILE accumulator
- [ ] `ostk refine --humanfile` extracts corrections from current session
- [ ] `:correct`, `:adjust` tack verbs → corrections log entry
- [ ] Corrections log is append-only (never overwrites existing entries)
- [ ] `ostk refine --bootstrap` scans registers-dump.md history
- [ ] `ostk install` creates empty humanfile.toml from template
- [ ] Round-trip: correction made → refine → next boot shows it → never re-taught

---

## The Outcome

After this ships, the operator teaches each preference exactly once.
`ostk refine --humanfile` runs at session end. Next session:

```
ostk 1.4 · boot:0.95 ◉

harness: claude-code
tool_routing: native · scheduling: background >5s · terse mode

needles: 270 open  agents: 0
ready.
```

The instance knows. No re-teaching required.

---

## Why This Matters

This is the difference between "an OS you configure once" and "an OS you configure
every session." The spec existed (team-boot.md, v0.1 quality gap). The pieces exist
(registers-dump.md, corrections, HUMANFILE concept). What was missing was the wiring.

→651–654 is that wiring.

---
promoted_at: 2026-03-13T03:35:11Z
status: spec
author: orchestrator
ac:
- ++attention
- --distraction
- USER+LLM
- USER -> .lm compile USER <- LLM
evidence: reap (→493) exposed the gap. 500 ghosts. No boot hygiene. The kernel has primitives but no login sequence.
created: 2026-03-09
version: 1
depends_on:
- bootloader
- humanfile
- agentfile-bootloader
- tack
title: llmOS.sh — The Login Shell
implements: []
---

# llmOS.sh — The Login Shell

> The kernel is ostk. The login shell is llmOS.sh. One boots the hardware. The other boots the human.

## The Problem

ostk has a kernel. It has a bootloader (boot.md). It has a process table, file CAS, compression, reap. What it doesn't have is a **login shell** — the thing that sits between power-on and productive work.

Today the boot sequence is manual:
```
human types "ostk boot"     # reads boot.md
human reads output               # orients
human types intent               # LLM parses tack, dispatches
```

Three steps, all human-initiated. The LLM doesn't know it's booting. It doesn't know when boot is DONE. There's no pre-login hygiene (reap, verify, env). There's no post-login calibration (Humanfile, Agentfile, convergence check). The human IS the shell — and when the human isn't present, nothing boots.

## What a Login Shell Does

Unix login shell (`/bin/bash`, `/bin/zsh`):
1. **Pre-login:** PAM, env setup, motd
2. **Login:** authenticate, load profile
3. **Post-login:** .bashrc, .zshrc — the user's environment
4. **Interactive loop:** prompt → parse → execute → display

llmOS.sh maps these:

```
llmOS.sh
  ├── PRE-LOGIN
  │   ├── ostk reap              # GC dead agents (PAM = identity hygiene)
  │   ├── ostk boot --verify     # kernel state check (fsck)
  │   └── env setup                  # OSTK_ROOT, PATH, OSTK_AGENT
  │
  ├── LOGIN
  │   ├── read Humanfile             # who is this human? (.profile)
  │   ├── read Agentfile             # what can this agent do? (capabilities)
  │   └── read boot.md               # session state (motd)
  │
  ├── POST-LOGIN
  │   ├── convergence check          # how many corrections last session?
  │   ├── compile ready              # hay → needles if stale
  │   └── nudge queue                # pending nudges for this agent
  │
  └── INTERACTIVE LOOP
      ├── prompt (tack input)        # human types compressed intent
      ├── parse (tack grammar)       # LLM decompresses
      ├── execute (kernel ops)       # ostk commands
      └── display (compressed out)   # squasher output
```

## The Acceptance Criteria, Unpacked

### `++attention`

The login shell FOCUSES the LLM. Today, Claude boots with 200k tokens of capability and zero orientation. It can do anything. It does nothing well until calibrated. llmOS.sh narrows the aperture:

- Boot.md is 1600 tokens. That's the world.
- Humanfile says "execute, don't ask." That's the posture.
- Dispatch queue says "→491, →492 are open P1s." That's the work.
- Nudge queue says "SWE v19 results unchecked." That's the priority.

Attention = constraint. The login shell constrains what the LLM sees at boot so it sees what matters.

### `--distraction`

The login shell REMOVES noise before the LLM sees it.

- Reap runs before boot.md is read. 500 ghosts never enter context.
- Read elision skips files the agent already has. 304 tokens saved.
- Squasher strips VTE codes from output. 60% token reduction.
- The Humanfile says "no options" so the agent never generates them.

Distraction = tokens spent on things that don't compile into work. The login shell minimizes these.

### `USER+LLM`

The login shell serves BOTH processors in the SMP architecture.

**For the human:**
- `llmOS.sh` is a single command. Not three. Not "boot then read then tell the agent."
- The console shows boot state — mission control, not log viewer.
- Nudges surface what the OS thinks matters. Human confirms or overrides.

**For the LLM:**
- Context is pre-loaded. No 5-turn orientation dance.
- Humanfile calibration means turn 1 is productive, not calibratory.
- The tack grammar is recognized — compressed input decompresses without clarification.

### `USER -> .lm :compile USER <- LLM`

The bidirectional compile. This is the core loop:

```
USER types:     ^->:add needles :delegate :notify
                ↓ (tack parse)
LLM compiles:   file →491 "delegate: ostk dispatches work..."
                file →492 "notify: ostk tells the human..."
                ↓ (execute)
Kernel writes:  .ostk/needles/issues.jsonl appended
                .ostk/needles/counter bumped
                ↓ (display)
USER sees:      →491 delegate — OS dispatches work to agents.
                →492 notify — OS tells the human what happened.
```

Human intent → tack → LLM parse → kernel operation → compressed result → human comprehension. The login shell is the COMPILER between these two processors. It doesn't add intelligence — it adds the pipeline that makes intelligence flow.

## What llmOS.sh Is NOT

- **Not the kernel.** The kernel is `ostk` (the Rust binary). llmOS.sh uses the kernel.
- **Not the agent.** The agent is Claude/GPT/whatever. llmOS.sh boots the agent into the OS.
- **Not CLAUDE.md.** CLAUDE.md is static instructions. llmOS.sh is a dynamic boot sequence that reads those instructions.
- **Not a framework.** No adoption required. If the agent doesn't know about llmOS.sh, the kernel still works. The shell makes it work BETTER.

## Implementation Shape

Two forms, same sequence:

### Form 1: Literal shell script
```bash
#!/bin/bash
# llmOS.sh — the login shell for ostk
set -e

export OSTK_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$OSTK_ROOT"

# PRE-LOGIN
ostk reap
ostk boot --verify

# LOGIN — the LLM reads these, not the script
# boot.md, Humanfile, Agentfile are injected via CLAUDE.md
# or via the MCP server's initial context

# POST-LOGIN
ostk show status --json > /tmp/.ostk-boot-state.json

# INTERACTIVE — launch the shell (the LLM)
exec claude --resume
```

### Form 2: Kernel subcommand
```
ostk login
```

Which runs the same sequence inside the Rust binary. The script is the prototype. The subcommand is the product.

### Form 3: Implicit (the real target)
The login sequence runs automatically when `ostk serve` starts. The MCP server's first response includes the boot context. No script. No subcommand. The login IS the connection.

This is the invisible path. Form 1 and 2 exist for humans who want to see the boot. Form 3 is the OS actually being an OS — booting invisibly when the agent connects.

## The Boot Lifecycle (Complete)

```
SHUTDOWN (previous session)
  └── registers-dump.md written
  └── boot.md regenerated
  └── commit + push

         ─── session boundary ───

POWER ON (new session)
  └── llmOS.sh / ostk login / ostk serve
      ├── PRE-LOGIN
      │   ├── reap (GC dead agents)
      │   └── verify (boot.md current?)
      ├── LOGIN
      │   ├── Humanfile → calibrate posture
      │   ├── Agentfile → constrain capabilities
      │   └── boot.md → orient to state
      ├── POST-LOGIN
      │   ├── convergence check
      │   ├── compile if stale
      │   └── load nudge queue
      └── READY
          └── first prompt is productive

         ─── interactive loop ───

SHUTDOWN (this session)
  └── ...
```

## Open Questions

1. **`ostk login` vs `ostk boot`** — is login a superset of boot? Or a sibling? Boot reads state. Login reads state AND prepares the agent. I think login calls boot internally.

2. **Form 3 timing** — does the MCP server send boot context on connection, or on first tool call? Connection is eager (wastes tokens if agent never uses it). First-call is lazy (first response is slow). The serve layer already has the digest — maybe boot context is just a richer first-response digest.

3. **Humanfile loading** — the Humanfile doesn't exist yet (`ostk learn human` not built). The login sequence should degrade gracefully: no Humanfile → CLAUDE.md only → still boots. Layered loading.

4. **Multi-agent login** — when 5 agents connect via `ostk serve`, each gets its own login context. Same boot.md, different Agentfiles, same Humanfile. The login sequence is per-agent but the state is shared.

---

*The kernel runs processes. The login shell boots humans. llmOS.sh is where the human becomes the operator.*

- [ ] Login shell lifecycle compounded into docs/spec/llmos-userspace.md Part II

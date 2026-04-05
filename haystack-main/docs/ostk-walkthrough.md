---
title: ostk — First Session Walkthrough
status: live
author: scott+haystack.prime
created: 2026-03-12
---

# ostk — First Session Walkthrough

> You have 5 minutes. By the end, you'll have filed your first needle,
> seen the OS boot, and understood why the TUI exists.

## Install

```sh
curl -fsSL https://ostk.ai/install.sh | sh
```

Or from source:
```sh
cargo install --git https://github.com/os-tack/haystack
```

---

## Step 1: Init your project (30 seconds)

```sh
cd your-project
ostk init
```

This creates `.ostk/` — the OS state directory. Think of it as `/proc/` for your project. One command. Nothing else changes.

---

## Step 2: Boot (1 minute)

```sh
ostk boot
```

You'll see:

```
ostk POST
[OK] .ostk/ present
[OK] audit.jsonl readable
[OK] HUMANFILE present
[OK] needle store intact
POST ready.

# ostk — boot context
## Recent commits
  (your recent git log here)
## Project stats
  Source files: 47
  Needles: 0 total, 0 open
```

This is the kernel reading the project state. The output IS the context the LLM will boot from.

---

## Step 3: File your first problem (1 minute)

You have a bug, a feature idea, a design question. Don't open a ticket. Type it:

```sh
ostk hay "the login page redirects to /home instead of /dashboard after OAuth"
```

Output: `~ the login page redirects...` — filed as hay (raw unstructured intent).

Now compile it into a needle:

```sh
ostk compile
```

Output: `added →1: login page redirects to /home instead of /dashboard after OAuth [P1] (task)`

You have your first needle. Check it:

```sh
ostk show status
```

---

## Step 4: Open the TUI (ongoing)

```sh
ostk tui
```

What you're looking at:
- **WORK pane** (bottom-left): your needle `→1` waiting to be worked
- **FLEET pane**: empty — no agents running yet
- **tack bar** (bottom): type here to talk to the OS

Type `:status` in the tack bar and press Enter. See the OS stats in OUTPUT.

Type `.? what should I work on first` — the LLM reads your boot context and answers in OUTPUT.

---

## Step 5: Dispatch an agent (1 minute)

Type this in the tack bar:

```
:delegate →1
```

The OS will dispatch an agent to work needle →1. You'll see it appear in the FLEET pane.

While the agent works, you can keep typing in the tack bar — your draft is never interrupted. That's the quickline (bottom strip) for fast corrections.

---

## Step 6: Watch the savings accumulate

Look at the status bar: `0M/100M` — token savings quota.

Every tool call the agent makes compresses through HSCP. Every compressed output saves tokens. The number grows. Your first 100M saved tokens are free.

---

## What's happening (the mental model)

```
YOU (operator)
  → type tack in TUI
    → kernel reads intent
      → LLM schedules work
        → agent runs tools
          → output surfaces in TUI
            → YOU review, adjust, approve
```

The TUI is the shell. The LLM is the CPU scheduler. Agents are processes. `.ostk/` is shared memory.

You never leave the TUI to get work done. The OS routes everything.

---

## Key commands to know

```sh
ostk boot          # read OS state
ostk tui           # open the shell
ostk hay "..."     # capture raw thought
ostk compile       # turn hay into needles
ostk show status   # see everything
ostk run Agentfile # spawn a specific agent
```

In the TUI:
```
:compile      → compile pending hay
:status       → OS snapshot
:reap         → clean dead agents
.? question   → ask the LLM
bare text     → file as hay automatically
```

---

## The invisible OS

ostk is invisible by design. It didn't change your tools. It didn't add a wrapper. It just started tracking what you're doing and compressing what goes to the LLM.

The savings show up in the status bar. The work happens in the FLEET pane. You stay in the TUI.

That's the OS.

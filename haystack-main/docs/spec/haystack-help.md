---
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — human typed :help, :?, ::, .? throughout. The OS needs to explain itself in its own language.
implements: []
---

# ostk help

> The OS explains itself by compiling its own patterns into guidance.

## Not a man page

`ostk help` is not `--help`. `--help` is clap printing flag descriptions. `ostk help` is the OS compiling its understanding of the human's intent patterns into guidance a new agent can use.

## Two directions

```
ostk help    → OS explains itself TO the human/agent (outward)
ostk learn   → OS absorbs the human's patterns (inward)
```

Both are compile operations. `help` compiles FROM the OS's state. `learn` compiles FROM the human's behavior.

## What `ostk help` produces

### For a new agent (boot context)
```
ostk help --boot
```
Produces the minimum context a fresh agent needs:
- What ostk is (one sentence)
- The vocabulary (needle, hay, thread, compile, refine)
- The intent signals this human uses (from intent-signal-gradient spec)
- The top 3 needles from registers-dump.md
- The CLI surface (needle, hay, show, compile, commit)

This IS the Agentfile PROMPT content. `ostk help --boot` generates what goes into `PROMPT file://` directives.

### For the human (shell reference)
```
ostk help
```
Shows:
- Current status (from `show status`)
- What the human can say (intent patterns the OS recognizes)
- What's running, what's next
- How to file hay, compile needles, show anything

### For an agent mid-session (--agents)
```
ostk --agents
```
Already exists. The interstitial guide. `help` subsumes it — `--agents` becomes `ostk help --agent`.

## What `ostk help` reads

| Source | What it provides |
|--------|-----------------|
| `.ostk/boot.md` | Project state, vocabulary, preferences |
| `.ostk/registers-dump.md` | Hot work, volatile context |
| `docs/spec/intent-signal-gradient.md` | How this human communicates |
| `.ostk/needles/issues.jsonl` | Open work counts |
| `.ostk/audit.jsonl` | Hay pending, session history |

## The compile operation

`ostk help` doesn't read a static file. It compiles from live state:

```
read boot.md + registers + intent spec + needles + audit
  → compile into oriented guidance
    → emit as structured text (for agents) or human-readable (for terminal)
```

The help output changes as the project changes. New needles → different "what's next." New intent patterns → different "how to communicate." The help is always current because it's compiled, not written.

## `--json` for agents

```
ostk help --boot --json
```

Machine-readable boot context. An Agentfile's PROMPT can reference this:
```
PROMPT file://<(ostk help --boot)
```

The help command generates the boot prompt dynamically.

## Acceptance criteria

- [ ] `ostk help` shows human-readable project status + intent guide
- [ ] `ostk help --boot` produces agent boot context from live state
- [ ] `ostk help --boot --json` produces machine-readable boot context
- [ ] `ostk --agents` becomes alias for `ostk help --agent`
- [ ] Output changes when needles/hay/registers change (compiled, not static)
- [ ] Intent patterns section reads from intent-signal-gradient spec

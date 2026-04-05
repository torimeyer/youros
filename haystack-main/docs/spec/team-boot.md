---
status: spec
version: 1
author: scottmeyer + rtx3 (architecture, cognitive, prior-art)
created: 2026-03-09
needle: →484
evidence: 3-round analysis. Prior art (git, vscode, k8s, shells) all converge on same 3-tier merge. R2 cognitive insight — context is f(repo) + g(operator), never f(repo, operator). Keep them separable.
implements: []
---

# Team Boot — Per-Operator Identity in Shared Repos

> My humanfile is not the same as the engineer sitting next to me.

## The Problem

Two engineers (Scott and Tori) use ostk on the same repo. Scott's corrections: "don't present options — execute." Tori's preference: "explain before acting." Same needles, same audit trail, different operators. The OS needs to separate repo state (shared) from operator state (personal).

## Architecture

### Three Scopes

| Scope | Path | Committed | Content |
|-------|------|-----------|---------|
| Global (user) | `~/.ostk/` | Never | humanfile.toml, bin/, config.toml |
| Shared (repo) | `.ostk/` | Yes | boot.md, needles/, audit.jsonl, specs |
| Personal (repo) | `.ostk/.local/` | Gitignored | registers-dump.md, sessions/, locks, gen_table, humanfile.override.toml |

### Merge Order

```
~/.ostk/config.toml          (global defaults)
  < .ostk/boot.md             (repo shared state)
    < .ostk/.local/            (repo personal state)
      < ~/.ostk/humanfile.toml (operator identity)
        < CLAUDE.md                (explicit human word — always wins)
```

### Boot Sequence

```
Phase 1: Repo context (same for all operators)
  1. Load .ostk/boot.md
  2. Load .ostk/needles/issues.jsonl
  3. Load .ostk/audit.jsonl (tail)

Phase 2: Operator context (unique per human)
  4. Resolve identity: $USER, git config user.name, or ~/.ostk/identity
  5. Load ~/.ostk/humanfile.toml (global patterns)
  6. Overlay .ostk/.local/humanfile.override.toml (repo-specific)
  7. Deliver merged context to agent
```

## What Lives Where

### `~/.ostk/` — The Operator (global, never committed)

```
~/.ostk/
  humanfile.toml           # compiled from correction history across all repos
  config.toml              # default model, budget, preferences
  bin/ostk             # the binary
  shims/                   # PATH-prefix shims
  identity                 # operator name (fallback if git config absent)
```

The humanfile is the memoization table from Insight #5 (Intent Dynamic Programming). Scott's table is deep — 20x compression. Tori's is empty — LLM falls back to maximum verbosity (safe default). Each operator's table fills independently across sessions.

### `.ostk/` — The Repo (shared, committed)

```
.ostk/
  boot.md                  # swap file — repo truth
  needles/issues.jsonl     # work queue
  audit.jsonl              # event trail
  specs.json               # page table (if built)
```

### `.ostk/.local/` — The Operator x Repo (personal, gitignored)

```
.ostk/.local/
  registers-dump.md        # volatile session state
  sessions/                # agent session logs
  gen_table.json           # runtime gen counters
  *.lock                   # runtime locks
  humanfile.override.toml  # repo-specific operator overrides
```

**Gitignore:** `.ostk/.local/` — one line.

## Conflict Resolution

Repo state wins over humanfile, always. Humanfile preferences are advisory — they shape how the agent communicates, not what it builds.

| Conflict | Resolution |
|----------|-----------|
| Two users file same needle differently | Needle in issues.jsonl is canonical |
| Scott prefers terse, Tori prefers verbose | Humanfile controls agent communication style — no conflict |
| Scott's correction contradicts Tori's | Each correction updates their own humanfile — independent |
| Runtime gen_table divergence | .local/ is per-operator, no sharing |

## The Composition Law

```
context = f(repo) + g(operator)
```

Never `f(repo, operator)`. Keep them separable. The kernel loads both; the agent merges them at inference time. Two operators, same repo, two completely different sessions — from the same boot sequence.

## What Ships

1. **`.ostk/.local/` directory** — move runtime state (sessions, locks, gen_table, registers-dump) out of committed tree
2. **`.gitignore` update** — add `.ostk/.local/`
3. **`~/.ostk/humanfile.toml`** — empty template created by `ostk install`
4. **Boot sequence** — two-phase load: repo then operator
5. **Identity resolution** — `$USER` → git config → `~/.ostk/identity` fallback chain

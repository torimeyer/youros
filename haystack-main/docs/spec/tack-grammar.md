---
title: "Tack Grammar — v2"
implements: []
---

# Tack Grammar — v2

status: spec
source: session 2026-03-09 usage corpus (audit-verified), implementation audit 2026-03-17
date: 2026-03-17
refined: →775 — scoped spec to match implementation, documented gaps

## Resolution order

fcp-ostk resolves tack input in this order:

1. **Exact match** → ostk command (fast, no LLM)
2. **Pattern match** → best-effort map to ostk command (fast, no LLM)
3. **LLM inference** → send to model for intent resolution (slow, fallback)

Priority: always try ostk-native resolution first. LLM is last resort.

## Implementation: parse() — 4 prefix patterns

`src/fcp/ostk.rs::parse()` recognizes exactly **4 prefix patterns** (in priority order):

| Prefix | Intent Type | Examples |
|--------|-------------|---------|
| `→` or `->` | `NeedleRef` | `→437`, `->437` |
| `::` | `Escalate` | `:: agent1 fix the build` |
| `.?` | `Query` | `.? status`, `.? why` |
| `:` | `Command` | `:compile`, `:boot --dry-run` |

Everything else returns `None` (unparseable — falls through to LLM).

### Resolution pipeline

`resolve()` (line 260) resolves a parsed `Command` intent against three verb sources, checked in order:

1. **.language** (Tier 1, highest priority) — compiled dialect from `.ostk/.language`
2. **HUMANFILE** (Tier 2) — `VERB` or `TACK` directives in `.ostk/HUMANFILE`
3. **static verb table** (Tier 2) — hardcoded in `static_verb_table()`

If no match, `resolve()` returns `resolved: false` with up to 3 edit-distance suggestions (Levenshtein, threshold 3).

### Lint (→597, Tier 0)

`lint()` performs pre-parse validation: parse the input, then check if the verb exists in any of the three tables. Returns `UnknownVerb`, `MalformedSequence`, or `Ok`.

## Verb → Command map (static table)

The static verb table in `static_verb_table()` maps verbs to ostk CLI commands. This is the **complete** list as implemented:

### 1:1 CLI commands

| Verb | Command |
|------|---------|
| `init` | `init` |
| `install` | `install` |
| `boot` | `boot` |
| `compile` | `compile` |
| `hay` | `hay` |
| `thread` | `thread` |
| `nudge` | `nudge` |
| `run` | `run` |
| `show` | `show` |
| `log` | `log` |
| `history` | `history` |
| `shutdown` | `shutdown` |
| `import` | `import` |
| `ps` | `ps` |
| `status` | `status` |
| `spawn` | `spawn` |
| `needle` | `needle add` |
| `reap` | `reap` |
| `commit` | `commit` |
| `draft` | `draft` |
| `promote` | `promote` |
| `decompose` | `decompose` |
| `trace` | `trace` |
| `amend` | `amend` |
| `shelve` | `shelve` |
| `unshelve` | `unshelve` |
| `diff` | `diff` |
| `post` | `post` |
| `merge` | `merge` |
| `secret` | `secret` |
| `serve` | `serve` |
| `listen` | `listen` |
| `scheduler` | `scheduler` |
| `ask` | `ask` |
| `audit` | `audit check` |
| `bench` | `bench` |
| `help` | `help` |
| `clock` | `clock` |
| `metrics` | `metrics` |
| `verify` | `verify` |
| `tack` | `tack` |
| `search` | `search` |
| `purge` | `purge` |
| `pull` | `pull` |
| `bail` | `bail` |
| `handoff` | `run` |
| `result` | `needle close` |

### Aliases

| Alias | Resolves To |
|-------|-------------|
| `showme` | `show` |
| `spec` | `promote` |
| `straw` | `hay` |
| `emerge` | `hay` |
| `proc` | `ps` |
| `correct` | `nudge` |
| `recover` | `boot` |
| `calibrate` | `compile` |
| `delegate` | `run` |
| `inform` | `nudge` |
| `ls` | `needle list` |
| `open` | `needle list` |
| `close` | `needle close` |
| `add` | `needle add` |
| `next` | `needle next` |
| `find` | `search` |
| `grep` | `search` |

## Operators — implemented vs specified

### IMPLEMENTED (parsed by `parse()`)

| Operator | Parser Handling | Meaning |
|----------|----------------|---------|
| `:` | Prefix match → `Command` intent | Hard demand (verb prefix) |
| `.?` | Prefix match → `Query` intent | Question |
| `→` / `->` | Prefix match → `NeedleRef` intent | Needle reference |
| `::` | Prefix match → `Escalate` intent | Escalate / nudge |

### NOT IMPLEMENTED (recognized in human input, not parsed by fcp-ostk)

These operators appear in the usage corpus and are understood by the LLM in context, but the `parse()` function does not recognize them. They fall through to Tier 3 (LLM inference) or are ignored.

| Operator | Corpus Meaning | Status |
|----------|---------------|--------|
| `.` | Soft modifier (`.p0`, `.c`) | **Not parsed** — args passed as raw strings |
| `->` (infix) | Sequence / flow (`:compile -> :ship`) | **Not parsed** — only prefix `->` recognized as NeedleRef |
| `=>` | Elevated priority | **Not parsed** |
| `<-` | Source / from (`USER<-`) | **Not parsed** |
| `<<-` | Compact / condense | **Not parsed** |
| `^` | Escalate | **Not parsed** |
| `++` / `--` | Intensity modifiers | **Not parsed** |
| `!` | Negation | **Not parsed** |
| `#` | Tag / scratch | **Not parsed** |
| `,` | List separator | **Not parsed** — whitespace split only |

**Design note**: This gap is intentional in the current architecture. The parser is deliberately simple (4 prefix checks). Complex operator semantics are deferred to the LLM (Tier 3), which already has the context to interpret them. Implementing these operators in the parser would require a proper expression grammar with infix handling, precedence, and modifier attachment — complexity that the LLM handles for free.

## Intensity via repetition

Recognized by the LLM but not by the parser:

| Symbol | Levels | Example |
|--------|--------|---------|
| `+` | 1-5+ (more = stronger) | `+++++error` |
| `-` | 1-2 (negation depth) | `--decrease` |
| `:` | 1-5 (demand intensity) | `:::::intent` (maximum demand) |

Note: repeated `:` (e.g., `:::::intent`) is parsed as a `Command` with verb `::::intent` — the parser strips the first `:` and treats the rest as the verb. This will fail verb lookup and fall through to LLM.

## Compound expressions

Compound expressions (chained verbs, modifier attachment, sequence operators) are **not implemented** in the parser. The parser processes a single tack expression per call.

Examples that work by falling through to LLM:

```
:compile :ship :delegate
  → LLM interprets as: compile, then commit+push, then spawn

:needle add "title" .p0 :delegate
  → LLM interprets: add needle with P0, then delegate
```

## Session-specific patterns

These patterns are LLM-interpreted, not parser-recognized:

| Pattern | Meaning |
|---------|---------|
| `.p0` `.p1` `.p2` | Priority shorthand |
| `:hs add` / `:hs work` | ostk CLI from tack |
| `:image#N` | Image reference |
| `:evidence screenshot` | Evidence attachment |
| `:env` | "in another terminal" |
| `USER<-` | User receives |
| `->rtx2` `->rtx3` | Iteration depth (round-trip count) |
| `:thread name` | Thread reference |
| `:compounds target` | Compounding reference |
| `:ref URL` | Reference link |
| `:include` | Include in scope |

## What fcp-ostk does at each resolution tier

### Tier 0: Lint (→597) (0ms, no LLM)
Static check: parse the input, check if the verb exists in .language, HUMANFILE, or static table. Returns `Ok`, `UnknownVerb(verb)`, or `MalformedSequence`.

### Tier 1: .language resolution (0ms, no LLM)
Verb found in `.ostk/.language` compiled dialect. Highest priority — overrides static table and HUMANFILE. Source: `ResolveSource::Language`, confidence: 1.0.

### Tier 2: Static / HUMANFILE resolution (0ms, no LLM)
Verb found in HUMANFILE extensions (confidence 0.9) or static verb table (confidence 0.8). Source: `ResolveSource::Humanfile` or `ResolveSource::Static`.

### Tier 3: LLM inference (slow, fallback)
Verb not recognized. `resolve()` returns `resolved: false` with edit-distance suggestions. The caller (harness/TUI) may send to the LLM with context: "User typed `:<verb>`. Available commands: [list]. What did the user intend?"

The OS never says "unknown verb." It always tries.

## HUMANFILE integration

The HUMANFILE's tack grammar section defines the user's dialect.
Custom verbs via `VERB <name> <command>` or `TACK <name> <command>` directives. Loaded at resolution time from `.ostk/HUMANFILE`.

```
# In HUMANFILE:
VERB pitchfork show --semantic
VERB discuss spawn round-table
TACK boost priority-modifier
```

## .language integration

`.ostk/.language` is the compiled dialect — a pipe-delimited table generated by `ostk compile` from audit.jsonl usage patterns.

Format: `verb | tier | layer | last_gen | half_life | momentum | resolution`

Loaded by `load_language_verbs()`. The `resolution` field (column 7) maps to the ostk command, with `ostk ` prefix stripped. Language verbs take priority over everything — they represent the kernel's learned understanding of the user's dialect.

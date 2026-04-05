---
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: session 2026-03-08 — intent layer had 3 names for one operation, compile overloaded, hay vs add confusion
implements: []
---

# CLI Surface — Standardized Intent Layer

## The Pipeline

```
straw    default. raw thought. goes on pile.
hay      the pile. uncompiled straws.
compile  hay -> issues (things needing attention)
issue    needs attention. broken, blocking, urgent.
refine   issue -> needle (fix now) OR backlog
needle   fix now. verb + target + test.
```

## The Surface

### Entry point — `ostk add`

```
ostk add "thought"                  files a straw (default, ~ prefix)
ostk add --issue "something broke"  files an issue (needs-attention flag)
ostk add --source agent-1           override source attribution
```

`add` replaces `hay` as THE entry point. One command, two modes.

### Compile — `ostk compile`

```
ostk compile                        triage straws -> issues -> needles
ostk compile --dry-run              show what would be triaged
ostk compile --cluster NAME         compile a discovered cluster
ostk compile --auto                 skip human review gate
ostk compile -O0|-O1|-O2|-O3       optimization level (per ostk-compile spec)
```

No change to compile semantics. It reads the pile, classifies, sharpens.

### Query — `ostk show`

```
ostk show <anything>                universal query (shipped)
ostk show hay                       list the pile
ostk show needles                   list needles (alias for needle list)
ostk show threads                   list threads
ostk show status                    project status
ostk show clock                     session clock
ostk show <id>                      single item by ID
ostk show <anything> --json         machine-readable output
```

`show` is the single read surface. Already shipped.

### Needle — `ostk needle`

```
ostk needle list [--status X] [--priority X] [--count] [--json]
ostk needle close <id> [--reason "text"]
ostk needle add "desc" [--priority P1] [--type needle]
ostk needle next [--claim] [--assignee NAME]
```

`needle add` stays for programmatic use (agents filing precise needles). Humans use `ostk add`.

### Thread — `ostk thread`

```
ostk thread create <name> --needles <id...>
ostk thread list
```

No change.

### Calibrate — `ostk calibrate <thread>`

```
ostk calibrate <thread>             telescope on a thread (per ostk-compile spec)
```

Already specced. No change.

### Infrastructure

```
ostk install [--symlinks|--no-symlinks]   bootstrap OS
ostk init                                  init project
ostk commit -m "msg" [--spec X] [--bead X] [--agent X]
ostk serve                                 MCP server
ostk spawn <name> <prompt> [--model X] [--budget X]
ostk run <agentfile>                       run agent
ostk audit check|backfill                  audit trail
ostk log [--filter X] [-n N]               audit log
ostk trace <id>                            attribution chain
ostk help [--boot] [--agent] [--json]      compiled help
ostk ps                                    fleet status
ostk status                                daemon health
ostk nudge <agent> "msg"                   inject context
```

No changes to infrastructure commands.

### Hidden aliases (backward compat, never shown in help)

```
ostk hay "thought"          -> ostk add "thought"
ostk issue <subcmd>         -> ostk needle <subcmd>
ostk work <subcmd>          -> ostk needle <subcmd>
```

## Code Delta

Changes required in `src/main.rs`:

1. **Add `Commands::Add`** — new variant with positional `thought: String`, `--issue` flag, `--source` option.
2. **Hide `Commands::Hay`** — add `#[command(hide = true)]`, delegate to `Commands::Add` logic.
3. **Wire `Commands::Add`** — if `--issue`, call `run_add` with type "issue"; else call `run_hay`.
4. **Add `Commands::Calibrate`** — new variant with positional `thread: String`. Stub to `commands_use::calibrate::run`.
5. **Update `Commands::Compile`** — add `--cluster`, `--auto`, `-O` args to match spec.
6. **Update about strings** — `Hay` about becomes "Alias for add (backward compat)".

Files touched:
- `src/main.rs` — Commands enum, dispatch match
- `src/commands/mod.rs` — add `pub mod calibrate` if new module needed
- No changes to `src/commands/work.rs`, `src/commands/show.rs`, `src/commands/commit.rs`

## What Does NOT Change

- `needle list`, `needle close`, `needle next`, `needle add` — unchanged
- `show` — unchanged
- `compile` semantics — unchanged (new flags are additive)
- `thread` — unchanged
- All infrastructure commands — unchanged
- Audit event format — `hay.filed` stays, `add` just calls the same path

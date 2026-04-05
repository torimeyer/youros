---
status: draft
title: CLI Hierarchy — Agent API Surface
needle: →617
author: scott+haystack.prime
created_at: 2026-03-12T17:24:52Z
compounds: fcp-ostk, tui-primary, escape-harness, llmOS-scheduler
---

# CLI Hierarchy — Agent API Surface

> TUI is the primary human interface. The CLI is the agent API.
> Design for agents and scripts, not for human fingers.

## The Problem

30+ flat top-level commands. Inconsistent shape:
- Noun+verb for some: `ostk needle add`, `ostk thread list`
- Bare verb for others: `ostk compile`, `ostk reap`, `ostk diff`

Agents can't discover subcommands without reading the full help.
fcp-ostk T1 verb resolution guesses from a flat list.
Fleet Agentfiles use different command shapes across authors.

## The Design

TUI is the human interface. CLI serves two callers:
1. **Agents** — inside ostk sessions, doing work
2. **Scripts** — CI, crons, automation

Both benefit from noun-first hierarchy:

```
ostk work pull                # claim next available needle
ostk work file "<text>"       # file hay
ostk work close →NNN          # close needle with reason
ostk work history →NNN        # audit trail for a needle

ostk os status                # full OS state
ostk os diff                  # session delta since boot
ostk os show →NNN             # needle / hay / thread lookup
ostk os clock                 # time, uptime, audit depth
ostk os threads               # thread list with needle counts
ostk os log                   # audit trail tail

ostk kernel serve             # MCP server (stdio)
ostk kernel spawn <agentfile> # spawn agent (alias: ostk run)
ostk kernel reap              # GC dead agents
ostk kernel verify            # integrity check
ostk kernel post              # power-on self test
ostk kernel boot              # read boot.md, report state
```

## Backward Compatibility

Existing flat commands become **aliases** — they do not break.
`ostk compile` → `ostk work compile`
`ostk reap` → `ostk kernel reap`
`ostk diff` → `ostk os diff`
`ostk show` → `ostk os show`

Aliases emit a `cli.deprecated` event to audit.jsonl (silent to stderr).
Agents see the nudge in the next boot.md if using deprecated surface.

## fcp-ostk verb map

tack verb → CLI command mapping becomes explicit:

| Tack verb | CLI command |
|-----------|-------------|
| `:compile` | `ostk work compile` |
| `:reap` | `ostk kernel reap` |
| `:diff` | `ostk os diff` |
| `:status` | `ostk os status` |
| `:delegate →NNN` | `ostk kernel spawn` with needle context |
| `:pull` | `ostk work pull` |
| `.? →NNN` | `ostk os show →NNN` |

T1 resolution hits these mappings exactly. No ambiguity.

## Compounding

- **fcp-ostk**: explicit verb→command map makes T1 resolution a lookup, not inference
- **Agentfile authoring**: fleet agents use the same surface — `ostk work pull`
- **escape harness**: agents with `ostk work` primitives have no reason to reach for Read/Edit/Bash
- **llmOS scheduler**: `ostk kernel spawn` with `FROM auto` becomes the standard dispatch verb

## Migration order

1. Add `ostk work`, `ostk os`, `ostk kernel` subcommand groups
2. Wire existing handlers under both old and new paths
3. Add `cli.deprecated` audit event on old path calls
4. Update `ostk --agents` guide to new surface
5. Update Agentfile templates
6. After one release cycle: remove old flat commands (keep aliases permanently)

## Acceptance criteria

- [ ] `ostk work pull` claims next needle (same as `ostk pull`)
- [ ] `ostk os status` shows OS state (same as `ostk show status`)
- [ ] `ostk kernel reap` GCs dead agents (same as `ostk reap`)
- [ ] All existing flat commands still work via alias
- [ ] Alias path emits `cli.deprecated` audit event
- [ ] `ostk work --help` lists all work subcommands
- [ ] `ostk os --help` lists all os subcommands
- [ ] `ostk kernel --help` lists all kernel subcommands
- [ ] `ostk --agents` guide updated to new surface
- [ ] fcp-ostk T1 verb map updated to new command paths

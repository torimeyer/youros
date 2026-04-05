# ostk Data Layer: Hot/Warm/Query Storage

> JSONL for writes, condensed for reads, queryable for intelligence.

## Problem

JSONL is the right write primitive (append-only, O_APPEND, multi-writer safe).
But it's terrible for reads — agents scanning 10K events to find "latest state 
of agent X" wastes context tokens. And structured queries ("which agents touched 
this file in the last hour?") require full log replay.

## Three-Layer Model

```
Agents write → [Hot: JSONL] → condense pipeline → [Warm: Condensed] → [Query: Dolt/SQLite]
                                                                            ↑
Agents read ←──────────────────────────────────────────────────────────────────
Intelligence reads ←───────────────────────────────────────────────────────────
```

### Hot Layer: JSONL (write path)

- `~/.local/share/ostk/events.jsonl`
- Append-only, O_APPEND for multi-writer safety
- Every event lands here immediately: spawn, kill, state change, spec amendment, 
  bead open/close, policy add/revoke, human-needed
- Never queried directly by agents — too verbose
- Retention: rotate after 100K lines or 24h (configurable)

### Warm Layer: Condensed State (read path for agents)

- `~/.local/share/ostk/state.json` (or `.jsonl`)
- Current state projections, rebuilt from hot layer periodically
- Updated by condense pipeline every 30s or on significant events
- Sections:
  - `agents`: current fleet state (alias, pid, model, context%, task, health)
  - `beads`: active work items (id, status, assignee, spec_ref)
  - `policies`: active policies (id, scope, message, ttl)
  - `specs`: document status (path, state: draft/spec, last_amended)
  - `inbox`: pending human-needed items
- This is what agents read. One file, current state, no replay needed.

### Query Layer: Dolt or SQLite (intelligence path)

- Dolt: git-for-data. Branch, diff, merge on coordination state.
  - `ostk log` = `dolt log` on the state table
  - `ostk diff` = what changed between two points in time
  - Enables: "roll back to the state before that spec amendment"
- SQLite alternative: simpler, no server, good enough for single-machine
  - Tables: events, agents, beads, policies, specs
  - Intelligence layer queries with SQL
- Decision: start with SQLite, migrate to Dolt when we need branching/versioning

## Condense Pipeline

Runs inside ostk daemon. Same concept as mish's squasher — reduce noise, 
preserve signal.

```
1. Read new events from JSONL since last checkpoint
2. For each event type:
   - spawn/kill/state → update agents projection
   - bead.open/close → update beads projection
   - policy.add/revoke → update policies projection
   - spec.amend → update specs projection
   - human_needed → update inbox projection
3. Write condensed state to warm layer
4. Insert into query layer (SQLite)
5. Update checkpoint offset
```

Intelligence layer can also condense history:
```json
{
  "prompt": "Summarize the last 500 events into key state changes",
  "model": "haiku",
  "context": { "events": [...] },
  "output": { "summary": "...", "key_changes": [...] }
}
```

## Agent Consumption

Agents never read raw JSONL. They get state through:

1. **Tool response annotations** — shim injects relevant state from warm layer
2. **`ostk status`** — reads warm layer, returns current fleet/bead/policy state
3. **`ostk query "which agents touched src/main.rs"`** — intelligence call against query layer

This means the data layer is invisible to agents. They call tools, tools check state. 
Same "invisible kernel" principle from the architecture.

## Dolt Advantages (future)

- `dolt branch` on coordination state → experiment with different agent configurations
- `dolt diff` → what changed when that spec was amended
- `dolt merge` → combine work from two parallel agent fleets
- `dolt log` → full audit trail with structured diffs, not raw events
- Git-like collaboration on the coordination layer itself

## Acceptance Criteria

- [ ] Hot layer: JSONL append works, events land in <1ms
- [ ] Condense pipeline runs every 30s, warm layer is current
- [ ] Agents can read warm layer in one tool call
- [ ] Query layer handles "agents that touched file X" in <100ms
- [ ] Log rotation doesn't lose unconsumed events
- [ ] Intelligence layer can query both warm and query layers

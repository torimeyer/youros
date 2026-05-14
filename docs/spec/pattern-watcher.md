---
title: Pattern Watcher
status: spec
created_at: 2026-05-14T18:43:38Z
promoted_at: 2026-05-14T18:45:40Z
---

# Pattern Watcher

A silent learning layer that observes how Tori works and surfaces what
it learns in layers, so myOS earns the right to act on its own. First
two patterns: task patterns (defer, accept, re-prompt, close) and
vocabulary patterns (new words used in chat, old words used in new
ways). Built on ostk-recall as the substrate — no new database or
service.

## What we want

Today MEMORY.md is hand-curated, over the load limit, and Tori is the
only writer. Every new behavior rule requires her to notice the pattern,
name it, write it, and trim something else. The OS should learn its
user instead. Pattern Watcher is that learning layer. It observes
silently, lets repeated observations cluster naturally via ostk-recall's
hybrid retrieval, and surfaces what it learned in three tiers (panel
only → inline chat hint → silent behavior change), with each tier
gated by an explicit user action so myOS never acts on its own until
Tori has said it can.

## Acceptance criteria

- [ ] A chat-driven task defer writes a `task:defer` bullet to `~/myos/observations/tasks.md` within one turn.
- [ ] A new vocabulary token used in chat writes a `vocab:new` bullet to `~/myos/observations/vocab.md` within one turn.
- [ ] `recall_stats` shows `observations` as a configured ostk-recall source with the expected chunk count after the watcher ingests.
- [ ] The "What I learned about you" panel renders at least one cluster after a session that produces observations.
- [ ] Confirming a cluster in the panel causes the next relevant turn to include an inline chat hint above the response.
- [ ] Approving a cluster for silent action causes the next matching task event to apply the pattern without a chat hint.
- [ ] With the ostk-recall daemon stopped, turns still complete without errors visible to Tori; observations queue in markdown and ingest when the daemon restarts.

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                      Each model session turn                       │
│                                                                    │
│  start-of-turn ──► recall_fault({query, intent: "narrative"})      │
│                    via ostk-recall MCP                             │
│                    ──► page injected into prompt context           │
│                                                                    │
│  end-of-turn ──► observation worth recording?                      │
│                  yes ──► append bullet to                          │
│                          ~/myos/observations/<kind>.md             │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
            ┌──────────────────────────────────┐
            │  ostk-recall watcher (~250 ms)   │
            │   ingests new markdown chunks    │
            │   into LanceDB + BM25            │
            └──────────────────────────────────┘
                              │
                              ▼
            ┌──────────────────────────────────┐
            │  Pattern panel (frontend)        │
            │  reads via recall_stats +        │
            │  recall(query="pattern:")        │
            └──────────────────────────────────┘
```

No new backend service. Write path is markdown append. Read path is
the ostk-recall MCP. Confidence is emergent from hybrid retrieval
ranking. Tier state lives in ostk decisions (`pattern:tier:<cluster_id>`).

## Components

### 1. Observation writer (per-session hook)

End-of-turn hook in `.claude/hooks/` that runs identically for Claude
and Gemini sessions. Decides whether anything observation-worthy
happened and appends one bullet per observation.

Two observation kinds in v1:

| kind   | file                              | trigger                                                                                                                      |
| ------ | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| task   | `~/myos/observations/tasks.md`    | task created, deferred, accepted, re-prompted, closed, or priority changed via chat                                          |
| vocab  | `~/myos/observations/vocab.md`    | a non-stopword token not seen in 30 days of session logs, or a known vocab token in a syntactic position not seen before     |

Bullet shape (one line per observation):

```
- 2026-05-14T19:32:11Z task:defer pri=P3 title="fix the badge thing" reason="tori said 'later'"
- 2026-05-14T19:34:02Z vocab:new token="elit" surrounding="say elit when you want plain language"
```

### 2. Observation reader (per-session pre-prompt injection)

Start-of-turn hook calls the ostk-recall MCP:

```
recall_fault({
  query: "<current task context, 1-2 sentences>",
  intent: "narrative",
  limit: 5
})
```

The returned page is injected into the model's prompt as a labeled
section ("WHAT MYOS HAS LEARNED ABOUT TORI THAT MIGHT BE RELEVANT").
Models are instructed to treat this as observational context, not
commands.

### 3. ostk-recall configuration

New source in `~/.config/ostk-recall/config.toml`:

```toml
[[sources]]
kind = "markdown"
project = "observations"
paths = ["~/myos/observations"]

[watch]
enabled = true
mode = "incremental"
```

### 4. "What I learned about you" panel

Sidebar tab in the myOS UI, not a permanent overlay. Each row is one
observation cluster, summarized by the model on render:

```
[task]  You usually defer P3s about formatting.       seen 12x, last 3h ago
[vocab] "elit" means: plain language, no metaphors.   seen 4x, last 2d ago
```

Row actions: Confirm (tier 1 → 2), Approve for silent action (tier 2 → 3),
Dismiss (write a `dismissed` marker so it does not resurface), Edit
(append rewritten observation, original stays but new one outranks).

### 5. Layered surfacing

| tier              | trigger                                                    | what it does                                                                |
| ----------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------- |
| 1. panel only     | observation has matches in recall                          | shows up in the panel, sorted by frequency                                  |
| 2. inline hint    | user clicked Confirm in the panel                          | model prepends a one-line hint at the top of relevant chat responses        |
| 3. silent action  | user clicked Approve for silent action                     | myOS applies the pattern without asking (e.g. auto-deprioritizes P3s)       |

Tier promotion writes `ostk decide pattern:tier:<cluster_id> <tier>`
with the user action as reason. Tier reads on each turn run
`ostk recall-search "pattern:tier:" --scope decisions`.

### 6. ostk-recall wire-up

`mem.fault_recall` is currently inactive on this machine. This spec
covers wiring it up: install the binary, create config with the
observations source plus existing-substrate sources (haystack `.ostk/`,
Claude Code session logs, Gemini CLI session logs, MEMORY.md), run
`ostk-recall init` + `scan`, register the MCP server in the relevant
client configs, run `ostk-recall watch` as a launchd service so the
corpus stays fresh.

## Data flow examples

### Vocab observation

1. Tori says "elit on the boot loadavg behavior".
2. End-of-turn hook tokenizes, notices `elit` is in known-vocab,
   notes `boot loadavg behavior` is a new context for that token.
3. Hook appends to `~/myos/observations/vocab.md`:
   `- 2026-05-14T19:40:00Z vocab:context token="elit" surrounding="boot loadavg behavior"`
4. ostk-recall watcher ingests within ~250 ms.
5. Next loadavg turn, the reader hook gets the observation back and
   Claude gives a plain-language answer.

### Task pattern graduates to silent action

1. Tori defers 12 P3 formatting tasks over two weeks. Each defer writes
   a `task:defer pri=P3 ...` bullet.
2. Panel shows: `[task] You usually defer P3s about formatting. seen 12x.`
3. Tori clicks Confirm.
4. Next time a P3 formatting task is created, Claude prepends:
   "Heads up, you usually defer these. Want me to drop the priority?"
5. Tori clicks Approve for silent action.
6. Next time a P3 formatting task is created, myOS sets P3 → P4
   automatically and notes the action in the audit stream.

## Error handling

| failure                                | behavior                                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------------------- |
| ostk-recall daemon down                | observations still write to markdown; reader gets nothing; turn proceeds                  |
| `recall_fault` MCP call times out (>2s)| turn proceeds without injected context; hook logs to audit                                |
| observations directory unwritable      | hook logs once per session, does not retry, does not block the turn                       |
| markdown file > 10 MB                  | hook rotates to `tasks.YYYY-MM.md`; ostk-recall handles both                              |
| tier-promotion decision write fails    | UI shows error, tier does not change; no silent corruption                                |

Pattern Watcher never blocks a turn. If the substrate is unavailable,
myOS behaves exactly like today, just without the layered learning.

## Testing

- Unit: writer produces correctly formatted bullets for each kind and
  sub-kind; reader calls `recall_fault` with the right query shape;
  tier promotion writes the right decision key.
- Integration: temp `~/myos/observations` directory + temp ostk-recall
  corpus; write a bullet, wait for the watcher, query via `recall_fault`,
  confirm round trip.
- End-to-end: simulate a chat that defers a P3 task five times; confirm
  the panel cluster appears; click Confirm; confirm next P3 triggers an
  inline hint.
- Resilience: stop the daemon mid-session; confirm turns still complete
  and observations still write; restart and confirm backlog ingests.

## Out of scope

- Agent trust patterns (which models Tori accepts vs re-prompts).
- Time-of-day patterns.
- Changes to ostk-recall itself.
- Editing or replacing MEMORY.md. The two coexist; Pattern Watcher is
  additive.
- Cross-machine sync. Pattern Watcher is local-first like ostk-recall.

## Open questions for the implementation plan

- Exact hook insertion points in `.claude/hooks/`.
- Whether the panel renders in the existing React sidebar or a new route.
- The cluster-summary prompt the panel uses to compress the recall page.
- Migration: backfill from existing session logs, or start from zero
  on day one (recommendation: start from zero; ostk-recall already
  indexes the old sessions).

## References

- ostk-recall: https://github.com/os-tack/ostk-recall
- Threading meta-bug that surfaced this design session: →1352
- MEMORY.md (the file this layer is designed to replace, eventually):
  `~/.claude/projects/-Users-torimeyer-claude-torios/memory/MEMORY.md`

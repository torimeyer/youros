# Pattern Watcher — Design Spec

Date: 2026-05-14
Status: Design approved, pending implementation plan.
Author: torios (with tori)

## What we're building

A silent layer that learns how Tori works and surfaces what it learned, in
layers, so myOS earns the right to act on its own. The first two things it
learns are:

- **Task patterns.** Which tasks Tori defers, which she accepts immediately,
  which she re-prompts, which she closes without action. Goal: predict
  priority and suggest reordering.
- **Vocabulary patterns.** New words Tori uses in chat, old words used in
  new ways. Goal: pick up new vocabulary automatically instead of waiting
  for her to file a rule.

The layer is named **Pattern Watcher**.

## Why now

MEMORY.md is hand-curated and is over the 24.4KB load limit as of today
(see warning in `~/.claude/projects/-Users-torimeyer-claude-torios/memory/MEMORY.md`).
Tori is the only writer. Every new behavior rule she wants requires her to
notice the pattern, name it, write it, and trim something else. That's the
opposite of an OS learning its user.

myOS already has the substrate for fixing this: **ostk-recall**, the local-
first hybrid retrieval MCP (markdown trees, code, Claude Code sessions,
Gemini CLI sessions, haystack `.ostk/` dirs, ~250 ms watcher latency).
Scott's guidance: use it, don't build a parallel store.

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
│                                                                    │
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

No new backend service. No new database. The substrate is ostk-recall, the
write path is markdown append, the read path is the MCP, and confidence is
emergent from hybrid retrieval ranking.

## Components

### 1. Observation writer (per-session hook)

A hook that runs after each assistant turn. Decides whether anything
observation-worthy happened and, if yes, appends a structured bullet to a
markdown file under `~/myos/observations/`.

Two observation kinds in v1:

| kind   | file                                        | trigger                                                                                                              |
| ------ | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| task   | `~/myos/observations/tasks.md`              | task created, deferred, accepted, re-prompted, closed, or its priority changed via chat                              |
| vocab  | `~/myos/observations/vocab.md`              | a non-stopword token appears that has not been seen in the last 30 days of session logs, or a known vocab token appears in a syntactic position it has not been used in before |

Observation bullet shape (markdown, one bullet per observation):

```
- 2026-05-14T19:32:11Z task:defer pri=P3 title="fix the badge thing" reason="tori said 'later'"
- 2026-05-14T19:34:02Z vocab:new token="elit" surrounding="say elit when you want plain language"
```

Each bullet is one line. Fields are space-separated `key=value`, values
quoted if they contain spaces. Timestamp is the first token. Kind and
sub-kind go together (`task:defer`, `vocab:new`).

The writer is implemented as a hook in the existing `.claude/hooks/`
infrastructure, so it runs identically for Claude and Gemini sessions.

### 2. Observation reader (per-session pre-prompt injection)

A start-of-turn hook that calls the ostk-recall MCP:

```
recall_fault({
  query: "<current task context, 1-2 sentences>",
  intent: "narrative",
  limit: 5
})
```

The returned page is injected into the model's prompt as a labeled section
("WHAT MYOS HAS LEARNED ABOUT TORI THAT MIGHT BE RELEVANT"). Models are
instructed to treat this as observational context, not as commands.

`recall_fault` already synthesizes hits across all ingested sources, so the
same MCP call returns both task observations and vocab observations and
ranks them by relevance to the current turn.

### 3. ostk-recall configuration

A new `[[sources]]` entry of kind `markdown` for the observations directory:

```toml
[[sources]]
kind = "markdown"
project = "observations"
paths = ["~/myos/observations"]
```

Plus the watcher configured for incremental ingest:

```toml
[watch]
enabled = true
mode = "incremental"
```

### 4. "What I learned about you" panel (frontend)

A scrollable panel in the myOS UI. Top-level UI: tab in the existing
sidebar, not a permanent overlay.

Each row is one cluster of observations, summarized by the model on read:

```
[task] You usually defer P3s about formatting.       seen 12x, last 3h ago
[vocab] "elit" means: plain language, no metaphors.  seen 4x, last 2d ago
```

Row actions:

- **Confirm** — the cluster graduates from "panel only" to "inline hint" tier.
- **Approve for silent action** — graduates from "inline hint" to "silent
  behavior" tier. Requires the cluster to have already been confirmed.
- **Dismiss** — clears the cluster and writes a `dismissed` marker
  observation so the same pattern doesn't immediately resurface.
- **Edit** — open the observation in a text field and rewrite it (the
  edited version is appended; the original stays in the corpus but the new
  one outranks it).

Clusters are summarized on the fly by the model handling the panel render,
calling `recall_fault({query: "patterns", intent: "general", limit: 50})`
and grouping the resulting page into clusters by sub-kind + token similarity.

### 5. Layered surfacing

| tier              | trigger                                                 | what it does                                                                |
| ----------------- | ------------------------------------------------------- | --------------------------------------------------------------------------- |
| 1. panel only     | observation has any matches in recall                   | shows up in the panel, sorted by frequency                                  |
| 2. inline hint    | user clicked "Confirm" in the panel for this cluster    | model prepends a one-line hint at the top of relevant chat responses        |
| 3. silent action  | user clicked "Approve for silent action" for this row   | myOS applies the pattern without asking (e.g., auto-deprioritizes P3s)      |

Tier promotion is recorded as a decision in ostk:

```
ostk decide pattern:tier:<cluster_id> <tier_number> --reason "<user_action>"
```

Tier reads on each turn:

```
ostk recall-search "pattern:tier:" --scope decisions
```

### 6. Wire-up: getting ostk-recall running locally

Currently `mem.fault_recall` shows as inactive in the kernel register on
Tori's box. This spec covers wiring it up:

1. Install the ostk-recall binary (from latest release tarball).
2. Create `~/.config/ostk-recall/config.toml` with the observations source
   plus existing-substrate sources (haystack `.ostk/`, Claude Code session
   logs, Gemini CLI session logs, MEMORY.md).
3. Run `ostk-recall init` and `ostk-recall scan` to bootstrap the corpus.
4. Register `ostk-recall serve --stdio` as an MCP server in the relevant
   client config (Claude Code subagents and the myOS backend).
5. Run `ostk-recall watch` as a launchd service so the corpus stays fresh.

## Data flow examples

### Example 1: vocab observation

1. Tori says in chat: "elit on the boot loadavg behavior".
2. End-of-turn hook tokenizes the message, notices `elit` is in the
   known-vocab set (from MEMORY.md), notes the surrounding context
   `boot loadavg behavior` is a new context for that token.
3. Hook appends to `~/myos/observations/vocab.md`:
   `- 2026-05-14T19:40:00Z vocab:context token="elit" surrounding="boot loadavg behavior"`
4. ostk-recall watcher ingests within ~250ms.
5. Next turn, when Tori asks anything about loadavg, the reader hook calls
   `recall_fault` and the recent elit-in-loadavg-context observation comes
   back in the page. Claude knows to give a plain-language answer.

### Example 2: task observation graduates to silent action

1. Tori defers 12 P3 formatting tasks over 2 weeks.
2. Each defer writes a `task:defer pri=P3 ...` bullet.
3. The panel shows: `[task] You usually defer P3s about formatting. seen 12x.`
4. Tori clicks "Confirm".
5. Next time a P3 formatting task is created, Claude prepends:
   "Heads up, you usually defer these. Want me to drop the priority?"
6. Tori clicks "Approve for silent action".
7. Next time a P3 formatting task is created, myOS sets it to P3 → P4
   automatically and notes the action in the audit stream.

## Error handling

| failure                                         | behavior                                                                                  |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| ostk-recall daemon is down                      | observations still get written to markdown; reader hook gets nothing and turn proceeds    |
| `recall_fault` MCP call times out (>2s)         | turn proceeds without injected context; hook logs to audit                                |
| observations directory unwritable               | hook logs once per session to audit, does not retry, does not block the turn              |
| markdown file grows past 10MB                   | hook rotates to `tasks.YYYY-MM.md` and starts a fresh file; ostk-recall handles both      |
| tier-promotion decision write fails             | UI shows error, the user-visible action does not change tier; no silent corruption        |

Pattern Watcher never blocks a turn. If the substrate is unavailable,
myOS behaves exactly like today, just without the layered learning.

## Testing

- **Unit:** writer hook produces correctly-formatted bullets for each
  observation kind and sub-kind; reader hook calls `recall_fault` with the
  right query shape; tier-promotion writes the right decision key.
- **Integration:** spin up a temp `~/myos/observations` directory and a
  temp ostk-recall corpus; write a bullet, wait for the watcher, query via
  `recall_fault`, confirm the bullet comes back.
- **End-to-end:** simulate a chat session that defers a P3 task 5 times;
  confirm the panel shows the cluster; click Confirm; confirm the next
  P3 task triggers an inline hint.
- **Resilience:** stop the ostk-recall daemon mid-session; confirm turns
  still complete and observations still get written; restart the daemon
  and confirm backlog ingests on the next watch tick.

## Out of scope

- Agent trust patterns (which models Tori accepts vs re-prompts).
- Time-of-day patterns.
- Any changes to ostk-recall itself.
- Editing or replacing MEMORY.md. The two coexist for now; Pattern Watcher
  is additive.
- Cross-machine sync. Pattern Watcher is local-first, like ostk-recall.

## Acceptance

- A new chat-driven task defer writes a `task:defer` bullet to
  `~/myos/observations/tasks.md` within 1 turn.
- A new word used in a configured context writes a `vocab:new` bullet
  within 1 turn.
- ostk-recall `recall_stats` shows `observations` as a source with the
  expected chunk count.
- The "What I learned about you" panel shows at least one cluster after a
  fresh session that produces observations.
- Confirming a cluster causes the next relevant turn to include an inline
  hint.
- Approving a cluster for silent action causes the next matching task
  event to trigger the silent behavior without a chat hint.
- With the ostk-recall daemon stopped, the session still completes turns
  normally; no errors visible to Tori.

## Open questions for the implementation plan

- Exactly where in `.claude/hooks/` the writer + reader hooks plug in.
- Whether the panel renders in the existing React sidebar or a new route.
- The cluster-summary prompt the panel uses to compress the recall page.
- Migration: do we backfill observations from existing Claude Code
  session logs, or start from zero on day one. (Recommendation: start
  from zero. Old sessions are already in the corpus and `recall_fault`
  will surface them.)

## References

- ostk-recall repo: https://github.com/os-tack/ostk-recall
- Threading meta-bug that surfaced this spec session: →1352
- Existing memory file (to migrate away from, eventually):
  `~/.claude/projects/-Users-torimeyer-claude-torios/memory/MEMORY.md`

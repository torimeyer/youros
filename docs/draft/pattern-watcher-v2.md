---
title: Pattern watcher v2
created_at: 2026-05-31T04:54:17Z
status: draft
spec_id: S003-v2
tasks:
  - "1827"
  - "1828"
  - "1829"
  - "1830"
  - "1831"
  - "1832"
  - "1833"
  - "1834"
  - "1835"
  - "1836"
  - "1837"
  - "1838"
  - "1839"
---

# Pattern Watcher v2

A learning layer that watches how you use myOS and surfaces what it notices
over time, so the system earns the right to act on patterns before doing so
on its own.

Phase 1 (already shipped) built the observation writer: the piece that silently
records events like task deferrals and new vocabulary words after each turn.
Phase 2 (this spec) adds the reader, the search index, the panel where you
review clusters, and the promotion flow that lets you say "yes, do that
automatically from now on."

## Problem

Today MEMORY.md is hand-curated, over the load limit, and you are the only
writer. Every behavior rule requires you to notice the pattern, name it, write
it, and trim something else to keep the file from overflowing.

The OS should be tracking its own patterns silently and surfacing them for your
review. Right now it does not. The observation writer exists, but observations
never make it back into context, there is no way to review them, and there is
no path from "I noticed this" to "do it automatically."

## Goals

- After a session that includes task deferrals or new vocabulary, observations
  land in `~/myos/observations/` automatically (phase 1, already done).
- Those observations become searchable via ostk-recall within a few seconds
  of being written.
- A sidebar panel shows you clusters of what the system has learned, in plain
  language.
- You can confirm a cluster to start getting a one-line heads-up in relevant
  chat responses.
- You can promote a confirmed cluster to silent action so the system handles
  it without prompting.
- If the search infrastructure goes offline, nothing breaks: observations still
  queue in markdown and ingest when it comes back.

## Non-goals

- Replacing MEMORY.md. The two coexist; this layer is additive.
- Cross-machine sync. Everything is local.
- Agent trust patterns, time-of-day patterns, or cross-session inference beyond
  what ostk-recall surfaces naturally.
- Editing or modifying ostk-recall itself.
- Changes to MEMORY.md format or tooling.

## What v1 already built

The observation writer (`api/services/pattern_watcher.py`) runs after every
chat turn (called from `api/routers/chat.py:1735`). It detects two events:

- **task:defer**: user message contains deferral language ("later", "not now",
  "park that", etc.) and appends a bullet to `~/myos/observations/tasks.md`.
- **vocab:new**: user message contains a non-stopword token not seen before in
  the vocab file, and appends a bullet to `~/myos/observations/vocab.md`.

Unit tests exist at `api/tests/test_pattern_watcher.py`.

**What v1 does NOT include (needed for v2):**
- Other task events: accept, re-prompt, close, priority change.
- The observation reader (pulling relevant observations back into context before
  each turn).
- ostk-recall configuration pointing at `~/myos/observations/`.
- The "What I learned about you" panel.
- Tier promotion (confirm, approve for silent action, dismiss).
- Resilience tests when the ostk-recall daemon is stopped.

## Acceptance Criteria

### Writer completeness

- [ ] A chat-driven task defer writes a `task:defer` bullet to
  `~/myos/observations/tasks.md` within one turn. (→1827)
- [ ] A new vocabulary token used in chat writes a `vocab:new` bullet to
  `~/myos/observations/vocab.md` within one turn. (→1828)

### Search index

- [ ] After the observation writer appends a bullet, running `recall_stats`
  shows `observations` as a configured source with a chunk count greater than
  zero. (→1829)

### Panel

- [ ] The "What I learned about you" panel in the myOS sidebar shows at least
  one cluster row after a session that produced observations. (→1830)

### Tier promotion

- [ ] Clicking "Confirm" on a cluster row causes the next relevant chat turn
  to include a one-line hint above the response. (→1831)
- [ ] Clicking "Approve for silent action" on a confirmed cluster causes the
  next matching task event to be handled automatically, without a chat hint.
  (→1832)

### Resilience

- [ ] When the ostk-recall daemon is stopped, chat turns still complete
  without any error visible to you; observations still write to markdown; when
  the daemon restarts, the backlog ingests. (→1833)

### Tests

- [ ] Unit test: the observation writer produces correctly formatted bullets
  for each kind (task, vocab) and sub-kind, with the right timestamp, key=value
  fields, and no extra whitespace. (→1834)
- [ ] Unit test: the observation reader calls `recall_fault` with
  `intent="narrative"` and `limit=5`. (→1835)
- [ ] Unit test: tier promotion writes the decision key
  `pattern:tier:<cluster_id>` with the user action as the reason. (→1836)
- [ ] Integration test: write a bullet to a temporary observations directory,
  wait up to 500ms for the ostk-recall watcher to ingest it, query via
  `recall_fault`, and confirm the round trip returns the bullet. (→1837)
- [ ] End-to-end test: simulate five chat turns that each defer a P3 task,
  confirm a cluster appears in the panel, click Confirm, confirm the next P3
  task turn includes an inline hint. (→1838)
- [ ] Resilience test: stop the ostk-recall daemon mid-session, confirm turns
  still complete and observations still write to markdown, restart the daemon,
  confirm the backlog ingests within 2 seconds. (→1839)

## Design

### 1. Observation writer (already shipped, minor gaps)

`api/services/pattern_watcher.py` handles `task:defer` and `vocab:new` today.

**Gaps to close in v2:**

The spec requires detecting task events beyond defer: accept (user agrees to
take on a task), re-prompt (user asks myOS to try again), close (user says a
task is done), and priority change (user bumps or drops a priority). These
cover the remaining sub-kinds listed in the original spec (S003) but not yet
implemented.

Bullet shape remains the same:
```
- 2026-05-14T19:32:11Z task:defer pri=P3 title="fix the badge thing" reason="tori said 'later'"
- 2026-05-14T19:34:02Z vocab:new token="elit" surrounding="say elit when you want plain language"
```

File rotation: when any observation file exceeds 10 MB, the writer rotates to
`tasks.YYYY-MM.md` (or `vocab.YYYY-MM.md`). ostk-recall watches the whole
directory so both files are indexed.

**No new file needed.** Existing: `api/services/pattern_watcher.py`

### 2. Observation reader (new)

At the start of each chat turn, before building the prompt, the chat router
calls `recall_fault` via the ostk-recall MCP:

```python
recall_fault({
    "query": "<last user message, trimmed to 200 chars>",
    "intent": "narrative",
    "limit": 5
})
```

If the call returns content, it is injected into the model prompt as a labeled
section:

```
WHAT MYOS HAS LEARNED ABOUT YOU THAT MIGHT BE RELEVANT
<recall content>
```

The model treats this as observational context, not as instructions.

**Rules:**
- If `recall_fault` raises any exception or times out after 2 seconds, the turn
  proceeds without the injected section. No error is shown to you.
- The reader runs only when `mem.fault_recall` is active in the kernel. If the
  MCP is inactive, the reader skips silently.

**New file needed:** The reader logic can live in `api/services/pattern_watcher.py`
alongside the writer, or in a thin helper imported by `api/routers/chat.py`.
See NEEDS CLARIFICATION #1.

### 3. ostk-recall configuration

A new source entry in `~/.config/ostk-recall/config.toml` (created if missing):

```toml
[[sources]]
kind = "markdown"
project = "observations"
paths = ["~/myos/observations"]

[watch]
enabled = true
mode = "incremental"
```

ostk-recall watcher runs as a launchd service so the corpus stays current.
After install: `ostk-recall init && ostk-recall scan` to populate the index.

See NEEDS CLARIFICATION #2.

### 4. "What I learned about you" panel

A sidebar drawer next to the Activity tab. It does not get its own route.

Each row is one cluster. Clusters are formed by ostk-recall's hybrid retrieval:
multiple bullets about the same pattern land near each other in the index and
get surfaced together when queried with `recall(query="pattern:")`.

Row layout:
```
[task]  You usually defer P3s about formatting.    seen 12x, last 3h ago
        [Confirm]  [Dismiss]
```

After Confirm:
```
[task]  You usually defer P3s about formatting.    seen 12x, last 3h ago
        [Approve for silent action]  [Undo]
```

**Row actions:**
- **Confirm**: tier 1 to 2. Writes `ostk decide pattern:tier:<cluster_id> 2`
  with reason "user confirmed in panel". The next relevant turn prepends a
  one-line hint.
- **Approve for silent action**: tier 2 to 3. Writes
  `ostk decide pattern:tier:<cluster_id> 3` with reason "user approved silent
  action". myOS applies the pattern without asking.
- **Dismiss**: writes a `dismissed` marker so the cluster does not resurface.
- **Edit**: you can rewrite the cluster summary. The original observation stays
  but the new wording outranks it in recall.

**Cluster summary text:** Generated by Sonnet on cluster creation (or when the
cluster grows by 5 new items). Result is cached via
`ostk decide pattern:summary:<cluster_id>`. The panel reads the cached summary
rather than re-running Sonnet on every render.

**Empty state:** When there are no observation clusters yet, the panel shows
"Nothing learned yet. Keep using myOS and patterns will appear here over time."

### 5. Tier promotion and silent action

| Tier | How you get here | What happens next |
|------|-----------------|-------------------|
| 1 | Observation has matches in recall | Shows up in panel, sorted by frequency |
| 2 | You clicked Confirm | Model prepends a one-line hint on relevant turns |
| 3 | You clicked Approve for silent action | myOS applies the pattern automatically |

Tier state is stored in ostk decisions. The key format is
`pattern:tier:<cluster_id>`. Each turn that might be affected reads tier state
via `ostk recall-search "pattern:tier:" --scope decisions`.

**What silent action means in practice:** For a P3 formatting task:defer
pattern at tier 3, the next time a P3 formatting task is created, myOS
automatically drops its priority and notes the action in the audit stream.
The audit entry is the only signal; no chat message is sent.

**No silent action runs without explicit tier 3 approval.** The OS never acts
on its own until you have clicked through both Confirm and Approve.

### 6. Resilience

Pattern Watcher must never block a turn.

| Failure | Behavior |
|---------|----------|
| ostk-recall daemon down | Observations still write to markdown; reader gets nothing; turn proceeds normally |
| `recall_fault` times out (over 2s) | Turn proceeds without injected context; exception logged to audit but not shown to you |
| Observations directory not writable | Writer logs once per session, does not retry, does not block the turn |
| Markdown file over 10 MB | Writer rotates to a monthly file; ostk-recall handles both |
| Tier promotion write fails | Panel shows an error inline; tier does not change; no silent corruption |

## Day-one data

On first run of the pattern watcher, backfill from MEMORY.md only. Hand-translate
existing curated MEMORY.md rules into observation bullets at
`~/myos/observations/` once. Do not backfill from session logs (too noisy, and
the observation format was designed forward-looking). This overrides the default
"start from zero" behavior.

Reference: S003, decision section "Migration: backfill from existing session
logs, or start from zero on day one."

## Failure handling examples

**Vocab round trip**

1. You say "elit on the boot loadavg behavior."
2. End-of-turn writer tokenizes, notices `elit` is in known vocab, notes
   `boot loadavg behavior` as a new context for that token.
3. Writer appends to `~/myos/observations/vocab.md`:
   `- 2026-05-14T19:40:00Z vocab:context token="elit" surrounding="boot loadavg behavior"`
4. ostk-recall watcher ingests within ~250 ms.
5. Next loadavg turn, the reader hook gets the observation back and the model
   gives a plain-language answer.

**Task pattern graduates to silent action**

1. You defer 12 P3 formatting tasks over two weeks. Each defer writes a
   `task:defer pri=P3 ...` bullet.
2. Panel shows: `[task] You usually defer P3s about formatting. seen 12x.`
3. You click Confirm.
4. Next time a P3 formatting task is created, the model prepends:
   "Heads up, you usually defer these. Want me to drop the priority?"
5. You click Approve for silent action.
6. Next time a P3 formatting task is created, myOS sets P3 to P4 automatically
   and records the action in the audit stream.

## Verified against the codebase

| Claim | Evidence |
|-------|----------|
| Observation writer exists and is called after each turn | `api/services/pattern_watcher.py` (gen=1); `api/routers/chat.py:1735` imports and calls `observe_turn` |
| Writer handles task:defer and vocab:new today | `api/services/pattern_watcher.py:_write_task_observations()` (line ~150), `_write_vocab_observations()` (line ~160) |
| Writer only detects defer, not accept/close/re-prompt/priority-change | `api/services/pattern_watcher.py:_DEFER_USER_PATTERNS` is the only event list; no accept/close patterns exist |
| Unit tests for v1 exist | `api/tests/test_pattern_watcher.py` covers AC1 (task:defer), AC2 (vocab:new), plus daemon-stopped edge case |
| No observation reader wired in chat | `api/routers/chat.py` has one `pattern_watcher` reference (line 1735, the write call); no `recall_fault` call exists |
| No ostk-recall config for observations | `grep -r "observations" ~/.config/ostk-recall/` returned nothing; `mem.fault_recall` is listed as inactive in the kernel context |
| No frontend panel for "What I learned about you" | `grep -r "learned about\|PatternPanel\|observations" app/src/` returned no matches |
| No tier promotion logic | `grep -r "pattern:tier\|cluster_id" api/ app/src/` returned no matches |
| Draft file in main repo was a stub (frontmatter only) | `docs/draft/pattern-watcher-v2.md` gen=5 had only title/created_at/status lines |

## NEEDS CLARIFICATION

**NC-1: Reader injection mechanism**

The spec says the reader injects a labeled section into the model prompt. But
the chat router (`api/routers/chat.py`) has its own prompt-building pipeline.
Where exactly does the injection happen? Options:
- (a) As a system message segment appended before the user message.
- (b) As a new turn in the conversation history labeled `[SYSTEM]`.
- (c) As an assistant prefill at the top of the response window.

Option (a) is the least disruptive. Need confirmation before implementation.

**NC-2: ostk-recall install state**

The kernel context shows `mem.fault_recall` as inactive. Is ostk-recall
installed on this machine? If not, the reader (AC →1835) and the integration
test (AC →1837) cannot run. The install path should be confirmed before
implementation of those ACs begins.

**NC-3: Cluster ID format**

The tier promotion key is `pattern:tier:<cluster_id>`. The spec does not define
how `cluster_id` is generated. Options:
- (a) A hash of the cluster's representative observation text.
- (b) A sequential integer assigned at cluster creation time.
- (c) Returned by ostk-recall as part of the query result.

This affects how the panel stores and retrieves tier state. Need a decision
before building the panel or the tier promotion logic.

**NC-4: Writer expansion scope for v2**

The original spec lists six task event sub-kinds (defer, accept, re-prompt,
close, priority change). The existing writer only handles defer. Should v2
add all five remaining sub-kinds, or just the ones covered by the ACs
(→1827 only requires defer)? Expanding all five now avoids a third phase but
requires more detection logic.

**NC-5: Panel empty state behavior**

When there are no observation clusters (fresh install, day one), should the
panel be hidden from the sidebar entirely, or show a placeholder? Hiding it
avoids clutter but makes it harder to discover. A placeholder ("Nothing learned
yet...") makes the feature visible but adds a zero-state to maintain.

## DECISION (2026-05-31, confirmed by Tori)
Resolves NEEDS CLARIFICATION above:
- NC-1 (reader injection): Option (a) — appended as a system-message segment before the user message (least disruptive to the chat pipeline).
- NC-2 (recall install): Resolved — ostk-recall is installed (~/.local/bin/ostk-recall) and configured (~/.config/ostk-recall/config.toml). Reader (→1835) + integration test (→1837) can run.
- NC-3 (cluster ID): use the ID returned by ostk-recall if present; fall back to a hash of the representative observation text. Confirm against engine output at build time.
- NC-4 (writer scope): v2 handles 'defer' only (matches AC →1827). Remaining sub-kinds (accept, re-prompt, close, priority change) deferred to a later phase.
- NC-5 (empty state): show the "Nothing learned yet…" placeholder per the draft's specified wording.

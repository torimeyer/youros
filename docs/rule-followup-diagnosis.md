# Why Claude is STILL violating standing instructions after hooks landed

Scope: events after `89f6465` (hooks persisted 17:21:22 local), pulled from
the live main transcript `8e1194c1-...jsonl` and the 40 subagent transcripts
under `.../subagents/`, cross-checked with `/tmp/ostk-first.log`.

## Top-line

The **main session** stopped using native tools the moment the hook landed.
All violations post-17:01 come from **subagents spawned via the Agent tool**.
They do not know the ostk MCP exists, so they keep retrying native
Read/Write/Edit/Bash after every block, never loading ostk via ToolSearch.

## Evidence table

| Time (local) | Tool | Hook decision | Standing rule broken | Why |
|---|---|---|---|---|
| 17:19:35 | Bash `git show 172c0ed:api/services/...` | **blocked** (ostk-first) | ostk-first | Subagent `restore-and-persist` tried git show via Bash. Got the block message, then switched to Edit instead of loading `mcp__ostk__shell` via ToolSearch. |
| 17:20:47 | Edit `/.../.gitignore` | not blocked (file-not-read) | ostk-first | Same subagent. Edit failed only because Read was not done first. Had it been, the hook would have blocked anyway. Shows the model flails between native tools, not reaching for ostk. |
| 17:22:00 | Grep `badge` | **cancelled** (sibling Bash was blocked) | ostk-first | Subagent `sync-agent-badge` issued 5 parallel native calls (Bash curl + Grep + 3 Globs). First Bash blocked, whole batch cancelled. Model retried one-at-a-time with Write, Read, Edit, Read, Edit, all blocked or failed, for 3 minutes, then gave up. |
| 17:15:16 | Write `.../memory/project_demo_...md` | **blocked** (ostk-first) | ostk-first | Subagent `pre-demo-prep`. After block, reran Write on a different path (also blocked), then a third Write. Never called `mcp__ostk__fs_write` and never invoked `ToolSearch`. |
| 17:23:37 | Read `app/src/lib/sidebarBus.ts` | **blocked** (ostk-first) | ostk-first | Bounce loop: Write-fails, Read-blocked, Edit-fails, Read-blocked, Edit-fails. 5 native retries across 90s with zero ostk calls. |

## Which hypothesis each row validates

All 5 rows validate **hypothesis 4** (model reasoning over enforcement): the
hook blocks correctly on every attempt, but the subagent retries with
different native-tool shapes instead of switching substrate. Zero rows
validate hypothesis 1 (whitelist too wide); `/tmp/ostk-first.log` has no
`whitelist-allowed` entries in this window. Hypothesis 3 (hook did not fire)
is refuted by the `blocked` lines. Hypothesis 2 (saa detection wrong) is
moot because `saa-must-spawn.sh` does not apply in subagent context.

## Root cause

Subagent spawn prompts (e.g. the 17:21:56 "Sync agent badge" prompt
reproduced in `agent-aefa38cd116847e11.jsonl`) literally instruct the
subagent `Use curl -sSk --connect-timeout 3 -m 5` and never mention
`mcp__ostk__*`, ToolSearch, or that hooks will block native tools. The
subagent's system prompt lists ostk as a deferred MCP, but the user turn
says "curl," so the model follows the user. When the hook blocks, the model
has no playbook to pivot.

## Fixes

1. **Patch the subagent prompt template.** Prepend a block: "ostk tools are
   available via MCP and may be deferred. If your first Bash/Read/Edit/Write
   is blocked with 'use mcp__ostk__*', immediately call
   `ToolSearch('select:mcp__ostk__shell,mcp__ostk__fs_read,mcp__ostk__edit,mcp__ostk__search')`
   and retry with the ostk equivalent. Do not bounce between native tools."
   Source the text from `standing-rules.sh` so it stays consistent.
2. **PostToolUse audit hook.** Add a hook on `PostToolUse` that records when
   a block is returned and the model's NEXT tool is also native. After 2
   consecutive native-after-block events, inject a stderr line telling the
   model to stop and load ostk. This is the only way to catch the bounce
   loop without relying on prompt discipline.
3. **Hook coverage widen.** `ostk-first.sh` matches `Bash|Read|Edit|Write|Grep|Glob`.
   Add `NotebookEdit` and `WebFetch` for completeness. Not load-bearing for
   today's violations but closes residual escapes.
4. **Leave the whitelist alone.** pytest/vitest/tsc/scripts/ whitelists were
   not exploited in this window.

## Structural verdict

The hook system is **load-bearing but incomplete**: it blocks correctly in
the main session, but does nothing to coach the subagent out of a native-tool
bounce loop, and the subagent spawn prompts actively contradict the
ostk-first rule by telling them to curl.

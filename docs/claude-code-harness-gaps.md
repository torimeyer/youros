# Claude Code harness gaps

Running catalogue of Claude Code harness behaviors that torios works around.
Each entry names the gap, the observable drift in torios when the gap is not
handled, and the current workaround. When an upstream harness fix ships the
entry stays in place with a note and a "resolved in <version>" banner so we
can delete the workaround without guessing whether it still matters.

## 1. PostToolUse for background Task calls fires at tool-call return, not subagent completion

**Needle**: →903

**What the harness does**: When the Task tool is invoked with
`run_in_background: true`, the tool call returns in under a second and the
harness emits both the `<task-notification>` reminder and the PostToolUse
hook event at that return. The underlying subagent keeps running in the
background for the full lifetime of its work (often many minutes). There is
no second hook event when the background subagent actually exits.

**What drifts if you trust the event**: Any PostToolUse hook that reads the
event as "subagent is done" will mark the agent `completed` within one
second of spawn while its transcript keeps growing. The Agents page shows 0
running subagents while the Claude Code status line correctly shows N.
Active-Sessions counters, agent-duration metrics, nudge targeting, and the
inline chat visibility all drift to zero. Users see the subagent still
producing output in the terminal and then "completed" in the app, which
reads as a bug in torios.

**Workarounds torios ships**:

- **Belt (in-payload)** in `.claude/hooks/complete-agent.sh`: parse
  `tool_input.run_in_background` and exit early when truthy. Accepts bool
  `true`, int `1`, and string `"true"`/`"1"`/`"yes"` because the flag has
  been serialized in at least two non-bool encodings across observed
  builds. Regression coverage: `tests/complete-bg-truthy-variants.sh`.
- **Suspenders 1 (per-tool-use side channel)**: at PreToolUse time,
  `register-agent.sh` writes `~/.myos/subagents/by-tool-use/<tool_use_id>.bg`
  when the parent set background. `complete-agent.sh` reads it back and
  exits early even when PostToolUse has stripped `tool_input` entirely.
  Regression coverage: `tests/complete-bg-side-channel.sh`.
- **Suspenders 2 (`last.bg` fallback)**: used when the harness omits
  `tool_use_id` from the payload (single-agent / legacy builds).
- **Transcript-idle detector** (`api/services/heartbeat_idle.py`): the
  detached heartbeat loop spawned by `register-agent.sh` polls the
  subagent JSONL mtime every 60s and POSTs `/complete` after 120s of
  silence (`TRANSCRIPT_IDLE_SECONDS`). This is what actually closes the
  row cleanly once the background subagent exits. Regression coverage:
  `tests/heartbeat-idle-complete.sh`.
- **Spawn-age ceiling** (`SPAWN_AGE_CEILING_SECONDS = 900`): flips the row
  to completed at 15 minutes regardless of transcript state. Protects
  against the "substring-in-4KB false match" that latches a probe-style
  agent's transcript onto an unrelated busy JSONL.

**Historical commits**:
- `e98d9fd` in-payload bg check in `complete-agent.sh`.
- `34edfb6` side-channel `.bg` files so the gutted-PostToolUse case works.
- `0d9e86f` Task-tool subagents without PID flip to completed on Task return.

**Upstream status**: filed in this docs entry. No public GitHub issue
tracker for Claude Code hook payloads at time of writing
(2026-04-24); when that channel exists, open an issue linking to this
entry and add a link here.

**How to know the workaround is no longer needed**: Claude Code emits a
second hook event (e.g. `AgentCompleted`, `BackgroundTaskExited`, or a
second PostToolUse with a new `tool_use_id` sentinel) when a background
Task subagent actually exits, OR the existing PostToolUse stops firing at
return for `run_in_background: true` calls. Either change means the
heartbeat-idle detector becomes the only needed close signal and the
`.bg` side-channel files can be retired.

## Notes for future entries

- One entry per distinct harness gap. Prefer a new section over
  stretching an existing one.
- Each entry must name the drift concretely (what the user sees when the
  workaround is missing) so a future reader can tell whether the entry
  still applies after a harness change.
- Link to regression tests by path so removing the workaround forces
  tests to change and the docs stay in sync.

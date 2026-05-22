# Tool cancellation pattern — root cause (2026-05-21)

## Summary

The harness message "The user doesn't want to proceed with this tool use" fires whenever a
user message arrives while a tool call is pending. This is a single harness behavior, not a
bug. The cause for Monitor (→1563) and the cause for bash are the same mechanism triggered
by different window sizes.

---

## Transcript evidence

Session: `7096d913-2c58-4fe1-9a0a-98702fc78b0f.jsonl`

Five consecutive `mcp__ostk__bash` cancellations:

| # | Bash called | User types | Cancel fires | Gap (call→user) | Gap (user→cancel) |
|---|---|---|---|---|---|
| 1 | L359 05:00:01 | L360 05:00:10 | L362 05:00:18 | 9s | 8s |
| 2 | L373 05:00:47 | L374 05:03:05 | L376 05:06:32 | 2m18s | 3m27s |
| 3 | L407 05:09:08 | L408 05:12:11 | L410 05:12:12 | 3m3s | 1s |
| 4 | L428 05:15:03 | L429 05:16:52 | L432 05:16:53 | 1m49s | 1s |
| 5 | L471 05:19:33 | L472 05:19:34 | L476 05:20:01 | 1s | 27s |

In every case: hooks passed (exit=0), bash execution started, user typed, harness cancelled.

**The "[Request interrupted by user for tool use]" message** (L363, L377, L411, L433, L477)
appears in the user turn immediately after each cancellation — confirming this is the harness
interrupt path, not a hook deny.

---

## Actual trigger

**The harness cancels any pending tool when a user message arrives**, regardless of which
tool is running. This is one mechanism with two observable windows:

### Pattern A — near-simultaneous (L471/L472)

The model generates text output + a bash tool call in the same turn. The user starts typing
their reply to the text while the model is still generating. Both arrive at the harness
within ~1 second of each other. The harness prioritises the user message and cancels bash.

Example: L466 [05:19:27] model says "A is verified working" → user types [05:19:34] →
L471 [05:19:33] bash called 6s after model text → cancel [05:20:01].

### Pattern B — long execution (L373, L407, L428)

Bash runs a long command (`scripts/run-vitest.sh`, full pytest suite). The user types a
check-in ("how's it going?", "what do I have to do for this?") minutes into execution. The
harness sees an unprocessed user message while bash is pending and cancels it.

Example: L373 [05:00:47] bash called → L374 [05:03:05] user types 2m18s later →
L376 [05:06:32] cancel fires.

---

## Why it broadened from Monitor-only to all bash

Monitor's hook window (200–500ms) is the cause for Monitor cancellations (→1563). Monitor
tools are cancelled because users type during hook execution before the Monitor even starts.

Bash tools pass their hooks quickly (~376ms avg from hook-trace.log) and begin executing.
The cancellation window for bash is the execution time itself — seconds to minutes for long
commands. The session was running full test suites (`scripts/run-vitest.sh`) which take
several minutes. The user was actively chatting. Every check-in message during a test run
hit the cancellation path.

Hook-trace confirmation — these bash entries show both PreToolUse hooks completing (exit=0)
but no PostToolUse hook, meaning execution started and was later interrupted:

```
22:00:05.391 heartbeat-and-drain.sh bash exit=0
22:00:05.730 pre-tool-guard.sh bash exit=0
[no post-tool-watch — cancelled mid-execution]

22:00:47.988 heartbeat-and-drain.sh bash exit=0
22:00:48.307 pre-tool-guard.sh bash exit=0
[no post-tool-watch — cancelled mid-execution]

22:09:08.592 heartbeat-and-drain.sh bash exit=0
22:09:08.914 pre-tool-guard.sh bash exit=0
[no post-tool-watch — cancelled mid-execution]
```

---

## Does the adhd_monitor_pairing.sh auto-arm fix address this?

**No.** That fix (commit `acabb9d`) converted a Monitor-specific deny into an auto-arm call.
It only affects Monitor. Bash cancellations happen during execution, not during hook
execution, so no hook change can prevent them. Different root cause, different fix needed.

---

## Cheapest fix (per feedback_cheapest_fix_first.md)

### Tier 1a — use spawn for long commands (immediate, no code change)

CLAUDE.md already documents this: use `mcp__ostk__spawn` for long-running processes.
`spawn` runs the command in the background and returns immediately — no pending tool call
for the harness to cancel. Poll with `interact(action="read_tail")`.

Rule to add to standing instructions:

> Any bash command expected to take more than ~10 seconds should use `mcp__ostk__spawn`
> + `mcp__ostk__interact` instead of `mcp__ostk__bash`. This prevents user messages from
> triggering cancellation mid-execution. `mcp__ostk__bash` is for fast commands only.

### Tier 1b — user awareness (immediate, no code change)

The pattern "user types check-in → tool cancels" is not obvious to the user. Add to user
memory / standing rules:

> If I am running a long command and you type a message, the harness will cancel the command.
> Either: (a) wait for the command to complete before typing, or (b) I will use spawn so your
> messages do not interrupt running operations.

### Tier 2 — hook-level (structural, small change)

No hook can prevent the harness from cancelling a pending tool when a user message arrives.
The only hook-level mitigation is to eliminate pending-tool windows by routing all long work
through `spawn`. A prompt-header note reminding the model to use spawn for slow commands
would enforce this systematically.

### Tier 3+ — harness change

Not applicable. Claude Code is closed-source. The cancellation is intentional harness
behavior (user is always able to interrupt). Tier 3 is not a real option here.

---

## Recommended action

1. **Add to standing instructions** (prompt-header.sh or STANDING_RULES): "Use
   `mcp__ostk__spawn` for any bash command that runs longer than ~10 seconds. `mcp__ostk__bash`
   is for fast commands only. Typing during a slow bash = harness cancels it."

2. **Close →1563 as "broadened"**: the Monitor root cause (hook latency window) is a special
   case of this general pattern. The fix documented there (reduce Monitor hook latency) remains
   valid for Monitor. For bash, the fix is spawn-over-bash for slow commands.

3. **No changes needed to adhd_monitor_pairing.sh** for the bash case — it's orthogonal.

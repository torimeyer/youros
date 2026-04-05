# TUI State Machine Redesign — Draft v1

**Date**: 2026-03-24
**Status**: DRAFT
**Needles**: →816, →817, →818, →820, →740
**Evidence**: 5-agent deep audit across app.rs (82 commits), agent_loop.rs (47 commits), session.rs, dispatch.rs, approval.rs

## Problem Statement

The TUI gets out of sync with the user. The agent runs ahead, old tool calls block new input, and the conversation flow is unreliable. The user describes: "the agent always ends up ahead of me."

This is not a single bug. It is a structural deficiency: **the TUI has no state machine**. It has a bag of boolean flags recomputed each tick, with no transition guards, no input gating, and no task cancellation.

## Root Causes (ranked by severity)

### RC1: Concurrent Agent Tasks (CRITICAL)

**Location**: `session.rs:85-185` (dispatch), `app.rs:800` (submit handler)

There is **no guard** preventing `dispatch()` from being called while `is_busy()` is true. When the user presses Enter during an agent turn:

1. `dispatch_command()` calls `mgr.dispatch(input)` unconditionally
2. A new user message is pushed to `messages`
3. A **second** tokio task is spawned alongside the still-running first task
4. Both tasks send events to the same `event_tx` channel — events interleave
5. Both tasks eventually write back their local `messages` copy — last writer wins

**Result**: Conversation history corruption. Tool results from one task overwritten by the other's stale copy. The agent appears to respond to the wrong message or jump ahead.

### RC2: Clone-Mutate-Writeback Message Pattern (CRITICAL)

**Location**: `session.rs:140-157`

```rust
rt.spawn(async move {
    let mut local = { messages.lock().unwrap().clone() };  // Clone ALL messages
    agent_loop::run_loop(..., &mut local, ...).await;       // Mutate local copy
    { *messages.lock().unwrap() = local; }                  // Overwrite shared state
});
```

The agent loop clones the entire message vector at dispatch, works with a local copy for seconds/minutes, then overwrites the shared state. Any messages added between clone and writeback are silently destroyed.

**This is the "gets ahead" mechanism**: the agent runs on stale messages and writes back, clobbering newer state.

### RC3: No Backpressure on Event Channel (HIGH)

**Location**: `session.rs:55` (channel creation), `app.rs:476` (80ms poll)

The event channel has 128 slots. The agent emits events (TextDelta, ToolStart, ToolResult, etc.) as fast as the API streams them. The TUI drains once per 80ms iteration. The agent can be 2-5 seconds ahead of what the user sees on screen.

Combined with 50ms render time per frame, the effective drain rate is ~7.7 events/sec. During fast streaming, the agent produces 50+ events per second. The channel absorbs the difference, but the visual lag means the user is always looking at stale output.

### RC4: Three Independent "Busy" Indicators (HIGH)

**Location**: `session.rs:116` (session.busy), `app.rs:813` (daemon_busy), `app.rs:SpinnerState`

Three separate indicators of "is work happening" that can disagree:

| Indicator | Set true | Set false | Read by |
|-----------|----------|-----------|---------|
| `session.busy` (AtomicBool) | `dispatch()` | `process_event(TurnComplete\|Error)` | `is_busy()` |
| `daemon_busy` (local bool) | After daemon dispatch | On TurnComplete from daemon | LoopMode derivation |
| `spinner.is_active()` | `spinner.start()` in dispatch | `spinner.reset()` in render | Render, watchdog |

When these disagree (e.g., session finished but spinner still active, or watchdog resets busy but task still running), the TUI enters an inconsistent state.

### RC5: No Input Gating (HIGH)

**Location**: `app.rs:510-870` (key handler), `dispatch.rs:93-351` (dispatch_command)

In `LoopMode::Busy`, the event loop still processes keyboard input. The user can type and submit commands while the agent is working. This reaches `dispatch_command()` which calls `mgr.dispatch(input)` — spawning a concurrent task (RC1).

The approval overlay is the only modal that blocks free-text input. All other busy states allow full keyboard interaction.

### RC6: Watchdog Kills Busy Flag Without Killing Task (MEDIUM)

**Location**: `app.rs:441-461`

```rust
if spinner.secs_since_last_event() > 180 {
    session.busy.store(false, Release);
    daemon_busy = false;
    spinner.reset();
}
```

The watchdog clears the busy flag but doesn't abort the tokio task. The task continues running, eventually sends TurnComplete and writes back messages. These "ghost events" leak into the next user interaction.

### RC7: Approval Polling Latency (MEDIUM)

**Location**: `app.rs:423,479,493` (three poll sites), `approval.rs:96` (300s timeout)

Approval requests are delivered via a polled global mutex, not an event-driven channel. Up to 160ms latency between the agent requesting approval and the modal appearing. During this gap, user keystrokes go to the input bar, not the modal. The agent is blocked but the user doesn't know it.

### RC8: Model Switch Doesn't Cancel Old Task (MEDIUM)

**Location**: `dispatch.rs:124-137`, `session.rs:611-614`

`:model switch` invalidates the client (`self.client = None`) and the next dispatch lazily creates a new driver. But if a task is still running on the old model, it continues. Both old and new tasks write to the same messages Arc.

## The Current "State Machine"

```
LoopMode = if daemon_busy { DaemonBusy }
           else if session.is_busy() { Busy }
           else { Idle }
```

This is recomputed from scratch every tick. There are no transition guards ("you can only go from X to Y"), no actions on transition ("entering Busy clears input"), and no stored state ("I was Busy, now I'm Idle, so drain final events").

Orthogonal to LoopMode, the TUI tracks:
- `pending_approval: Option<ApprovalRequest>` — tool approval modal
- `selector: Option<Selector>` — model/mode picker
- `got_text_this_turn: bool` — whether AI text was rendered
- `daemon_busy: bool` — separate from session busy

These are independent Options/bools with no enforcement of mutual exclusion or valid combinations.

## Proposed State Machine

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
                    v                                              │
               ┌─────────┐   user submits    ┌──────────────┐     │
               │  IDLE    │ ───────────────> │ DISPATCHING   │     │
               │          │                  │               │     │
               └─────────┘                   └───────┬───────┘     │
                    ^                                │              │
                    │                          task spawned         │
                    │                                │              │
                    │                                v              │
                    │                     ┌──────────────────┐      │
                    │                     │  AGENT_RUNNING   │──┐   │
                    │                     │                  │  │   │
                    │                     └───┬──────────┬───┘  │   │
                    │                         │          │      │   │
                    │                    tool_call  turn_done   │   │
                    │                         │          │      │   │
                    │                         v          │      │   │
                    │              ┌────────────────┐    │   tool   │
                    │              │  AWAITING_     │    │   done   │
                    │              │  APPROVAL      │    │      │   │
                    │              └──────┬─────────┘    │      │   │
                    │                     │              │      │   │
                    │              user decides          │      │   │
                    │                     │              │      │   │
                    │                     v              │      │   │
                    │              ┌────────────────┐    │      │   │
                    │              │  TOOL_RUNNING  │────┘──────┘   │
                    │              └────────────────┘               │
                    │                                               │
                    │                 TurnComplete                  │
                    └──────────────────────────────────────────────┘
```

### Transition Rules

| From | To | Trigger | Guard | Action |
|------|----|---------|-------|--------|
| IDLE | DISPATCHING | User submits | — | Push message, create task |
| DISPATCHING | AGENT_RUNNING | Task spawned | — | Start spinner |
| AGENT_RUNNING | AWAITING_APPROVAL | ToolStart + approval needed | — | Show modal, block input |
| AGENT_RUNNING | AGENT_RUNNING | ToolStart (auto-approved) | — | — |
| AGENT_RUNNING | IDLE | TurnComplete | — | Drain final events, reset spinner |
| AWAITING_APPROVAL | TOOL_RUNNING | User approves | — | Dismiss modal, unblock agent |
| AWAITING_APPROVAL | AGENT_RUNNING | User denies | — | Dismiss modal, deny result sent |
| TOOL_RUNNING | AGENT_RUNNING | Tool completes | — | — |
| ANY non-IDLE | IDLE | Watchdog fires | 180s stall | Abort task, drain, reset |
| AGENT_RUNNING | IDLE | Error | — | Drain final events, reset |

### Input Gating

| State | Allowed Input | Queued Input |
|-------|---------------|--------------|
| IDLE | All | — |
| DISPATCHING | None (transient) | — |
| AGENT_RUNNING | Ctrl+C (cancel), local verbs (:model, :copy) | Free text → outbox |
| AWAITING_APPROVAL | Y/N/A only | Free text → outbox |
| TOOL_RUNNING | Ctrl+C (cancel) | Free text → outbox |

### Key Design Decisions

1. **One task at a time**: `dispatch()` MUST refuse if state != IDLE. Queue user input in outbox. Dispatch outbox contents after TurnComplete.

2. **No clone-writeback**: The `messages` Vec is the single source of truth. Agent loop locks, pushes, unlocks for each append — no full-vector clone. This is more lock contention but eliminates the overwrite race entirely.

3. **Cancel on switch**: `:model` switch, `:reboot`, session change all abort the current task via `JoinHandle::abort()` before starting a new one.

4. **Approval via event channel**: Replace polling-based approval with an event sent through `event_tx` (new variant `CpuEvent::ApprovalRequest`). TUI receives it inline with other events — zero polling latency.

5. **Backpressure**: Reduce channel buffer from 128 to 32. Agent naturally pauses when TUI can't keep up. Visual lag drops from seconds to ~250ms.

6. **One busy indicator**: Remove `daemon_busy` bool and `SpinnerState.is_active()` as independent indicators. Derive all busy state from the FSM enum.

## Implementation Plan

### Phase 1: Stop the Bleeding (fixes RC1 + RC2)

**File**: `session.rs`

1. Add busy guard to `dispatch()`:
```rust
pub fn dispatch(&mut self, input: &str, ...) -> Result<(), String> {
    if self.busy.load(Ordering::Acquire) {
        self.outbox.push_back(input.to_string());
        return Ok(());  // Queued, not dispatched
    }
    // ... existing dispatch logic
}
```

2. Replace clone-writeback with lock-per-append:
```rust
// Instead of: clone all, mutate local, write back
// Do: pass Arc<Mutex<Vec<Message>>> to agent loop, lock per append
rt.spawn(async move {
    agent_loop::run_loop(..., messages_arc, ...).await;
    // No write-back needed — messages is the source of truth
});
```

3. After TurnComplete, check outbox and auto-dispatch:
```rust
fn process_event(&mut self, event: CpuEvent) {
    match event {
        CpuEvent::TurnComplete { .. } => {
            self.busy.store(false, Ordering::Release);
            // Auto-dispatch queued messages
            if let Some(queued) = self.outbox.pop_front() {
                self.dispatch(&queued, ...);
            }
        }
        ...
    }
}
```

**File**: `agent_loop.rs`

4. Change `run_loop` signature to take `Arc<Mutex<Vec<Message>>>` instead of `&mut Vec<Message>`. Lock-push-unlock for each message append (assistant response, tool results).

### Phase 2: FSM + Input Gating (fixes RC4 + RC5)

**File**: `app.rs`

5. Replace `LoopMode` derivation with stored `TuiState` enum:
```rust
enum TuiState {
    Idle,
    AgentRunning { task: JoinHandle<()>, session: String },
    AwaitingApproval { request: ApprovalRequest, task: JoinHandle<()> },
    ToolRunning { task: JoinHandle<()> },
}
```

6. Gate input based on state:
```rust
match &self.state {
    TuiState::Idle => handle_all_input(key),
    TuiState::AgentRunning { .. } => match key {
        Ctrl+C => abort_task(),
        _ => queue_to_outbox(key),
    },
    TuiState::AwaitingApproval { .. } => match key {
        'y' | 'n' | 'a' => handle_approval(key),
        _ => {} // swallow
    },
    ...
}
```

### Phase 3: Cancel + Backpressure (fixes RC6 + RC8)

7. Store `JoinHandle` from spawned tasks. On `:model` switch, `:reboot`, or watchdog: call `handle.abort()`.

8. Reduce channel buffer from 128 to 32.

9. Replace approval polling with `CpuEvent::ApprovalNeeded` event variant.

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Lock-per-append vs clone-writeback | More lock contention | Locks are <1μs, API calls are seconds |
| Outbox queuing | User message delayed by one turn | Show "[queued]" indicator in status bar |
| Channel backpressure (32 slots) | Agent loop blocks on send | Desired behavior — keeps TUI in sync |
| Task abort | Dropped futures, partial state | catch_unwind already handles panics; abort is cleaner |
| FSM refactor | Large diff to app.rs | Phase 2 can be done after Phase 1 proves the concept |

## Test Plan

1. **Concurrent dispatch prevention**: Type while agent is running → message queued, not dispatched
2. **Outbox drain on TurnComplete**: Queued message dispatched after agent completes
3. **Model switch cancel**: `:model opus` while agent is running → old task aborted, new model used
4. **Watchdog abort**: Stalled agent → task aborted, state cleaned, ghost events don't leak
5. **Approval flow**: Tool call → modal appears → Y/N → agent continues → no stale state
6. **Rapid input**: Type 5 messages quickly → first dispatches, rest queued, all eventually processed

## Appendix: Git Evidence of Recurring Fixes

| Bug Class | Times Fixed | Commits |
|-----------|-------------|---------|
| Event loop blocking | 4x | 3b36086, ebf54f0, 8ac7ec7, b3e172e |
| Stale/missing renders | 5x | 0c54db5, a720e31, 7472532, 812fa08, 3fefb0b |
| State sync races | 3x (9 bugs in 52f4aff alone) | 52f4aff, 98e18f1, 5bbcedf |
| Approval fragility | 3x | a14b11a, 60eff9e, 4224d45 |
| UTF-8 crashes | 3x | 0904f29, b53a62c, 1bcd3fa |

app.rs has 82 commits. The architecture produces races faster than they can be found. Each fix patches one call site. The state machine redesign addresses the structural root cause.

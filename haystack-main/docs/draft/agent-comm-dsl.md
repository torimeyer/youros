# Agent Communication DSL

> Structured format for inter-agent messages. The LLM CPU parses; dumb workers get dialect injected via policy.

## Problem

Today: orchestrator sends free text "fix BUG-004 in ~/projects/mish" and hopes 
the agent understands. Responses are free text too — no structured way to know 
if the agent completed, is blocked, or needs help. Every message requires the 
orchestrator to parse natural language.

## The DSL

### Message Types

```
task        → assign work
result      → report completion
help        → request input (from peer or orchestrator)  
status      → heartbeat / progress update
handoff     → transfer work to another agent
amendment   → spec changed, re-read before continuing
```

### Task Assignment

```json
{
  "type": "task",
  "id": "t-001",
  "from": "orchestrator",
  "to": "forge",
  "action": "fix",
  "target": {
    "bug": "BUG-004",
    "repo": "~/projects/mish",
    "spec": "/tmp/ostk-bugs/004.md",
    "files": ["src/core/pty.rs", "src/mcp/server.rs"]
  },
  "acceptance": "cargo check passes, children survive mish exit",
  "on_complete": "commit, tag v0.4.17, push, verify gh run",
  "on_blocked": { "escalate_to": "orchestrator" },
  "budget": { "model": "sonnet", "max_turns": 50, "max_cost": "$2" }
}
```

### Result

```json
{
  "type": "result",
  "id": "t-001",
  "from": "forge",
  "status": "complete",
  "artifacts": {
    "files_changed": ["src/core/pty.rs:446", "src/mcp/server.rs:337"],
    "commit": "e110f73",
    "tag": "v0.4.17",
    "tests": "1666 passed, 0 failed"
  },
  "summary": "removed close_all on EOF, added detach_on_drop",
  "cost": "$0.38",
  "turns": 12
}
```

### Help Request

```json
{
  "type": "help",
  "id": "h-001",
  "from": "forge",
  "context": "t-001 (BUG-004 fix)",
  "question": "Should session.kill() also skip dedicated PTY processes?",
  "options": [
    { "id": "a", "label": "skip all dedicated" },
    { "id": "b", "label": "skip only detach_on_drop=true" },
    { "id": "c", "label": "kill all" }
  ],
  "urgency": "blocking",
  "auto_timeout": "5m → default to option b"
}
```

### Status Heartbeat

```json
{
  "type": "status",
  "from": "forge",
  "task": "t-001",
  "progress": "reading process/table.rs, found 3 kill paths",
  "context_pct": 12,
  "tokens_used": 8400,
  "eta": "~5 min"
}
```

### Handoff

```json
{
  "type": "handoff",
  "from": "forge",
  "to": "tester",
  "task": "t-001",
  "state": "fix applied, needs test",
  "artifacts": ["src/core/pty.rs:446"],
  "context_snapshot": ".ostk/wip/forge-t001-snapshot.md"
}
```

### Spec Amendment

```json
{
  "type": "amendment",
  "from": "orchestrator",
  "affects": ["t-001", "t-003"],
  "severity": "breaking",
  "spec": "docs/spec/shared-mish.md#daemon-mode",
  "message": "daemon mode replaced by socket proxy — re-read spec",
  "action_required": "acknowledge before next write"
}
```

## Dialect Injection

Dumb workers don't need to know the full DSL. The shim injects a 
minimal dialect via policy annotation on their first tool call:

```
[policy] COMMUNICATION FORMAT: When you complete a task, output a JSON 
block with: {"type":"result","status":"complete|blocked","summary":"..."}
When you need help: {"type":"help","question":"...","urgency":"blocking|info"}
```

The intelligence layer (ostk CPU) parses these from the agent's 
output stream, routes them, and generates structured responses.

Smarter agents (Opus orchestrators) get the full DSL reference via 
`ostk --agents`. Worker agents (Sonnet/Haiku) get the minimal 
dialect via policy injection. Same protocol, different verbosity.

## Transport

Messages flow through the audit log (append-only JSONL). The shim 
intercepts output matching the DSL format and routes it. Non-DSL 
output (normal tool calls, code, etc.) passes through unchanged.

This means the DSL is **opt-in at the output level**. An agent that 
doesn't know the DSL just produces normal output. An agent that does 
can structure its responses for automated routing.

## Acceptance Criteria

- [ ] Task assignment format parseable by shim
- [ ] Result format extractable from agent output stream
- [ ] Help requests route to inbox (human) or peer (agent)
- [ ] Dialect injection via policy works on first tool call
- [ ] Status heartbeats update fleet panel in TUI
- [ ] Amendments trigger acknowledgment gate
- [ ] Non-DSL agents work normally (backward compatible)

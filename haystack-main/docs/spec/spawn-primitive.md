---
status: spec
author: orchestrator
created: 2026-03-07
conversation: session 26dba0bd, exchanges ~450-470
implements: []
---

# Spawn Primitive — P003

> The proven pattern for launching isolated Claude instances from ostk.
> Discovered 2026-03-07 after 150+ agent-minutes of failed PTY-based approaches.

## The Primitive

```bash
cat <input> | claude -p \
    --model <model> \
    --team-name ostk \
    --agent-id <unique-id> \
    --agent-name <display-name> \
    --permission-mode bypassPermissions \
    --max-budget-usd <budget> \
    --output-format json \
    "<prompt>"
```

### Key flags

| Flag | Purpose |
|------|---------|
| `-p` / `--print` | Non-interactive, pipe-friendly. No TUI. |
| `--model haiku\|sonnet\|opus` | Model isolation. No inherited settings. |
| `--permission-mode bypassPermissions` | No interactive prompts. |
| `--max-budget-usd N` | Hard budget cap per invocation. |
| `--output-format json` | Structured output for programmatic consumption. |
| `--no-session-persistence` | Don't save session to disk. |
| `--mcp-config <path>` | Control which MCP servers are available. |
| `--team-name ostk` | **CRITICAL**: Official nesting bypass. Claude Code checks `CLAUDECODE=1` and blocks nested sessions UNLESS `--team-name` + `--agent-id` + `--agent-name` are provided (the agent teams feature). No env var hacking needed. |
| `--agent-id <id>` | Required with --team-name. Unique worker identifier. |
| `--agent-name <name>` | Required with --team-name. Display name for the worker. |

### Why this works

- **No PTY** — no paste mode bugs (BUG-001), no backpressure, no SIGHUP
- **No context inheritance** — fresh instance, no 1M context flag from parent
- **No babysitting** — pipe in, wait for stdout, done
- **Model isolation** — haiku for health checks ($0.001), sonnet for tasks, opus for design
- **Budget isolation** — hard cap prevents runaway costs
- **Structured output** — JSON for programmatic routing, text for human reading

### Proven performance

| Test | Model | Time | Result |
|------|-------|------|--------|
| "What is the capital of France?" | haiku | 10s | "Paris" |
| BUG-009 analysis (piped .md file) | sonnet | 11s | Correct root cause + fix proposal |
| 3x parallel subagent research | default | 2-3 min | All three returned comprehensive reports |

### Anti-pattern: Dedicated PTY agents

| Metric | PTY agents (today) | Print mode (proven) |
|--------|-------------------|---------------------|
| Time to first output | 5-35 min | 10-30 sec |
| Shipped code | 0 lines | N/A (orchestrator applies) |
| Agent-minutes wasted | 150+ | 0 |
| Paste mode failures | 8+ | 0 |
| Context inheritance | Yes (1M + high effort) | No |
| Babysitting needed | Constant (poll every 30s) | None (wait for stdout) |

## Usage in ostk

### Worker bees (simple tasks)

```bash
# Fix a bug
cat /tmp/ostk-bugs/009.md | env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p --model sonnet --max-budget-usd 2 \
  "Read this bug report. Output a JSON object with fields: root_cause, fix_description, files_to_modify, estimated_lines."

# Health check
echo "$AGENT_AUDIT_TAIL" | env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p --model haiku --max-budget-usd 0.01 \
  "Is this agent productive or stuck? Reply JSON: {status, evidence, recommendation}"

# Spec review
cat docs/draft/pull-model.md | env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p --model sonnet --max-budget-usd 1 \
  "Review this spec. Does it contradict any of these active specs: [list]. Reply JSON."
```

### Orchestrator (multi-turn, design conversations)

For multi-turn design work, use the existing dedicated PTY pattern with `--model opus`.
Only for conversations where persistent state matters.

## Relationship to Agentfile

The Agentfile `FROM` directive maps to `--model`:
```dockerfile
FROM claude-sonnet-4-6    → --model sonnet
FROM claude-haiku-4-5     → --model haiku
FROM claude-opus-4-6      → --model opus
```

The `LIMIT` directives map to flags:
```dockerfile
LIMIT budget $2           → --max-budget-usd 2
LIMIT context 200k        → (not yet available as CLI flag)
```

## Acceptance Criteria

- [ ] `env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT` prevents nesting error
- [ ] `--model` flag isolates model selection from parent
- [ ] Piped input reaches the agent as context
- [ ] `--output-format json` returns parseable JSON
- [ ] `--max-budget-usd` enforces hard cap
- [ ] Worker completes in <30s for simple tasks
- [ ] No PTY, no paste mode, no babysitting required

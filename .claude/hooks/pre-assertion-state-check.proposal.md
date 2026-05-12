# Hook Proposal: pre-assertion-state-check

**Status:** Proposal only. Do not enable without review.

## Problem

The parent pclaude session repeatedly makes wrong assertions about agent death, session identity, and repo state based on fast/partial signals (header absence, kernel-elided git output, stale /api/agents row fields). Existing rules fire too late — after the assertion is made, not before.

## Design

**Type:** PreToolUse on the text output tool (or a UserPromptSubmit variant that inspects the reply being drafted)

**Trigger:** The outgoing reply contains any of these phrases and the turn has NOT run at least one of the corroborating commands:

Assertion vocabulary (trigger):
- "agent died", "agent is dead", "agent stalled", "went silent", "no progress", "agent terminated"
- "is wclaude", "is a peer session", "peer claude", "another session"
- "git rewound", "main moved back", "cherry-pick reverted", "HEAD is at"

Required corroborating receipts (at least one must appear in the same turn's tool call outputs):
- `git log --all` output
- `curl /api/agents` output
- `ps aux` or `pgrep` output
- `git log --oneline -10 --no-pager` output

**Action on trigger:** Emit a warning to stderr:

```
[pre-assertion-state-check] State-change assertion detected without a corroborating receipt.
  Assertion phrase matched: "<phrase>"
  Required: git log --all, curl /api/agents, ps aux, or git log --no-pager in this turn.
  See: feedback_false_alarm_meta_pattern.md
```

Do NOT block the turn. Emit warning only (the hook surfaces friction, not a hard gate — hard gates cause workarounds).

## Implementation sketch

```bash
#!/usr/bin/env bash
# .claude/hooks/pre-assertion-state-check.sh
# Fires on PreToolUse when tool is the text/reply output

REPLY="${CLAUDE_TOOL_INPUT:-}"  # injected by harness

# Check for state-change vocabulary
if echo "$REPLY" | grep -qiE \
  'agent (died|is dead|stalled|went silent|no progress|terminated)|is wclaude|peer (session|claude)|git rewound|main moved|cherry.pick reverted|HEAD is at [0-9a-f]{7}'; then

  # Check if any receipt command was run this turn
  TURN_TOOL_CALLS="${CLAUDE_TURN_TOOL_CALLS:-}"  # harness-provided
  if ! echo "$TURN_TOOL_CALLS" | grep -qE \
    'git log --all|curl /api/agents|ps aux|pgrep|git log.*--no-pager'; then
    echo "[pre-assertion-state-check] WARNING: state-change assertion without a corroborating receipt. See feedback_false_alarm_meta_pattern.md" >&2
  fi
fi

exit 0  # never block
```

## Why not a hard block

Hard blocks cause workarounds ("I'll just rephrase it"). A warning surfaces the pattern without interrupting flow. The goal is to build habit, not to catch every case.

## Related rules

- [[false-alarm-meta-pattern]]
- [[feedback_never_declare_agent_dead_without_full_probe]]
- [[feedback_verify_before_asserting]]
- [[feedback_receipts_for_every_done_claim]]

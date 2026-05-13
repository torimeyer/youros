#!/usr/bin/env bash
# Rule: standing_rules_blocks / zero_byte_transcript_check
# Replaces: ZERO-BYTE block from standing-rules.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_standing_rules_zero_byte_check() {
  # Note: the \{ below is intentional — prevents shell from interpreting {} as a
  # variable expansion inside the heredoc. The backslash is rendered literally
  # in the model's context.
  cat <<'EOF'

ZERO-BYTE TRANSCRIPT CLAIM CHECK (run before sending any reply):
Trigger: your reply names a cause for a 0-byte transcript OR cites a memory entry to explain a 0-byte/silent subagent failure.
Before stating any cause from memory, you MUST show ALL of the following in the same reply:
  - What \{ "loggedIn": true, ... } returned THIS turn (run the command now, do not quote session-start context)
  - The value of completed_at and spawned_at for the failing agent row, from a live /api/agents call THIS turn
  - If citing a memory entry: a verbatim line from that file read THIS turn (path:line format)
Quota cap (feedback_quota_silent_fail.md) is only valid when auth status shows a Max subscription, NOT apiKeySource: ANTHROPIC_API_KEY. Missing these = regenerate. See feedback_zero_byte_transcript_diagnose_order.md for the correct ordered checklist.
EOF
  log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "zero_byte_transcript_check block emitted"
  return 0
}

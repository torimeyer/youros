#!/usr/bin/env bash
# Rule: standing_rules_blocks / receipts_check
# Replaces: RECEIPTS CHECK block from standing-rules.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_standing_rules_receipts_check() {
  cat <<'EOF'

RECEIPTS CHECK (run before sending any reply):
Trigger words: "done", "fixed", "landed", "committed", "passing", "shipped", "complete", "resolved", or any relay of a subagent saying the same.
If your reply uses any trigger word, it MUST include at least one of:
  - A commit hash + message from `git log` run this turn
  - Verbatim command output (pytest summary, curl response, grep hit, tsc exit line) from a tool call this turn
  - A testid / aria-label verified via `grep` this turn
  - A direct quote from a file read this turn, with path
If none of the above are in the reply, regenerate. Relaying a subagent's "tests pass" without re-running the tests yourself is banned. See feedback_receipts_for_every_done_claim.md.
EOF
  log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "receipts_check block emitted"
  return 0
}

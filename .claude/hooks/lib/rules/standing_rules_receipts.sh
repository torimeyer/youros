#!/usr/bin/env bash
# Rule: standing_rules_blocks / receipts_check
# Replaces: RECEIPTS CHECK block from standing-rules.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_standing_rules_receipts_check() {
  cat <<'EOF'
RECEIPTS: done/fixed/landed/passing/shipped needs proof from THIS turn (commit hash, verbatim output, or file quote). Never relay a subagent's pass claim unchecked.
EOF
  log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "receipts_check block emitted"
  return 0
}

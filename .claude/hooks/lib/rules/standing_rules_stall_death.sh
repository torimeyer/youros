#!/usr/bin/env bash
# Rule: standing_rules_blocks / stall_death_check
# Replaces: STALL/DEATH block from standing-rules.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_standing_rules_stall_death_check() {
  cat <<'EOF'

STALL/DEATH ASSERTION CHECK (run before sending any reply):
Trigger: your reply uses any of "stalled", "stalling", "died", "dead", "dropped", "hard-stalled", "abandoned", "no progress", "agent went quiet" alongside an agent name.
If your reply contains any trigger word, it MUST include evidence from at least 3 of these 5 checks:
  - ps -p <pid> output (state, elapsed) — process alive/dead is ground truth
  - git log --all main..<branch> --oneline — even one commit = not dead
  - git status of the worktree — dirty files = active work in progress
  - transcript tail via /api/agents/<name>/transcript — recent bytes = actively writing
  - /api/agents row: status + transcript_bytes from two reads, not one snapshot
Fast signals (bytes flat for N ticks, current_step flat) are hypotheses, not conclusions. If fewer than 3 checks are present in the reply, regenerate. See feedback_false_alarm_meta_pattern.md.
EOF
  log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "stall_death_check block emitted"
  return 0
}

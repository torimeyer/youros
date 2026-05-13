#!/usr/bin/env bash
# Rule: humanfile_render
# Replaces: HUMANFILE section of standing-rules.sh (dynamic read instead of static heredoc)
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_humanfile_render_check() {
  local hf_path
  hf_path=$(rule_param "humanfile_render.path" "~/.ostk/HUMANFILE")
  # Expand ~ manually since rule_param returns a string
  hf_path="${hf_path/#\~/$HOME}"

  if [ ! -f "$hf_path" ]; then
    log_rule_fire "humanfile_render" "UserPromptSubmit" "allow" "HUMANFILE not found at $hf_path"
    return 0
  fi

  echo ""
  echo "ACTIVE HUMANFILE RULES (read every turn):"
  echo ""
  cat "$hf_path"

  log_rule_fire "humanfile_render" "UserPromptSubmit" "allow" "rendered $hf_path"
  return 0
}

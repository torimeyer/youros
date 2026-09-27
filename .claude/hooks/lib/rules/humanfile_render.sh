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

  # Skip comments, blank lines, SIGN and heredoc blocks (e.g. AVAILABLE <<MODELS):
  # they are for ostk, not the model, and re-enter context on every prompt.
  awk '/^#/ || /^[[:space:]]*$/ || /^SIGN / {next}
       /<</ {split($0, a, "<<"); endtag=a[2]; next}
       endtag != "" {if ($0 == endtag) endtag=""; next}
       {print}' "$hf_path"

  log_rule_fire "humanfile_render" "UserPromptSubmit" "allow" "rendered $hf_path"
  return 0
}

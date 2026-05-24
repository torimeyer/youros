#!/usr/bin/env bash
# Rule: monitor_misuse_guard
# Replaces: monitor-misuse-guard.sh
# Called from: pre-tool-guard.sh on Monitor
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.

_monitor_misuse_guard_check() {
  local tool="${1:-Monitor}" cmd="$2"

  if [ -z "$cmd" ]; then
    log_rule_fire "monitor_misuse_guard" "$tool" "allow" "no command to check"
    return 0
  fi

  local BLOCK_REASON="Monitor is for streaming events, not file reads. Use mcp__ostk__read for files (or native Read if mcp__ostk__read is not loaded; load it via ToolSearch(query='select:mcp__ostk__read')). Monitor commands should use tail -f, inotifywait -m, or while-true polling loops, not one-shot file I/O."

  # ALLOW streaming patterns first
  if echo "$cmd" | grep -qE 'tail\s+.*-[a-zA-Z]*f|inotifywait\s+.*-m|while\s+(true|:|\[)'; then
    log_rule_fire "monitor_misuse_guard" "$tool" "allow" "streaming pattern detected"
    return 0
  fi

  # BLOCK: python3 with open/read file I/O
  if echo "$cmd" | grep -qi "python3"; then
    if echo "$cmd" | grep -q "open\s*("; then
      if echo "$cmd" | grep -qE '\.(read|readlines)\s*\(\)|open\s*\([^)]*['"'"'"]\s*w\s*['"'"'"]'; then
        log_rule_fire "monitor_misuse_guard" "$tool" "block" "python3 file read in Monitor"
        deny "$BLOCK_REASON"
      fi
    fi
  fi

  # BLOCK: one-shot cat
  if echo "$cmd" | grep -qE '^\s*cat\s+\S'; then
    log_rule_fire "monitor_misuse_guard" "$tool" "block" "one-shot cat in Monitor"
    deny "$BLOCK_REASON"
  fi

  # BLOCK: one-shot head
  if echo "$cmd" | grep -qE '^\s*head\s+'; then
    log_rule_fire "monitor_misuse_guard" "$tool" "block" "one-shot head in Monitor"
    deny "$BLOCK_REASON"
  fi

  # BLOCK: bare tail without -f
  if echo "$cmd" | grep -qE '^\s*tail\s+'; then
    if ! echo "$cmd" | grep -qE '-[a-zA-Z]*f\s|--follow'; then
      log_rule_fire "monitor_misuse_guard" "$tool" "block" "bare tail without -f in Monitor"
      deny "$BLOCK_REASON"
    fi
  fi

  # BLOCK: wc -l (one-shot file size)
  if echo "$cmd" | grep -qE '^\s*wc\s+-l'; then
    log_rule_fire "monitor_misuse_guard" "$tool" "block" "wc -l in Monitor"
    deny "$BLOCK_REASON"
  fi

  log_rule_fire "monitor_misuse_guard" "$tool" "allow" "command is streaming-safe"
  return 0
}

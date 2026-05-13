#!/usr/bin/env bash
# Rule-fire logger — source this file from any enforcement hook.
#
# Provides one function:
#   log_rule_fire <rule_key> <tool_name> <decision: allow|block> <reason>
#
# Appends one JSON line to ~/.claude/logs/rule-fires.jsonl.
# Rotates the log when it exceeds 10MB: renames to .1, starts fresh.
# One generation kept.

log_rule_fire() {
  local rule_key="$1" tool_name="$2" decision="$3" reason="$4"
  local log_dir log_file log_bak max_bytes size
  log_dir="${HOME}/.claude/logs"
  log_file="${log_dir}/rule-fires.jsonl"
  log_bak="${log_file}.1"
  max_bytes=10485760  # 10MB

  mkdir -p "$log_dir" 2>/dev/null || return 0

  # Rotation check
  if [ -f "$log_file" ]; then
    size=$(python3 -c "import os; print(os.path.getsize('${log_file}'))" 2>/dev/null || echo 0)
    if [ "${size:-0}" -gt "$max_bytes" ]; then
      mv "$log_file" "$log_bak" 2>/dev/null || true
    fi
  fi

  python3 - "$rule_key" "$tool_name" "$decision" "$reason" "$log_file" <<'PYEOF' 2>/dev/null
import json, os, sys, hashlib, datetime

rule_key  = sys.argv[1]
tool_name = sys.argv[2]
decision  = sys.argv[3]
reason    = sys.argv[4]
log_file  = sys.argv[5]

session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")

# Digest of first arg in the environment (best-effort, not security-critical)
tool_input = os.environ.get("CLAUDE_TOOL_INPUT_ARG0", "")
if tool_input:
    digest = "sha256:" + hashlib.sha256(tool_input.encode()).hexdigest()[:16]
else:
    digest = ""

entry = {
    "ts":               datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "rule":             rule_key,
    "tool":             tool_name,
    "decision":         decision,
    "reason":           reason,
    "session_id":       session_id,
    "tool_args_digest": digest,
}

with open(log_file, "a") as f:
    f.write(json.dumps(entry) + "\n")
PYEOF
  return 0
}

#!/usr/bin/env bash
# Rule: keep_going_pending_tasks
# Replaces: keep-going-on-pending-tasks.sh (UserPromptSubmit)
# Called from: prompt-header.sh
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.
# Args: $1=user_msg (lowercased)

_keep_going_pending_tasks_check() {
  local user_msg="${1:-}"
  local BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
  local SCRIPT_DIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../}"

  # Gate on keep-going phrases
  local MSG_LOWER
  MSG_LOWER=$(printf '%s' "$user_msg" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//')
  case "$MSG_LOWER" in
    "keep going"*|"continue"*|"next"*|"what's next"*|"what next"*|"whats next"*|"go"|"go "*)
      : ;;
    *)
      log_rule_fire "keep_going_pending_tasks" "UserPromptSubmit" "allow" "no keep-going phrase"
      return 0
      ;;
  esac

  local TASKS_JSON AGENTS_JSON
  if [ "${MYOS_TASKS_FIXTURE+x}" = "x" ]; then
    TASKS_JSON="$MYOS_TASKS_FIXTURE"
  else
    TASKS_JSON="$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 5 \
      "${BACKEND_URL}/api/tasks?limit=30" 2>/dev/null)"
  fi

  if [ "${MYOS_AGENTS_FIXTURE+x}" = "x" ]; then
    AGENTS_JSON="$MYOS_AGENTS_FIXTURE"
  else
    AGENTS_JSON="$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 8 \
      "${BACKEND_URL}/api/agents" 2>/dev/null)"
  fi

  [ -z "$TASKS_JSON" ] && { log_rule_fire "keep_going_pending_tasks" "UserPromptSubmit" "allow" "no tasks data"; return 0; }
  [ -z "$AGENTS_JSON" ] && { log_rule_fire "keep_going_pending_tasks" "UserPromptSubmit" "allow" "no agents data"; return 0; }

  local TMP_T TMP_A
  TMP_T="$(mktemp -t kgo-tasks-XXXXXX)"
  TMP_A="$(mktemp -t kgo-agents-XXXXXX)"
  trap 'rm -f "$TMP_T" "$TMP_A"' RETURN
  printf '%s' "$TASKS_JSON" > "$TMP_T"
  printf '%s' "$AGENTS_JSON" > "$TMP_A"

  # Find keep-going-check.py relative to lib/
  local KEEP_GOING_PY
  KEEP_GOING_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../keep-going-check.py"
  if [ ! -f "$KEEP_GOING_PY" ]; then
    log_rule_fire "keep_going_pending_tasks" "UserPromptSubmit" "allow" "keep-going-check.py not found"
    return 0
  fi

  KGO_TASKS_FILE="$TMP_T" KGO_AGENTS_FILE="$TMP_A" python3 "$KEEP_GOING_PY"

  log_rule_fire "keep_going_pending_tasks" "UserPromptSubmit" "allow" "keep-going check complete"
  return 0
}

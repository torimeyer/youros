#!/usr/bin/env bash
# Rule: task_hygiene
# Replaces: task-hygiene.sh
# Called from: pre-tool-guard.sh on mcp__ostk__needle* / mcp__ostk__add*
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.
# Args: $1=tool $2=title $3=priority $4=ac

_task_hygiene_check() {
  local tool="$1" TITLE="$2" PRIORITY="$3" AC="$4"
  local min_len
  min_len=$(rule_param "task_hygiene.min_title_length" "5")

  # ---- 1. Garbage title check ----
  if [ -z "$TITLE" ]; then
    log_rule_fire "task_hygiene" "$tool" "block" "empty title"
    deny "title is empty. Every task needs a clear, specific title that describes the work."
  fi

  local TITLE_LEN=${#TITLE}
  if [ "$TITLE_LEN" -lt "${min_len:-5}" ]; then
    log_rule_fire "task_hygiene" "$tool" "block" "title too short (${TITLE_LEN} chars)"
    deny "title '$TITLE' is too short (fewer than ${min_len:-5} characters). Write a title that actually describes the work."
  fi

  local TITLE_LOWER
  TITLE_LOWER=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]')

  local garbage_prefixes
  garbage_prefixes=$(rule_param "task_hygiene.garbage_prefixes" '["session in","untitled","fix it","todo","test"]')

  for pattern in "session in" "untitled" "fix it" "todo" "test"; do
    if echo "$TITLE_LOWER" | grep -qi "^${pattern}"; then
      log_rule_fire "task_hygiene" "$tool" "block" "garbage prefix in title"
      deny "title '$TITLE' looks like a placeholder or auto-generated name. Write a specific title that describes what needs to be done."
    fi
  done

  local WORD_COUNT
  WORD_COUNT=$(echo "$TITLE" | wc -w | tr -d ' ')
  if [ "$WORD_COUNT" -le 1 ]; then
    for word in "sort" "fix" "update" "test" "todo" "done" "work" "task" "thing" "stuff" "misc" "other" "temp" "wip"; do
      if [ "$TITLE_LOWER" = "$word" ]; then
        log_rule_fire "task_hygiene" "$tool" "block" "single generic word title: $word"
        deny "title '$TITLE' is a single generic word with no real meaning. Write a title that explains what specifically needs to be done."
      fi
    done
  fi

  # ---- 2. Lowercase priority check ----
  if [ -n "$PRIORITY" ]; then
    case "$PRIORITY" in
      p0|p1|p2|p3)
        local PRIORITY_UP
        PRIORITY_UP=$(echo "$PRIORITY" | tr '[:lower:]' '[:upper:]')
        log_rule_fire "task_hygiene" "$tool" "block" "lowercase priority $PRIORITY"
        deny "priority '$PRIORITY' must be uppercase. Use ${PRIORITY_UP} instead."
        ;;
    esac
  fi

  # ---- 3. Missing AC on P0 / P1 ----
  if [ "$PRIORITY" = "P0" ] || [ "$PRIORITY" = "P1" ]; then
    if [ -z "$AC" ]; then
      log_rule_fire "task_hygiene" "$tool" "block" "P0/P1 without AC"
      deny "P0 and P1 tasks require acceptance criteria (ac field). Add a clear description of what 'done' looks like before filing."
    fi
  fi

  log_rule_fire "task_hygiene" "$tool" "allow" "title and priority valid"
  return 0
}

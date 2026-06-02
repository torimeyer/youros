#!/usr/bin/env bash
# Rule: saa_must_spawn
# Replaces: saa-must-spawn.sh (PreToolUse Bash|Read|Edit|Write|Grep|Glob)
# Called from: pre-tool-guard.sh
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.
# Args: $1=tool $2=cmd

_saa_must_spawn_check() {
  local tool="$1" cmd="$2"

  # Subagent skip: worktree contexts should not enforce saa on themselves.
  case "${CLAUDE_PROJECT_DIR:-}" in
    */.claude/worktrees/*) log_rule_fire "saa_must_spawn" "$tool" "allow" "subagent worktree context"; return 0 ;;
  esac

  # Agent/Task calls are the desired action — always allow.
  case "$tool" in
    Agent|Task) log_rule_fire "saa_must_spawn" "$tool" "allow" "tool is Agent/Task"; return 0 ;;
  esac

  # Locate transcript
  local PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  [ -z "$PROJ_DIR" ] && { log_rule_fire "saa_must_spawn" "$tool" "allow" "no proj dir"; return 0; }
  local SLUG
  SLUG=$(echo "$PROJ_DIR" | sed 's|/|-|g')
  local LAST
  LAST=$(ls -t "$HOME/.claude/projects/$SLUG/"*.jsonl 2>/dev/null | head -1)
  [ -z "$LAST" ] && { log_rule_fire "saa_must_spawn" "$tool" "allow" "no transcript found"; return 0; }

  local MSG
  MSG=$(grep '"type":"user"' "$LAST" | tail -1 | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
c=d.get('message',{}).get('content')
print((c if isinstance(c,str) else '').lower())
" 2>/dev/null)

  local VERB=""
  case "$MSG" in
    "saa "*)      VERB="saa" ;;
    "diagnose "*) VERB="diagnose" ;;
    "fix "*)      VERB="fix" ;;
    *)            log_rule_fire "saa_must_spawn" "$tool" "allow" "no trigger word in last message"; return 0 ;;
  esac

  # Allow if Agent/Task already appeared in this turn's transcript.
  local AGENT_FIRED
  AGENT_FIRED=$(LAST_JSONL="$LAST" python3 - <<'PY' 2>/dev/null
import os, json, sys
path = os.environ.get("LAST_JSONL", "")
try:
    with open(path) as f:
        lines = f.readlines()
except Exception:
    sys.exit(0)
last_user_pos = -1
for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get("type") == "user":
            c = d.get("message", {}).get("content", "")
            if isinstance(c, str):
                last_user_pos = i
    except Exception:
        continue
if last_user_pos < 0:
    sys.exit(0)
for line in lines[last_user_pos + 1:]:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get("type") != "assistant":
            continue
        content = d.get("message", {}).get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    if item.get("name") in ("Agent", "Task"):
                        print("yes")
                        sys.exit(0)
    except Exception:
        continue
PY
  )
  [ "$AGENT_FIRED" = "yes" ] && { log_rule_fire "saa_must_spawn" "$tool" "allow" "Agent/Task already fired this turn"; return 0; }

  # Allow ancillary commands
  local ancillary_prefixes
  ancillary_prefixes=$(rule_param "saa_must_spawn.ancillary_cmd_prefixes" '["scripts/dev-","scripts/run-vitest.sh","scripts/e2e_smoke.sh","ostk work ","git status","git log","git diff","git show","curl --connect-timeout"]')
  case "$cmd" in
    *scripts/dev-*|*scripts/run-vitest.sh*|*scripts/e2e_smoke.sh*) log_rule_fire "saa_must_spawn" "$tool" "allow" "ancillary command"; return 0 ;;
    *"ostk work "*) log_rule_fire "saa_must_spawn" "$tool" "allow" "ostk work ancillary"; return 0 ;;
    # Read-only status probes — safe to run even when saa_must_spawn is active
    *"git status"*|*"git log"*|*"git diff"*|*"git show"*) log_rule_fire "saa_must_spawn" "$tool" "allow" "read-only git probe"; return 0 ;;
    *"curl --connect-timeout"*) log_rule_fire "saa_must_spawn" "$tool" "allow" "curl with timeout probe"; return 0 ;;
  esac

  local reason="the user said '$VERB'. Rule: spawn a subagent via Agent, no inline work."
  log_rule_fire "saa_must_spawn" "$tool" "block" "trigger word '$VERB' detected, no Agent fired yet"
  advise "$reason"
}

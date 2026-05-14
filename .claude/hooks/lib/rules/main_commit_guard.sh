#!/usr/bin/env bash
# Rule: main_commit_guard
# Replaces: (new — →1348)
# Called from: pre-tool-guard.sh (Bash|mcp__ostk__bash section)
# Purpose: Prevent subagents running inside a worktree from accidentally
#          committing directly to `main`.
#
# Background: →1346 subagent committed c0a22a2 directly to main instead of
#             its worktree branch `worktree-agent-diagnose-1346-...`.
#             isolation_bridge.sh contains no symlink claim — the agent
#             hallucinated the justification. The correct fix is this guard.
#
# Trigger conditions (all must be true):
#   1. The Bash command contains `git commit`
#   2. We are running as a worktree agent (CLAUDE_PROJECT_DIR contains
#      /.claude/worktrees/agent-) OR the effective cwd is inside a
#      .claude/worktrees/agent-* path
#   3. The git branch at the effective cwd resolves to `main`
#
# The effective cwd is (in priority order):
#   - cwd extracted from the tool input JSON ($3 arg or INPUT_JSON env)
#   - -C <path> extracted from the git command itself
#   - HOOK_CWD env var (used in tests / shell-level callers)
#   - PWD (hook process cwd)
#
# Non-blocking on non-commit commands.
# Exits via deny() (exit 2) when all conditions are met.

_main_commit_guard_check() {
  local tool="${1:-Bash}" cmd="${2:-}" input_json="${3:-${INPUT_JSON:-}}"

  # Fast path: skip if command doesn't look like a git commit
  if ! printf '%s' "$cmd" | grep -qE '(^|[[:space:]])git(\s+-C\s+\S+)?\s+commit(\s|$)'; then
    return 0
  fi

  # 1. Try to extract cwd from the tool input JSON
  local cwd_from_json=""
  if [ -n "$input_json" ]; then
    cwd_from_json=$(printf '%s' "$input_json" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read(), strict=False)
    ti = d.get('tool_input', {}) or {}
    v = (ti.get('cwd') or '').strip()
    if v:
        print(v)
except Exception:
    pass
" 2>/dev/null)
  fi

  # 2. Try -C <path> from command itself
  local c_arg=""
  c_arg=$(printf '%s' "$cmd" | python3 -c "
import sys, re
m = re.search(r'git\s+-C\s+(\S+)', sys.stdin.read())
if m:
    print(m.group(1).rstrip(';').rstrip('\"').rstrip(\"'\"))
" 2>/dev/null)

  # Determine the path to check for the git branch.
  # Priority: explicit cwd from JSON > -C arg in command > CLAUDE_PROJECT_DIR
  # (when it IS the worktree) > HOOK_CWD > PWD.
  local check_path=""
  if [ -n "$cwd_from_json" ] && [ -d "$cwd_from_json" ]; then
    check_path="$cwd_from_json"
  elif [ -n "$c_arg" ] && [ -d "$c_arg" ]; then
    check_path="$c_arg"
  elif printf '%s' "${CLAUDE_PROJECT_DIR:-}" | grep -qE '\.claude/worktrees/agent-'; then
    # CLAUDE_PROJECT_DIR is the worktree — check the branch there
    check_path="${CLAUDE_PROJECT_DIR}"
  else
    check_path="${HOOK_CWD:-${PWD:-}}"
  fi

  # Determine if we are in a worktree-agent context:
  local in_worktree_agent=0
  if printf '%s' "${CLAUDE_PROJECT_DIR:-}" | grep -qE '\.claude/worktrees/agent-'; then
    in_worktree_agent=1
  fi
  if printf '%s' "$check_path" | grep -qE '\.claude/worktrees/agent-'; then
    in_worktree_agent=1
  fi

  if [ "$in_worktree_agent" -eq 0 ]; then
    return 0
  fi

  # Resolve absolute path and check branch
  if [ -n "$check_path" ] && [ -d "$check_path" ]; then
    check_path=$(cd "$check_path" 2>/dev/null && pwd || echo "$check_path")
  fi

  local branch=""
  branch=$(git -C "$check_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

  if [ "$branch" = "main" ]; then
    log_rule_fire "main_commit_guard" "$tool" "block" \
      "worktree agent tried to commit to main (path=${check_path})"
    deny "main_commit_guard: running as a worktree agent but the current git branch is 'main' at (${check_path}). Worktree agents MUST commit to their own branch, not main. Verify with: git -C ${check_path} rev-parse --abbrev-ref HEAD"
  fi

  log_rule_fire "main_commit_guard" "$tool" "allow" "branch=${branch:-unknown} is not main"
  return 0
}

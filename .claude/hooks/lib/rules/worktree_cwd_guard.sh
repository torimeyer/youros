#!/usr/bin/env bash
# Rule: worktree_cwd_guard (→1311)
# Structurally prevents cwd-leak where subagents in git worktrees run
# bash or fs_ops calls that land on main instead of their isolated branch.
#
# Root cause: mcp__ostk__bash routes through the MAIN ostk daemon
# (OSTK_SOCKET → main daemon). sh_run.rs defaults cwd to the daemon's
# project_root (= main checkout) when no cwd= is given. git commit/add
# without cwd= therefore commit to main.
#
# Two leak paths blocked:
#   1. bash: git write ops (commit/add/push/stash) without cwd= →
#      block and tell the agent the correct worktree path.
#   2. fs_ops: absolute path under parent repo root but NOT under the
#      worktree → block and show the corrected path.
#
# Activation: CLAUDE_PROJECT_DIR must contain /.claude/worktrees/agent-
# (set by spawn handler line 4680 of api/routers/agents.py).
#
# Called from: pre-tool-guard.sh
# Assumes: deny, log_rule_fire already sourced by caller.

# GIT_WRITE_PATTERN: operations that must have cwd= to stay on the branch.
_WCG_GIT_WRITE_RE='git (commit|add|push|stash|cherry-pick|rebase|merge)'

_wcg_is_worktree_agent() {
  [[ "${CLAUDE_PROJECT_DIR:-}" == *"/.claude/worktrees/agent-"* ]]
}

_wcg_parent_repo() {
  # Strip /.claude/worktrees/agent-<anything> suffix to get parent repo root.
  echo "${CLAUDE_PROJECT_DIR:-}" | sed 's|/.claude/worktrees/agent-.*||'
}

_worktree_cwd_guard_bash() {
  local tool="$1" input_json="$2"
  _wcg_is_worktree_agent || return 0

  local wt_path="${CLAUDE_PROJECT_DIR:-}"
  local parent_repo
  parent_repo=$(_wcg_parent_repo)

  # Parse cwd and cmd from tool input.
  local parsed
  parsed=$(INPUT_JSON="$input_json" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}', strict=False)
    ti = d.get('tool_input', {}) or {}
    cwd = (ti.get('cwd') or '').strip()
    cmd = (ti.get('cmd') or ti.get('command') or '').strip()
    sys.stdout.write(cwd + '\x1f' + cmd)
except Exception:
    sys.stdout.write('\x1f')
" 2>/dev/null)

  local cwd_val cmd_val
  IFS=$'\x1f' read -r cwd_val cmd_val <<<"${parsed:-}"

  # Only enforce for git write operations — other bash calls (osascript,
  # python3, sqlite3, etc.) don't touch the git index and are safe.
  if ! echo "$cmd_val" | grep -qE "$_WCG_GIT_WRITE_RE"; then
    return 0
  fi

  # Block only when cwd= is ABSENT. When an agent explicitly passes cwd=
  # (even to the parent repo), treat it as intentional — blocking would
  # prevent legitimate parent-repo commits (e.g. committing hook files
  # from a worktree agent). The incidents (→1304, →1310) were always
  # MISSING cwd=, never an explicit wrong one.
  if [ -z "$cwd_val" ]; then
    log_rule_fire "worktree_cwd_guard" "$tool" "block" \
      "git-write bash without cwd= in worktree agent"
    deny "git operation without cwd= in a worktree agent: the ostk daemon runs git from the main checkout. Add cwd=\"${wt_path}\" so the commit lands on your branch, not main. (→1311)"
  fi
}

_worktree_cwd_guard_fs_ops() {
  local tool="$1" input_json="$2"
  _wcg_is_worktree_agent || return 0

  local wt_path="${CLAUDE_PROJECT_DIR:-}"
  local parent_repo
  parent_repo=$(_wcg_parent_repo)

  # Only check quick-mode (single path= field). Batch ops carry per-item
  # paths; parsing them here is expensive and agents rarely use batch for
  # out-of-worktree paths. Extend if incidents prove otherwise.
  local path_val
  path_val=$(INPUT_JSON="$input_json" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}', strict=False)
    ti = d.get('tool_input', {}) or {}
    print((ti.get('path') or '').strip())
except Exception:
    print('')
" 2>/dev/null)

  [ -z "$path_val" ] && return 0

  # Block: absolute path is under the parent repo root but NOT under the
  # worktree. Relative paths are also dangerous (daemon resolves them
  # against main checkout cwd) but harder to rewrite safely; a follow-up
  # can tighten this when incidents prove it necessary.
  if [[ "$path_val" == "${parent_repo}/"* ]] && \
     [[ "$path_val" != "${wt_path}/"* ]]; then
    local suffix="${path_val#${parent_repo}/}"
    local corrected="${wt_path}/${suffix}"
    log_rule_fire "worktree_cwd_guard" "$tool" "block" \
      "fs_ops path under parent repo not worktree"
    deny "fs_ops path '${path_val}' points to the parent repo checkout, not your worktree. Use '${corrected}' instead. (→1311)"
  fi
}

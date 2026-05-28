#!/usr/bin/env bash
# Rule: work_close_guard
# Created: →1381
# Called from: pre-tool-guard.sh (Bash|mcp__ostk__bash section)
# Purpose: Block `ostk work close →NNN` when no commit on main references
#          that task ID. Prevents agents from closing tasks they haven't
#          actually fixed.
#
# Background: →1365 and →1374 were closed overnight by agents that had zero
#             commits on main referencing those task IDs. The kernel accepted
#             the close with no verification. The work was never done.
#
# Trigger: Bash/mcp__ostk__bash command matches `ostk work close`
# Guard:   Parses the task ID, runs git log main --oneline, greps for →ID.
#          Denies if no match found.
#
# --no-verify: pass to bypass the check for legitimate edge cases:
#   - Work landed without the task ID in the commit subject
#   - Fix is in a different repo
#   - Close is administrative (duplicate / won't-fix)
#
# Fail-open: if the task ID cannot be parsed, or git log fails, the check
#            is skipped and the close proceeds. This avoids blocking legitimate
#            closes due to tooling issues.

_work_close_guard_check() {
    local tool="${1:-Bash}" cmd="${2:-}"

    # Fast path: only intercept commands that actually invoke ostk work close
    # as a program, not as text inside a quoted argument (e.g. curl -d '...ostk work close...').
    # Use shlex tokenization so quoted strings are properly excluded.
    local is_close_cmd
    is_close_cmd=$(WCG_CMD="$cmd" python3 -c "
import os, shlex
cmd = os.environ.get('WCG_CMD', '').strip()
try:
    tokens = shlex.split(cmd)
    for i in range(len(tokens) - 2):
        if tokens[i] in ('ostk',) and tokens[i+1] == 'work' and tokens[i+2] == 'close':
            print('yes')
            break
except ValueError:
    # Unparseable (unclosed quote etc.) — fail-open, don't block
    pass
" 2>/dev/null)
    if [ "${is_close_cmd:-}" != "yes" ]; then
        return 0
    fi

    # --no-verify: explicit bypass via env var prefix in the command.
    # Use: OSTK_WORK_CLOSE_NO_VERIFY=1 ostk work close →NNN
    # The kernel ignores unknown env vars; the hook reads the assignment from
    # the command string so --no-verify doesn't need to be a kernel flag.
    if printf '%s' "$cmd" | grep -qE '(^|[[:space:]])OSTK_WORK_CLOSE_NO_VERIFY=1([[:space:]]|$)'; then
        log_rule_fire "work_close_guard" "$tool" "allow" "OSTK_WORK_CLOSE_NO_VERIFY=1 bypass present"
        return 0
    fi

    # Extract the numeric task ID.
    # Handles all of: →1374, 1374, "→1374", '→1374'
    # Uses env var to avoid the heredoc+pipe stdin conflict.
    local task_id
    task_id=$(WCG_CMD="$cmd" python3 -c "
import sys, re, os
cmd = os.environ.get('WCG_CMD', '')
# Strip everything before 'close' so we don't match numbers in flags
m = re.search(r'ostk\s+work\s+close\s+(.*)', cmd)
if not m:
    sys.exit(0)
after = m.group(1).strip()
# Find first run of digits (the task ID)
n = re.search(r'(\d+)', after)
if n:
    print(n.group(1))
" 2>/dev/null)

    if [ -z "$task_id" ]; then
        # Cannot parse — fail-open so valid closes aren't blocked
        log_rule_fire "work_close_guard" "$tool" "allow" "could not parse task ID from: ${cmd}, fail-open"
        return 0
    fi

    # Locate the repo root.
    # When running inside a worktree the CLAUDE_PROJECT_DIR is the worktree path;
    # git log main still works because all worktrees share the same object store.
    local search_path="${CLAUDE_PROJECT_DIR:-${PWD}}"
    local repo_root
    repo_root=$(git -C "$search_path" rev-parse --show-toplevel 2>/dev/null || echo "")

    if [ -z "$repo_root" ]; then
        log_rule_fire "work_close_guard" "$tool" "allow" "could not determine repo root, fail-open"
        return 0
    fi

    # git log main — check both → and -> arrow styles
    local match
    match=$(git -C "$repo_root" log main --oneline 2>/dev/null \
            | grep -E "(→|->)${task_id}([^0-9]|$)" \
            | head -1 || true)

    if [ -z "$match" ]; then
        log_rule_fire "work_close_guard" "$tool" "block" \
            "no commit on main references →${task_id}"
        deny "work_close_guard: no commit on main references →${task_id}. The work may not be done yet. If the fix landed without the task ID in the commit subject, or the close is administrative, prefix the command with OSTK_WORK_CLOSE_NO_VERIFY=1 to override."
    fi

    log_rule_fire "work_close_guard" "$tool" "allow" \
        "commit on main references →${task_id}: ${match}"
    return 0
}

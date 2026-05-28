#!/usr/bin/env bash
# Consolidated PreToolUse:* guard.
# Replaces: bash-guards.sh, ostk-first.sh, saa-must-spawn.sh,
#           monitor-misuse-guard.sh, needle-hygiene.sh, measure-before-edit.sh
# Agent and heartbeat/drain are handled by pre-agent-guard.sh and heartbeat-and-drain.sh.

HOOK_NAME=$(basename "$0")
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/deny.sh"
init_deny_traps
. "$LIB/load-rule.sh"
. "$LIB/log-fire.sh"
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

set -u
INPUT=$(cat)

# Single-pass JSON parse: extract tool_name and command.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
cmd = (ti.get("command") or ti.get("cmd") or "")
sys.stdout.write(tool + "\x1f" + cmd)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL CMD <<<"$PARSED"

# Agent is handled by pre-agent-guard.sh; skip it here.
case "$TOOL" in
    Agent|Task) exit 0 ;;
esac

# ---- Dispatch by tool ----

case "$TOOL" in
    # --- Bash|Monitor|mcp__ostk__bash: bash guards + tool-specific rules ---
    Bash|Monitor|mcp__ostk__bash)
        . "$LIB/rules/bash_guards.sh"
        rule_enabled bash_guards && _bash_guards_check "$TOOL" "$CMD"

        if [ "$TOOL" = "Monitor" ]; then
            # Monitor: also check for misuse and arm ADHD sentinel
            . "$LIB/rules/monitor_misuse_guard.sh"
            rule_enabled monitor_misuse_guard && _monitor_misuse_guard_check "$TOOL" "$CMD"

            # ADHD monitor pairing: arm sentinel when Monitor fires
            . "$LIB/rules/adhd_monitor_pairing.sh"
            if rule_enabled adhd_monitor_pairing; then
                SENTINEL_PATH=$(INPUT_JSON="$INPUT" python3 -c "
import os, json
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}')
    sid = (d.get('session_id') or '').strip() or os.environ.get('CLAUDE_SESSION_ID','').strip() or 'default'
    print(os.path.join(os.path.expanduser('~'), '.myos', f'.adhd-monitor-armed-{sid}'))
except Exception:
    print('')
" 2>/dev/null)
                _adhd_monitor_pairing_arm "$TOOL" "$SENTINEL_PATH"
            fi
        fi

        if [ "$TOOL" = "Bash" ] || [ "$TOOL" = "mcp__ostk__bash" ]; then
            . "$LIB/rules/ostk_first.sh"
            rule_enabled ostk_first && _ostk_first_check "$TOOL" "$CMD"
            . "$LIB/rules/saa_must_spawn.sh"
            rule_enabled saa_must_spawn && _saa_must_spawn_check "$TOOL" "$CMD"
            . "$LIB/rules/worktree_cwd_guard.sh"
            rule_enabled worktree_cwd_guard && _worktree_cwd_guard_bash "$TOOL" "$INPUT"
            . "$LIB/rules/main_commit_guard.sh"
            rule_enabled main_commit_guard && _main_commit_guard_check "$TOOL" "$CMD" "$INPUT"
        fi
        ;;

    # --- Read|Edit|Write|Grep|Glob ---
    Read|Edit|Write|Grep|Glob)
        . "$LIB/rules/ostk_first.sh"
        rule_enabled ostk_first && _ostk_first_check "$TOOL" "$CMD"
        . "$LIB/rules/saa_must_spawn.sh"
        rule_enabled saa_must_spawn && _saa_must_spawn_check "$TOOL" "$CMD"

        if [ "$TOOL" = "Edit" ] || [ "$TOOL" = "Write" ]; then
            . "$LIB/rules/measure_before_edit.sh"
            if rule_enabled measure_before_edit; then
                # Read last user message for perf-keyword check
                LAST_MSG=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
proj = os.environ.get('CLAUDE_PROJECT_DIR','')
if not proj:
    import subprocess
    proj = subprocess.check_output(['git','rev-parse','--show-toplevel'], stderr=subprocess.DEVNULL, cwd='.').decode().strip()
import re
slug = re.sub(r'/', '-', proj)
import glob as g
files = sorted(g.glob(os.path.expanduser(f'~/.claude/projects/{slug}/*.jsonl')), key=os.path.getmtime, reverse=True)
if not files:
    sys.exit(0)
with open(files[0]) as f:
    lines = f.readlines()
for line in reversed(lines):
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        if d.get('type') == 'user':
            c = d.get('message',{}).get('content','')
            if isinstance(c, str):
                print(c.lower())
                break
    except Exception:
        continue
" 2>/dev/null)
                _measure_before_edit_check "$TOOL" "${LAST_MSG:-}"
            fi
        fi
        ;;

    # --- mcp__ostk__fs_ops: worktree path guard ---
    mcp__ostk__fs_ops)
        . "$LIB/rules/worktree_cwd_guard.sh"
        rule_enabled worktree_cwd_guard && _worktree_cwd_guard_fs_ops "$TOOL" "$INPUT"
        ;;

    # --- mcp__ostk__needle* / mcp__ostk__add* ---
    mcp__ostk__needle*|mcp__ostk__add*)
        . "$LIB/rules/task_hygiene.sh"
        if rule_enabled task_hygiene; then
            # Parse needle fields
            NHG_PARSED=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}')
    ti = d.get('tool_input', {}) or {}
    title = ti.get('title', '') or ''
    priority = ti.get('priority', '') or ''
    ac = ti.get('ac', '') or ''
    sys.stdout.write(title + '\x1f' + priority + '\x1f' + ac)
except Exception:
    pass
" 2>/dev/null)
            IFS=$'\x1f' read -r NHG_TITLE NHG_PRIORITY NHG_AC <<<"${NHG_PARSED:-}"
            _task_hygiene_check "$TOOL" "${NHG_TITLE:-}" "${NHG_PRIORITY:-}" "${NHG_AC:-}"
        fi
        ;;

    *)
        # Unknown tool: no rules apply
        exit 0
        ;;
esac

exit 0

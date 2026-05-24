#!/usr/bin/env bash
# Consolidated PostToolUse:* watch.
# Replaces: edit-postwatch.sh, bash-postwatch.sh, measure-before-edit.sh (PostToolUse path)

HOOK_NAME=$(basename "$0")
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/load-rule.sh"
. "$LIB/log-fire.sh"
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

set -u
INPUT=$(cat 2>/dev/null || true)

# Single-pass parse: tool_name, file_path, command.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
path = ti.get("file_path", "") or ""
cmd = ti.get("command") or ti.get("cmd") or ""
sys.stdout.write(tool + "\x1f" + path + "\x1f" + cmd)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL FILE_PATH CMD <<<"$PARSED"

# Export HOOK_INPUT for functions that need the full payload
export HOOK_INPUT="$INPUT"

case "$TOOL" in
    Edit|Write)
        . "$LIB/rules/edit_postwatch.sh"
        rule_enabled edit_postwatch && _edit_postwatch_check "$TOOL" "$FILE_PATH"
        # bash_postwatch also covers Edit|Write for the retry-queue and native-block-recovery
        . "$LIB/rules/bash_postwatch.sh"
        rule_enabled bash_postwatch && _bash_postwatch_check "$TOOL" "$CMD"
        ;;
    Bash|Read|Grep|Glob)
        . "$LIB/rules/bash_postwatch.sh"
        rule_enabled bash_postwatch && _bash_postwatch_check "$TOOL" "$CMD"
        ;;
    Monitor)
        # Detect Monitor internal error in ADHD mode: disarm sentinel + emit reminder.
        . "$LIB/rules/adhd_monitor_pairing.sh"
        if rule_enabled adhd_monitor_pairing && [ -f "$HOME/.myos/.adhd_mode" ]; then
            MON_PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
US = '\x1f'
try:
    d = json.loads(os.environ.get('INPUT_JSON','') or '{}', strict=False)
except Exception:
    sys.exit(0)
sid = (d.get('session_id') or '').strip() or os.environ.get('CLAUDE_SESSION_ID','').strip() or 'default'
sentinel = os.path.join(os.path.expanduser('~'), '.myos', f'.adhd-monitor-armed-{sid}')
resp = d.get('tool_response', '') or ''
if isinstance(resp, dict):
    resp = str(resp.get('output', '') or resp.get('content', '') or '')
resp_lower = resp.lower()
failed = any(x in resp_lower for x in ['internal error', 'missing due to', 'tool result missing', 'failed to start'])
sys.stdout.write(sentinel + US + ('1' if failed else '0'))
PY
            )
            IFS=$'\x1f' read -r MON_SENTINEL MON_FAILED <<<"${MON_PARSED:-}"
            if [ "${MON_FAILED:-0}" = "1" ]; then
                _adhd_monitor_pairing_disarm "Monitor" "${MON_SENTINEL:-}" "Monitor returned internal error"
                printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"ADHD-MODE MONITOR FAILURE: Monitor returned an internal error and the sentinel has been disarmed. The next Agent spawn will be BLOCKED until a working Monitor is armed. IMMEDIATELY: (1) retry Monitor with a simpler command (a plain `while true; do sleep 60; echo tick; done` loop), OR (2) call ScheduleWakeup(delaySeconds=60) as a fallback heartbeat. Never continue in ADHD mode without a confirmed-running heartbeat."}}\n'
            fi
        fi
        ;;
    mcp__ostk__bash|mcp__ostk__shell)
        # Authoritative open-needle count after `ostk boot` — the [loadavg] line in
        # boot output is unreliable (see feedback_boot_loadavg_needles_unreliable.md);
        # this prints the real count alongside it so the model can't quote the wrong one.
        if [[ "$CMD" =~ ^ostk[[:space:]]+boot ]]; then
            _needle_count=$(ostk work list --status open 2>/dev/null | grep -c '^  →' || echo 0)
            printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"AUTHORITATIVE OPEN NEEDLES: %s (the [loadavg] needles line in ostk boot output is unreliable per feedback_boot_loadavg_needles_unreliable.md; this is the real count from ostk work list --status open)."}}\n' "$_needle_count"
        fi
        ;;
    *)
        exit 0
        ;;
esac

exit 0

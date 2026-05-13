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
    *)
        exit 0
        ;;
esac

exit 0

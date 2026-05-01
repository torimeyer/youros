#!/bin/bash
# Combined PostToolUse watch for Edit and Write.
#
# Replaces two separate hooks with one parse-once dispatcher:
#   - check-tsx              (run tsc --noEmit on edited .tsx/.ts files,
#                             warn-only, skip if tsc isn't installed)
#   - saa-after-3-hypotheses (track edit cycles per file, suggest saa
#                             after 3 edits, tori-gated)
#
# All checks are non-blocking (exit 0). Both fail-open: tsc missing
# means check-tsx skips; missing tori flag means saa reminder skips.

set -u
INPUT=$(cat 2>/dev/null || true)

# Parse once: tool_name, file_path.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
path = ti.get("file_path", "") or ""
sys.stdout.write(tool + "\x1f" + path)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL FILE_PATH <<<"$PARSED"

case "$TOOL" in
    Edit|Write) : ;;
    *) exit 0 ;;
esac

# ---------------------------------------------------------------------
# 1. check-tsx: run tsc on .tsx/.ts files in app/src/. Warn-only.
#    Skip if node_modules/.bin/tsc isn't there (fresh install).
# ---------------------------------------------------------------------
case "$FILE_PATH" in
    */app/src/*.tsx|*/app/src/*.ts)
        APP_DIR=$(echo "$FILE_PATH" | sed 's|/src/.*|/|')
        if [ -f "${APP_DIR}tsconfig.json" ] && [ -x "${APP_DIR}node_modules/.bin/tsc" ]; then
            TSC_OUTPUT=$(cd "$APP_DIR" && npx tsc --noEmit 2>&1) || {
                echo "TypeScript warnings after edit (non-blocking):" >&2
                echo "$TSC_OUTPUT" | grep -E "error TS" | head -10 >&2
            }
        fi
        ;;
esac

# ---------------------------------------------------------------------
# 2. saa-after-3-hypotheses: count edits per file in 30-min window,
#    surface saa reminder after 3+. Tori-gated.
# ---------------------------------------------------------------------
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ -f "$TORI_CONFIG" ] && grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    if [ -n "$FILE_PATH" ]; then
        STATE="${MYOS_EDIT_CYCLES_STATE:-${HOME}/.myos/hooks/edit-cycles.json}"
        mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
        FILE_PATH="$FILE_PATH" HOOK_STATE="$STATE" python3 <<'PYEOF' 2>/dev/null
import os, sys, json, time
file_path = os.environ.get("FILE_PATH", "")
state_file = os.environ.get("HOOK_STATE", "")
if not file_path:
    sys.exit(0)
now = time.time()
WINDOW = 1800
state = {"files": {}}
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    pass
files = {k: v for k, v in state.get("files", {}).items()
         if now - v.get("last_ts", 0) < WINDOW}
entry = files.get(file_path, {"count": 0, "last_ts": now})
entry["count"] += 1
entry["last_ts"] = now
files[file_path] = entry
try:
    with open(state_file, "w") as f:
        json.dump({"files": files}, f)
except Exception:
    pass
count = entry["count"]
if count >= 3:
    print("")
    print("SAA REMINDER (non-blocking -- rule 2 from 2026-04-27 retro, {} edits this session):".format(count))
    print("  File: {}".format(file_path))
    print("  3+ consecutive edits is the inline-diagnosis limit. Spawn a subagent (saa) for")
    print("  remaining hypotheses instead of continuing inline. See feedback_saa_rules.md.")
PYEOF
    fi
fi

exit 0

#!/bin/bash
# PostToolUse Edit|Write: track edit cycles per file within a 30-minute window.
# After 3 edits to the same file, surface an saa reminder (non-blocking).
# Rule 2 from 2026-04-27 retro.
#
# Tori-personal gate: uses "saa" vocabulary. No-op unless
# ~/.myos/config.json has "enable_tori_rules": true.
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ ! -f "$TORI_CONFIG" ] || ! grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    exit 0
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
case "$TOOL" in
  Edit|Write) ;;
  *) exit 0 ;;
esac

TMP_INPUT=$(mktemp -t saa-cycles.XXXXXX)
trap 'rm -f "$TMP_INPUT"' EXIT
printf '%s' "$INPUT" > "$TMP_INPUT"

STATE="${MYOS_EDIT_CYCLES_STATE:-${HOME}/.myos/hooks/edit-cycles.json}"
mkdir -p "$(dirname "$STATE")" 2>/dev/null || true

HOOK_TMP="$TMP_INPUT" HOOK_STATE="$STATE" python3 <<'PYEOF'
import os, sys, json, time

inp_file = os.environ.get("HOOK_TMP", "")
state_file = os.environ.get("HOOK_STATE", "")

try:
    with open(inp_file) as f:
        d = json.load(f)
    file_path = d.get("tool_input", {}).get("file_path", "")
except Exception:
    sys.exit(0)

if not file_path:
    sys.exit(0)

now = time.time()
WINDOW = 1800  # 30-minute session window

state = {"files": {}}
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    pass

files = {k: v for k, v in state.get("files", {}).items() if now - v.get("last_ts", 0) < WINDOW}
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
exit 0
